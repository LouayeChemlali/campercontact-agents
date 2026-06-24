"""Flask application for Campercontact Moderator Tools."""

import logging
import math
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

import bigquery_client
import pipeline_client
from config import (
    FLASK_SECRET_KEY,
    GD_URL,
    POLLING_INTERVAL_SECONDS,
    POLLING_TIMEOUT_SECONDS,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

_AMS = ZoneInfo("Europe/Amsterdam")
_MAX_IDS = 50


# ---------------------------------------------------------------------------
# Template filter
# ---------------------------------------------------------------------------

@app.template_filter("datetimeformat")
def datetimeformat(value):
    """Format a datetime as '23 Jun 2026, 08:07' in Amsterdam local time."""
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(_AMS)
    return f"{local.day} {local.strftime('%b %Y, %H:%M')}"


@app.template_filter("thousands")
def thousands_filter(n):
    """Format an integer with comma thousands separators, e.g. 12847 -> '12,847'."""
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return n or 0


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Input page: profile ID form and recently processed list."""
    bq = bigquery_client.get_client()
    recent = bigquery_client.get_recent_profiles(bq)
    return render_template(
        "index.html",
        gd_url_set=bool(GD_URL),
        recent_profiles=recent,
    )


@app.route("/run", methods=["POST"])
def run_pipeline():
    """Parse IDs, trigger the pipeline, redirect to the results page."""
    raw = request.form.get("profile_ids", "").strip()
    if not raw:
        flash("Please enter at least one profile ID.", "warning")
        return redirect(url_for("index"))

    ids = _parse_ids(raw)
    if not ids:
        flash("No valid profile IDs found. IDs must be numeric (e.g. 160668).", "warning")
        return redirect(url_for("index"))

    if len(ids) > _MAX_IDS:
        flash(f"Enter at most {_MAX_IDS} profile IDs per run.", "warning")
        return redirect(url_for("index"))

    triggered_at = datetime.now(timezone.utc).isoformat()
    result = pipeline_client.trigger_pipeline(ids)

    if not result.success:
        flash(result.error_message or "Pipeline trigger failed.", "error")
        return redirect(url_for("index"))

    if not result.run_id:
        flash(
            "The pipeline started but did not return a run ID. "
            "Check that the Gap Detector service is responding correctly.",
            "error",
        )
        return redirect(url_for("index"))

    if result.warning:
        flash(result.warning, "warning")

    # Extract key pipeline stats from the response to display in the debug panel.
    data = result.pipeline_data or {}
    hg = (data.get("hint_generator") or {}).get("response") or {}
    sf = (data.get("source_finder") or {}).get("response") or {}
    em = (data.get("entity_matcher") or {}).get("response") or {}
    ca = data.get("confidence_agent") or {}

    return redirect(
        url_for(
            "results",
            run_id=result.run_id,
            triggered_at=triggered_at,
            profile_ids=",".join(ids),
            gd_queued=data.get("queued_rows", ""),
            sf_processed=sf.get("items_processed", ""),
            em_loaded=em.get("profiles_loaded", ""),
            hg_candidates=(hg.get("prioritization") or {}).get("candidate_rows", ""),
            hg_hints=hg.get("field_hints_generated", ""),
            hg_summaries=hg.get("profile_summaries_generated", ""),
            ca_triggered=ca.get("triggered", ""),
            ca_rows_ready=ca.get("rows_ready", ""),
            ca_inserted=ca.get("inserted", ""),
        )
    )


@app.route("/results/<run_id>")
def results(run_id):
    """Results page: renders a shell that poll.js fills in as data arrives."""
    qs = request.args
    pipeline_stats = {
        "gd_queued": qs.get("gd_queued"),
        "sf_processed": qs.get("sf_processed"),
        "em_loaded": qs.get("em_loaded"),
        "hg_candidates": qs.get("hg_candidates"),
        "hg_hints": qs.get("hg_hints"),
        "hg_summaries": qs.get("hg_summaries"),
        "ca_triggered": qs.get("ca_triggered"),
        "ca_rows_ready": qs.get("ca_rows_ready"),
        "ca_inserted": qs.get("ca_inserted"),
    }
    return render_template(
        "results.html",
        run_id=run_id,
        profile_ids=_ids_from_qs(),
        triggered_at=qs.get("triggered_at", ""),
        polling_interval=POLLING_INTERVAL_SECONDS,
        polling_timeout=POLLING_TIMEOUT_SECONDS,
        gd_url_set=bool(GD_URL),
        pipeline_stats=pipeline_stats,
    )


@app.route("/api/status/<run_id>")
def api_status(run_id):
    """Polling endpoint: returns JSON state for each requested profile."""
    ids = _ids_from_qs()
    triggered_at = request.args.get("triggered_at") or None
    # run_id IS the gap_detector_run_id - use it to filter BQ results to this run only.

    bq = bigquery_client.get_client()
    status = bigquery_client.get_profile_status(
        bq, ids, triggered_at, gap_detector_run_id=run_id
    )

    profiles = []
    for pid in ids:
        entry = status.get(pid, {})
        hints = _rows_to_json(entry.get("hints", []))
        summary = _row_to_json(entry.get("summary"))
        ready = entry.get("hints_ready", False) or entry.get("summary_ready", False)
        profiles.append({
            "profile_id": pid,
            "ready": ready,
            "hints_ready": entry.get("hints_ready", False),
            "summary_ready": entry.get("summary_ready", False),
            "hints": hints,
            "summary": summary,
        })

    return jsonify({
        "run_id": run_id,
        "profiles": profiles,
        "all_ready": bool(profiles) and all(p["ready"] for p in profiles),
        "elapsed_seconds": _elapsed(triggered_at),
    })


_QUEUE_LIMIT_CHOICES = [25, 50, 100, 200]
_QUEUE_LIMIT_DEFAULT = 50


@app.route("/queue")
def queue():
    """Priority queue page: ML-ranked list of profiles most needing attention."""
    try:
        limit = int(request.args.get("limit", _QUEUE_LIMIT_DEFAULT))
        limit = max(1, min(limit, 500))
    except ValueError:
        limit = _QUEUE_LIMIT_DEFAULT

    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    offset = (page - 1) * limit
    bq = bigquery_client.get_client()
    profiles_raw = bigquery_client.get_priority_queue(bq, limit=limit, offset=offset)
    stats = bigquery_client.get_queue_stats(bq)

    total_profiles = stats.get("total_profiles", 0)
    total_pages = max(1, math.ceil(total_profiles / limit)) if total_profiles else 1

    return render_template(
        "queue.html",
        profiles=_rows_to_json(profiles_raw),
        stats=stats,
        current_limit=limit,
        current_page=page,
        total_pages=total_pages,
        limit_choices=_QUEUE_LIMIT_CHOICES,
        gd_url_set=bool(GD_URL),
    )


@app.route("/profile/<profile_id>")
def view_profile(profile_id):
    """Profile lookup page: shows the most recent hints for one profile."""
    bq = bigquery_client.get_client()
    hints = bigquery_client.get_hints_for_profile(bq, profile_id)
    summary = bigquery_client.build_profile_summary(hints, profile_id) if hints else None

    if not profile_id.isdigit():
        flash("Invalid profile ID.", "error")
        return redirect(url_for("index"))

    return render_template(
        "profile.html",
        profile_id=profile_id,
        profile_name=(summary or {}).get("profile_name") or profile_id,
        hints=_rows_to_json(hints),
        summary=_row_to_json(summary),
        gd_url_set=bool(GD_URL),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_ids(raw: str) -> list[str]:
    """Split comma/newline input, keep only non-empty numeric strings, deduplicated."""
    seen: set[str] = set()
    result: list[str] = []
    for token in re.split(r"[,\n\r\s]+", raw):
        token = token.strip()
        if token and token.isdigit() and token not in seen:
            seen.add(token)
            result.append(token)
    return result


def _ids_from_qs() -> list[str]:
    """Return profile IDs from the current request query string."""
    raw = request.args.get("profile_ids", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def _elapsed(triggered_at: str | None) -> int:
    """Return whole seconds elapsed since triggered_at, or 0 on any error."""
    if not triggered_at:
        return 0
    try:
        start = datetime.fromisoformat(triggered_at.replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - start).total_seconds()))
    except ValueError:
        log.warning("Could not parse triggered_at: %s", triggered_at)
        return 0


def _row_to_json(row: dict | None) -> dict | None:
    """Convert a BigQuery row dict to a JSON-safe dict."""
    if row is None:
        return None
    out: dict = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            out[k] = v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        elif isinstance(v, date):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = float(v)
        else:
            out[k] = v
    return out


def _rows_to_json(rows: list[dict]) -> list[dict]:
    """Convert a list of BigQuery row dicts to JSON-safe dicts."""
    return [_row_to_json(r) for r in rows if r is not None]
