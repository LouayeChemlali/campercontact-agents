from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Optional

import google.auth.transport.requests
import google.oauth2.id_token
import requests
from fastapi import FastAPI
from google.cloud import bigquery

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

# Optional: set this to the base URL of the Source Finder Cloud Run service,
# for example https://source-finder-agent-xxxxx-ew.a.run.app
SOURCE_FINDER_URL = os.getenv("SOURCE_FINDER_URL", "").rstrip("/")
AUTO_TRIGGER_SOURCE_FINDER = os.getenv("AUTO_TRIGGER_SOURCE_FINDER", "false").lower() == "true"
SOURCE_FINDER_AUTH = os.getenv("SOURCE_FINDER_AUTH", "true").lower() == "true"

client = bigquery.Client(project=PROJECT_ID)


@app.get("/health")
def health():
    return {"status": "ok", "agent": "gap-detector-agent"}


def _get_cloud_run_auth_headers(audience_url: str) -> Dict[str, str]:
    """Return an identity-token auth header for a private Cloud Run service."""
    if not SOURCE_FINDER_AUTH:
        return {}

    auth_request = google.auth.transport.requests.Request()
    token = google.oauth2.id_token.fetch_id_token(auth_request, audience_url)
    return {"Authorization": f"Bearer {token}"}


def _trigger_pending_source_finder(*, limit: int, write_bigquery: bool = True, gap_detector_run_id: str = "") -> Dict[str, Any]:
    """Call Source Finder's queue runner endpoint after Gap Detector has queued rows."""
    if not SOURCE_FINDER_URL:
        return {
            "triggered": False,
            "reason": "SOURCE_FINDER_URL is not configured",
        }

    endpoint = f"{SOURCE_FINDER_URL}/run-pending-source-finder"
    headers = _get_cloud_run_auth_headers(SOURCE_FINDER_URL)

    response = requests.post(
        endpoint,
        headers=headers,
        json={
            "limit": limit,
            "write_bigquery": write_bigquery,
            "gap_detector_run_id": gap_detector_run_id,
        },
        timeout=300,
    )
    response.raise_for_status()

    return {
        "triggered": True,
        "endpoint": endpoint,
        "response": response.json(),
    }


@app.post("/run-gap-detector")
def run_gap_detector(payload: Optional[Dict[str, Any]] = None):
    payload = payload or {}

    run_id = f"gap-detector-run-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    query = f"""
    INSERT INTO `{SOURCE_QUEUE_TABLE}`
    (
      queue_id,
      gap_detector_run_id,
      sitecode,
      priority,
      status,
      attempts,
      last_error,
      created_at,
      updated_at,
      processed_at
    )
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
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
        ]
    )
    client.query(query, job_config=job_config).result()

    count_query = f"""
    SELECT COUNT(*) AS queued_rows
    FROM `{SOURCE_QUEUE_TABLE}`
    WHERE gap_detector_run_id = @run_id
    """

    count_job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
        ]
    )

    result = list(client.query(count_query, job_config=count_job_config).result())
    queued_rows = int(result[0]["queued_rows"])

    should_trigger = bool(payload.get("trigger_source_finder", AUTO_TRIGGER_SOURCE_FINDER))
    source_finder_result: Dict[str, Any] = {"triggered": False}

    if should_trigger and queued_rows > 0:
        limit = int(payload.get("source_finder_limit", min(queued_rows, 5)))
        write_bigquery = bool(payload.get("write_bigquery", True))
        source_finder_result = _trigger_pending_source_finder(
            limit=limit,
            write_bigquery=write_bigquery,
            gap_detector_run_id=run_id,
        )

    return {
        "status": "completed",
        "gap_detector_run_id": run_id,
        "queued_rows": queued_rows,
        "source_finder": source_finder_result,
    }
