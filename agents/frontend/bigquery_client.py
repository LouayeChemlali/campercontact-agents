"""BigQuery reads for the moderator frontend. All queries are parameterized."""

import logging

from google.cloud import bigquery

from config import BIGQUERY_PROJECT, BQ_CONFIDENCE_TABLE, BQ_HINTS_TABLE, BQ_QUEUE_TABLE, BQ_SUMMARIES_TABLE

log = logging.getLogger(__name__)


def get_client() -> bigquery.Client:
    """Return a BigQuery client for the configured project."""
    return bigquery.Client(project=BIGQUERY_PROJECT)


# ---------------------------------------------------------------------------
# Index page
# ---------------------------------------------------------------------------

def get_recent_profiles(client: bigquery.Client, limit: int = 10) -> list[dict]:
    """
    Return the most recently processed profiles.

    Reads from hint_profile_summaries, ordered newest first.
    Returns plain dicts so templates do not need to import BigQuery types.
    """
    query = f"""
        SELECT
            CAST(profile_id AS STRING) AS profile_id,
            profile_name,
            created_at
        FROM `{BQ_SUMMARIES_TABLE}`
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY CAST(profile_id AS STRING)
            ORDER BY created_at DESC
        ) = 1
        ORDER BY created_at DESC
        LIMIT @limit
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("limit", "INT64", int(limit))
        ]
    )
    try:
        rows = client.query(query, job_config=job_config).result()
        return [dict(row) for row in rows]
    except Exception as exc:
        log.error("get_recent_profiles failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Polling: /api/status/<run_id>
# ---------------------------------------------------------------------------

def get_profile_status(
    client: bigquery.Client,
    profile_ids: list[str],
    triggered_after: str | None = None,
    gap_detector_run_id: str | None = None,
) -> dict[str, dict]:
    """
    Return the current pipeline state for each requested profile.

    When gap_detector_run_id is provided it is used as the primary filter so
    only results from this specific run are returned. This avoids returning
    stale hints from an earlier run for the same profile.

    triggered_after is used as a fallback when no gap_detector_run_id is given.

    Each profile entry contains:
      hints_ready    bool
      summary_ready  bool
      hints          list[dict]
      summary        dict | None
    """
    status: dict[str, dict] = {
        pid: {
            "hints_ready": False,
            "summary_ready": False,
            "hints": [],
            "summary": None,
        }
        for pid in profile_ids
    }

    if not profile_ids:
        return status

    hints = _fetch_hints(client, profile_ids, triggered_after, gap_detector_run_id)
    summaries = _fetch_summaries(client, profile_ids, triggered_after, gap_detector_run_id)

    by_profile: dict[str, list[dict]] = {}
    for hint in hints:
        pid = str(hint.get("profile_id", ""))
        by_profile.setdefault(pid, []).append(hint)

    for pid, profile_hints in by_profile.items():
        if pid in status:
            status[pid]["hints"] = profile_hints
            status[pid]["hints_ready"] = True

    for summary in summaries:
        pid = str(summary.get("profile_id", ""))
        if pid in status:
            status[pid]["summary"] = summary
            status[pid]["summary_ready"] = True

    return status


def _fetch_hints(
    client: bigquery.Client,
    profile_ids: list[str],
    triggered_after: str | None,
    gap_detector_run_id: str | None = None,
) -> list[dict]:
    """Fetch field-level hints filtered by run ID when available, else by timestamp.

    Falls back to timestamp filtering if the gap_detector_run_id column does not
    yet exist in the table (older deployments).
    """
    base_params: list = [
        bigquery.ArrayQueryParameter("profile_ids", "STRING", [str(p) for p in profile_ids])
    ]

    if gap_detector_run_id:
        run_params = base_params + [
            bigquery.ScalarQueryParameter("gap_detector_run_id", "STRING", gap_detector_run_id)
        ]
        run_query = f"""
            SELECT *
            FROM `{BQ_HINTS_TABLE}`
            WHERE CAST(profile_id AS STRING) IN UNNEST(@profile_ids)
              AND CAST(gap_detector_run_id AS STRING) = @gap_detector_run_id
            ORDER BY profile_id, score_delta DESC NULLS LAST
        """
        try:
            rows = client.query(
                run_query, job_config=bigquery.QueryJobConfig(query_parameters=run_params)
            ).result()
            return [dict(row) for row in rows]
        except Exception as exc:
            # Column may not exist yet - fall through to timestamp filter.
            log.warning("_fetch_hints run-ID filter failed (%s), falling back to timestamp", exc)

    # Fallback: filter by creation timestamp.
    params = base_params[:]
    extra = ""
    if triggered_after:
        extra = "AND created_at > @triggered_after"
        params.append(bigquery.ScalarQueryParameter("triggered_after", "TIMESTAMP", triggered_after))

    query = f"""
        SELECT *
        FROM `{BQ_HINTS_TABLE}`
        WHERE CAST(profile_id AS STRING) IN UNNEST(@profile_ids)
          {extra}
        ORDER BY profile_id, score_delta DESC NULLS LAST
    """
    try:
        rows = client.query(
            query, job_config=bigquery.QueryJobConfig(query_parameters=params)
        ).result()
        return [dict(row) for row in rows]
    except Exception as exc:
        log.error("_fetch_hints failed: %s", exc)
        return []


def _fetch_summaries(
    client: bigquery.Client,
    profile_ids: list[str],
    triggered_after: str | None,
    gap_detector_run_id: str | None = None,
) -> list[dict]:
    """Fetch profile-level summaries filtered by run ID when available, else by timestamp.

    Falls back to timestamp filtering if the gap_detector_run_id column does not
    yet exist in the table (older deployments).
    """
    base_params: list = [
        bigquery.ArrayQueryParameter("profile_ids", "STRING", [str(p) for p in profile_ids])
    ]

    if gap_detector_run_id:
        run_params = base_params + [
            bigquery.ScalarQueryParameter("gap_detector_run_id", "STRING", gap_detector_run_id)
        ]
        run_query = f"""
            SELECT *
            FROM `{BQ_SUMMARIES_TABLE}`
            WHERE CAST(profile_id AS STRING) IN UNNEST(@profile_ids)
              AND CAST(gap_detector_run_id AS STRING) = @gap_detector_run_id
        """
        try:
            rows = client.query(
                run_query, job_config=bigquery.QueryJobConfig(query_parameters=run_params)
            ).result()
            return [dict(row) for row in rows]
        except Exception as exc:
            log.warning("_fetch_summaries run-ID filter failed (%s), falling back to timestamp", exc)

    params = base_params[:]
    extra = ""
    if triggered_after:
        extra = "AND created_at > @triggered_after"
        params.append(bigquery.ScalarQueryParameter("triggered_after", "TIMESTAMP", triggered_after))

    query = f"""
        SELECT *
        FROM `{BQ_SUMMARIES_TABLE}`
        WHERE CAST(profile_id AS STRING) IN UNNEST(@profile_ids)
          {extra}
    """
    try:
        rows = client.query(
            query, job_config=bigquery.QueryJobConfig(query_parameters=params)
        ).result()
        return [dict(row) for row in rows]
    except Exception as exc:
        log.error("_fetch_summaries failed: %s", exc)
        return []


def get_confidence_scores(
    client: bigquery.Client,
    profile_ids: list[str],
    gap_detector_run_id: str | None = None,
) -> dict[str, dict[str, dict]]:
    """
    Return confidence scores from the confidence agent, keyed by profile_id then field_name.

    Returns an empty dict if the table does not exist or has no rows for this run.
    """
    if not profile_ids:
        return {}

    params: list = [
        bigquery.ArrayQueryParameter("profile_ids", "STRING", [str(p) for p in profile_ids])
    ]
    run_filter = ""
    if gap_detector_run_id:
        run_filter = "AND CAST(gap_detector_run_id AS STRING) = @gap_detector_run_id"
        params.append(bigquery.ScalarQueryParameter("gap_detector_run_id", "STRING", gap_detector_run_id))

    query = f"""
        SELECT
            CAST(profile_id AS STRING) AS profile_id,
            field_name,
            ROUND(confidence_score, 3) AS confidence_score,
            confidence_level,
            confidence_decision
        FROM `{BQ_CONFIDENCE_TABLE}`
        WHERE CAST(profile_id AS STRING) IN UNNEST(@profile_ids)
          {run_filter}
    """
    try:
        rows = client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
        result: dict[str, dict[str, dict]] = {}
        for row in rows:
            r = dict(row)
            pid = r["profile_id"]
            field = r["field_name"]
            result.setdefault(pid, {})[field] = {
                "confidence_score": r["confidence_score"],
                "confidence_level": r["confidence_level"],
                "confidence_decision": r["confidence_decision"],
            }
        return result
    except Exception as exc:
        log.warning("get_confidence_scores failed (table may not exist yet): %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Priority queue: /queue
# ---------------------------------------------------------------------------

def get_queue_stats(client: bigquery.Client) -> dict:
    """
    Return aggregate statistics about the full priority queue.

    Runs two queries: one for priority/anomaly counts (deduped by sitecode),
    one for top fields by profile count.
    """
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
            COUNTIF(anomaly_review_tier = 'HIGH')                                              AS anomaly_high,
            COUNTIF(anomaly_review_tier IN ('MEDIUM_KMEANS', 'MEDIUM_AUTOENCODER'))            AS anomaly_medium,
            COUNTIF(anomaly_review_tier = 'NORMAL')                                            AS anomaly_normal,
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
    """
    Return one page of profiles from the ML priority queue, one row per sitecode.

    Takes the highest-scoring hint per profile, ordered by integrated_priority_score.
    """
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
            bigquery.ScalarQueryParameter("limit",  "INT64", int(limit)),
            bigquery.ScalarQueryParameter("offset", "INT64", int(offset)),
        ]
    )
    try:
        rows = client.query(query, job_config=job_config).result()
        return [dict(row) for row in rows]
    except Exception as exc:
        log.error("get_priority_queue failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Profile lookup: /profile/<profile_id>
# ---------------------------------------------------------------------------

def get_hints_for_profile(
    client: bigquery.Client,
    profile_id: str,
) -> list[dict]:
    """
    Return the most recent hints for a profile, one per field.

    Uses QUALIFY to keep only the newest hint per field, so re-running the
    pipeline does not produce duplicates in the view.
    """
    query = f"""
        SELECT *
        FROM `{BQ_HINTS_TABLE}`
        WHERE CAST(profile_id AS STRING) = @profile_id
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY CAST(profile_id AS STRING), field_name
            ORDER BY created_at DESC
        ) = 1
        ORDER BY score_delta DESC NULLS LAST
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("profile_id", "STRING", str(profile_id))
        ]
    )
    try:
        rows = client.query(query, job_config=job_config).result()
        return [dict(row) for row in rows]
    except Exception as exc:
        log.error("get_hints_for_profile failed for %s: %s", profile_id, exc)
        return []


def get_summary_for_profile(
    client: bigquery.Client,
    profile_id: str,
) -> dict | None:
    """
    Return the most recent profile-level summary, or None if none exists.
    """
    query = f"""
        SELECT *
        FROM `{BQ_SUMMARIES_TABLE}`
        WHERE CAST(profile_id AS STRING) = @profile_id
        ORDER BY created_at DESC
        LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("profile_id", "STRING", str(profile_id))
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
        return dict(rows[0]) if rows else None
    except Exception as exc:
        log.error("get_summary_for_profile failed for %s: %s", profile_id, exc)
        return None
