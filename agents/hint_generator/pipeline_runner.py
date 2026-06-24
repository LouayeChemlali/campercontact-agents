from __future__ import annotations

import datetime
import os
import uuid
from collections import defaultdict
from typing import Any, Optional

from google.cloud import bigquery

# Reuse the existing text-generation functions from main.py.
from main import (
    build_profile_summary_rows,
    fallback_field_hint,
    generate_field_hint,
    build_suggested_action,
    score_delta,
)

PROJECT_ID = os.getenv("PROJECT_ID", "project-62cd3637-0b98-4aa5-8d5")
ENTITY_MATCHER_INPUT_TABLE = os.getenv(
    "ENTITY_MATCHER_INPUT_TABLE",
    f"{PROJECT_ID}.entity_matcher_pipeline.entity_matcher_output_v2",
)
ANOMALY_TABLE = os.getenv(
    "ANOMALY_TABLE",
    f"{PROJECT_ID}.anomaly_detector_final.combined_quality_anomaly_scores_v1",
)
PRIORITIZED_CANDIDATES_TABLE = os.getenv(
    "PRIORITIZED_CANDIDATES_TABLE",
    f"{PROJECT_ID}.hint_prioritization.prioritized_hint_candidates_v1",
)
FIELD_HINTS_TABLE = os.getenv(
    "FIELD_HINTS_TABLE",
    f"{PROJECT_ID}.primary_dataset.hint_field_results",
)
PROFILE_SUMMARIES_TABLE = os.getenv(
    "PROFILE_SUMMARIES_TABLE",
    f"{PROJECT_ID}.primary_dataset.hint_profile_summaries",
)


def _table_ref(table_name: str) -> str:
    return f"`{table_name}`"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _table_exists(client: bigquery.Client, table_name: str) -> bool:
    try:
        client.get_table(table_name)
        return True
    except Exception:
        return False


def ensure_prioritized_table(client: bigquery.Client) -> None:
    query = f"""
    CREATE SCHEMA IF NOT EXISTS `{PROJECT_ID}.hint_prioritization`;

    CREATE TABLE IF NOT EXISTS {_table_ref(PRIORITIZED_CANDIDATES_TABLE)} (
      hint_candidate_id STRING,
      gap_detector_run_id STRING,
      sitecode STRING,
      profile_id STRING,
      profile_name STRING,
      field_name STRING,
      current_value STRING,
      suggested_value STRING,
      source_url_internal STRING,
      source_domain_internal STRING,
      entity_match_score FLOAT64,
      verification_status STRING,
      recommendation_type STRING,
      prehint_score FLOAT64,
      posthint_score_est FLOAT64,
      score_delta FLOAT64,
      ml_reason STRING,
      baseline_priority_score FLOAT64,
      anomaly_review_tier STRING,
      anomaly_priority_bonus FLOAT64,
      integrated_priority_score FLOAT64,
      integrated_priority_label STRING,
      integrated_queue_rank INT64,
      gap_recommended_actions STRING,
      created_at TIMESTAMP
    );
    """
    client.query(query).result()


def refresh_prioritized_candidates(
    client: bigquery.Client,
    gap_detector_run_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create/refresh the prioritized hint candidate table from Entity Matcher output.

    This is the SQL-only Hint Prioritization step. It deduplicates raw Entity Matcher rows,
    keeps the best candidate per profile + field, and optionally adds anomaly bonus points.
    """
    ensure_prioritized_table(client)
    anomaly_exists = _table_exists(client, ANOMALY_TABLE)

    if gap_detector_run_id:
        delete_query = f"""
        DELETE FROM {_table_ref(PRIORITIZED_CANDIDATES_TABLE)}
        WHERE gap_detector_run_id = @gap_detector_run_id
        """
        client.query(
            delete_query,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("gap_detector_run_id", "STRING", gap_detector_run_id)
                ]
            ),
        ).result()
    else:
        # For safety, do not delete all historical candidates unless explicitly scoped.
        pass

    anomaly_join = ""
    anomaly_select = """
      'NORMAL' AS anomaly_review_tier,
      FALSE AS is_kmeans_anomaly,
      FALSE AS is_autoencoder_anomaly,
      CAST(NULL AS FLOAT64) AS kmeans_normalized_distance,
      CAST(NULL AS FLOAT64) AS autoencoder_mean_squared_error,
    """
    if anomaly_exists:
        anomaly_join = f"""
        LEFT JOIN {_table_ref(ANOMALY_TABLE)} AS anomalies
          ON CAST(best.sitecode AS STRING) = CAST(anomalies.sitecode AS STRING)
        """
        anomaly_select = """
          COALESCE(anomalies.anomaly_review_tier, 'NORMAL') AS anomaly_review_tier,
          COALESCE(anomalies.is_kmeans_anomaly, FALSE) AS is_kmeans_anomaly,
          COALESCE(anomalies.is_autoencoder_anomaly, FALSE) AS is_autoencoder_anomaly,
          SAFE_CAST(anomalies.kmeans_normalized_distance AS FLOAT64) AS kmeans_normalized_distance,
          SAFE_CAST(anomalies.autoencoder_mean_squared_error AS FLOAT64) AS autoencoder_mean_squared_error,
        """

    insert_query = f"""
    INSERT INTO {_table_ref(PRIORITIZED_CANDIDATES_TABLE)} (
      hint_candidate_id,
      gap_detector_run_id,
      sitecode,
      profile_id,
      profile_name,
      field_name,
      current_value,
      suggested_value,
      source_url_internal,
      source_domain_internal,
      entity_match_score,
      verification_status,
      recommendation_type,
      prehint_score,
      posthint_score_est,
      score_delta,
      ml_reason,
      baseline_priority_score,
      anomaly_review_tier,
      anomaly_priority_bonus,
      integrated_priority_score,
      integrated_priority_label,
      integrated_queue_rank,
      gap_recommended_actions,
      created_at
    )
    WITH raw_candidates AS (
      SELECT
        CAST(gap_detector_run_id AS STRING) AS gap_detector_run_id,
        CAST(profile_id AS STRING) AS sitecode,
        CAST(profile_id AS STRING) AS profile_id,
        CAST(profile_name AS STRING) AS profile_name,
        CAST(field_name AS STRING) AS field_name,
        CAST(current_value AS STRING) AS current_value,
        CAST(external_value AS STRING) AS suggested_value,
        CAST(source_url AS STRING) AS source_url_internal,
        CAST(source_domain AS STRING) AS source_domain_internal,
        SAFE_CAST(entity_match_score AS FLOAT64) AS entity_match_score,
        CAST(verification_status AS STRING) AS verification_status,
        CAST(gap_recommended_actions AS STRING) AS gap_recommended_actions,
        run_timestamp,
        CASE
          WHEN field_name IN ('email', 'phone', 'website') THEN 'update_contact_info'
          WHEN field_name IN ('address', 'city', 'country') THEN 'verify_location_info'
          WHEN field_name = 'name' THEN 'verify_profile_name'
          ELSE 'review_external_evidence'
        END AS recommendation_type,
        CASE
          WHEN verification_status = 'NEW_INFO' THEN 4.00
          WHEN verification_status = 'MISMATCH_INFO' THEN 3.00
          WHEN verification_status = 'MATCH' THEN 1.00
          ELSE 0.00
        END AS status_priority_points,
        CASE
          WHEN field_name IN ('email', 'phone', 'website') THEN 2.00
          WHEN field_name IN ('address', 'city', 'country', 'name') THEN 1.50
          ELSE 1.00
        END AS field_priority_points
      FROM {_table_ref(ENTITY_MATCHER_INPUT_TABLE)}
      WHERE verification_status IN ('NEW_INFO', 'MISMATCH_INFO', 'MATCH')
        AND NULLIF(TRIM(CAST(external_value AS STRING)), '') IS NOT NULL
        AND (@gap_detector_run_id IS NULL OR CAST(gap_detector_run_id AS STRING) = @gap_detector_run_id)
    ),
    scored AS (
      SELECT
        *,
        ROUND(
          status_priority_points
          + field_priority_points
          + COALESCE(entity_match_score, 0.0) * 2.0,
          2
        ) AS baseline_priority_score,
        ROW_NUMBER() OVER (
          PARTITION BY gap_detector_run_id, profile_id, field_name
          ORDER BY
            status_priority_points DESC,
            COALESCE(entity_match_score, 0.0) DESC,
            IF(source_domain_internal IS NULL OR source_domain_internal = '', 0, 1) DESC,
            run_timestamp DESC
        ) AS candidate_rank
      FROM raw_candidates
    ),
    best AS (
      SELECT * EXCEPT(candidate_rank)
      FROM scored
      WHERE candidate_rank = 1
    ),
    with_anomaly AS (
      SELECT
        best.*,
        {anomaly_select}
        CASE
          WHEN {"COALESCE(anomalies.anomaly_review_tier, 'NORMAL')" if anomaly_exists else "'NORMAL'"} = 'HIGH' THEN 1.00
          WHEN {"COALESCE(anomalies.anomaly_review_tier, 'NORMAL')" if anomaly_exists else "'NORMAL'"} IN ('MEDIUM_KMEANS', 'MEDIUM_AUTOENCODER') THEN 0.50
          ELSE 0.00
        END AS anomaly_priority_bonus
      FROM best
      {anomaly_join}
    ),
    final_scores AS (
      SELECT
        *,
        ROUND(baseline_priority_score + anomaly_priority_bonus, 2) AS integrated_priority_score
      FROM with_anomaly
    ),
    ranked AS (
      SELECT
        *,
        CASE
          WHEN integrated_priority_score >= 7.00 THEN 'high_priority'
          WHEN integrated_priority_score >= 4.50 THEN 'medium_priority'
          ELSE 'low_priority'
        END AS integrated_priority_label,
        ROW_NUMBER() OVER (
          PARTITION BY gap_detector_run_id
          ORDER BY integrated_priority_score DESC, baseline_priority_score DESC, sitecode, field_name
        ) AS integrated_queue_rank
      FROM final_scores
    )
    SELECT
      GENERATE_UUID() AS hint_candidate_id,
      gap_detector_run_id,
      sitecode,
      profile_id,
      profile_name,
      field_name,
      current_value,
      suggested_value,
      source_url_internal,
      source_domain_internal,
      entity_match_score,
      verification_status,
      recommendation_type,
      CAST(NULL AS FLOAT64) AS prehint_score,
      CAST(NULL AS FLOAT64) AS posthint_score_est,
      CAST(NULL AS FLOAT64) AS score_delta,
      CONCAT(
        'Priority based on Entity Matcher status ', verification_status,
        ', field ', field_name,
        ', source ', COALESCE(source_domain_internal, 'unknown source'),
        ', anomaly tier ', anomaly_review_tier,
        '. Baseline priority score: ', CAST(baseline_priority_score AS STRING),
        '; anomaly bonus: ', CAST(anomaly_priority_bonus AS STRING),
        '; integrated score: ', CAST(integrated_priority_score AS STRING),
        '.'
      ) AS ml_reason,
      baseline_priority_score,
      anomaly_review_tier,
      anomaly_priority_bonus,
      integrated_priority_score,
      integrated_priority_label,
      integrated_queue_rank,
      gap_recommended_actions,
      CURRENT_TIMESTAMP() AS created_at
    FROM ranked
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("gap_detector_run_id", "STRING", gap_detector_run_id)
        ]
    )
    client.query(insert_query, job_config=job_config).result()

    count_query = f"""
    SELECT COUNT(*) AS candidate_rows
    FROM {_table_ref(PRIORITIZED_CANDIDATES_TABLE)}
    WHERE (@gap_detector_run_id IS NULL OR gap_detector_run_id = @gap_detector_run_id)
    """
    result = list(client.query(count_query, job_config=job_config).result())
    return {
        "prioritized_table": PRIORITIZED_CANDIDATES_TABLE,
        "gap_detector_run_id": gap_detector_run_id,
        "candidate_rows": int(result[0]["candidate_rows"]),
        "anomaly_table_used": anomaly_exists,
    }


def fetch_prioritized_inputs(
    client: bigquery.Client,
    gap_detector_run_id: Optional[str] = None,
    profile_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    where_parts = ["1=1"]
    params: list[Any] = []

    # Do not filter weak/source-only website candidates here.
    # The Confidence Agent is now responsible for marking these as LOW / hide_or_manual_review.
    if gap_detector_run_id:
        where_parts.append("gap_detector_run_id = @gap_detector_run_id")
        params.append(bigquery.ScalarQueryParameter("gap_detector_run_id", "STRING", gap_detector_run_id))
    if profile_id:
        where_parts.append("profile_id = @profile_id")
        params.append(bigquery.ScalarQueryParameter("profile_id", "STRING", profile_id))
    limit_clause = "LIMIT @limit" if limit else ""
    if limit:
        params.append(bigquery.ScalarQueryParameter("limit", "INT64", int(limit)))

    query = f"""
    SELECT
      profile_id,
      profile_name,
      field_name,
      current_value,
      suggested_value,
      source_url_internal,
      source_domain_internal,
      entity_match_score,
      verification_status,
      recommendation_type,
      prehint_score,
      posthint_score_est,
      ml_reason,
      baseline_priority_score,
      anomaly_review_tier,
      anomaly_priority_bonus,
      integrated_priority_score,
      integrated_priority_label,
      integrated_queue_rank,
      gap_detector_run_id
    FROM (
      SELECT
        base.* REPLACE (
          CASE
            WHEN LOWER(COALESCE(CAST(base.field_name AS STRING), '')) = 'website'
              THEN COALESCE(
                NULLIF(TRIM(CAST(base.current_value AS STRING)), ''),
                NULLIF(TRIM(CAST(master.contact_url AS STRING)), '')
              )
            ELSE CAST(base.current_value AS STRING)
          END AS current_value
        )
      FROM {_table_ref(PRIORITIZED_CANDIDATES_TABLE)} AS base
      LEFT JOIN `{PROJECT_ID}.primary_dataset.profile_master_sitecode_clean_v2` AS master
        ON CAST(base.profile_id AS STRING) = CAST(master.sitecode AS STRING)
    )
    WHERE {' AND '.join(where_parts)}
    ORDER BY integrated_queue_rank ASC
    {limit_clause}
    """
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    return [dict(row.items()) for row in client.query(query, job_config=job_config).result()]


def build_field_hint_rows_from_prioritized(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output_rows = []
    created_at = _now_iso()

    placeholder_values = {
        "the current value",
        "current value",
        "the suggested improvement",
        "suggested improvement",
        "none",
        "null",
        "nan",
        "n/a",
        "unknown",
        "",
    }

    def _clean_value(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _is_placeholder(value: Any) -> bool:
        text = _clean_value(value).lower().strip(" '\".")
        return text in placeholder_values

    for row in rows:
        row = dict(row)

        # Remove fake template placeholders before generating moderator-facing text.
        if _is_placeholder(row.get("current_value")):
            row["current_value"] = ""

        if _is_placeholder(row.get("suggested_value")):
            row["suggested_value"] = ""

        field_name = _clean_value(row.get("field_name")).lower()
        source_url = _clean_value(row.get("source_url_internal") or row.get("source_url"))
        source_domain = _clean_value(row.get("source_domain_internal") or row.get("source_domain"))

        # Do not drop candidate rows here. The Confidence Agent is the safety gate.
        # For source-only website candidates, write a row so Confidence Agent can classify it as LOW.
        if not _clean_value(row.get("suggested_value")):
            if field_name == "website" and source_url:
                row["suggested_value"] = source_url
            elif source_url:
                row["suggested_value"] = source_url
            elif source_domain:
                row["suggested_value"] = f"Candidate source from {source_domain}"
            elif _clean_value(row.get("ml_reason")):
                row["suggested_value"] = "Candidate value requires moderator review"
            else:
                row["suggested_value"] = "Candidate value unavailable"

        row["score_delta"] = score_delta(row.get("prehint_score"), row.get("posthint_score_est"))

        hint_text = generate_field_hint(row)
        suggested_action = build_suggested_action(row)

        is_external_source_page = (
            str(row.get("field_name") or "").strip().lower() == "website"
            and row.get("suggested_value")
            and row.get("source_url_internal")
            and str(row.get("suggested_value")).strip().rstrip("/") == str(row.get("source_url_internal")).strip().rstrip("/")
        )
        if is_external_source_page:
            suggested_action = (
                "Use the external source page to verify the missing official website/contact details; "
                "do not copy the source-page URL as the accommodation website without checking it."
            )

        output_rows.append({
            "hint_id": str(uuid.uuid4()),
            "gap_detector_run_id": row.get("gap_detector_run_id"),
            "profile_id": row.get("profile_id"),
            "profile_name": row.get("profile_name"),
            "field_name": row.get("field_name"),
            "recommendation_type": row.get("recommendation_type"),
            "current_value": row.get("current_value"),
            "suggested_value": row.get("suggested_value"),
            "prehint_score": row.get("prehint_score"),
            "posthint_score_est": row.get("posthint_score_est"),
            "score_delta": row.get("score_delta"),
            "ml_reason": row.get("ml_reason"),
            "hint_text": hint_text,
            "suggested_action": suggested_action,
            "source_url_internal": row.get("source_url_internal"),
            "source_domain_internal": row.get("source_domain_internal"),
            "entity_match_score": row.get("entity_match_score"),
            "verification_status": row.get("verification_status"),
            "created_at": created_at,
        })
    return output_rows


def _insert_rows_json(client: bigquery.Client, table_name: str, rows: list[dict[str, Any]], dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return {"table": table_name, "rows_ready": len(rows), "inserted": False}
    if not rows:
        return {"table": table_name, "rows_ready": 0, "inserted": False}
    errors = client.insert_rows_json(table_name, rows)
    if errors:
        raise RuntimeError(f"BigQuery insert errors for {table_name}: {errors}")
    return {"table": table_name, "rows_inserted": len(rows), "inserted": True}


def run_hint_generator(
    gap_detector_run_id: Optional[str] = None,
    profile_id: Optional[str] = None,
    limit: Optional[int] = 20,
    refresh_prioritization: bool = True,
    write_bigquery: bool = True,
) -> dict[str, Any]:
    client = bigquery.Client(project=PROJECT_ID)

    prioritization_result = {"skipped": True}
    if refresh_prioritization:
        prioritization_result = refresh_prioritized_candidates(
            client=client,
            gap_detector_run_id=gap_detector_run_id,
        )

    input_rows = fetch_prioritized_inputs(
        client=client,
        gap_detector_run_id=gap_detector_run_id,
        profile_id=profile_id,
        limit=limit,
    )
    field_hint_rows = build_field_hint_rows_from_prioritized(input_rows)
    summary_rows = build_profile_summary_rows(field_hint_rows)

    field_write = _insert_rows_json(client, FIELD_HINTS_TABLE, field_hint_rows, dry_run=not write_bigquery)
    summary_write = _insert_rows_json(client, PROFILE_SUMMARIES_TABLE, summary_rows, dry_run=not write_bigquery)

    status_counts: dict[str, int] = defaultdict(int)
    for row in input_rows:
        status_counts[str(row.get("verification_status"))] += 1

    return {
        "status": "completed",
        "gap_detector_run_id": gap_detector_run_id,
        "profile_id": profile_id,
        "prioritization": prioritization_result,
        "input_rows_loaded": len(input_rows),
        "field_hints_generated": len(field_hint_rows),
        "profile_summaries_generated": len(summary_rows),
        "status_counts": dict(status_counts),
        "field_hint_write": field_write,
        "profile_summary_write": summary_write,
    }
