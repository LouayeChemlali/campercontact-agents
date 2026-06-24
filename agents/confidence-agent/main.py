import datetime

# reads hint rows from BQ, scores each one, writes confidence results back

import os
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI
from pydantic import BaseModel, Field
from google.cloud import bigquery


app = FastAPI(title="Campercontact Confidence Agent")

PROJECT_ID = os.getenv("PROJECT_ID", "project-62cd3637-0b98-4aa5-8d5")

HINT_FIELD_TABLE = os.getenv(
    "HINT_FIELD_TABLE",
    f"{PROJECT_ID}.primary_dataset.hint_field_results",
)
HINT_SUMMARY_TABLE = os.getenv(
    "HINT_SUMMARY_TABLE",
    f"{PROJECT_ID}.primary_dataset.hint_profile_summaries",
)
OUTPUT_TABLE = os.getenv(
    "OUTPUT_TABLE",
    f"{PROJECT_ID}.primary_dataset.hint_confidence_results",
)

client = bigquery.Client(project=PROJECT_ID)


class RunConfidenceRequest(BaseModel):
    gap_detector_run_id: Optional[str] = None
    profile_ids: List[str] = Field(default_factory=list)
    limit: Optional[int] = 50
    write_bigquery: bool = True
    replace_existing_for_run: bool = True


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def domain_from_row(row: Dict[str, Any]) -> str:
    domain = clean_string(row.get("source_domain_internal")).lower()
    if domain:
        return domain.replace("www.", "")

    url = clean_string(row.get("source_url_internal"))
    if not url:
        return ""
    return urlparse(url).netloc.lower().replace("www.", "")


# known camping platforms score higher than generic or social media sites
def source_reliability_score(domain: str) -> float:
    domain = (domain or "").lower()

    if not domain:
        return 0.35

    strong_domains = [
        "park4night",
        "pincamp",
        "adac",
        "camping.info",
        "eurocampings",
        "acsi",
        "stellplatz.info",
        "camperstop",
        "caramaps",
        "jetcamp",
        "campingcarpark",
        "pitchup",
    ]
    medium_domains = [
        "campspace",
        "campingdirect",
        "campsites.co.uk",
        "alanrogers",
        "suncamp",
    ]
    weak_domains = [
        "facebook",
        "instagram",
        "tripadvisor",
        "google",
    ]

    if any(token in domain for token in strong_domains):
        return 0.85
    if any(token in domain for token in medium_domains):
        return 0.75
    if any(token in domain for token in weak_domains):
        return 0.55

    return 0.65


def contradiction_penalty(verification_status: str) -> float:
    status = (verification_status or "").strip().lower()

    if status in {"matched", "match", "exact_match", "verified"}:
        return 0.00
    if status in {"partial_match", "possible_match", "weak_match"}:
        return 0.12
    if status in {"conflict", "conflicting"}:
        return 0.35
    if status in {"mismatch", "not_matched", "no_match"}:
        return 0.50

    return 0.18


def normalized_uplift_score(row: Dict[str, Any]) -> float:
    score_delta = safe_float(row.get("score_delta"))

    if score_delta is None:
        pre = safe_float(row.get("prehint_score"))
        post = safe_float(row.get("posthint_score_est"))
        if pre is not None and post is not None:
            score_delta = post - pre

    if score_delta is None:
        return 0.30

    return clamp(score_delta / 25.0)


def hint_quality_score(row: Dict[str, Any]) -> float:
    hint_text = clean_string(row.get("hint_text"))
    suggested_action = clean_string(row.get("suggested_action"))
    suggested_value = clean_string(row.get("suggested_value"))

    score = 0.40
    if hint_text:
        score += 0.25
    if suggested_action:
        score += 0.20
    if suggested_value:
        score += 0.15

    return clamp(score)


# catches cases where we only found a source page, not an actual field value
def is_weak_external_website_hint(row: Dict[str, Any]) -> bool:
    suggested_value = clean_string(row.get("suggested_value"))
    source_url = clean_string(row.get("source_url_internal"))
    entity_score = safe_float(row.get("entity_match_score"), 0.0) or 0.0

    return bool(
        suggested_value
        and source_url
        and suggested_value == source_url
        and entity_score < 0.65
    )


def confidence_reason(
    entity_score: float,
    source_score: float,
    uplift_score: float,
    penalty: float,
    hint_quality: float,
    weak_external_website_hint: bool,
) -> str:
    if weak_external_website_hint:
        return (
            "Low confidence because the suggested value is only the source URL and "
            "the entity match score is below the safety threshold."
        )

    return (
        "Confidence combines entity match score, source reliability, estimated score uplift, "
        "hint completeness, and verification-status penalty. "
        f"Components: entity={entity_score:.2f}, source={source_score:.2f}, "
        f"uplift={uplift_score:.2f}, hint_quality={hint_quality:.2f}, penalty={penalty:.2f}."
    )


# entity match carries the most weight (0.55) since it's the strongest direct signal
def score_confidence(row: Dict[str, Any]) -> Dict[str, Any]:
    entity_score = clamp(safe_float(row.get("entity_match_score"), 0.50) or 0.50)
    domain = domain_from_row(row)
    source_score = source_reliability_score(domain)
    uplift_score = normalized_uplift_score(row)
    hint_quality = hint_quality_score(row)
    penalty = contradiction_penalty(clean_string(row.get("verification_status")))
    weak_external = is_weak_external_website_hint(row)

    raw_score = (
        entity_score * 0.55
        + source_score * 0.20
        + uplift_score * 0.10
        + hint_quality * 0.15
        - penalty
    )
    final_score = clamp(raw_score)

    if weak_external:
        final_score = min(final_score, 0.35)

    if final_score >= 0.80:
        level = "HIGH"
        decision = "show_hint"
    elif final_score >= 0.60:
        level = "MEDIUM"
        decision = "needs_moderator_review"
    else:
        level = "LOW"
        decision = "hide_or_manual_review"

    return {
        "source_reliability_score": round(source_score, 4),
        "normalized_uplift_score": round(uplift_score, 4),
        "contradiction_penalty": round(penalty, 4),
        "confidence_score": round(final_score, 4),
        "confidence_level": level,
        "confidence_decision": decision,
        "confidence_reason": confidence_reason(
            entity_score=entity_score,
            source_score=source_score,
            uplift_score=uplift_score,
            penalty=penalty,
            hint_quality=hint_quality,
            weak_external_website_hint=weak_external,
        ),
    }


def ensure_output_table() -> None:
    schema = [
        bigquery.SchemaField("confidence_id", "STRING"),
        bigquery.SchemaField("gap_detector_run_id", "STRING"),
        bigquery.SchemaField("hint_id", "STRING"),
        bigquery.SchemaField("profile_id", "STRING"),
        bigquery.SchemaField("profile_name", "STRING"),
        bigquery.SchemaField("field_name", "STRING"),
        bigquery.SchemaField("recommendation_type", "STRING"),
        bigquery.SchemaField("hint_text", "STRING"),
        bigquery.SchemaField("suggested_action", "STRING"),
        bigquery.SchemaField("current_value", "STRING"),
        bigquery.SchemaField("suggested_value", "STRING"),
        bigquery.SchemaField("prehint_score", "FLOAT"),
        bigquery.SchemaField("posthint_score_est", "FLOAT"),
        bigquery.SchemaField("score_delta", "FLOAT"),
        bigquery.SchemaField("ml_reason", "STRING"),
        bigquery.SchemaField("entity_match_score", "FLOAT"),
        bigquery.SchemaField("verification_status", "STRING"),
        bigquery.SchemaField("source_url_internal", "STRING"),
        bigquery.SchemaField("source_domain_internal", "STRING"),
        bigquery.SchemaField("source_reliability_score", "FLOAT"),
        bigquery.SchemaField("normalized_uplift_score", "FLOAT"),
        bigquery.SchemaField("contradiction_penalty", "FLOAT"),
        bigquery.SchemaField("confidence_score", "FLOAT"),
        bigquery.SchemaField("confidence_level", "STRING"),
        bigquery.SchemaField("confidence_decision", "STRING"),
        bigquery.SchemaField("confidence_reason", "STRING"),
        bigquery.SchemaField("created_at", "TIMESTAMP"),
    ]

    table = bigquery.Table(OUTPUT_TABLE, schema=schema)
    client.create_table(table, exists_ok=True)


def fetch_hint_rows(payload: RunConfidenceRequest) -> List[Dict[str, Any]]:
    where_parts = ["1=1"]
    query_params: List[bigquery.QueryParameter] = []

    if payload.gap_detector_run_id:
        where_parts.append("CAST(gap_detector_run_id AS STRING) = @gap_detector_run_id")
        query_params.append(
            bigquery.ScalarQueryParameter(
                "gap_detector_run_id",
                "STRING",
                payload.gap_detector_run_id,
            )
        )

    clean_profile_ids = [str(x) for x in payload.profile_ids if str(x).strip()]
    if clean_profile_ids:
        where_parts.append("CAST(profile_id AS STRING) IN UNNEST(@profile_ids)")
        query_params.append(
            bigquery.ArrayQueryParameter("profile_ids", "STRING", clean_profile_ids)
        )

    limit_clause = ""
    if payload.limit is not None:
        limit_clause = f"LIMIT {max(0, int(payload.limit))}"

    sql = f"""
    SELECT
      CAST(gap_detector_run_id AS STRING) AS gap_detector_run_id,
      CAST(hint_id AS STRING) AS hint_id,
      CAST(profile_id AS STRING) AS profile_id,
      CAST(profile_name AS STRING) AS profile_name,
      CAST(field_name AS STRING) AS field_name,
      CAST(recommendation_type AS STRING) AS recommendation_type,
      CAST(hint_text AS STRING) AS hint_text,
      CAST(suggested_action AS STRING) AS suggested_action,
      CAST(current_value AS STRING) AS current_value,
      CAST(suggested_value AS STRING) AS suggested_value,
      SAFE_CAST(prehint_score AS FLOAT64) AS prehint_score,
      SAFE_CAST(posthint_score_est AS FLOAT64) AS posthint_score_est,
      SAFE_CAST(score_delta AS FLOAT64) AS score_delta,
      CAST(ml_reason AS STRING) AS ml_reason,
      SAFE_CAST(entity_match_score AS FLOAT64) AS entity_match_score,
      CAST(verification_status AS STRING) AS verification_status,
      CAST(source_url_internal AS STRING) AS source_url_internal,
      CAST(source_domain_internal AS STRING) AS source_domain_internal
    FROM `{HINT_FIELD_TABLE}`
    WHERE {' AND '.join(where_parts)}
    ORDER BY created_at DESC
    {limit_clause}
    """

    job_config = bigquery.QueryJobConfig(query_parameters=query_params)
    return [dict(row) for row in client.query(sql, job_config=job_config).result()]


def delete_existing_rows(payload: RunConfidenceRequest) -> None:
    where_parts = []
    query_params: List[bigquery.QueryParameter] = []

    if payload.gap_detector_run_id:
        where_parts.append("gap_detector_run_id = @gap_detector_run_id")
        query_params.append(
            bigquery.ScalarQueryParameter(
                "gap_detector_run_id",
                "STRING",
                payload.gap_detector_run_id,
            )
        )

    clean_profile_ids = [str(x) for x in payload.profile_ids if str(x).strip()]
    if clean_profile_ids:
        where_parts.append("profile_id IN UNNEST(@profile_ids)")
        query_params.append(
            bigquery.ArrayQueryParameter("profile_ids", "STRING", clean_profile_ids)
        )

    if not where_parts:
        return

    sql = f"""
    DELETE FROM `{OUTPUT_TABLE}`
    WHERE {' AND '.join(where_parts)}
    """
    job_config = bigquery.QueryJobConfig(query_parameters=query_params)
    client.query(sql, job_config=job_config).result()


def build_output_rows(hint_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    created_at = utc_now_iso()
    output_rows = []

    for row in hint_rows:
        confidence = score_confidence(row)

        output_rows.append({
            "confidence_id": str(uuid.uuid4()),
            "gap_detector_run_id": row.get("gap_detector_run_id"),
            "hint_id": row.get("hint_id"),
            "profile_id": row.get("profile_id"),
            "profile_name": row.get("profile_name"),
            "field_name": row.get("field_name"),
            "recommendation_type": row.get("recommendation_type"),
            "hint_text": row.get("hint_text"),
            "suggested_action": row.get("suggested_action"),
            "current_value": row.get("current_value"),
            "suggested_value": row.get("suggested_value"),
            "prehint_score": row.get("prehint_score"),
            "posthint_score_est": row.get("posthint_score_est"),
            "score_delta": row.get("score_delta"),
            "ml_reason": row.get("ml_reason"),
            "entity_match_score": row.get("entity_match_score"),
            "verification_status": row.get("verification_status"),
            "source_url_internal": row.get("source_url_internal"),
            "source_domain_internal": row.get("source_domain_internal"),
            "source_reliability_score": confidence["source_reliability_score"],
            "normalized_uplift_score": confidence["normalized_uplift_score"],
            "contradiction_penalty": confidence["contradiction_penalty"],
            "confidence_score": confidence["confidence_score"],
            "confidence_level": confidence["confidence_level"],
            "confidence_decision": confidence["confidence_decision"],
            "confidence_reason": confidence["confidence_reason"],
            "created_at": created_at,
        })

    return output_rows


def insert_output_rows(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return

    errors = client.insert_rows_json(OUTPUT_TABLE, rows)
    if errors:
        raise RuntimeError(f"BigQuery insert errors for {OUTPUT_TABLE}: {errors}")


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "agent": "confidence-agent",
        "project_id": PROJECT_ID,
        "input_table": HINT_FIELD_TABLE,
        "summary_table": HINT_SUMMARY_TABLE,
        "output_table": OUTPUT_TABLE,
    }


@app.post("/run-confidence-agent")
def run_confidence_agent(payload: RunConfidenceRequest) -> Dict[str, Any]:
    ensure_output_table()

    hint_rows = fetch_hint_rows(payload)
    output_rows = build_output_rows(hint_rows)

    inserted = False
    if payload.write_bigquery and output_rows:
        if payload.replace_existing_for_run:
            delete_existing_rows(payload)
        insert_output_rows(output_rows)
        inserted = True

    return {
        "status": "completed",
        "agent": "confidence-agent",
        "gap_detector_run_id": payload.gap_detector_run_id,
        "profile_ids": payload.profile_ids,
        "input_table": HINT_FIELD_TABLE,
        "output_table": OUTPUT_TABLE,
        "rows_ready": len(output_rows),
        "inserted": inserted,
        "preview": output_rows[:5],
    }
