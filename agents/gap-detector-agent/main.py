from __future__ import annotations

# central orchestrator, queues profiles in BigQuery then fires each agent in sequence

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

# Optional: set this to the base URL of the Entity Matcher Cloud Run service,
# for example https://entity-matcher-agent-xxxxx-ew.a.run.app
ENTITY_MATCHER_URL = os.getenv("ENTITY_MATCHER_URL", "").rstrip("/")
AUTO_TRIGGER_ENTITY_MATCHER = os.getenv("AUTO_TRIGGER_ENTITY_MATCHER", "false").lower() == "true"
ENTITY_MATCHER_AUTH = os.getenv("ENTITY_MATCHER_AUTH", "true").lower() == "true"

# Optional: set this to the base URL of the Hint Generator Cloud Run service.
HINT_GENERATOR_URL = os.getenv("HINT_GENERATOR_URL", "").rstrip("/")
AUTO_TRIGGER_HINT_GENERATOR = os.getenv("AUTO_TRIGGER_HINT_GENERATOR", "false").lower() == "true"
CONFIDENCE_AGENT_URL = os.getenv("CONFIDENCE_AGENT_URL", "").rstrip("/")
AUTO_TRIGGER_CONFIDENCE_AGENT = os.getenv("AUTO_TRIGGER_CONFIDENCE_AGENT", "false").lower() == "true"
CONFIDENCE_AGENT_AUTH = os.getenv("CONFIDENCE_AGENT_AUTH", "true").lower() == "true"
HINT_GENERATOR_AUTH = os.getenv("HINT_GENERATOR_AUTH", "true").lower() == "true"

client = bigquery.Client(project=PROJECT_ID)


@app.get("/health")
def health():
    return {"status": "ok", "agent": "gap-detector-agent", "version": "confidence-pipeline-v2"}


def _get_cloud_run_auth_headers(audience_url: str) -> Dict[str, str]:
    """Return an identity-token auth header for a private Cloud Run service."""
    if not SOURCE_FINDER_AUTH:
        return {}

    auth_request = google.auth.transport.requests.Request()
    token = google.oauth2.id_token.fetch_id_token(auth_request, audience_url)
    return {"Authorization": f"Bearer {token}"}


def _get_entity_matcher_auth_headers(audience_url: str) -> Dict[str, str]:
    """Return an identity-token auth header for the private Entity Matcher service."""
    if not ENTITY_MATCHER_AUTH:
        return {}

    auth_request = google.auth.transport.requests.Request()
    token = google.oauth2.id_token.fetch_id_token(auth_request, audience_url)
    return {"Authorization": f"Bearer {token}"}


def _trigger_entity_matcher(
    *,
    gap_detector_run_id: str,
    write_bigquery: bool = True,
    append: bool = True,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Call Entity Matcher after Source Finder has written source evidence."""
    if not ENTITY_MATCHER_URL:
        return {
            "triggered": False,
            "reason": "ENTITY_MATCHER_URL is not configured",
        }

    endpoint = f"{ENTITY_MATCHER_URL}/run-entity-matcher"
    headers = _get_entity_matcher_auth_headers(ENTITY_MATCHER_URL)

    payload: Dict[str, Any] = {
        "gap_detector_run_id": gap_detector_run_id,
        "write_bigquery": write_bigquery,
        "append": append,
        "use_gap_filter": True,
    }
    if limit is not None:
        payload["limit"] = int(limit)

    response = requests.post(
        endpoint,
        headers=headers,
        json=payload,
        timeout=300,
    )
    response.raise_for_status()

    return {
        "triggered": True,
        "endpoint": endpoint,
        "response": response.json(),
    }


def _get_hint_generator_auth_headers(audience_url: str) -> Dict[str, str]:
    """Return auth headers for the Hint Generator service."""
    if not HINT_GENERATOR_AUTH:
        return {}
    auth_request = google.auth.transport.requests.Request()
    token = google.oauth2.id_token.fetch_id_token(auth_request, audience_url)
    return {"Authorization": f"Bearer {token}"}


def _trigger_hint_generator(
    *,
    gap_detector_run_id: str,
    write_bigquery: bool = True,
    limit: Optional[int] = None,
    refresh_prioritization: bool = True,
) -> Dict[str, Any]:
    """Call Hint Generator after Entity Matcher has written match evidence."""
    if not HINT_GENERATOR_URL:
        return {
            "triggered": False,
            "reason": "HINT_GENERATOR_URL is not configured",
        }

    endpoint = f"{HINT_GENERATOR_URL}/run-hint-generator"
    headers = _get_hint_generator_auth_headers(HINT_GENERATOR_URL)
    payload: Dict[str, Any] = {
        "gap_detector_run_id": gap_detector_run_id,
        "write_bigquery": write_bigquery,
        "refresh_prioritization": refresh_prioritization,
    }
    if limit is not None:
        payload["limit"] = int(limit)

    response = requests.post(endpoint, headers=headers, json=payload, timeout=600)
    response.raise_for_status()
    return {
        "triggered": True,
        "endpoint": endpoint,
        "response": response.json(),
    }


def _get_confidence_agent_auth_headers(audience_url: str) -> Dict[str, str]:
    """Return an identity-token auth header for the private Confidence Agent Cloud Run service."""
    if not CONFIDENCE_AGENT_AUTH:
        return {}

    auth_request = google.auth.transport.requests.Request()
    token = google.oauth2.id_token.fetch_id_token(auth_request, audience_url)
    return {"Authorization": f"Bearer {token}"}


def _trigger_confidence_agent(
    *,
    gap_detector_run_id: str,
    profile_ids=None,
    limit: int = 5,
    write_bigquery: bool = True,
) -> Dict[str, Any]:
    """Call Confidence Agent after Hint Generator has written hint rows."""
    if not CONFIDENCE_AGENT_URL:
        return {
            "triggered": False,
            "reason": "CONFIDENCE_AGENT_URL is not configured",
        }

    endpoint = f"{CONFIDENCE_AGENT_URL}/run-confidence-agent"
    headers = _get_confidence_agent_auth_headers(CONFIDENCE_AGENT_URL)

    payload = {
        "gap_detector_run_id": gap_detector_run_id,
        "profile_ids": profile_ids or [],
        "limit": limit,
        "write_bigquery": write_bigquery,
    }

    response = requests.post(endpoint, headers=headers, json=payload, timeout=600)
    response.raise_for_status()

    return {
        "triggered": True,
        "endpoint": endpoint,
        "response": response.json(),
    }


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
    """Queue profiles for the pipeline and trigger each downstream agent in order."""
    payload = payload or {}

    run_id = f"gap-detector-run-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    raw_profile_ids = payload.get("profile_ids") or []
    if payload.get("profile_id"):
        raw_profile_ids.append(payload.get("profile_id"))

    profile_ids = [
        str(profile_id).strip()
        for profile_id in raw_profile_ids
        if str(profile_id).strip()
    ]

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
      AND (
        ARRAY_LENGTH(@profile_ids) = 0
        OR CAST(sitecode AS STRING) IN UNNEST(@profile_ids)
      )
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
            bigquery.ArrayQueryParameter("profile_ids", "STRING", profile_ids),
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
            bigquery.ArrayQueryParameter("profile_ids", "STRING", profile_ids),
        ]
    )

    result = list(client.query(count_query, job_config=count_job_config).result())
    queued_rows = int(result[0]["queued_rows"])

    # each stage only runs if its flag is set, defaults come from env vars
    should_trigger_source_finder = bool(payload.get("trigger_source_finder", AUTO_TRIGGER_SOURCE_FINDER))
    should_trigger_entity_matcher = bool(payload.get("trigger_entity_matcher", AUTO_TRIGGER_ENTITY_MATCHER))
    should_trigger_hint_generator = bool(payload.get("trigger_hint_generator", AUTO_TRIGGER_HINT_GENERATOR))
    should_trigger_confidence_agent = bool(payload.get("trigger_confidence_agent", AUTO_TRIGGER_CONFIDENCE_AGENT))

    source_finder_result: Dict[str, Any] = {"triggered": False}
    entity_matcher_result: Dict[str, Any] = {"triggered": False}
    hint_generator_result: Dict[str, Any] = {"triggered": False}
    confidence_agent_result: Dict[str, Any] = {"triggered": False}

    source_finder_limit = int(payload.get("source_finder_limit", min(queued_rows, 5))) if queued_rows else 0
    write_bigquery = bool(payload.get("write_bigquery", True))

    if should_trigger_source_finder and queued_rows > 0:
        source_finder_result = _trigger_pending_source_finder(
            limit=source_finder_limit,
            write_bigquery=write_bigquery,
            gap_detector_run_id=run_id,
        )

    # Entity Matcher should only run after Source Finder has had a chance to write
    # source evidence for this same gap_detector_run_id. This keeps the two
    # starting inputs aligned: Gap Detector context + Source Finder evidence.
    if should_trigger_entity_matcher:
        entity_matcher_result = _trigger_entity_matcher(
            gap_detector_run_id=run_id,
            write_bigquery=write_bigquery,
            append=bool(payload.get("entity_matcher_append", True)),
            limit=payload.get("entity_matcher_limit"),
        )

    if should_trigger_hint_generator:
        hint_generator_result = _trigger_hint_generator(
            gap_detector_run_id=run_id,
            write_bigquery=write_bigquery,
            limit=payload.get("hint_generator_limit", payload.get("entity_matcher_limit")),
            refresh_prioritization=bool(payload.get("refresh_prioritization", True)),
        )

    if should_trigger_confidence_agent:
        confidence_agent_limit = int(
            payload.get(
                "confidence_agent_limit",
                payload.get("hint_generator_limit", 5),
            )
        )

        confidence_agent_result = _trigger_confidence_agent(
            gap_detector_run_id=run_id,
            profile_ids=payload.get("profile_ids", []),
            limit=confidence_agent_limit,
            write_bigquery=bool(payload.get("write_bigquery", True)),
        )

    return {
        "status": "completed",
        "gap_detector_run_id": run_id,
        "queued_rows": queued_rows,
        "targeted_profile_ids": profile_ids,
        "source_finder": source_finder_result,
        "entity_matcher": entity_matcher_result,
        "hint_generator": hint_generator_result,
        "confidence_agent": confidence_agent_result,
    }
