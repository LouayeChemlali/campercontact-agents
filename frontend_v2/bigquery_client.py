"""BigQuery reads for the Campercontact moderator frontend."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from typing import Any

from google.cloud import bigquery

from config import BIGQUERY_PROJECT, BQ_CONFIDENCE_TABLE, BQ_QUEUE_TABLE

log = logging.getLogger(__name__)

_CONFIDENCE_LEVEL_ORDER = {
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
}


def get_client() -> bigquery.Client:
    """Return a BigQuery client for the configured project."""
    return bigquery.Client(project=BIGQUERY_PROJECT)


# confidence results are the single source of truth for everything the frontend shows
def get_recent_profiles(client: bigquery.Client, limit: int = 10) -> list[dict]:
    """Return recently processed profiles from the final confidence table."""
    query = f"""
        SELECT
            CAST(profile_id AS STRING) AS profile_id,
            ANY_VALUE(profile_name) AS profile_name,
            MAX(created_at) AS created_at,
            COUNT(*) AS hint_count,
            COUNTIF(confidence_level = 'HIGH') AS high_count,
            COUNTIF(confidence_level = 'MEDIUM') AS medium_count,
            COUNTIF(confidence_level = 'LOW') AS low_count
        FROM `{BQ_CONFIDENCE_TABLE}`
        GROUP BY CAST(profile_id AS STRING)
        ORDER BY created_at DESC
        LIMIT @limit
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("limit", "INT64", int(limit))]
    )
    try:
        rows = client.query(query, job_config=job_config).result()
        return [dict(row) for row in rows]
    except Exception as exc:
        log.error("get_recent_profiles failed: %s", exc)
        return []


def get_profile_status(
    client: bigquery.Client,
    profile_ids: list[str],
    triggered_after: str | None = None,
    gap_detector_run_id: str | None = None,
) -> dict[str, dict]:
    """
    Return current result state for each requested profile.

    The final Confidence Agent table is the single source of truth. A profile is
    considered ready when at least one row exists for the requested run/profile.
    LOW-confidence rows still count as ready, but the UI hides them by default.
    """
    status: dict[str, dict] = {
        str(pid): {
            "hints_ready": False,
            "summary_ready": False,
            "hints": [],
            "summary": None,
        }
        for pid in profile_ids
    }

    if not profile_ids:
        return status

    hints = get_confidence_results(
        client,
        profile_ids,
        gap_detector_run_id=gap_detector_run_id,
        triggered_after=triggered_after,
    )

    by_profile: dict[str, list[dict]] = {}
    for hint in hints:
        pid = str(hint.get("profile_id", ""))
        by_profile.setdefault(pid, []).append(hint)

    for pid, profile_hints in by_profile.items():
        if pid not in status:
            continue
        status[pid]["hints"] = profile_hints
        status[pid]["hints_ready"] = True
        status[pid]["summary_ready"] = True
        status[pid]["summary"] = build_profile_summary(profile_hints, pid)

    return status


def get_confidence_results(
    client: bigquery.Client,
    profile_ids: list[str],
    *,
    gap_detector_run_id: str | None = None,
    triggered_after: str | None = None,
) -> list[dict]:
    """Fetch final Confidence Agent rows filtered by run/profile."""
    if not profile_ids:
        return []

    params: list[Any] = [
        bigquery.ArrayQueryParameter("profile_ids", "STRING", [str(p) for p in profile_ids])
    ]

    filters = ["CAST(profile_id AS STRING) IN UNNEST(@profile_ids)"]

    if gap_detector_run_id:
        filters.append("CAST(gap_detector_run_id AS STRING) = @gap_detector_run_id")
        params.append(bigquery.ScalarQueryParameter("gap_detector_run_id", "STRING", gap_detector_run_id))
    elif triggered_after:
        filters.append("created_at > @triggered_after")
        params.append(bigquery.ScalarQueryParameter("triggered_after", "TIMESTAMP", triggered_after))

    where_clause = "\n          AND ".join(filters)

    query = f"""
        SELECT
            CAST(profile_id AS STRING) AS profile_id,
            CAST(gap_detector_run_id AS STRING) AS gap_detector_run_id,
            profile_name,
            field_name,
            hint_text,
            suggested_action,
            ROUND(CAST(confidence_score AS FLOAT64), 3) AS confidence_score,
            confidence_level,
            confidence_decision,
            confidence_reason,
            source_domain_internal,
            source_url_internal,
            source_reliability_score,
            normalized_uplift_score,
            contradiction_penalty,
            confidence_id,
            hint_id,
            created_at
        FROM `{BQ_CONFIDENCE_TABLE}`
        WHERE {where_clause}
        ORDER BY
            CAST(profile_id AS STRING),
            CASE confidence_level
                WHEN 'HIGH' THEN 1
                WHEN 'MEDIUM' THEN 2
                WHEN 'LOW' THEN 3
                ELSE 4
            END,
            confidence_score DESC,
            created_at DESC
    """
    try:
        rows = client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
        return [dict(row) for row in rows]
    except Exception as exc:
        log.error("get_confidence_results failed: %s", exc)
        return []


def build_profile_summary(hints: list[dict], profile_id: str) -> dict:
    """Build a lightweight profile summary from confidence rows for the UI."""
    if not hints:
        return {
            "profile_id": str(profile_id),
            "profile_name": str(profile_id),
            "profile_summary_text": "No Confidence Agent rows were found for this profile.",
            "top_actions": [],
            "created_at": None,
            "high_count": 0,
            "medium_count": 0,
            "low_count": 0,
            "total_hints": 0,
        }

    levels = Counter((h.get("confidence_level") or "UNKNOWN") for h in hints)
    profile_name = next((h.get("profile_name") for h in hints if h.get("profile_name")), None)
    created_at = _max_created_at(hints)

    total = len(hints)
    high = levels.get("HIGH", 0)
    medium = levels.get("MEDIUM", 0)
    low = levels.get("LOW", 0)

    if high:
        summary_text = f"{total} hint(s) found. {high} are recommended updates, {medium} need review, and {low} are low-confidence."
    elif medium:
        summary_text = f"{total} hint(s) found. {medium} need moderator review and {low} are low-confidence."
    elif low:
        summary_text = (
            f"{total} low-confidence candidate(s) found. They are hidden by default because the external match is weak "
            "and are not recommended updates."
        )
    else:
        summary_text = f"{total} hint(s) found. Review confidence details before using them."

    actions: list[str] = []
    seen: set[str] = set()
    for hint in hints:
        action = (hint.get("suggested_action") or "").strip()
        if action and action not in seen and hint.get("confidence_level") in {"HIGH", "MEDIUM"}:
            seen.add(action)
            actions.append(action)

    return {
        "profile_id": str(profile_id),
        "profile_name": profile_name or str(profile_id),
        "profile_summary_text": summary_text,
        "top_actions": actions[:5],
        "created_at": created_at,
        "high_count": high,
        "medium_count": medium,
        "low_count": low,
        "total_hints": total,
    }


def _max_created_at(hints: list[dict]):
    values = [h.get("created_at") for h in hints if h.get("created_at") is not None]
    if not values:
        return None
    try:
        return max(values)
    except TypeError:
        # Defensive fallback if BigQuery returns mixed timestamp representations.
        return values[0]


# queries behind the /queue page
def get_queue_stats(client: bigquery.Client) -> dict:
    """Return aggregate statistics about the full priority queue."""
    defaults: dict = {
        "total_profiles": 0,
        "high_count": 0,
        "medium_count": 0,
        "low_count": 0,
        "anomaly_high": 0,
        "anomaly_medium": 0,
        "anomaly_normal": 0,
        "country_count": 0,
        "top_fields": [],
    }

    counts_query = f"""
        SELECT
            COUNT(*) AS total_profiles,
            COUNTIF(integrated_priority_label = 'high_priority')   AS high_count,
            COUNTIF(integrated_priority_label = 'medium_priority') AS medium_count,
            COUNTIF(integrated_priority_label = 'low_priority')    AS low_count,
            COUNTIF(anomaly_review_tier = 'HIGH') AS anomaly_high,
            COUNTIF(anomaly_review_tier IN ('MEDIUM_KMEANS', 'MEDIUM_AUTOENCODER')) AS anomaly_medium,
            COUNTIF(anomaly_review_tier = 'NORMAL') AS anomaly_normal,
            COUNT(DISTINCT country) AS country_count
        FROM (
            SELECT *
            FROM `{BQ_QUEUE_TABLE}`
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY sitecode
                ORDER BY integrated_priority_score DESC
            ) = 1
        )
    """

    fields_query = f"""
        SELECT field_name, COUNT(DISTINCT sitecode) AS profile_count
        FROM `{BQ_QUEUE_TABLE}`
        WHERE field_name IS NOT NULL
        GROUP BY field_name
        ORDER BY profile_count DESC
        LIMIT 6
    """

    try:
        rows = list(client.query(counts_query).result())
        if rows:
            row = dict(rows[0])
            defaults.update({k: int(v) if v is not None else 0 for k, v in row.items()})
    except Exception as exc:
        log.error("get_queue_stats counts query failed: %s", exc)

    try:
        rows = list(client.query(fields_query).result())
        defaults["top_fields"] = [
            {"field_name": r["field_name"], "profile_count": int(r["profile_count"])}
            for r in rows
        ]
    except Exception as exc:
        log.error("get_queue_stats fields query failed: %s", exc)

    return defaults


def get_priority_queue(
    client: bigquery.Client, limit: int = 50, offset: int = 0
) -> list[dict]:
    """Return one page of profiles from the ML priority queue, one row per sitecode."""
    query = f"""
        SELECT
            sitecode,
            profile_name,
            field_name,
            issue_type,
            country,
            estimated_moderator_effort,
            integrated_priority_label,
            integrated_priority_score,
            integrated_queue_rank,
            anomaly_review_tier,
            prehint_score,
            expected_score_uplift
        FROM `{BQ_QUEUE_TABLE}`
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY sitecode
            ORDER BY integrated_priority_score DESC, integrated_queue_rank ASC
        ) = 1
        ORDER BY integrated_priority_score DESC
        LIMIT @limit
        OFFSET @offset
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("limit", "INT64", int(limit)),
            bigquery.ScalarQueryParameter("offset", "INT64", int(offset)),
        ]
    )
    try:
        rows = client.query(query, job_config=job_config).result()
        return [dict(row) for row in rows]
    except Exception as exc:
        log.error("get_priority_queue failed: %s", exc)
        return []


# queries behind the /profile/<profile_id> page
def get_hints_for_profile(client: bigquery.Client, profile_id: str) -> list[dict]:
    """Return the most recent Confidence Agent hints for a profile."""
    query = f"""
        SELECT
            CAST(profile_id AS STRING) AS profile_id,
            CAST(gap_detector_run_id AS STRING) AS gap_detector_run_id,
            profile_name,
            field_name,
            hint_text,
            suggested_action,
            ROUND(CAST(confidence_score AS FLOAT64), 3) AS confidence_score,
            confidence_level,
            confidence_decision,
            confidence_reason,
            source_domain_internal,
            source_url_internal,
            source_reliability_score,
            normalized_uplift_score,
            contradiction_penalty,
            confidence_id,
            hint_id,
            created_at
        FROM `{BQ_CONFIDENCE_TABLE}`
        WHERE CAST(profile_id AS STRING) = @profile_id
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY CAST(profile_id AS STRING), field_name
            ORDER BY created_at DESC
        ) = 1
        ORDER BY
            CASE confidence_level
                WHEN 'HIGH' THEN 1
                WHEN 'MEDIUM' THEN 2
                WHEN 'LOW' THEN 3
                ELSE 4
            END,
            confidence_score DESC,
            created_at DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("profile_id", "STRING", str(profile_id))]
    )
    try:
        rows = client.query(query, job_config=job_config).result()
        return [dict(row) for row in rows]
    except Exception as exc:
        log.error("get_hints_for_profile failed for %s: %s", profile_id, exc)
        return []


def get_summary_for_profile(client: bigquery.Client, profile_id: str) -> dict | None:
    """Return a generated summary based on the latest confidence rows."""
    hints = get_hints_for_profile(client, profile_id)
    if not hints:
        return None
    return build_profile_summary(hints, profile_id)


# Backward-compatible helper name. The confidence table now already contains all fields.
def get_confidence_scores(
    client: bigquery.Client,
    profile_ids: list[str],
    gap_detector_run_id: str | None = None,
) -> dict[str, dict[str, dict]]:
    rows = get_confidence_results(client, profile_ids, gap_detector_run_id=gap_detector_run_id)
    result: dict[str, dict[str, dict]] = {}
    for row in rows:
        pid = str(row.get("profile_id", ""))
        field = str(row.get("field_name", ""))
        result.setdefault(pid, {})[field] = {
            "confidence_score": row.get("confidence_score"),
            "confidence_level": row.get("confidence_level"),
            "confidence_decision": row.get("confidence_decision"),
            "confidence_reason": row.get("confidence_reason"),
        }
    return result
