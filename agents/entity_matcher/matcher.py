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
    NO_DATA,
)

_COMPARATORS = {
    "string_exact": compare_string_exact,
    "string_fuzzy": compare_string_fuzzy,
    "numeric_exact": compare_numeric_exact,
    "numeric_geo": compare_numeric_geo,
    "numeric_directional": compare_numeric_directional,
    "boolean": compare_boolean,
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


def match_profile(cc_profile: dict, source_rows: list[dict]) -> list[dict]:
    """Compare one CC profile against all its source rows, for all active fields."""
    results = []
    run_ts = datetime.now(timezone.utc).isoformat()
    num_sources = len(source_rows)
    profile_id = str(cc_profile.get("sitecode") or cc_profile.get("profile_id", ""))
    profile_name = str(cc_profile.get("name") or cc_profile.get("title") or "")

    for source_row in source_rows:
        source_url = str(source_row.get("source_url") or "")
        source_domain = _extract_domain(source_url)
        source_title = str(source_row.get("source_title") or "")
        source_snippet = str(source_row.get("source_snippet") or "")
        source_finder_run_id = str(source_row.get("source_finder_run_id") or source_row.get("run_id") or "")

        for field_cfg in ACTIVE_FIELDS:
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

            results.append({
                "profile_id": profile_id,
                "profile_name": profile_name,
                "matched_source_url": source_url,
                "matched_source_domain": source_domain,
                "source_title": source_title,
                "source_snippet": source_snippet,
                "matched_field": field_cfg["field"],
                "current_campercontact_value": str(cc_val) if cc_val is not None else "",
                "external_source_value": str(src_val) if src_val is not None else "",
                "entity_match_score": round(score, 4),
                "verification_status": status,
                "comparison_type": compare_type,
                "num_sources": num_sources,
                "source_finder_run_id": source_finder_run_id,
                "gap_detector_run_id": str(source_row.get("gap_detector_run_id") or ""),
                "run_timestamp": run_ts,
            })

    return results


def match_batch(profiles_with_sources: list[tuple[dict, list[dict]]]) -> list[dict]:
    """Run match_profile for a list of (cc_profile, source_rows) tuples."""
    all_results = []
    for cc_profile, source_rows in profiles_with_sources:
        all_results.extend(match_profile(cc_profile, source_rows))
    return all_results
