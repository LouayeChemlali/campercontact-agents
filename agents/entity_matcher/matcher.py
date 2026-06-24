import json
from datetime import datetime, timezone
from urllib.parse import urlparse

from .config import ACTIVE_FIELDS
from .comparators import (
    compare_string_exact,
    compare_string_fuzzy,
    compare_numeric_exact,
    compare_numeric_geo,
    compare_numeric_directional,
    compare_boolean,
)

_COMPARATORS = {
    "string_exact": compare_string_exact,
    "string_fuzzy": compare_string_fuzzy,
    "numeric_exact": compare_numeric_exact,
    "numeric_geo": compare_numeric_geo,
    "numeric_directional": compare_numeric_directional,
    "boolean": compare_boolean,
}

_FIELD_KEYWORDS = {
    "email": ["email", "e-mail", "mail"],
    "website": ["website", "web site", "site", "url", "link"],
    "name": ["name", "title", "accommodation name", "profile name"],
    "city": ["city", "plaats", "town", "municipality"],
    "country": ["country", "land"],
    "address": ["address", "street", "straat", "house number", "huisnummer", "zipcode", "zip", "postcode"],
}


def _get_cc_value(field_cfg: dict, cc_profile: dict):
    if field_cfg["cc_field"] == "__composite_address__":
        parts = [str(cc_profile.get(p) or "").strip() for p in field_cfg["cc_parts"]]
        assembled = " ".join(p for p in parts if p)
        return assembled if assembled.strip() else None
    val = cc_profile.get(field_cfg["cc_field"])
    if (val is None or str(val).strip() == "") and "cc_fallback" in field_cfg:
        val = cc_profile.get(field_cfg["cc_fallback"])
    return val


def _extract_domain(url: str | None) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url if url.startswith("http") else "https://" + url)
        return parsed.netloc or url
    except Exception:
        return url


def _json_safe(value):
    try:
        json.dumps(value, default=str)
        return value
    except TypeError:
        return str(value)


def _actions_to_text(recommended_actions) -> str:
    if recommended_actions is None:
        return ""
    if isinstance(recommended_actions, str):
        return recommended_actions.lower()
    if isinstance(recommended_actions, (list, tuple, set)):
        return " ".join(_actions_to_text(item) for item in recommended_actions).lower()
    if isinstance(recommended_actions, dict):
        return " ".join(_actions_to_text(v) for v in recommended_actions.values()).lower()
    return str(recommended_actions).lower()


def _recommended_actions_as_json(gap_row: dict | None) -> str:
    if not gap_row:
        return ""
    actions = gap_row.get("recommended_actions")
    if actions is None:
        return ""
    try:
        return json.dumps(_json_safe(actions), ensure_ascii=False, default=str)
    except Exception:
        return str(actions)


def _fields_from_gap_row(gap_row: dict | None) -> set[str]:
    """Infer which active matcher fields are relevant from Gap Detector output.

    The exact recommended_actions structure can vary, so this uses a safe keyword
    mapping. If no field can be inferred, the matcher falls back to all active fields
    instead of accidentally producing zero comparisons.
    """
    if not gap_row:
        return {field_cfg["field"] for field_cfg in ACTIVE_FIELDS}

    text = _actions_to_text(gap_row.get("recommended_actions"))
    if not text:
        return {field_cfg["field"] for field_cfg in ACTIVE_FIELDS}

    selected = set()
    for field_name, keywords in _FIELD_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            selected.add(field_name)

    return selected or {field_cfg["field"] for field_cfg in ACTIVE_FIELDS}


def match_profile(
    cc_profile: dict,
    source_rows: list[dict],
    gap_row: dict | None = None,
    *,
    use_gap_filter: bool = True,
) -> list[dict]:
    """Compare one CC profile against all its source rows, for relevant active fields."""
    results = []
    run_ts = datetime.now(timezone.utc).isoformat()
    num_sources = len(source_rows)
    profile_id = str(cc_profile.get("sitecode") or cc_profile.get("profile_id", ""))
    profile_name = str(cc_profile.get("name") or cc_profile.get("title") or "")
    gap_actions_json = _recommended_actions_as_json(gap_row)

    allowed_fields = _fields_from_gap_row(gap_row) if use_gap_filter else {f["field"] for f in ACTIVE_FIELDS}

    for source_row in source_rows:
        source_url = str(source_row.get("source_url") or "")
        source_domain = str(source_row.get("source_domain") or "") or _extract_domain(source_url)
        source_title = str(source_row.get("page_title") or source_row.get("source_title") or "")
        source_snippet = str(source_row.get("snippet") or source_row.get("source_snippet") or "")
        source_finder_run_id = str(source_row.get("source_finder_run_id") or source_row.get("run_id") or "")

        for field_cfg in ACTIVE_FIELDS:
            field_name = field_cfg["field"]
            if field_name not in allowed_fields:
                continue

            cc_val = _get_cc_value(field_cfg, cc_profile)
            src_val = source_row.get(field_cfg["source_field"])

            compare_type = field_cfg["compare_type"]
            comparator = _COMPARATORS[compare_type]

            kwargs = {}
            if compare_type == "string_exact":
                kwargs["normalize"] = field_cfg.get("normalize")
            elif compare_type == "string_fuzzy":
                kwargs["threshold"] = field_cfg.get("threshold", 0.85)

            status, score = comparator(cc_val, src_val, **kwargs)
            current_value = str(cc_val) if cc_val is not None else ""
            external_value = str(src_val) if src_val is not None else ""

            results.append({
                "profile_id": profile_id,
                "profile_name": profile_name,

                # Canonical names expected by Hint Generator / downstream pipeline.
                "field_name": field_name,
                "current_value": current_value,
                "external_value": external_value,
                "source_url": source_url,
                "source_domain": source_domain,

                # Backwards-compatible names from the original matcher prototype.
                "matched_source_url": source_url,
                "matched_source_domain": source_domain,
                "matched_field": field_name,
                "current_campercontact_value": current_value,
                "external_source_value": external_value,

                "source_title": source_title,
                "source_snippet": source_snippet,
                "entity_match_score": round(score, 4),
                "verification_status": status,
                "comparison_type": compare_type,
                "num_sources": num_sources,
                "source_finder_run_id": source_finder_run_id,
                "gap_detector_run_id": str(source_row.get("gap_detector_run_id") or ""),
                "gap_recommended_actions": gap_actions_json,
                "run_timestamp": run_ts,
            })

    return results


def match_batch(
    profiles_gap_and_sources: list[tuple[dict, list[dict], dict | None]] | list[tuple[dict, list[dict]]],
    *,
    use_gap_filter: bool = True,
) -> list[dict]:
    """Run match_profile for profiles with their Source Finder and optional Gap rows."""
    all_results = []
    for item in profiles_gap_and_sources:
        if len(item) == 3:
            cc_profile, source_rows, gap_row = item
        else:
            cc_profile, source_rows = item
            gap_row = None
        all_results.extend(
            match_profile(
                cc_profile,
                source_rows,
                gap_row,
                use_gap_filter=use_gap_filter,
            )
        )
    return all_results
