from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import FastAPI
from google.cloud import bigquery
from pydantic import BaseModel

app = FastAPI()

PROJECT_ID = os.getenv("PROJECT_ID", "project-62cd3637-0b98-4aa5-8d5")

# Entity matcher output - same table the hint generator reads from.
ENTITY_TABLE = os.getenv(
    "ENTITY_TABLE",
    f"{PROJECT_ID}.entity_matcher_pipeline.entity_matcher_output_v2",
)
# ML layer scores joined in the hint generator view.
ML_TABLE = os.getenv(
    "ML_TABLE",
    f"{PROJECT_ID}.primary_dataset.v_ml_layer_hints",
)
OUTPUT_TABLE = os.getenv(
    "OUTPUT_TABLE",
    f"{PROJECT_ID}.primary_dataset.confidence_agent_output",
)

client = bigquery.Client(project=PROJECT_ID)


class ConfidenceRequest(BaseModel):
    gap_detector_run_id: Optional[str] = None


@app.get("/health")
def health():
    return {"status": "ok", "agent": "confidence-agent"}


@app.post("/run-confidence-agent")
def run_confidence_agent(payload: ConfidenceRequest = ConfidenceRequest()) -> Dict[str, Any]:
    gap_run_filter = ""
    params: list = []

    if payload.gap_detector_run_id:
        gap_run_filter = "WHERE CAST(e.gap_detector_run_id AS STRING) = @gap_detector_run_id"
        params.append(
            bigquery.ScalarQueryParameter("gap_detector_run_id", "STRING", payload.gap_detector_run_id)
        )

    # Check that entity matcher has rows for this run before scoring.
    check_query = f"""
        SELECT COUNT(*) AS row_count
        FROM `{ENTITY_TABLE}` e
        {gap_run_filter}
    """
    entity_rows = list(
        client.query(check_query, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
    )[0]["row_count"]

    if entity_rows == 0:
        return {
            "status": "waiting",
            "agent": "confidence-agent",
            "gap_detector_run_id": payload.gap_detector_run_id,
            "entity_matcher_rows": 0,
            "message": "No entity matcher rows found for this run yet.",
        }

    run_filter_insert = (
        f"AND CAST(e.gap_detector_run_id AS STRING) = '{payload.gap_detector_run_id}'"
        if payload.gap_detector_run_id else ""
    )

    scoring_sql = f"""
    CREATE OR REPLACE TABLE `{OUTPUT_TABLE}` AS

    WITH joined AS (
      SELECT
        e.profile_id,
        e.profile_name,
        CAST(e.gap_detector_run_id AS STRING) AS gap_detector_run_id,
        e.matched_source_url,
        e.matched_source_domain,
        e.source_title,
        e.source_snippet,
        e.matched_field AS field_name,
        e.current_campercontact_value,
        e.external_source_value,
        SAFE_CAST(e.entity_match_score AS FLOAT64) AS entity_match_score,
        e.verification_status,

        m.recommendation_type,
        SAFE_CAST(m.prehint_score AS FLOAT64) AS prehint_score,
        SAFE_CAST(m.posthint_score_est AS FLOAT64) AS posthint_score_est,
        m.ml_reason

      FROM `{ENTITY_TABLE}` e
      LEFT JOIN `{ML_TABLE}` m
        ON CAST(e.profile_id AS STRING) = CAST(m.profile_id AS STRING)
        AND e.matched_field = m.field_name
      WHERE 1=1 {run_filter_insert}
    ),

    scored AS (
      SELECT
        *,

        -- Source reliability: higher-trust domains get a higher score.
        CASE
          WHEN LOWER(matched_source_domain) LIKE '%booking%'     THEN 0.85
          WHEN LOWER(matched_source_domain) LIKE '%tripadvisor%' THEN 0.80
          WHEN LOWER(matched_source_domain) LIKE '%google%'      THEN 0.75
          WHEN LOWER(matched_source_domain) LIKE '%facebook%'    THEN 0.60
          WHEN matched_source_domain IS NOT NULL AND matched_source_domain != '' THEN 0.65
          ELSE 0.40
        END AS source_reliability_score,

        COALESCE(posthint_score_est - prehint_score, 0) AS expected_profile_quality_uplift,

        -- Normalize uplift to [0, 1] range (cap at 100 point swing).
        LEAST(GREATEST(COALESCE(posthint_score_est - prehint_score, 0) / 100.0, 0), 1)
          AS normalized_uplift_score,

        -- Map entity matcher verification statuses to contradiction penalties.
        CASE
          WHEN verification_status = 'MATCH'         THEN 0.00
          WHEN verification_status = 'NEW_INFO'      THEN 0.10
          WHEN verification_status = 'MISMATCH_INFO' THEN 0.35
          WHEN verification_status = 'CC_LOWER_RATE' THEN 0.15
          WHEN verification_status = 'CC_HIGHER_RATE'THEN 0.15
          ELSE 0.25
        END AS contradiction_penalty

      FROM joined
    ),

    final AS (
      SELECT
        *,
        LEAST(GREATEST(
          (
            COALESCE(entity_match_score, 0) * 0.50
            + source_reliability_score       * 0.25
            + normalized_uplift_score        * 0.25
            - contradiction_penalty
          ),
          0
        ), 1) AS confidence_score
      FROM scored
    )

    SELECT
      profile_id,
      profile_name,
      gap_detector_run_id,
      field_name,
      current_campercontact_value,
      external_source_value,
      matched_source_url,
      matched_source_domain,
      source_title,
      source_snippet,
      entity_match_score,
      source_reliability_score,
      prehint_score,
      posthint_score_est,
      expected_profile_quality_uplift,
      normalized_uplift_score,
      contradiction_penalty,
      confidence_score,
      CASE
        WHEN confidence_score >= 0.80 THEN 'HIGH'
        WHEN confidence_score >= 0.55 THEN 'MEDIUM'
        ELSE 'LOW'
      END AS confidence_level,
      CASE
        WHEN confidence_score >= 0.80 THEN 'auto_generate_hint'
        WHEN confidence_score >= 0.55 THEN 'send_to_human_review'
        ELSE 'reject_recommendation'
      END AS confidence_decision,
      recommendation_type,
      ml_reason,
      CURRENT_TIMESTAMP() AS created_at
    FROM final
    """

    client.query(scoring_sql).result()

    scored_rows = list(client.query(f"SELECT COUNT(*) AS n FROM `{OUTPUT_TABLE}`").result())[0]["n"]

    return {
        "status": "completed",
        "agent": "confidence-agent",
        "gap_detector_run_id": payload.gap_detector_run_id,
        "entity_matcher_rows": entity_rows,
        "scored_rows": scored_rows,
        "output_table": OUTPUT_TABLE,
    }
