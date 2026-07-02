import argparse
import datetime
import os
import uuid
from collections import defaultdict

from google.cloud import bigquery

try:
    from google import genai
    from google.genai import types
except Exception:
    genai = None
    types = None


# generates moderator hints using gemini, falls back to a template if the model is unavailable

PROJECT_ID = "project-62cd3637-0b98-4aa5-8d5"
DATASET = "primary_dataset"

# Change these table names when Louaye and Joey give you the final tables/views.
ENTITY_MATCHER_TABLE = os.getenv("ENTITY_MATCHER_TABLE", "mock_entity_matcher_results")
ML_LAYER_TABLE = os.getenv("ML_LAYER_TABLE", "mock_ml_layer_results")

FIELD_HINTS_TABLE = "hint_field_results"
PROFILE_SUMMARIES_TABLE = "hint_profile_summaries"

# Gemini / Vertex AI settings.
# Keep USE_GEMINI=true for the real LLM version.
# Set USE_GEMINI=false if you want to test the BigQuery flow without model calls.
USE_GEMINI = os.getenv("USE_GEMINI", "true").lower() == "true"
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "global")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


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


def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def score_delta(prehint_score, posthint_score_est):
    if prehint_score is None or posthint_score_est is None:
        return None
    return float(posthint_score_est) - float(prehint_score)


def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


# values that look like template artifacts, don't pass these to gemini as real data
_PLACEHOLDER_VALUES = {
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


def is_placeholder_value(value):
    """Return True when a value is only a template placeholder, not real evidence."""
    text = clean_text(value)
    if not text:
        return True
    return text.lower().strip(" '\".") in _PLACEHOLDER_VALUES


def moderator_value_text(value, field_name, *, role):
    """Format current/suggested values so templates never show fake placeholders."""
    field_label = clean_text(field_name) or "this field"
    if is_placeholder_value(value):
        if role == "current":
            return f"the current {field_label} value appears to be missing or unavailable"
        return f"a possible {field_label} improvement was detected, but the exact value should be checked"
    return clean_text(value)


def normalize_input_row(row):
    """Remove known placeholder values before prompting or writing hint text."""
    normalized = dict(row)
    if is_placeholder_value(normalized.get("current_value")):
        normalized["current_value"] = ""
    if is_placeholder_value(normalized.get("suggested_value")):
        normalized["suggested_value"] = ""
    return normalized


def build_gemini_client():
    """Build and return a Vertex AI Gemini client."""
    if genai is None:
        raise RuntimeError("google-genai is not installed. Run: pip install -r requirements.txt")

    return genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=VERTEX_LOCATION,
    )


# low temperature keeps the output factual and consistent across runs
def call_gemini(prompt, max_output_tokens=220):
    """Call the Gemini model with the given prompt and return the text response."""
    client = build_gemini_client()

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=max_output_tokens,
        ),
    )

    text = getattr(response, "text", None)
    if not text:
        raise RuntimeError("Gemini returned an empty response.")

    return text.strip()


def fallback_field_hint(row):
    """Deterministic backup if Gemini is disabled or unavailable."""
    profile_name = clean_text(row.get("profile_name")) or "this profile"
    field_name = clean_text(row.get("field_name")) or "this field"
    current_value = moderator_value_text(row.get("current_value"), field_name, role="current")
    suggested_value = moderator_value_text(row.get("suggested_value"), field_name, role="suggested")
    if is_placeholder_value(row.get("current_value")):
        current_clause = current_value[:1].upper() + current_value[1:]
    else:
        current_clause = f"The current value is '{current_value}'"
    pre = row.get("prehint_score")
    post = row.get("posthint_score_est")
    delta = row.get("score_delta")
    ml_reason = clean_text(row.get("ml_reason"))

    score_sentence = ""
    if pre is not None and post is not None:
        if delta is not None and delta > 0:
            score_sentence = f" This could improve the estimated profile score from {pre:g} to {post:g} (+{delta:g})."
        else:
            score_sentence = f" The estimated profile score would remain around {post:g}, so this should be reviewed mainly for profile quality."

    reason_sentence = f" Reason: {ml_reason}" if ml_reason else ""

    return (
        f"{profile_name} may need an update for '{field_name}'. {current_clause}, "
        f"while the suggested improvement is '{suggested_value}'.{score_sentence} "
        f"A moderator should review this before updating the profile.{reason_sentence}"
    ).strip()


def build_field_hint_prompt(row):
    field_name = clean_text(row.get("field_name")) or "this field"
    current_value_text = moderator_value_text(row.get("current_value"), field_name, role="current")
    suggested_value_text = moderator_value_text(row.get("suggested_value"), field_name, role="suggested")

    return f"""
{SYSTEM_STYLE_RULES}

Generate one short field-level hint.

Input:
profile_id: {row.get('profile_id')}
profile_name: {row.get('profile_name')}
field_name: {row.get('field_name')}
recommendation_type: {row.get('recommendation_type')}
current_value: {current_value_text}
suggested_value: {suggested_value_text}
prehint_score: {row.get('prehint_score')}
posthint_score_est: {row.get('posthint_score_est')}
score_delta: {row.get('score_delta')}
ml_reason: {row.get('ml_reason')}
entity_match_score: {row.get('entity_match_score')}
verification_status: {row.get('verification_status')}
source_domain_internal: {row.get('source_domain_internal')}

Write 2 to 4 sentences.
If the current value is missing/unavailable, say that directly. Never write "the current value" as if it were real data.
Do not display the raw source URL.
""".strip()


def generate_field_hint(row):
    """Generate a field-level hint using Gemini, falling back to a template if unavailable."""
    if not USE_GEMINI:
        return fallback_field_hint(row)

    prompt = build_field_hint_prompt(row)

    try:
        return call_gemini(prompt, max_output_tokens=220)
    except Exception as exc:
        print(f"Gemini field-hint call failed; using fallback. Error: {exc}")
        return fallback_field_hint(row)


def build_suggested_action(row):
    field_name = clean_text(row.get("field_name")) or "this field"
    recommendation_type = clean_text(row.get("recommendation_type")) or "profile improvement"

    if recommendation_type in {"add_more_images", "missing_images", "improve_images"}:
        return "Review whether more relevant, high-quality images can be added to the profile."

    if recommendation_type in {"missing_facility", "add_facility"}:
        return f"Review the supporting evidence and add the '{field_name}' facility only if confirmed."

    if recommendation_type in {"improve_description", "missing_description"}:
        return "Review whether the profile description can be made more complete and useful for visitors."

    if recommendation_type in {"missing_contact_info", "update_contact_info"}:
        return f"Review the possible {field_name} update before adding it to the profile."

    return f"Review the recommended update for '{field_name}' before changing the profile."


def fetch_joined_inputs(client, profile_id=None, limit=None):
    """
    Joins Entity Matcher output with ML Layer output.

    Required Entity Matcher columns expected by this prototype:
    - profile_id
    - profile_name
    - field_name
    - current_value
    - external_value
    - source_url
    - source_domain
    - entity_match_score
    - verification_status

    Required ML Layer columns expected by this prototype:
    - profile_id
    - field_name
    - recommendation_type
    - prehint_score
    - posthint_score_est
    - ml_reason
    """

    where_clause = "WHERE 1=1"
    query_params = []

    if profile_id:
        where_clause += " AND em.profile_id = @profile_id"
        query_params.append(bigquery.ScalarQueryParameter("profile_id", "STRING", profile_id))

    limit_clause = f"LIMIT {int(limit)}" if limit else ""

    query = f"""
        SELECT
            em.profile_id,
            em.profile_name,
            em.field_name,
            CAST(em.current_value AS STRING) AS current_value,
            CAST(em.external_value AS STRING) AS suggested_value,
            CAST(em.source_url AS STRING) AS source_url_internal,
            CAST(em.source_domain AS STRING) AS source_domain_internal,
            SAFE_CAST(em.entity_match_score AS FLOAT64) AS entity_match_score,
            CAST(em.verification_status AS STRING) AS verification_status,

            ml.recommendation_type,
            SAFE_CAST(ml.prehint_score AS FLOAT64) AS prehint_score,
            SAFE_CAST(ml.posthint_score_est AS FLOAT64) AS posthint_score_est,
            CAST(ml.ml_reason AS STRING) AS ml_reason
        FROM `{PROJECT_ID}.{DATASET}.{ENTITY_MATCHER_TABLE}` em
        INNER JOIN `{PROJECT_ID}.{DATASET}.{ML_LAYER_TABLE}` ml
            ON em.profile_id = ml.profile_id
           AND em.field_name = ml.field_name
        {where_clause}
        {limit_clause}
    """

    job_config = bigquery.QueryJobConfig(query_parameters=query_params)
    return [dict(row) for row in client.query(query, job_config=job_config).result()]


def build_field_hint_rows(joined_rows):
    """Generate field-level hints for all input rows and return them as a list."""
    output_rows = []
    created_at = utc_now_iso()

    for raw_row in joined_rows:
        row = normalize_input_row(raw_row)

        # Keep source-only website candidates instead of dropping them.
        # These are usually LOW-confidence candidates, but the Confidence Agent
        # still needs a row so it can explicitly classify them as hide/manual-review.
        field_name = clean_text(row.get("field_name")).lower()
        source_url = clean_text(row.get("source_url_internal") or row.get("source_url"))
        source_domain = clean_text(row.get("source_domain_internal") or row.get("source_domain"))

        if not row.get("suggested_value") and field_name == "website" and source_url:
            row["suggested_value"] = source_url

        row["score_delta"] = score_delta(row.get("prehint_score"), row.get("posthint_score_est"))

        has_useful_value = bool(clean_text(row.get("suggested_value")))
        has_reason = bool(clean_text(row.get("ml_reason")))
        has_source_evidence = bool(source_url or source_domain)

        # Do not drop candidate rows at this stage.
        # Even weak or source-only rows should be written so the Confidence Agent
        # can classify them as LOW / hide_or_manual_review.
        if not has_useful_value:
            if source_url:
                row["suggested_value"] = source_url
            elif source_domain:
                row["suggested_value"] = f"Candidate source from {source_domain}"
            elif has_reason:
                row["suggested_value"] = "Candidate value requires moderator review"
            else:
                row["suggested_value"] = "Candidate value unavailable"

        hint_text = generate_field_hint(row)
        suggested_action = build_suggested_action(row)

        output_rows.append({
            "hint_id": str(uuid.uuid4()),
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


def fallback_profile_summary(profile_name, field_hint_rows):
    sorted_hints = sorted(
        field_hint_rows,
        key=lambda x: x.get("score_delta") or 0,
        reverse=True,
    )
    top_fields = [row.get("field_name") for row in sorted_hints[:3] if row.get("field_name")]

    total_delta = sum(row.get("score_delta") or 0 for row in field_hint_rows)

    if top_fields:
        fields_text = ", ".join(top_fields)
        return (
            f"{profile_name} could mainly be improved by reviewing {fields_text}. "
            f"Together, the suggested changes have an estimated total score impact of +{total_delta:g}. "
            f"A moderator should review the individual actions before updating the profile."
        )

    return (
        f"{profile_name} has several possible profile improvements. "
        "A moderator should review the generated field-level hints before updating the profile."
    )


def build_profile_summary_prompt(profile_id, profile_name, field_hint_rows):
    top_rows = sorted(
        field_hint_rows,
        key=lambda x: x.get("score_delta") or 0,
        reverse=True,
    )[:5]

    action_lines = []
    for index, row in enumerate(top_rows, start=1):
        action_lines.append(
            f"{index}. field_name={row.get('field_name')}; "
            f"recommendation_type={row.get('recommendation_type')}; "
            f"score_delta={row.get('score_delta')}; "
            f"prehint_score={row.get('prehint_score')}; "
            f"posthint_score_est={row.get('posthint_score_est')}; "
            f"suggested_action={row.get('suggested_action')}"
        )

    actions_text = "\n".join(action_lines)

    return f"""
{SYSTEM_STYLE_RULES}

Generate one short profile-level summary for a moderator.

Input:
profile_id: {profile_id}
profile_name: {profile_name}
field_level_actions:
{actions_text}

Write 2 to 3 sentences.
Summarize the main improvements and mention the estimated score impact if useful.
Do not include source URLs.
Do not overclaim.
""".strip()


def generate_profile_summary(profile_id, profile_name, field_hint_rows):
    """Generate a profile-level summary using Gemini, falling back to a template if unavailable."""
    if not USE_GEMINI:
        return fallback_profile_summary(profile_name, field_hint_rows)

    prompt = build_profile_summary_prompt(profile_id, profile_name, field_hint_rows)

    try:
        return call_gemini(prompt, max_output_tokens=180)
    except Exception as exc:
        print(f"Gemini profile-summary call failed; using fallback. Error: {exc}")
        return fallback_profile_summary(profile_name, field_hint_rows)


def build_profile_summary_rows(field_hint_rows):
    """Generate one profile-level summary per profile from the field hints."""
    grouped = defaultdict(list)
    for row in field_hint_rows:
        grouped[row.get("profile_id")].append(row)

    summary_rows = []
    created_at = utc_now_iso()

    for profile_id, rows in grouped.items():
        profile_name = rows[0].get("profile_name") or "this profile"

        sorted_rows = sorted(rows, key=lambda x: x.get("score_delta") or 0, reverse=True)
        top_actions = [row.get("suggested_action") for row in sorted_rows[:5] if row.get("suggested_action")]

        total_delta = sum(row.get("score_delta") or 0 for row in rows)
        pre_scores = [row.get("prehint_score") for row in rows if row.get("prehint_score") is not None]
        prehint_score = pre_scores[0] if pre_scores else None
        posthint_score_est = prehint_score + total_delta if prehint_score is not None else None

        summary_text = generate_profile_summary(profile_id, profile_name, rows)

        summary_rows.append({
            "summary_id": str(uuid.uuid4()),
            "profile_id": profile_id,
            "profile_name": profile_name,
            "profile_summary_text": summary_text,
            "top_actions": top_actions,
            "total_estimated_score_delta": total_delta,
            "prehint_score": prehint_score,
            "posthint_score_est": posthint_score_est,
            "number_of_field_hints": len(rows),
            "created_at": created_at,
        })

    return summary_rows


def insert_rows(client, table_name, rows):
    """Insert rows into a BigQuery table by table name."""
    if not rows:
        print(f"No rows to insert into {table_name}.")
        return

    table_id = f"{PROJECT_ID}.{DATASET}.{table_name}"
    errors = client.insert_rows_json(table_id, rows)

    if errors:
        raise RuntimeError(f"BigQuery insert errors for {table_id}: {errors}")

    print(f"Inserted {len(rows)} rows into {table_id}")


def print_preview(field_hint_rows, summary_rows):
    print("\n--- FIELD HINT PREVIEW ---")
    for row in field_hint_rows[:10]:
        print(f"\nProfile: {row.get('profile_name')} ({row.get('profile_id')})")
        print(f"Field: {row.get('field_name')}")
        print(f"Score: {row.get('prehint_score')} -> {row.get('posthint_score_est')} ({row.get('score_delta'):+g})")
        print(f"Hint: {row.get('hint_text')}")
        print(f"Action: {row.get('suggested_action')}")
        if row.get("source_domain_internal"):
            print(f"Internal source domain: {row.get('source_domain_internal')}")

    print("\n--- PROFILE SUMMARY PREVIEW ---")
    for row in summary_rows[:10]:
        print(f"\nProfile: {row.get('profile_name')} ({row.get('profile_id')})")
        print(f"Total estimated score impact: +{row.get('total_estimated_score_delta'):g}")
        print(f"Summary: {row.get('profile_summary_text')}")


def main():
    """CLI entry point for generating hints from BigQuery inputs."""
    parser = argparse.ArgumentParser(description="Campercontact Hint Generator prototype")
    parser.add_argument("--profile_id", help="Optional: run for one profile_id only")
    parser.add_argument("--limit", type=int, help="Optional: limit number of joined input rows")
    parser.add_argument("--dry_run", action="store_true", help="Generate and preview hints without inserting into BigQuery")
    args = parser.parse_args()

    client = bigquery.Client(project=PROJECT_ID)

    joined_rows = fetch_joined_inputs(
        client=client,
        profile_id=args.profile_id,
        limit=args.limit,
    )
    print(f"Fetched {len(joined_rows)} joined Entity Matcher + ML Layer rows.")

    field_hint_rows = build_field_hint_rows(joined_rows)
    print(f"Generated {len(field_hint_rows)} field-level hints.")

    summary_rows = build_profile_summary_rows(field_hint_rows)
    print(f"Generated {len(summary_rows)} profile-level summaries.")

    print_preview(field_hint_rows, summary_rows)

    if args.dry_run:
        print("\nDry run only. No rows inserted into BigQuery.")
        return

    insert_rows(client, FIELD_HINTS_TABLE, field_hint_rows)
    insert_rows(client, PROFILE_SUMMARIES_TABLE, summary_rows)


if __name__ == "__main__":
    main()
