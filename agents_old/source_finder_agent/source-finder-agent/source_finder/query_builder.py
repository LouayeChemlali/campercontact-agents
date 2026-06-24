from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import urlparse


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _quote(value: str) -> str:
    value = value.strip().replace('"', '')
    return f'"{value}"' if value else ""


def _domain_from_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return parsed.netloc.replace("www.", "")


def build_profile_queries(profile: Dict[str, Any]) -> List[str]:
    """Build profile-specific queries for one target profile.

    This replaces the old broad QUERY_LIST. Queries are intentionally specific so the
    Vertex AI Search allowlisted datastore returns candidate pages for the selected
    accommodation, not generic camping/camper pages.
    """
    name = _clean(profile.get("name"))
    address = _clean(profile.get("address_house_number"))
    zipcode = _clean(profile.get("zipcode"))
    city = _clean(profile.get("city"))
    country = _clean(profile.get("country"))
    website = _clean(profile.get("website"))
    sitecode = _clean(profile.get("sitecode"))

    queries: List[str] = []

    if name and city and country:
        queries.append(f"{_quote(name)} {_quote(city)} {_quote(country)}")
    if name and city:
        queries.append(f"{_quote(name)} {_quote(city)}")
    if name and address:
        queries.append(f"{_quote(name)} {_quote(address)}")
    if name and zipcode:
        queries.append(f"{_quote(name)} {_quote(zipcode)}")
    if name and country:
        queries.append(f"{_quote(name)} {_quote(country)}")
    if name:
        queries.append(f"{_quote(name)} camping")
        queries.append(f"{_quote(name)} camperplaats")
        queries.append(f"{_quote(name)} motorhome")
    if website:
        domain = _domain_from_url(website)
        if domain:
            queries.append(_quote(domain))
    if sitecode and name:
        queries.append(f"{_quote(name)} {_quote(sitecode)}")

    # Keep stable order and remove duplicates/empty strings.
    seen = set()
    unique_queries = []
    for query in queries:
        query = " ".join(query.split()).strip()
        if query and query not in seen:
            seen.add(query)
            unique_queries.append(query)

    return unique_queries
