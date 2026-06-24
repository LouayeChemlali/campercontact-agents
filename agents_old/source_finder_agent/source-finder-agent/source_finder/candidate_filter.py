from __future__ import annotations

import re
from typing import Any, Dict, List
from urllib.parse import urlparse


STOPWORDS = {
    "camping", "camper", "camperplaats", "motorhome", "site", "area",
    "the", "de", "het", "een", "la", "le", "les", "il", "lo", "di",
    "of", "and", "en", "van", "der", "den", "www", "com", "nl",
    "agriturismo", "campingplatz", "campings", "campeggi", "beste",
    "top", "alle", "best", "migliori", "tutti", "park"
}


BAD_URL_PATTERNS = [
    "/search?",
    "/search/",
    "?lat=",
    "&lng=",
    "/archives/",
    "reviews.asp",
    "/reviews/",
    "/theme/",
    "/rv-campsite/",
    "/wi-fi-available",
    "/wifi",
    "/category/",
    "/categories/",
]


LISTING_TITLE_PATTERNS = [
    r"\bbeste\s+\d+\s+campings\b",
    r"\bde beste\s+\d+\s+campings\b",
    r"\balle\s+\d+\s+campings\b",
    r"\btop\s+\d+\s+campings\b",
    r"\btutti\s+i\s+\d+\s+campeggi\b",
    r"\bi migliori\s+\d+\s+campeggi\b",
    r"\bi top\s+\d+\s+campeggi\b",
    r"\b\d+\s+campings?\s+in\b",
    r"\b\d+\s+campeggi\s+in\b",
    r"\bfind the perfect\b",
]


def _clean(value: Any) -> str:
    return str(value or "").strip().lower()


def _slug(value: Any) -> str:
    text = _clean(value)
    text = re.sub(r"[^a-z0-9À-ÿ]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def _tokens(value: Any) -> List[str]:
    text = re.sub(r"[^a-z0-9À-ÿ]+", " ", _clean(value))
    return [t for t in text.split() if len(t) >= 3 and t not in STOPWORDS]


def _is_bad_url(url: str) -> bool:
    url = _clean(url)
    return any(pattern in url for pattern in BAD_URL_PATTERNS)


def _is_listing_title(title: str) -> bool:
    title = _clean(title)
    return any(re.search(pattern, title) for pattern in LISTING_TITLE_PATTERNS)


def _canonical_key(candidate: Dict[str, Any]) -> str:
    url = _clean(candidate.get("source_url"))
    domain = _clean(candidate.get("source_domain"))

    # Collapse multilingual Park4Night pages for same place ID.
    match = re.search(r"park4night\.com/(?:[a-z]{2}/)?place/(\d+)", url)
    if match:
        return f"park4night_place_{match.group(1)}"

    # Collapse multilingual Camperstop pages with same numeric ID.
    match = re.search(r"camperstop\.com/.*/(\d+)/?$", url)
    if match:
        return f"camperstop_{match.group(1)}"

    # Collapse Stellplatz multilingual versions by final slug.
    if "stellplatz.info" in domain:
        slug = url.rstrip("/").split("/")[-1]
        return f"stellplatz_{slug}"

    return f"{domain}_{url}"


def candidate_relevance_score(candidate: Dict[str, Any], profile: Dict[str, Any]) -> float:
    url = _clean(candidate.get("source_url"))
    title = _clean(candidate.get("page_title"))
    snippet = _clean(candidate.get("snippet"))

    all_text = f"{url} {title} {snippet}"
    title_url = f"{url} {title}"

    profile_name = _clean(profile.get("name"))
    profile_slug = _slug(profile.get("name"))
    city = _clean(profile.get("city"))
    city_slug = _slug(city)
    zipcode = _clean(profile.get("zipcode"))

    score = 0.0

    if _is_bad_url(url):
        return -99.0

    if _is_listing_title(title):
        score -= 8.0

    # Strong exact evidence.
    if profile_name and profile_name in title:
        score += 12.0

    if profile_slug and profile_slug in _slug(url):
        score += 12.0

    # City/slug is important for chain-style names, e.g. Silves vs Tavira vs Falesia.
    if city and city in title_url:
        score += 4.0

    if city_slug and city_slug in _slug(url):
        score += 4.0

    if zipcode and zipcode in all_text:
        score += 4.0

    # Token matching, but title/URL matters much more than snippet.
    name_tokens = _tokens(profile.get("name"))
    title_url_matches = sum(1 for t in name_tokens if t in title_url)
    snippet_matches = sum(1 for t in name_tokens if t in snippet)

    score += title_url_matches * 2.5
    score += snippet_matches * 0.4

    # Address tokens help confirm exactness.
    address_tokens = _tokens(profile.get("address_house_number"))
    address_matches = sum(1 for t in address_tokens if t in all_text)
    score += min(address_matches, 4) * 2.0

    # Penalise sibling locations from the same chain when city is different.
    sibling_terms = ["tavira", "falesia", "falésia", "albufeira", "olhos-de-agua", "olhos de água"]
    if city and city not in all_text:
        for term in sibling_terms:
            if term in all_text:
                score -= 10.0

    # Park4Night place pages are only useful if the exact profile name appears
    # in the page title or the profile slug appears in the URL.
    # Do not trust snippet-only matches, because search snippets can mention nearby results.
    if "park4night.com" in url and "/place/" in url:
        name_in_title = bool(profile_name and profile_name in title)
        slug_in_url = bool(profile_slug and profile_slug in _slug(url))

        if not (name_in_title or slug_in_url):
            return -99.0

    return score


def filter_candidates_for_profile(
    candidates: List[Dict[str, Any]],
    profile: Dict[str, Any],
    min_score: float = 10.0,
) -> List[Dict[str, Any]]:
    kept = []
    removed = []
    seen_keys = set()

    for candidate in candidates:
        url = candidate.get("source_url", "")
        score = candidate_relevance_score(candidate, profile)
        candidate["profile_relevance_score"] = score

        if score < min_score:
            removed.append((score, url))
            continue

        key = _canonical_key(candidate)
        if key in seen_keys:
            removed.append((-50.0, url))
            continue

        seen_keys.add(key)
        kept.append(candidate)

    kept = sorted(
        kept,
        key=lambda row: row.get("profile_relevance_score", 0),
        reverse=True,
    )

    if removed:
        print("Removed low-relevance candidate URLs:")
        for score, url in removed[:80]:
            print(f"  - score={score:.2f} {url}")

    return kept
