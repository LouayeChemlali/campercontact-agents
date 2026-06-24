from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import google.auth.transport.requests
import google.oauth2.id_token
import requests
from fastapi import BackgroundTasks, FastAPI
from google.cloud import bigquery

log = logging.getLogger(__name__)

app = FastAPI()

PROJECT_ID = os.getenv("PROJECT_ID", "project-62cd3637-0b98-4aa5-8d5")
GAP_OUTPUT_TABLE = os.getenv(
    "GAP_OUTPUT_TABLE",
    f"{PROJECT_ID}.gap_detector_final.t10_gap_detector_output",
)
SOURCE_QUEUE_TABLE = os.getenv(
    "SOURCE_QUEUE_TABLE",
    f"{PROJECT_ID}.primary_dataset.source_finder_queue",
)

SOURCE_FINDER_URL = os.getenv("SOURCE_FINDER_URL", "").rstrip("/")
AUTO_TRIGGER_SOURCE_FINDER = os.getenv("AUTO_TRIGGER_SOURCE_FINDER", "false").lower() == "true"
SOURCE_FINDER_AUTH = os.getenv("SOURCE_FINDER_AUTH", "true").lower() == "true"

ENTITY_MATCHER_URL = os.getenv("ENTITY_MATCHER_URL", "").rstrip("/")
AUTO_TRIGGER_ENTITY_MATCHER = os.getenv("AUTO_TRIGGER_ENTITY_MATCHER", "false").lower() == "true"
ENTITY_MATCHER_AUTH = os.getenv("ENTITY_MATCHER_AUTH", "true").lower() == "true"

CONFIDENCE_AGENT_URL = os.getenv("CONFIDENCE_AGENT_URL", "").rstrip("/")
AUTO_TRIGGER_CONFIDENCE_AGENT = os.getenv("AUTO_TRIGGER_CONFIDENCE_AGENT", "false").lower() == "true"
CONFIDENCE_AGENT_AUTH = os.getenv("CONFIDENCE_AGENT_AUTH", "true").lower() == "true"

HINT_GENERATOR_URL = os.getenv("HINT_GENERATOR_URL", "").rstrip("/")
AUTO_TRIGGER_HINT_PRIORITIZATION = os.getenv("AUTO_TRIGGER_HINT_PRIORITIZATION", "false").lower() == "true"
AUTO_TRIGGER_HINT_GENERATOR = os.getenv("AUTO_TRIGGER_HINT_GENERATOR", "false").lower() == "true"
HINT_GENERATOR_AUTH = os.getenv("HINT_GENERATOR_AUTH", "true").lower() == "true"

client = bigquery.Client(project=PROJECT_ID)


@app.get("/health")
def health():
    return {"status": "ok", "agent": "gap-detector-agent"}


def _id_token(audience_url: str) -> str:
    auth_request = google.auth.transport.requests.Request()
    return google.oauth2.id_token.fetch_id_token(auth_request, audience_url)


def _auth_headers(service_url: str, enabled: bool) -> Dict[str, str]:
    if not enabled:
        return {}
    return {"Authorization": f"Bearer {_id_token(service_url)}"}


def _post(service_url: str, endpoint: str, auth_enabled: bool, body: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    """POST to a Cloud Run service and return the parsed JSON response."""
    url = f"{service_url}/{endpoint.lstrip('/')}"
    headers = _auth_headers(service_url, auth_enabled)
    response = requests.post(url, headers=headers, json=body, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _trigger_source_finder(*, limit: int, write_bigquery: bool, gap_detector_run_id: str) -> Dict[str, Any]:
    if not SOURCE_FINDER_URL:
        return {"triggered": False, "reason": "SOURCE_FINDER_URL not configured"}
    result = _post(
        SOURCE_FINDER_URL, "/run-pending-source-finder", SOURCE_FINDER_AUTH,
        {"limit": limit, "write_bigquery": write_bigquery, "gap_detector_run_id": gap_detector_run_id},
        timeout=300,
    )
    return {"triggered": True, "endpoint": f"{SOURCE_FINDER_URL}/run-pending-source-finder", "response": result}


def _trigger_entity_matcher(*, gap_detector_run_id: str, write_bigquery: bool, limit: Optional[int]) -> Dict[str, Any]:
    if not ENTITY_MATCHER_URL:
        return {"triggered": False, "reason": "ENTITY_MATCHER_URL not configured"}
    body: Dict[str, Any] = {"gap_detector_run_id": gap_detector_run_id, "write_bigquery": write_bigquery, "append": True}
    if limit is not None:
        body["limit"] = int(limit)
    result = _post(ENTITY_MATCHER_URL, "/run-entity-matcher", ENTITY_MATCHER_AUTH, body, timeout=300)
    return {"triggered": True, "endpoint": f"{ENTITY_MATCHER_URL}/run-entity-matcher", "response": result}


def _trigger_confidence_agent(*, gap_detector_run_id: str) -> Dict[str, Any]:
    if not CONFIDENCE_AGENT_URL:
        return {"triggered": False, "reason": "CONFIDENCE_AGENT_URL not configured"}
    result = _post(
        CONFIDENCE_AGENT_URL, "/run-confidence-agent", CONFIDENCE_AGENT_AUTH,
        {"gap_detector_run_id": gap_detector_run_id},
        timeout=300,
    )
    return {"triggered": True, "endpoint": f"{CONFIDENCE_AGENT_URL}/run-confidence-agent", "response": result}


def _trigger_hint_prioritization(*, gap_detector_run_id: str) -> Dict[str, Any]:
    """Call /run-hint-prioritization on the hint generator service.

    This must run AFTER entity matcher and BEFORE hint generator.
    It reads entity matcher output and writes prioritized candidates that
    the hint generator then reads to produce hint text.
    """
    if not HINT_GENERATOR_URL:
        return {"triggered": False, "reason": "HINT_GENERATOR_URL not configured"}
    result = _post(
        HINT_GENERATOR_URL, "/run-hint-prioritization", HINT_GENERATOR_AUTH,
        {"gap_detector_run_id": gap_detector_run_id},
        timeout=300,
    )
    return {"triggered": True, "endpoint": f"{HINT_GENERATOR_URL}/run-hint-prioritization", "response": result}


def _trigger_hint_generator(*, gap_detector_run_id: str, write_bigquery: bool, limit: Optional[int], refresh_prioritization: bool) -> Dict[str, Any]:
    if not HINT_GENERATOR_URL:
        return {"triggered": False, "reason": "HINT_GENERATOR_URL not configured"}
    body: Dict[str, Any] = {
        "gap_detector_run_id": gap_detector_run_id,
        "write_bigquery": write_bigquery,
        "refresh_prioritization": refresh_prioritization,
    }
    if limit is not None:
        body["limit"] = int(limit)
    result = _post(HINT_GENERATOR_URL, "/run-hint-generator", HINT_GENERATOR_AUTH, body, timeout=600)
    return {"triggered": True, "endpoint": f"{HINT_GENERATOR_URL}/run-hint-generator", "response": result}


@app.post("/run-gap-detector")
def run_gap_detector(background_tasks: BackgroundTasks, payload: Optional[Dict[str, Any]] = None):
    """
    Entry point for the full pipeline.

    Queues the profiles into BigQuery synchronously (fast), then immediately
    returns the run_id so the frontend can start polling. The remaining stages
    (Source Finder → Entity Matcher → Confidence Agent → Hint Generator) run in
    a background task so the HTTP response is not blocked by their combined duration.

    Accepts profile_ids to restrict which profiles are queued. When provided,
    only those sitecodes are inserted into the source finder queue. When omitted,
    all profiles with recommended actions are queued.
    """
    payload = payload or {}
    profile_ids: List[str] = [str(p) for p in (payload.get("profile_ids") or [])]
    write_bigquery = bool(payload.get("write_bigquery", True))
    refresh_prioritization = bool(payload.get("refresh_prioritization", True))

    run_id = f"gap-detector-run-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    if profile_ids:
        profile_filter = "AND CAST(sitecode AS STRING) IN UNNEST(@profile_ids)"
        params = [
            bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
            bigquery.ArrayQueryParameter("profile_ids", "STRING", profile_ids),
        ]
    else:
        profile_filter = ""
        params = [bigquery.ScalarQueryParameter("run_id", "STRING", run_id)]

    insert_query = f"""
    INSERT INTO `{SOURCE_QUEUE_TABLE}`
    (queue_id, gap_detector_run_id, sitecode, priority, status, attempts,
     last_error, created_at, updated_at, processed_at)
    SELECT
      GENERATE_UUID(),
      @run_id,
      CAST(sitecode AS STRING),
      1,
      'pending',
      0,
      NULL,
      CURRENT_TIMESTAMP(),
      CURRENT_TIMESTAMP(),
      NULL
    FROM `{GAP_OUTPUT_TABLE}`
    WHERE ARRAY_LENGTH(recommended_actions) > 0
      {profile_filter}
    """
    client.query(insert_query, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()

    count_params = [bigquery.ScalarQueryParameter("run_id", "STRING", run_id)]
    count_rows = list(client.query(
        f"SELECT COUNT(*) AS queued_rows FROM `{SOURCE_QUEUE_TABLE}` WHERE gap_detector_run_id = @run_id",
        job_config=bigquery.QueryJobConfig(query_parameters=count_params),
    ).result())
    queued_rows = int(count_rows[0]["queued_rows"])

    # Kick off the pipeline chain asynchronously so we can return immediately.
    background_tasks.add_task(
        _run_pipeline_chain,
        run_id=run_id,
        payload=payload,
        queued_rows=queued_rows,
        write_bigquery=write_bigquery,
        refresh_prioritization=refresh_prioritization,
    )

    return {
        "status": "triggered",
        "gap_detector_run_id": run_id,
        "targeted_profile_ids": profile_ids,
        "queued_rows": queued_rows,
    }


def _run_pipeline_chain(
    *,
    run_id: str,
    payload: Dict[str, Any],
    queued_rows: int,
    write_bigquery: bool,
    refresh_prioritization: bool,
) -> None:
    """Run SF → EM → Prioritization → CA → HG sequentially.

    Called as a FastAPI BackgroundTask after the HTTP response has been sent.
    Errors are logged per-stage so one failure does not abort the rest.

    Stage order matters:
      SF writes sources that EM reads.
      EM writes candidates that Prioritization scores.
      Prioritization writes prioritized_hint_candidates that CA and HG read.
      CA runs after Prioritization to score confidence on the same candidates.
    """
    if bool(payload.get("trigger_source_finder", AUTO_TRIGGER_SOURCE_FINDER)) and queued_rows > 0:
        sf_limit = int(payload.get("source_finder_limit", min(queued_rows, 5)))
        try:
            _trigger_source_finder(
                limit=sf_limit, write_bigquery=write_bigquery, gap_detector_run_id=run_id
            )
        except Exception as exc:
            log.error("[%s] Source Finder failed: %s", run_id, exc)

    if bool(payload.get("trigger_entity_matcher", AUTO_TRIGGER_ENTITY_MATCHER)):
        try:
            _trigger_entity_matcher(
                gap_detector_run_id=run_id,
                write_bigquery=write_bigquery,
                limit=payload.get("entity_matcher_limit"),
            )
        except Exception as exc:
            log.error("[%s] Entity Matcher failed: %s", run_id, exc)

    # Prioritization runs after EM: scores EM output and writes
    # prioritized_hint_candidates which the hint generator then reads.
    if bool(payload.get("trigger_hint_prioritization", AUTO_TRIGGER_HINT_PRIORITIZATION)):
        try:
            _trigger_hint_prioritization(gap_detector_run_id=run_id)
        except Exception as exc:
            log.error("[%s] Hint Prioritization failed: %s", run_id, exc)

    if bool(payload.get("trigger_confidence_agent", AUTO_TRIGGER_CONFIDENCE_AGENT)):
        try:
            _trigger_confidence_agent(gap_detector_run_id=run_id)
        except Exception as exc:
            log.error("[%s] Confidence Agent failed: %s", run_id, exc)

    if bool(payload.get("trigger_hint_generator", AUTO_TRIGGER_HINT_GENERATOR)):
        try:
            _trigger_hint_generator(
                gap_detector_run_id=run_id,
                write_bigquery=write_bigquery,
                limit=payload.get("hint_generator_limit"),
                refresh_prioritization=refresh_prioritization,
            )
        except Exception as exc:
            log.error("[%s] Hint Generator failed: %s", run_id, exc)
