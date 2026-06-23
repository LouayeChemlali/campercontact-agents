/*
 * poll.js - results page polling module for Campercontact Moderator Tools.
 *
 * Reads window.POLL_CONFIG (written by results.html), polls /api/status,
 * and fills in profile cards as data arrives. Self-contained IIFE, no deps.
 */

(function () {
  'use strict';

  const cfg = window.POLL_CONFIG;

  // Client-side start time as fallback if the server's elapsed_seconds is 0.
  const startedAt = Date.now();

  // AbortController lets us cancel in-flight fetches when the user navigates away.
  const controller = new AbortController();
  window.addEventListener('beforeunload', () => { controller.abort(); clearInterval(timerInterval); });

  // Once done is true, no further polls are scheduled.
  let done = false;

  // Phrase last written to the status bar. Compared before each write to avoid redundant aria-live updates.
  let lastStatusPhrase = '';

  // -------------------------------------------------------------------------
  // Elapsed timer: ticks every second, independent of the poll cycle.
  // -------------------------------------------------------------------------
  const timerEl = document.getElementById('elapsed-timer');

  function tickTimer() {
    if (!timerEl) return;
    const secs = Math.floor((Date.now() - startedAt) / 1000);
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    timerEl.textContent = m + ':' + String(s).padStart(2, '0');
  }

  tickTimer();
  const timerInterval = setInterval(tickTimer, 1000);

  // -------------------------------------------------------------------------
  // Kick off after one interval so the page can paint first.
  // -------------------------------------------------------------------------
  setTimeout(pollOnce, cfg.intervalMs);

  // -------------------------------------------------------------------------
  // Core polling loop (recursive setTimeout, not setInterval).
  // Schedules the next call only after the current fetch completes,
  // so a slow BigQuery response cannot cause request pile-up.
  // -------------------------------------------------------------------------
  async function pollOnce() {
    if (done) return;

    const url = buildUrl();

    try {
      const res = await fetch(url, { signal: controller.signal });
      if (!res.ok) throw new Error('Server returned ' + res.status);
      const data = await res.json();
      handleData(data);
    } catch (err) {
      if (err.name === 'AbortError') return;
      handleNetworkError(err);
    }
  }

  function buildUrl() {
    const base = cfg.statusUrl.replace('__RUN_ID__', encodeURIComponent(cfg.runId));
    const params = new URLSearchParams({
      triggered_at: cfg.triggeredAt,
      profile_ids: cfg.profileIds.join(','),
    });
    return base + '?' + params.toString();
  }

  // -------------------------------------------------------------------------
  // Data handler — called on each successful poll response.
  // -------------------------------------------------------------------------
  function handleData(data) {
    // Use the larger of server-reported elapsed and client clock.
    const serverElapsed = data.elapsed_seconds || 0;
    const clientElapsed = (Date.now() - startedAt) / 1000;
    const elapsed = Math.max(serverElapsed, clientElapsed);

    let readyCount = 0;

    for (const profile of data.profiles) {
      if (!profile.ready) continue;
      readyCount++;

      const card = document.getElementById('profile-card-' + profile.profile_id);
      if (!card || !card.querySelector('[data-loading]')) continue;

      renderProfile(card, profile, data.run_id);
    }

    const total = data.profiles.length;

    if (data.all_ready) {
      finish('success', readyCount, total);
      return;
    }

    if (elapsed >= cfg.timeoutMs / 1000) {
      finish('timeout', readyCount, total);
      return;
    }

    // Update status bar only when the count changes to avoid spamming aria-live.
    const phrase = readyCount + ' of ' + total + ' profile' + (total !== 1 ? 's' : '') +
      ' loaded. Checking again in ' + (cfg.intervalMs / 1000) + 's...';
    writeStatus(phrase);

    setTimeout(pollOnce, cfg.intervalMs);
  }

  // -------------------------------------------------------------------------
  // End states: success, timeout, network error.
  // Each shows distinct UI so the moderator knows what happened and why.
  // -------------------------------------------------------------------------
  function finish(state, readyCount, total) {
    done = true;
    clearInterval(timerInterval);
    hideSpinner();

    if (state === 'success') {
      writeStatus('All results loaded.');
      return;
    }

    if (state === 'timeout') {
      const loaded = readyCount + ' of ' + total +
        ' profile' + (total !== 1 ? 's' : '') + ' loaded. Pipeline timed out.';
      writeStatus(loaded);
      showTimeoutBanner();
      return;
    }
  }

  function handleNetworkError(err) {
    console.error('[poll.js] fetch failed:', err);
    done = true;
    hideSpinner();
    writeStatus('Could not reach the server.');
    showErrorBanner();
  }

  // -------------------------------------------------------------------------
  // Rendering: fill in a profile card from API data.
  // -------------------------------------------------------------------------
  function renderProfile(card, profile, runId) {
    const summary = profile.summary || {};
    const hints   = profile.hints   || [];

    const tmpl = document.getElementById('tmpl-profile-loaded');
    const node = tmpl.content.cloneNode(true);

    fill(node, 'profile_name', summary.profile_name || '#' + profile.profile_id);
    fill(node, 'profile_id',   profile.profile_id);
    fill(node, 'hint_count',   hints.length + ' hint' + (hints.length !== 1 ? 's' : ''));
    fill(node, 'profile_summary_text', summary.profile_summary_text || 'No summary available.');
    fill(node, 'score_line',   formatScoreLine(summary));
    fill(node, 'run_id',       runId || cfg.runId);
    fill(node, 'created_at',   formatDate(summary.created_at));

    // Top actions: render only if the array is non-empty.
    const actions = Array.isArray(summary.top_actions) ? summary.top_actions : [];
    if (actions.length > 0) {
      const section = node.querySelector('[data-section="top-actions"]');
      const list    = node.querySelector('[data-field="top_actions_list"]');
      actions.forEach(action => {
        const li = document.createElement('li');
        li.textContent = action;
        list.appendChild(li);
      });
      section.classList.remove('hidden');
    }

    // Field hints: show section only if hints exist.
    if (hints.length > 0) {
      const section   = node.querySelector('[data-section="hints"]');
      const container = node.querySelector('[data-field="hints_content"]');
      container.appendChild(buildHintsBlock(hints));
      section.classList.remove('hidden');
    }

    // Swap loading placeholder for rendered content.
    card.querySelector('[data-loading]').remove();
    card.appendChild(node);
  }

  // ---------------------------------------------------------------------------
  // Hints: card grid for 5 or fewer, table for 6+.
  // ---------------------------------------------------------------------------
  function buildHintsBlock(hints) {
    return hints.length <= 5 ? buildHintCards(hints) : buildHintTable(hints);
  }

  function buildHintCards(hints) {
    const grid = document.createElement('div');
    grid.className = 'grid gap-3';
    hints.forEach(hint => {
      const node = cloneHintCard(hint);
      grid.appendChild(node);
    });
    return grid;
  }

  function cloneHintCard(hint) {
    const tmpl = document.getElementById('tmpl-hint-card');
    const node = tmpl.content.cloneNode(true);

    fill(node, 'field_name',    capitalize(hint.field_name   || ''));
    fill(node, 'hint_text',     hint.hint_text               || '');
    fill(node, 'current_value', hint.current_value           || 'Not set');
    fill(node, 'suggested_value', hint.suggested_value       || '');
    fill(node, 'score_delta',   formatDelta(hint.score_delta));

    const link = node.querySelector('[data-field="source_link"]');
    link.textContent = hint.source_domain_internal || '';
    if (hint.source_url_internal) link.href = hint.source_url_internal;

    applyBadge(node.querySelector('[data-field="status_badge"]'), hint.verification_status);

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
    ['Field', 'Current', 'Suggested', 'Hint', 'Source', 'Status', 'Score'].forEach(label => {
      const th = document.createElement('th');
      th.className = 'pb-2 pr-4 text-xs font-semibold text-gray-500 uppercase tracking-wide';
      th.textContent = label;
      headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);

    const tbody = document.createElement('tbody');
    hints.forEach(hint => {
      const node = cloneHintRow(hint);
      tbody.appendChild(node);
    });

    table.appendChild(thead);
    table.appendChild(tbody);
    wrapper.appendChild(table);
    return wrapper;
  }

  function cloneHintRow(hint) {
    const tmpl = document.getElementById('tmpl-hint-row');
    const node = tmpl.content.cloneNode(true);

    fill(node, 'field_name',      capitalize(hint.field_name     || ''));
    fill(node, 'current_value',   hint.current_value             || 'Not set');
    fill(node, 'suggested_value', hint.suggested_value           || '');
    fill(node, 'hint_text',       hint.hint_text                 || '');
    fill(node, 'score_delta',     formatDelta(hint.score_delta));

    const link = node.querySelector('[data-field="source_link"]');
    link.textContent = hint.source_domain_internal || '';
    if (hint.source_url_internal) link.href = hint.source_url_internal;

    applyBadge(node.querySelector('[data-field="status_badge"]'), hint.verification_status);

    return node;
  }

  // -------------------------------------------------------------------------
  // Badge helper — applies shape class from custom.css plus color from safelist.
  // -------------------------------------------------------------------------
  const BADGE_CLASSES = {
    MATCH:          'bg-green-100 text-green-800',
    MISMATCH_INFO:  'bg-amber-100 text-amber-800',
    NEW_INFO:       'bg-blue-100  text-blue-800',
    CC_LOWER_RATE:  'bg-amber-100 text-amber-800',
    CC_HIGHER_RATE: 'bg-amber-100 text-amber-800',
  };

  const BADGE_LABELS = {
    MATCH:          'Match',
    MISMATCH_INFO:  'Mismatch',
    NEW_INFO:       'New info',
    CC_LOWER_RATE:  'Lower rate',
    CC_HIGHER_RATE: 'Higher rate',
  };

  function applyBadge(el, status) {
    if (!el) return;
    const color = BADGE_CLASSES[status] || 'bg-gray-100 text-gray-700';
    el.className = 'badge ' + color;
    el.textContent = BADGE_LABELS[status] || (status || 'Unknown');
  }

  // -------------------------------------------------------------------------
  // Formatting
  // -------------------------------------------------------------------------
  function formatScoreLine(summary) {
    const pre   = summary.prehint_score;
    const post  = summary.posthint_score_est;
    const delta = summary.total_estimated_score_delta;

    if (pre == null || post == null) return 'Score impact: not available';

    const sign    = delta != null && delta >= 0 ? '+' : '';
    const deltaStr = delta != null ? ' (' + sign + round1(delta) + ')' : '';

    // Unicode right-arrow (U+2192) is the visual separator between pre and post scores.
    return 'Score: ' + round1(pre) + ' → ' + round1(post) + deltaStr;
  }

  function formatDelta(delta) {
    if (delta == null) return '';
    return (delta >= 0 ? '+' : '') + round1(delta);
  }

  function round1(n) {
    return parseFloat(n).toFixed(1);
  }

  // Format an ISO timestamp as "23 Jun 2026, 08:07" in Amsterdam local time.
  const _dateFmt = new Intl.DateTimeFormat('en-GB', {
    timeZone:  'Europe/Amsterdam',
    day:       'numeric',
    month:     'short',
    year:      'numeric',
    hour:      '2-digit',
    minute:    '2-digit',
    hour12:    false,
  });

  function formatDate(isoStr) {
    if (!isoStr) return '';
    try {
      return _dateFmt.format(new Date(isoStr));
    } catch (_) {
      return isoStr;
    }
  }

  function capitalize(str) {
    return str ? str.charAt(0).toUpperCase() + str.slice(1) : str;
  }

  // -------------------------------------------------------------------------
  // DOM helpers
  // -------------------------------------------------------------------------
  function fill(node, field, text) {
    const el = node.querySelector('[data-field="' + field + '"]');
    if (el) el.textContent = text ?? '';
  }

  function writeStatus(phrase) {
    if (phrase === lastStatusPhrase) return;
    lastStatusPhrase = phrase;
    document.getElementById('status-text').textContent = phrase;
  }

  function hideSpinner() {
    const s = document.getElementById('status-spinner');
    if (s) s.classList.add('hidden');
  }

  function showTimeoutBanner() {
    const banner = document.getElementById('timeout-banner');
    if (!banner) return;
    banner.classList.remove('hidden');

    // Populate the retry form with only the profile IDs that did not load.
    const pending = cfg.profileIds.filter(pid => {
      const card = document.getElementById('profile-card-' + pid);
      return card && card.querySelector('[data-loading]');
    });
    document.getElementById('retry-ids').value = pending.join(',');

    document.getElementById('retry-btn').addEventListener('click', () => {
      document.getElementById('retry-form').submit();
    }, { once: true });
  }

  function showErrorBanner() {
    const container = document.getElementById('profiles-container');
    if (!container) return;

    const banner = document.createElement('div');
    banner.className = [
      'mt-6 flex items-start gap-3',
      'bg-red-50 border border-red-200 text-red-800',
      'px-4 py-3 rounded-lg text-sm',
    ].join(' ');
    banner.setAttribute('role', 'alert');

    const icon = document.createElement('div');
    icon.innerHTML = [
      '<svg class="w-5 h-5 mt-0.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">',
      '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>',
      '</svg>',
    ].join('');

    const msg = document.createElement('span');
    msg.textContent = 'Could not reach the server. ';

    const btn = document.createElement('button');
    btn.textContent = 'Refresh the page';
    btn.className = 'underline font-medium';
    btn.addEventListener('click', () => location.reload());

    msg.appendChild(btn);
    msg.appendChild(document.createTextNode(' to try again.'));

    banner.appendChild(icon.firstElementChild);
    banner.appendChild(msg);
    container.after(banner);
  }

}());
