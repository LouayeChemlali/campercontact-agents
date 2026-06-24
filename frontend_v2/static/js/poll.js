/* Live polling for the results page. */
(function () {
  'use strict';

  const cfg = window.POLL_CONFIG || {};
  if (!cfg.runId || !cfg.statusUrl || !Array.isArray(cfg.profileIds)) return;

  const startedAt = Date.now();
  let done = false;
  let lastStatusPhrase = '';

  const timerInterval = setInterval(updateTimer, 1000);
  updateTimer();
  pollOnce();

  function updateTimer() {
    if (done) return;
    const elapsed = Math.floor((Date.now() - startedAt) / 1000);
    const mins = Math.floor(elapsed / 60);
    const secs = String(elapsed % 60).padStart(2, '0');
    const el = document.getElementById('elapsed-timer');
    if (el) el.textContent = mins + ':' + secs;
  }

  async function pollOnce() {
    if (done) return;

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);

    try {
      const res = await fetch(buildUrl(), {
        headers: { 'Accept': 'application/json' },
        signal: controller.signal,
      });
      clearTimeout(timeout);
      if (!res.ok) throw new Error('Server returned ' + res.status);
      const data = await res.json();
      handleData(data);
    } catch (err) {
      clearTimeout(timeout);
      if (err.name === 'AbortError') return setTimeout(pollOnce, cfg.intervalMs || 5000);
      handleNetworkError(err);
    }
  }

  function buildUrl() {
    const base = cfg.statusUrl.replace('__RUN_ID__', encodeURIComponent(cfg.runId));
    const params = new URLSearchParams({
      triggered_at: cfg.triggeredAt || '',
      profile_ids: cfg.profileIds.join(','),
    });
    return base + '?' + params.toString();
  }

  function handleData(data) {
    const serverElapsed = data.elapsed_seconds || 0;
    const clientElapsed = (Date.now() - startedAt) / 1000;
    const elapsed = Math.max(serverElapsed, clientElapsed);

    let readyCount = 0;

    for (const profile of data.profiles || []) {
      if (!profile.ready) continue;
      readyCount++;

      const card = document.getElementById('profile-card-' + profile.profile_id);
      if (!card || !card.querySelector('[data-loading]')) continue;

      const hints = profile.hints || [];
      if (hints.length === 0) {
        renderNoHintsCard(card, profile.profile_id);
      } else {
        renderProfile(card, profile, data.run_id);
      }
    }

    const total = (data.profiles || []).length;

    if (data.all_ready) {
      finish('success', readyCount, total);
      return;
    }

    if (elapsed >= (cfg.timeoutMs || 600000) / 1000) {
      if (readyCount === 0 && elapsed >= 90) {
        finish('no_rows', 0, total);
        return;
      }
      finish('timeout', readyCount, total);
      return;
    }

    writeStatus(
      readyCount + ' of ' + total + ' profile' + (total !== 1 ? 's' : '') +
      ' loaded. Checking again in ' + ((cfg.intervalMs || 5000) / 1000) + 's...'
    );

    setTimeout(pollOnce, cfg.intervalMs || 5000);
  }

  function finish(state, readyCount, total) {
    done = true;
    clearInterval(timerInterval);
    hideSpinner();

    if (state === 'success') {
      writeStatus('All Confidence Agent results loaded.');
      return;
    }

    if (state === 'no_rows') {
      writeStatus('Pipeline finished polling, but no Confidence Agent rows were found yet.');
      showNoHintsBanner();
      return;
    }

    if (state === 'timeout') {
      writeStatus(
        readyCount + ' of ' + total + ' profile' + (total !== 1 ? 's' : '') +
        ' loaded. Pipeline polling timed out.'
      );
      showTimeoutBanner();
    }
  }

  function handleNetworkError(err) {
    console.error('[poll.js] fetch failed:', err);
    done = true;
    clearInterval(timerInterval);
    hideSpinner();
    writeStatus('Could not reach the frontend server.');
    showErrorBanner();
  }

  function renderProfile(card, profile, runId) {
    const summary = profile.summary || {};
    const hints = profile.hints || [];
    const counts = countLevels(hints);

    const tmpl = document.getElementById('tmpl-profile-loaded');
    const node = tmpl.content.cloneNode(true);

    fill(node, 'profile_name', summary.profile_name || firstNonEmpty(hints, 'profile_name') || '#' + profile.profile_id);
    fill(node, 'profile_id', profile.profile_id);
    fill(node, 'hint_count', formatCounts(counts, hints.length));
    fill(node, 'profile_summary_text', summary.profile_summary_text || buildSummaryText(counts, hints.length));
    fill(node, 'run_id', runId || cfg.runId);
    fill(node, 'created_at', formatDate(summary.created_at || firstNonEmpty(hints, 'created_at')));

    const high = hints.filter(h => h.confidence_level === 'HIGH');
    const medium = hints.filter(h => h.confidence_level === 'MEDIUM');
    const low = hints.filter(h => h.confidence_level === 'LOW');
    const other = hints.filter(h => !['HIGH', 'MEDIUM', 'LOW'].includes(h.confidence_level));

    renderSection(node, 'recommended', high, 'Recommended updates', false, 'hint');
    renderSection(node, 'review', medium.concat(other), 'Review-needed hints', false, 'hint');
    renderSection(node, 'low-confidence', low, 'Rejected / low-confidence candidates', true, 'low');

    const actions = Array.isArray(summary.top_actions) ? summary.top_actions : [];
    if (actions.length > 0) {
      const section = node.querySelector('[data-section="top-actions"]');
      const list = node.querySelector('[data-field="top_actions_list"]');
      actions.forEach(action => {
        const li = document.createElement('li');
        li.textContent = action;
        list.appendChild(li);
      });
      section.classList.remove('hidden');
    }

    card.querySelector('[data-loading]').remove();
    card.appendChild(node);
  }

  function renderSection(node, sectionName, hints, title, collapsed, mode) {
    if (!hints.length) return;
    const section = node.querySelector('[data-section="' + sectionName + '"]');
    const titleEl = section.querySelector('[data-field="section_title"]');
    const countEl = section.querySelector('[data-field="section_count"]');
    const content = section.querySelector('[data-field="section_content"]');
    const isLow = mode === 'low';

    titleEl.textContent = title;
    countEl.textContent = hints.length + (isLow ? ' candidate' : ' hint') + (hints.length !== 1 ? 's' : '');

    if (collapsed) {
      const details = document.createElement('details');
      details.className = 'bg-red-50 border border-red-100 rounded-xl p-3';
      const summary = document.createElement('summary');
      summary.className = 'cursor-pointer text-sm font-medium text-red-800';
      summary.textContent = isLow
        ? 'Show rejected candidates for debugging/manual review'
        : 'Show hidden results';
      const explanation = document.createElement('p');
      explanation.className = 'mt-2 text-xs text-red-700 leading-relaxed';
      explanation.textContent = isLow
        ? 'These are not recommended updates. The system kept them hidden because the evidence or entity match was too weak.'
        : '';
      const inner = document.createElement('div');
      inner.className = 'mt-3';
      inner.appendChild(buildHintsBlock(hints, mode));
      details.appendChild(summary);
      if (isLow) details.appendChild(explanation);
      details.appendChild(inner);
      content.appendChild(details);
    } else {
      content.appendChild(buildHintsBlock(hints, mode));
    }

    section.classList.remove('hidden');
  }

  function buildHintsBlock(hints, mode) {
    if (mode === 'low') return buildLowCandidateCards(hints);
    return hints.length <= 5 ? buildHintCards(hints) : buildHintTable(hints);
  }

  function buildLowCandidateCards(hints) {
    const grid = document.createElement('div');
    grid.className = 'grid gap-3';
    hints.forEach(hint => grid.appendChild(cloneLowCandidateCard(hint)));
    return grid;
  }

  function cloneLowCandidateCard(hint) {
    const card = document.createElement('div');
    card.className = 'border border-red-100 bg-white rounded-xl p-4';

    const header = document.createElement('div');
    header.className = 'flex items-start justify-between gap-2 mb-3';

    const left = document.createElement('div');
    const field = document.createElement('p');
    field.className = 'text-sm font-semibold text-gray-800';
    field.textContent = humanize(hint.field_name || 'candidate');
    const label = document.createElement('p');
    label.className = 'text-xs text-red-700 mt-0.5';
    label.textContent = 'Rejected candidate — not recommended for automatic update';
    left.appendChild(field);
    left.appendChild(label);

    const badges = document.createElement('div');
    badges.className = 'flex items-center gap-1.5 shrink-0';
    const confidenceBadge = document.createElement('span');
    const decisionBadge = document.createElement('span');
    applyConfidenceBadge(confidenceBadge, hint.confidence_level, hint.confidence_score);
    applyDecisionBadge(decisionBadge, hint.confidence_decision);
    badges.appendChild(confidenceBadge);
    badges.appendChild(decisionBadge);

    header.appendChild(left);
    header.appendChild(badges);
    card.appendChild(header);

    const grid = document.createElement('div');
    grid.className = 'grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs mb-3';
    grid.appendChild(labelValue('Candidate value', candidateValue(hint)));
    grid.appendChild(labelValue('Source domain', hint.source_domain_internal || 'No source domain'));
    card.appendChild(grid);

    const reason = document.createElement('p');
    reason.className = 'text-xs text-gray-600 leading-relaxed mb-2';
    reason.textContent = hint.confidence_reason || 'No confidence reason available.';
    card.appendChild(reason);

    const created = document.createElement('p');
    created.className = 'text-xs text-gray-400';
    created.textContent = hint.created_at ? 'Created: ' + formatDate(hint.created_at) : '';
    card.appendChild(created);

    fillTechnicalDetails(card, hint);
    return card;
  }

  function labelValue(labelText, valueText) {
    const wrapper = document.createElement('div');
    const label = document.createElement('span');
    label.className = 'block text-gray-400 mb-0.5';
    label.textContent = labelText;
    const value = document.createElement('span');
    value.className = 'text-gray-800 font-medium break-all';
    value.textContent = valueText || 'Unavailable';
    wrapper.appendChild(label);
    wrapper.appendChild(value);
    return wrapper;
  }

  function candidateValue(hint) {
    return hint.source_url_internal || hint.suggested_value || hint.hint_text || 'Unavailable';
  }

  function buildHintCards(hints) {
    const grid = document.createElement('div');
    grid.className = 'grid gap-3';
    hints.forEach(hint => grid.appendChild(cloneHintCard(hint)));
    return grid;
  }

  function cloneHintCard(hint) {
    const tmpl = document.getElementById('tmpl-hint-card');
    const node = tmpl.content.cloneNode(true);

    fill(node, 'field_name', humanize(hint.field_name || ''));
    fill(node, 'hint_text', hint.hint_text || 'No hint text available.');
    fill(node, 'suggested_action', hint.suggested_action || 'Review manually');
    fill(node, 'confidence_reason', hint.confidence_reason || 'No confidence reason available.');
    fill(node, 'source_domain', hint.source_domain_internal || 'No source domain');
    fill(node, 'created_at', formatDate(hint.created_at));

    applyConfidenceBadge(node.querySelector('[data-field="confidence_badge"]'), hint.confidence_level, hint.confidence_score);
    applyDecisionBadge(node.querySelector('[data-field="decision_badge"]'), hint.confidence_decision);
    fillTechnicalDetails(node, hint);

    return node;
  }

  function buildHintTable(hints) {
    const wrapper = document.createElement('div');
    wrapper.className = 'overflow-x-auto';

    const table = document.createElement('table');
    table.className = 'w-full text-left';

    const thead = document.createElement('thead');
    const headerRow = document.createElement('tr');
    headerRow.className = 'border-b border-gray-200';
    ['Field', 'Hint', 'Suggested action', 'Confidence', 'Decision', 'Source', 'Created'].forEach(label => {
      const th = document.createElement('th');
      th.className = 'pb-2 pr-4 text-xs font-semibold text-gray-500 uppercase tracking-wide';
      th.textContent = label;
      headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);

    const tbody = document.createElement('tbody');
    hints.forEach(hint => tbody.appendChild(cloneHintRow(hint)));

    table.appendChild(thead);
    table.appendChild(tbody);
    wrapper.appendChild(table);
    return wrapper;
  }

  function cloneHintRow(hint) {
    const tmpl = document.getElementById('tmpl-hint-row');
    const node = tmpl.content.cloneNode(true);

    fill(node, 'field_name', humanize(hint.field_name || ''));
    fill(node, 'hint_text', hint.hint_text || '');
    fill(node, 'suggested_action', hint.suggested_action || 'Review manually');
    fill(node, 'source_domain', hint.source_domain_internal || '');
    fill(node, 'created_at', formatDate(hint.created_at));
    applyConfidenceBadge(node.querySelector('[data-field="confidence_badge"]'), hint.confidence_level, hint.confidence_score);
    applyDecisionBadge(node.querySelector('[data-field="decision_badge"]'), hint.confidence_decision);

    return node;
  }

  const CONFIDENCE_CLASSES = {
    HIGH: 'bg-green-100 text-green-800',
    MEDIUM: 'bg-amber-100 text-amber-800',
    LOW: 'bg-red-100 text-red-800',
  };

  function applyConfidenceBadge(el, level, score) {
    if (!el) return;
    const normalized = level || 'UNKNOWN';
    el.className = 'badge ' + (CONFIDENCE_CLASSES[normalized] || 'bg-gray-100 text-gray-700');
    el.textContent = humanize(normalized) + (score != null ? ' (' + Math.round(score * 100) + '%)' : '');
  }

  const DECISION_CLASSES = {
    show: 'bg-green-100 text-green-800',
    recommended_update: 'bg-green-100 text-green-800',
    review: 'bg-amber-100 text-amber-800',
    manual_review: 'bg-amber-100 text-amber-800',
    hide_or_manual_review: 'bg-red-100 text-red-800',
    hide: 'bg-red-100 text-red-800',
  };

  function applyDecisionBadge(el, decision) {
    if (!el) return;
    const key = (decision || 'review').toLowerCase();
    el.className = 'badge ' + (DECISION_CLASSES[key] || 'bg-gray-100 text-gray-700');
    el.textContent = humanize(key);
  }

  function fillTechnicalDetails(node, hint) {
    const details = node.querySelector('[data-field="technical_details"]');
    if (!details) return;
    const rows = [
      ['source_url_internal', hint.source_url_internal],
      ['source_reliability_score', hint.source_reliability_score],
      ['normalized_uplift_score', hint.normalized_uplift_score],
      ['contradiction_penalty', hint.contradiction_penalty],
      ['confidence_id', hint.confidence_id],
      ['hint_id', hint.hint_id],
    ].filter(([, value]) => value !== null && value !== undefined && value !== '');

    if (!rows.length) {
      details.remove();
      return;
    }

    const list = details.querySelector('[data-field="technical_details_list"]');
    rows.forEach(([label, value]) => {
      const div = document.createElement('div');
      div.className = 'grid grid-cols-3 gap-2 py-0.5';
      const k = document.createElement('span');
      k.className = 'text-gray-400';
      k.textContent = label;
      const v = document.createElement('span');
      v.className = 'col-span-2 text-gray-600 break-all';
      v.textContent = String(value);
      div.appendChild(k);
      div.appendChild(v);
      list.appendChild(div);
    });
  }

  function countLevels(hints) {
    return hints.reduce((acc, h) => {
      const level = h.confidence_level || 'UNKNOWN';
      acc[level] = (acc[level] || 0) + 1;
      return acc;
    }, {});
  }

  function formatCounts(counts, total) {
    const parts = [];
    if (counts.HIGH) parts.push(counts.HIGH + ' high');
    if (counts.MEDIUM) parts.push(counts.MEDIUM + ' medium');
    if (counts.LOW) parts.push(counts.LOW + ' low');
    return parts.length ? parts.join(' · ') : total + ' hint' + (total !== 1 ? 's' : '');
  }

  function buildSummaryText(counts, total) {
    if (counts.HIGH) return total + ' hint(s) found. High-confidence hints are shown as recommended updates.';
    if (counts.MEDIUM) return total + ' hint(s) found. These should be reviewed before updating the profile.';
    if (counts.LOW) return total + ' low-confidence candidate(s) found. They are hidden by default because the match is weak and are not recommended updates.';
    return total + ' hint(s) found.';
  }

  function firstNonEmpty(items, key) {
    for (const item of items || []) {
      if (item && item[key]) return item[key];
    }
    return '';
  }

  const _dateFmt = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/Amsterdam',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });

  function parseUtcDate(value) {
    if (!value) return null;
    let s = String(value);
    const hasTimezone = /[zZ]$|[+-]\d{2}:\d{2}$/.test(s);
    if (!hasTimezone) s += 'Z';
    return new Date(s);
  }

  function formatDate(value) {
    if (!value) return '';
    try {
      const dt = parseUtcDate(value);
      if (!dt || Number.isNaN(dt.getTime())) return value;
      return _dateFmt.format(dt) + ' Amsterdam time';
    } catch (_) {
      return value;
    }
  }

  function humanize(value) {
    return String(value || '')
      .replace(/_/g, ' ')
      .toLowerCase()
      .replace(/^\w|\s\w/g, c => c.toUpperCase());
  }

  function fill(node, field, text) {
    const el = node.querySelector('[data-field="' + field + '"]');
    if (el) el.textContent = text ?? '';
  }

  function writeStatus(phrase) {
    if (phrase === lastStatusPhrase) return;
    lastStatusPhrase = phrase;
    const el = document.getElementById('status-text');
    if (el) el.textContent = phrase;
  }

  function hideSpinner() {
    const s = document.getElementById('status-spinner');
    if (s) s.classList.add('hidden');
  }

  function showNoHintsBanner() {
    const banner = document.getElementById('no-hints-banner');
    if (banner) banner.classList.remove('hidden');
  }

  function renderNoHintsCard(card, profileId) {
    card.querySelector('[data-loading]').remove();
    const div = document.createElement('div');
    div.className = 'p-6 text-sm text-gray-500';
    div.textContent = 'Profile #' + profileId + ' was processed, but no Confidence Agent rows were found for this run.';
    card.appendChild(div);
  }

  function showTimeoutBanner() {
    const banner = document.getElementById('timeout-banner');
    if (!banner) return;
    banner.classList.remove('hidden');

    const pending = cfg.profileIds.filter(pid => {
      const card = document.getElementById('profile-card-' + pid);
      return card && card.querySelector('[data-loading]');
    });
    const retryIds = document.getElementById('retry-ids');
    if (retryIds) retryIds.value = pending.join(',');

    const retryBtn = document.getElementById('retry-btn');
    if (retryBtn) {
      retryBtn.addEventListener('click', () => {
        document.getElementById('retry-form').submit();
      }, { once: true });
    }
  }

  function showErrorBanner() {
    const container = document.getElementById('profiles-container');
    if (!container) return;

    const banner = document.createElement('div');
    banner.className = 'mt-6 bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg text-sm';
    banner.setAttribute('role', 'alert');
    banner.textContent = 'Could not reach the server. Refresh the page to try again.';
    container.after(banner);
  }
}());
