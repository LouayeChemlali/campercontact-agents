import re
from rapidfuzz import fuzz

# comparison functions used by the entity matcher, each returns (status, score)

# verification status constants
MATCH = "MATCH"
CC_LOWER_RATE = "CC_LOWER_RATE"
CC_HIGHER_RATE = "CC_HIGHER_RATE"
MISMATCH_INFO = "MISMATCH_INFO"
NEW_INFO = "NEW_INFO"
NO_DATA = "NO_DATA"


def _is_empty(val) -> bool:
    return val is None or str(val).strip() == ""


def _empty_check(cc_val, src_val):
    """Returns (status, score) if either side is empty, else None to continue."""
    cc_empty = _is_empty(cc_val)
    src_empty = _is_empty(src_val)
    if cc_empty and src_empty:
        return (NO_DATA, 0.0)
    if cc_empty and not src_empty:
        return (NEW_INFO, 0.0)
    if not cc_empty and src_empty:
        return (NO_DATA, 0.0)
    return None


def _normalize_email(val: str) -> str:
    return val.strip().lower()


def _normalize_url(val: str) -> str:
    url = val.strip().lower()
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^www\.", "", url)
    url = url.rstrip("/")
    return url


def _apply_normalize(val: str, normalize: str | None) -> str:
    if normalize == "email":
        return _normalize_email(val)
    if normalize == "url":
        return _normalize_url(val)
    return val.strip().lower()


def compare_string_exact(cc_val, src_val, normalize: str | None = None) -> tuple[str, float]:
    """Exact string comparison after optional normalisation (email, URL, or plain lowercase)."""
    check = _empty_check(cc_val, src_val)
    if check is not None:
        return check
    cc_norm = _apply_normalize(str(cc_val), normalize)
    src_norm = _apply_normalize(str(src_val), normalize)
    if cc_norm == src_norm:
        return (MATCH, 1.0)
    return (MISMATCH_INFO, 0.0)


# fuzzy threshold defaults to 0.85 for name/city/country, lower for address (0.75)
def compare_string_fuzzy(cc_val, src_val, threshold: float = 0.85) -> tuple[str, float]:
    """Fuzzy string comparison using token ratio, returns MATCH when similarity meets the threshold."""
    check = _empty_check(cc_val, src_val)
    if check is not None:
        return check
    cc_norm = str(cc_val).strip().lower()
    src_norm = str(src_val).strip().lower()
    score = fuzz.ratio(cc_norm, src_norm) / 100.0
    if score >= threshold:
        return (MATCH, score)
    return (MISMATCH_INFO, score)


def compare_numeric_exact(cc_val, src_val) -> tuple[str, float]:
    """Exact numeric equality."""
    check = _empty_check(cc_val, src_val)
    if check is not None:
        return check
    try:
        if float(cc_val) == float(src_val):
            return (MATCH, 1.0)
        return (MISMATCH_INFO, 0.0)
    except (ValueError, TypeError):
        return (MISMATCH_INFO, 0.0)


def compare_numeric_geo(cc_val, src_val, tolerance: float = 0.001) -> tuple[str, float]:
    """For later use with lat/lon: match within a small tolerance."""
    check = _empty_check(cc_val, src_val)
    if check is not None:
        return check
    try:
        diff = abs(float(cc_val) - float(src_val))
        if diff <= tolerance:
            return (MATCH, 1.0)
        return (MISMATCH_INFO, 0.0)
    except (ValueError, TypeError):
        return (MISMATCH_INFO, 0.0)


def compare_numeric_directional(cc_val, src_val) -> tuple[str, float]:
    """For later use with rates: distinguishes higher/lower."""
    check = _empty_check(cc_val, src_val)
    if check is not None:
        return check
    try:
        cc_f, src_f = float(cc_val), float(src_val)
        if cc_f == src_f:
            return (MATCH, 1.0)
        if cc_f < src_f:
            return (CC_LOWER_RATE, 0.0)
        return (CC_HIGHER_RATE, 0.0)
    except (ValueError, TypeError):
        return (MISMATCH_INFO, 0.0)


def compare_boolean(cc_val, src_val) -> tuple[str, float]:
    """Boolean comparison, treating 'true', '1', and 'yes' as equivalent."""
    check = _empty_check(cc_val, src_val)
    if check is not None:
        return check
    cc_b = str(cc_val).strip().lower() in ("true", "1", "yes")
    src_b = str(src_val).strip().lower() in ("true", "1", "yes")
    if cc_b == src_b:
        return (MATCH, 1.0)
    return (MISMATCH_INFO, 0.0)
