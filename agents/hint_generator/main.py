"""Hint Generator — FastAPI Cloud Run service.

Exposes two endpoints:
  POST /run-hint-prioritization — join entity matcher output with ML/anomaly
    data and write scored candidates to prioritized_hint_candidates_v1.
  POST /run-hint-generator     — generate Gemini (or fallback) hints from
    prioritized candidates and write to hint_field_results /
    hint_profile_summaries.
"""

from __future__ import annotations

import datetime
import os
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from google.cloud import bigquery
from pydantic import BaseModel, Field

try:
    from google import genai
    from google.genai import types as genai_types
except Exception:
    genai = None
    genai_types = None

app = FastAPI()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

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
ML_LAYER_TABLE = f"{PROJECT_ID}.primary_dataset.v_ml_layer_hints"
GAP_OUTPUT_TABLE = f"{PROJECT_ID}.gap_detector_final.t10_gap_detector_output"

USE_GEMINI = os.getenv("USE_GEMINI", "true").lower() == "true"
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "global")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class PrioritizationRequest(BaseModel):
    gap_detector_run_id: Optional[str] = None


class HintGeneratorRequest(BaseModel):
    gap_detector_run_id: Optional[str] = None
    profile_id: Optional[str] = None
    limit: Optional[int] = Field(default=20, ge=1)
    refresh_prioritization: bool = True
    write_bigquery: bool = True


# ---------------------------------------------------------------------------
# Gemini helpers
# ---------------------------------------------------------------------------

SYSTEM_STYLE_RULES = """
You generate short profile-improvement hints for Campercontact moderators.

Rules:
- Use only the provided input.
- Do not invent facts, scores, facilities, sources, or profile details.
- Do not include raw source URLs in the hint text.
- Do not copy or quote long external snippets.
- Use cautious language such as "may", "could", "suggests", and "should be reviewed".
- Explain the score impact using the given prehint_score and posthint_score_est.
- Keep the hint useful, specific, and moderator-facing.
- Output plain text only. No markdown table. No bullet list unless asked.
""".strip()


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _score_delta(pre: Any, post: Any) -> Optional[float]:
    try:
        return float(post) - float(pre)
    except (TypeError, ValueError):
        return None


def _build_gemini_client():
    if genai is None:
        raise RuntimeError("google-genai is not installed.")
    return genai.Client(vertexai=True, project=PROJECT_ID, location=VERTEX_LOCATION)


def _call_gemini(prompt: str, max_tokens: int = 220) -> str:
    client = _build_gemini_client()
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=max_tokens,
        ),
    )
    text = getattr(response, "text", None)
    if not text:
        raise RuntimeError("Gemini returned an empty response.")
    return text.strip()


def _fallback_field_hint(row: dict) -> str:
    profile_name = _clean(row.get("profile_name")) or "this profile"
    field_name = _clean(row.get("field_name")) or "this field"
    current = _clean(row.get("current_value")) or "the current value"
    suggested = _clean(row.get("suggested_value")) or "the suggested improvement"
    pre = row.get("prehint_score")
    post = row.get("posthint_score_est")
    delta = row.get("score_delta")
    reason = _clean(row.get("ml_reason"))

    score_sentence = ""
    if pre is not None and post is not None:
        if delta is not None and delta > 0:
            score_sentence = (
                f" This could improve the estimated profile score from {pre:g} to {post:g} (+{delta:g})."
            )
        else:
            score_sentence = (
                f" The estimated profile score would remain around {post:g}, "
                "so this should be reviewed mainly for profile quality."
            )

    reason_sentence = f" Reason: {reason}" if reason else ""
    return (
        f"{profile_name} may need an update for '{field_name}'. "
        f"The current value is '{current}', while the suggested improvement is '{suggested}'."
        f"{score_sentence} A moderator should review this before updating the profile.{reason_sentence}"
    ).strip()


def _field_hint_prompt(row: dict) -> str:
    return (
        f"{SYSTEM_STYLE_RULES}\n\n"
        "Generate one short field-level hint.\n\n"
        f"Input:\n"
        f"profile_id: {row.get('profile_id')}\n"
        f"profile_name: {row.get('profile_name')}\n"
        f"field_name: {row.get('field_name')}\n"
        f"recommendation_type: {row.get('recommendation_type')}\n"
        f"current_value: {row.get('current_value')}\n"
        f"suggested_value: {row.get('suggested_value')}\n"
        f"prehint_score: {row.get('prehint_score')}\n"
        f"posthint_score_est: {row.get('posthint_score_est')}\n"
        f"score_delta: {row.get('score_delta')}\n"
        f"ml_reason: {row.get('ml_reason')}\n"
        f"entity_match_score: {row.get('entity_match_score')}\n"
        f"verification_status: {row.get('verification_status')}\n"
        f"source_domain_internal: {row.get('source_domain_internal')}\n\n"
        "Write 2 to 4 sentences.\nDo not display the raw source URL."
    )


def _generate_field_hint(row: dict) -> str:
    if not USE_GEMINI:
        return _fallback_field_hint(row)
    try:
        return _call_gemini(_field_hint_prompt(row), max_tokens=220)
    except Exception as exc:
        print(f"Gemini field-hint failed; using fallback. Error: {exc}")
        return _fallback_field_hint(row)


def _suggested_action(row: dict) -> str:
    field = _clean(row.get("field_name")) or "this field"
    rec = _clean(row.get("recommendation_type")) or "profile improvement"
    if rec in {"add_more_images", "missing_images", "improve_images"}:
        return "Review whether more relevant, high-quality images can be added to the profile."
    if rec in {"missing_facility", "add_facility"}:
        return f"Review the supporting evidence and add the '{field}' facility only if confirmed."
    if rec in {"improve_description", "missing_description"}:
        return "Review whether the profile description can be made more complete and useful for visitors."
    if rec in {"missing_contact_info", "update_contact_info"}:
        return f"Review the possible {field} update before adding it to the profile."
    return f"Review the recommended update for '{field}' before changing the profile."


def _build_field_hint_rows(candidates: List[dict], gap_detector_run_id: Optional[str]) -> List[dict]:
    rows = []
    created_at = _utc_now()
    for row in candidates:
        delta = _score_delta(row.get("prehint_score"), row.get("posthint_score_est"))
        row["score_delta"] = delta
        if not row.get("suggested_value") and not row.get("ml_reason"):
            continue
        rows.append({
            "hint_id": str(uuid.uuid4()),
            "profile_id": str(row.get("profile_id") or ""),
            "profile_name": str(row.get("profile_name") or ""),
            "field_name": str(row.get("field_name") or ""),
            "recommendation_type": str(row.get("recommendation_type") or ""),
            "current_value": str(row.get("current_value") or ""),
            "suggested_value": str(row.get("suggested_value") or ""),
            "prehint_score": row.get("prehint_score"),
            "posthint_score_est": row.get("posthint_score_est"),
            "score_delta": delta,
            "ml_reason": str(row.get("ml_reason") or ""),
            "hint_text": _generate_field_hint(row),
            "suggested_action": _suggested_action(row),
            "source_url_internal": str(row.get("source_url_internal") or ""),
            "source_domain_internal": str(row.get("source_domain_internal") or ""),
            "entity_match_score": row.get("entity_match_score"),
            "verification_status": str(row.get("verification_status") or ""),
            "created_at": created_at,
            "gap_detector_run_id": gap_detector_run_id or "",
        })
    return rows


def _fallback_profile_summary(profile_name: str, hint_rows: List[dict]) -> str:
    sorted_hints = sorted(hint_rows, key=lambda r: r.get("score_delta") or 0, reverse=True)
    top_fields = [r.get("field_name") for r in sorted_hints[:3] if r.get("field_name")]
    total_delta = sum(r.get("score_delta") or 0 for r in hint_rows)
    if top_fields:
        return (
            f"{profile_name} could mainly be improved by reviewing {', '.join(top_fields)}. "
            f"Together, the suggested changes have an estimated total score impact of +{total_delta:g}. "
            "A moderator should review the individual actions before updating the profile."
        )
    return (
        f"{profile_name} has several possible profile improvements. "
        "A moderator should review the generated field-level hints before updating the profile."
    )


def _profile_summary_prompt(profile_id: str, profile_name: str, hint_rows: List[dict]) -> str:
    top = sorted(hint_rows, key=lambda r: r.get("score_delta") or 0, reverse=True)[:5]
    lines = "\n".join(
        f"{i+1}. field_name={r.get('field_name')}; recommendation_type={r.get('recommendation_type')}; "
        f"score_delta={r.get('score_delta')}; prehint_score={r.get('prehint_score')}; "
        f"posthint_score_est={r.get('posthint_score_est')}; suggested_action={r.get('suggested_action')}"
        for i, r in enumerate(top)
    )
    return (
        f"{SYSTEM_STYLE_RULES}\n\n"
        "Generate one short profile-level summary for a moderator.\n\n"
        f"Input:\nprofile_id: {profile_id}\nprofile_name: {profile_name}\n"
        f"field_level_actions:\n{lines}\n\n"
        "Write 2 to 3 sentences.\n"
        "Summarize the main improvements and mention the estimated score impact if useful.\n"
        "Do not include source URLs.\nDo not overclaim."
    )


def _generate_profile_summary(profile_id: str, profile_name: str, hint_rows: List[dict]) -> str:
    if not USE_GEMINI:
        return _fallback_profile_summary(profile_name, hint_rows)
    try:
        return _call_gemini(_profile_summary_prompt(profile_id, profile_name, hint_rows), max_tokens=180)
    except Exception as exc:
        print(f"Gemini profile-summary failed; using fallback. Error: {exc}")
        return _fallback_profile_summary(profile_name, hint_rows)


def _build_profile_summary_rows(hint_rows: List[dict], gap_detector_run_id: Optional[str]) -> List[dict]:
    grouped: Dict[str, list] = defaultdict(list)
    for row in hint_rows:
        grouped[str(row.get("profile_id") or "")].append(row)

    summary_rows = []
    created_at = _utc_now()
    for profile_id, rows in grouped.items():
        profile_name = rows[0].get("profile_name") or "this profile"
        sorted_rows = sorted(rows, key=lambda r: r.get("score_delta") or 0, reverse=True)
        top_actions = [r.get("suggested_action") for r in sorted_rows[:5] if r.get("suggested_action")]
        total_delta = sum(r.get("score_delta") or 0 for r in rows)
        pre_scores = [r["prehint_score"] for r in rows if r.get("prehint_score") is not None]
        prehint = pre_scores[0] if pre_scores else None
        posthint = prehint + total_delta if prehint is not None else None
        summary_rows.append({
            "summary_id": str(uuid.uuid4()),
            "profile_id": profile_id,
            "profile_name": str(profile_name),
            "profile_summary_text": _generate_profile_summary(profile_id, str(profile_name), rows),
            "top_actions": top_actions,
            "total_estimated_score_delta": total_delta,
            "prehint_score": prehint,
            "posthint_score_est": posthint,
            "number_of_field_hints": len(rows),
            "created_at": created_at,
            "gap_detector_run_id": gap_detector_run_id or "",
        })
    return summary_rows


def _insert_rows(client: bigquery.Client, table: str, rows: List[dict]) -> int:
    if not rows:
        return 0
    errors = client.insert_rows_json(table, rows)
    if errors:
        raise RuntimeError(f"BigQuery streaming insert errors for {table}: {errors}")
    return len(rows)


# ---------------------------------------------------------------------------
# Prioritization logic
# ---------------------------------------------------------------------------

def _run_prioritization(client: bigquery.Client, gap_detector_run_id: Optional[str]) -> int:
    """Insert scored candidates into prioritized_hint_candidates_v1 for this run."""
    run_filter = ""
    params: list = []
    if gap_detector_run_id:
        run_filter = "AND CAST(e.gap_detector_run_id AS STRING) = @gap_detector_run_id"
        params.append(bigquery.ScalarQueryParameter("gap_detector_run_id", "STRING", gap_detector_run_id))

    sql = f"""
    INSERT INTO `{PRIORITIZED_CANDIDATES_TABLE}`
    WITH base AS (
      SELECT
        GENERATE_UUID() AS hint_candidate_id,
        CAST(e.gap_detector_run_id AS STRING) AS gap_detector_run_id,
        CAST(e.profile_id AS STRING)           AS sitecode,
        CAST(e.profile_id AS STRING)           AS profile_id,
        e.profile_name,
        e.matched_field                        AS field_name,
        e.current_campercontact_value          AS current_value,
        e.external_source_value                AS suggested_value,
        e.matched_source_url                   AS source_url_internal,
        e.matched_source_domain                AS source_domain_internal,
        SAFE_CAST(e.entity_match_score AS FLOAT64) AS entity_match_score,
        e.verification_status,
        COALESCE(CAST(ml.recommendation_type AS STRING), 'review') AS recommendation_type,
        SAFE_CAST(ml.prehint_score AS FLOAT64)     AS prehint_score,
        SAFE_CAST(ml.posthint_score_est AS FLOAT64) AS posthint_score_est,
        COALESCE(SAFE_CAST(ml.posthint_score_est AS FLOAT64), 0.0)
          - COALESCE(SAFE_CAST(ml.prehint_score AS FLOAT64), 0.0) AS score_delta,
        CAST(ml.ml_reason AS STRING)           AS ml_reason,
        COALESCE(a.anomaly_review_tier, 'NORMAL') AS anomaly_review_tier,
        CASE
          WHEN a.anomaly_review_tier = 'HIGH'                                THEN 0.20
          WHEN a.anomaly_review_tier IN ('MEDIUM_KMEANS','MEDIUM_AUTOENCODER') THEN 0.10
          ELSE 0.0
        END AS anomaly_priority_bonus,
        TO_JSON_STRING(g.recommended_actions) AS gap_recommended_actions,
        CURRENT_TIMESTAMP()                    AS created_at
      FROM `{ENTITY_MATCHER_INPUT_TABLE}` e
      LEFT JOIN `{ML_LAYER_TABLE}` ml
        ON CAST(e.profile_id AS STRING) = ml.profile_id
       AND e.matched_field               = ml.field_name
      LEFT JOIN `{ANOMALY_TABLE}` a
        ON CAST(e.profile_id AS STRING) = CAST(a.sitecode AS STRING)
      LEFT JOIN `{GAP_OUTPUT_TABLE}` g
        ON CAST(e.profile_id AS STRING) = CAST(g.sitecode AS STRING)
      WHERE e.verification_status != 'NO_DATA'
        AND e.matched_field IS NOT NULL
        {run_filter}
    ),
    scored AS (
      SELECT
        *,
        ROUND(LEAST(GREATEST(
          COALESCE(score_delta / 100.0, 0.0) * 0.70
          + COALESCE(entity_match_score, 0.0) * 0.30
          + anomaly_priority_bonus
        , 0.0), 1.0), 4) AS integrated_priority_score
      FROM base
    )
    SELECT
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
      ROUND(
        COALESCE(score_delta / 100.0, 0.0) * 0.70
        + COALESCE(entity_match_score, 0.0) * 0.30
      , 4) AS baseline_priority_score,
      anomaly_review_tier,
      anomaly_priority_bonus,
      integrated_priority_score,
      CASE
        WHEN integrated_priority_score >= 0.70 THEN 'high_priority'
        WHEN integrated_priority_score >= 0.40 THEN 'medium_priority'
        ELSE 'low_priority'
      END AS integrated_priority_label,
      CAST(RANK() OVER (ORDER BY integrated_priority_score DESC) AS INT64) AS integrated_queue_rank,
      gap_recommended_actions,
      created_at
    FROM scored
    """

    job = client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params))
    result = job.result()
    return result.num_dml_affected_rows or 0


def _fetch_prioritized_candidates(
    client: bigquery.Client,
    gap_detector_run_id: Optional[str],
    profile_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[dict]:
    where_parts = ["1=1"]
    params: list = []

    if gap_detector_run_id:
        where_parts.append("CAST(gap_detector_run_id AS STRING) = @gap_detector_run_id")
        params.append(bigquery.ScalarQueryParameter("gap_detector_run_id", "STRING", gap_detector_run_id))
    if profile_id:
        where_parts.append("CAST(profile_id AS STRING) = @profile_id")
        params.append(bigquery.ScalarQueryParameter("profile_id", "STRING", profile_id))

    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    query = f"""
        SELECT *
        FROM `{PRIORITIZED_CANDIDATES_TABLE}`
        WHERE {' AND '.join(where_parts)}
        ORDER BY integrated_priority_score DESC
        {limit_clause}
    """
    rows = client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "agent": "hint-generator-agent"}


@app.post("/run-hint-prioritization")
def run_prioritization_endpoint(req: PrioritizationRequest) -> Dict[str, Any]:
    client = bigquery.Client(project=PROJECT_ID)
    rows_written = _run_prioritization(client, req.gap_detector_run_id)
    return {
        "status": "completed",
        "agent": "hint-prioritization",
        "gap_detector_run_id": req.gap_detector_run_id,
        "rows_written": rows_written,
    }


@app.post("/run-hint-generator")
def run_hint_generator_endpoint(req: HintGeneratorRequest) -> Dict[str, Any]:
    client = bigquery.Client(project=PROJECT_ID)

    if req.refresh_prioritization and req.gap_detector_run_id:
        _run_prioritization(client, req.gap_detector_run_id)

    candidates = _fetch_prioritized_candidates(
        client, req.gap_detector_run_id, req.profile_id, req.limit
    )

    if not candidates:
        return {
            "status": "no_candidates",
            "agent": "hint-generator",
            "gap_detector_run_id": req.gap_detector_run_id,
            "candidates_found": 0,
            "hints_written": 0,
            "summaries_written": 0,
        }

    hint_rows = _build_field_hint_rows(candidates, req.gap_detector_run_id)
    summary_rows = _build_profile_summary_rows(hint_rows, req.gap_detector_run_id)

    hints_written = 0
    summaries_written = 0
    if req.write_bigquery:
        hints_written = _insert_rows(client, FIELD_HINTS_TABLE, hint_rows)
        summaries_written = _insert_rows(client, PROFILE_SUMMARIES_TABLE, summary_rows)

    return {
        "status": "completed",
        "agent": "hint-generator",
        "gap_detector_run_id": req.gap_detector_run_id,
        "candidates_found": len(candidates),
        "hints_written": hints_written,
        "summaries_written": summaries_written,
    }
