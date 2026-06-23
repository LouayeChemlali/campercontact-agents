import os
from fastapi import FastAPI
from google.cloud import bigquery

app = FastAPI()

PROJECT_ID = os.getenv("PROJECT_ID", "project-62cd3637-0b98-4aa5-8d5")
BQ_DATASET = os.getenv("BQ_DATASET", "Confidence_agent")

ENTITY_TABLE = os.getenv("ENTITY_TABLE", "entity_matcher_output")
ML_TABLE = os.getenv("ML_TABLE", "ml_layer_output")
OUTPUT_TABLE = os.getenv("OUTPUT_TABLE", "confidence_agent_output")

client = bigquery.Client(project=PROJECT_ID)


def table_id(table_name: str) -> str:
    return f"{PROJECT_ID}.{BQ_DATASET}.{table_name}"


@app.get("/health")
def health():
    return {
        "status": "ok",
        "agent": "confidence-agent"
    }


@app.post("/run-confidence-agent")
def run_confidence_agent():
    entity_table = table_id(ENTITY_TABLE)
    ml_table = table_id(ML_TABLE)
    output_table = table_id(OUTPUT_TABLE)

    entity_count_sql = f"""
    SELECT COUNT(*) AS row_count
    FROM `{entity_table}`
    """

    ml_count_sql = f"""
    SELECT COUNT(*) AS row_count
    FROM `{ml_table}`
    """

    entity_rows = list(client.query(entity_count_sql).result())[0]["row_count"]
    ml_rows = list(client.query(ml_count_sql).result())[0]["row_count"]

    if entity_rows == 0 or ml_rows == 0:
        return {
            "status": "waiting",
            "agent": "confidence-agent",
            "entity_matcher_rows": entity_rows,
            "ml_layer_rows": ml_rows,
            "message": "Waiting for Entity Matcher and ML Layer outputs"
        }

    scoring_sql = f"""
    CREATE OR REPLACE TABLE `{output_table}` AS

    WITH joined AS (
      SELECT
        e.profile_id,
        e.profile_name,
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

      FROM `{entity_table}` e
      LEFT JOIN `{ml_table}` m
      ON e.profile_id = m.profile_id
      AND e.matched_field = m.field_name
    ),

    scored AS (
      SELECT
        *,

        CASE
          WHEN matched_source_domain LIKE '%campercontact%' THEN 0.95
          WHEN matched_source_domain LIKE '%google%' THEN 0.85
          WHEN matched_source_domain LIKE '%booking%' THEN 0.80
          WHEN matched_source_domain LIKE '%tripadvisor%' THEN 0.75
          WHEN matched_source_domain LIKE '%facebook%' THEN 0.60
          ELSE 0.50
        END AS source_reliability_score,

        COALESCE(posthint_score_est - prehint_score, 0) AS expected_profile_quality_uplift,

        LEAST(
          GREATEST(COALESCE(posthint_score_est - prehint_score, 0) / 100, 0),
          1
        ) AS normalized_uplift_score,

        CASE
          WHEN verification_status = 'matched' THEN 0
          WHEN verification_status = 'partial_match' THEN 0.15
          WHEN verification_status = 'conflict' THEN 0.40
          WHEN verification_status = 'mismatch' THEN 0.60
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
            + source_reliability_score * 0.25
            + normalized_uplift_score * 0.25
            - contradiction_penalty
          ),
          0
        ), 1) AS confidence_score

      FROM scored
    )

    SELECT
      profile_id,
      profile_name,
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
        WHEN confidence_score >= 0.85 THEN 'HIGH'
        WHEN confidence_score >= 0.60 THEN 'MEDIUM'
        ELSE 'LOW'
      END AS confidence_level,

      CASE
        WHEN confidence_score >= 0.85 THEN 'auto_generate_hint'
        WHEN confidence_score >= 0.60 THEN 'send_to_human_review'
        ELSE 'reject_recommendation'
      END AS confidence_decision,

      CONCAT(
        'Confidence uses entity match score, source reliability, expected uplift, and contradiction penalty.'
      ) AS confidence_reason,

      recommendation_type,
      ml_reason,

      CURRENT_TIMESTAMP() AS created_at

    FROM final
    """

    client.query(scoring_sql).result()

    output_count_sql = f"""
    SELECT COUNT(*) AS scored_rows
    FROM `{output_table}`
    """

    scored_rows = list(client.query(output_count_sql).result())[0]["scored_rows"]

    return {
        "status": "completed",
        "agent": "confidence-agent",
        "entity_matcher_rows": entity_rows,
        "ml_layer_rows": ml_rows,
        "scored_rows": scored_rows,
        "output_table": output_table
    }