from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .config import FETCH_TIMEOUT_SECONDS, USER_AGENT
from .robots import can_fetch

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:(?:\+|00)\d{1,3}[\s().-]?)?(?:\d[\s().-]?){7,}\d")
POSTCODE_RE = re.compile(r"\b\d{4}\s?[A-Z]{2}\b|\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b", re.I)
PRICE_RE = re.compile(r"(?:€|EUR|euro)\s?\d+[\d.,]*|\d+[\d.,]*\s?(?:€|EUR|euro)", re.I)

FACILITY_KEYWORDS = [
    "electricity", "water", "drinking water", "waste water", "grey water", "chemical toilet",
    "toilet", "shower", "wifi", "wi-fi", "restaurant", "shop", "laundry", "washing machine",
    "dogs allowed", "pets allowed", "playground", "swimming pool", "pool", "service area",
    "dump station", "sanitary", "disabled facilities", "bbq", "barbecue",
    "elektriciteit", "stroom", "water", "drinkwater", "afvalwater", "loosplaats", "chemisch toilet",
    "toilet", "douche", "honden toegestaan", "huisdieren toegestaan", "speeltuin", "zwembad",
    "wasserij", "wasmachine", "restaurant", "winkel", "sanitair",
    "électricité", "eau", "douche", "toilettes", "chiens acceptés", "animaux acceptés",
    "Strom", "Wasser", "Dusche", "Toilette", "Hunde erlaubt", "Haustiere erlaubt",
]

OPENING_KEYWORDS = [
    "open all year", "open year round", "opening hours", "opening period", "season", "closed",
    "geopend", "hele jaar", "openingstijden", "openingsperiode", "gesloten",
    "ouvert", "toute l'année", "heures d'ouverture", "fermé",
    "ganzjährig", "Öffnungszeiten", "geöffnet", "geschlossen",
]


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(_safe_text(v) for v in value if v)
    if isinstance(value, dict):
        return ", ".join(_safe_text(v) for v in value.values() if v)
    return str(value).strip()


def _flatten_json_ld(data: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(data, list):
        for item in data:
            yield from _flatten_json_ld(item)
    elif isinstance(data, dict):
        if "@graph" in data:
            yield from _flatten_json_ld(data.get("@graph"))
        yield data


def _first(values: Iterable[str]) -> str:
    for value in values:
        value = (value or "").strip()
        if value:
            return value
    return ""


def _extract_json_ld(soup: BeautifulSoup) -> Dict[str, str]:
    extracted = {
        "source_name_found": "",
        "source_address_found": "",
        "source_city_found": "",
        "source_country_found": "",
        "source_phone_found": "",
        "source_email_found": "",
        "source_website_found": "",
        "source_latitude_found": "",
        "source_longitude_found": "",
    }

    relevant_types = {
        "Campground", "LodgingBusiness", "LocalBusiness", "Place", "TouristAttraction",
        "Accommodation", "Hotel", "RVPark",
    }

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        for item in _flatten_json_ld(data):
            item_type = item.get("@type", "")
            item_types = set(item_type if isinstance(item_type, list) else [item_type])
            if item_types and not (item_types & relevant_types):
                # Still allow a partial item if it clearly has address/contact fields.
                if not any(k in item for k in ["address", "telephone", "geo", "email"]):
                    continue

            address = item.get("address", {}) or {}
            geo = item.get("geo", {}) or {}

            extracted["source_name_found"] = extracted["source_name_found"] or _safe_text(item.get("name"))
            extracted["source_phone_found"] = extracted["source_phone_found"] or _safe_text(item.get("telephone"))
            extracted["source_email_found"] = extracted["source_email_found"] or _safe_text(item.get("email"))
            extracted["source_website_found"] = extracted["source_website_found"] or _safe_text(item.get("url"))

            if isinstance(address, dict):
                street = _safe_text(address.get("streetAddress"))
                postal = _safe_text(address.get("postalCode"))
                city = _safe_text(address.get("addressLocality"))
                region = _safe_text(address.get("addressRegion"))
                country = _safe_text(address.get("addressCountry"))
                extracted["source_address_found"] = extracted["source_address_found"] or ", ".join(
                    part for part in [street, postal, city, region] if part
                )
                extracted["source_city_found"] = extracted["source_city_found"] or city
                extracted["source_country_found"] = extracted["source_country_found"] or country
            else:
                extracted["source_address_found"] = extracted["source_address_found"] or _safe_text(address)

            if isinstance(geo, dict):
                extracted["source_latitude_found"] = extracted["source_latitude_found"] or _safe_text(geo.get("latitude"))
                extracted["source_longitude_found"] = extracted["source_longitude_found"] or _safe_text(geo.get("longitude"))

    return extracted


def _text_excerpt_around(text: str, keywords: List[str], window: int = 220) -> str:
    lower = text.lower()
    snippets = []
    for keyword in keywords:
        idx = lower.find(keyword.lower())
        if idx >= 0:
            start = max(0, idx - window // 2)
            end = min(len(text), idx + len(keyword) + window // 2)
            snippets.append(" ".join(text[start:end].split()))
        if len(snippets) >= 3:
            break
    return " | ".join(snippets)


def _extract_visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    return " ".join(text.split())


def extract_external_source_fields(url: str, profile_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Fetch a candidate source page and extract lightweight structured fields.

    This replaces the separate Extractor agent for the first implementation. The goal is not
    perfect deep extraction; the goal is to give Entity Matcher enough external field values
    to compare against the Gap Detector/profile table.
    """
    base = {
        "source_name_found": "",
        "source_address_found": "",
        "source_city_found": "",
        "source_country_found": "",
        "source_phone_found": "",
        "source_email_found": "",
        "source_website_found": "",
        "source_latitude_found": "",
        "source_longitude_found": "",
        "source_facilities_text": "",
        "source_opening_text": "",
        "source_price_text": "",
        "source_page_text_excerpt": "",
        "extraction_status": "not_started",
        "extraction_error": "",
    }

    if not can_fetch(url):
        base["extraction_status"] = "blocked_by_robots_txt"
        return base

    try:
        response = requests.get(
            url,
            timeout=FETCH_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en,nl;q=0.9,de;q=0.8,fr;q=0.8"},
        )
        response.raise_for_status()
    except Exception as exc:
        base["extraction_status"] = "fetch_failed"
        base["extraction_error"] = str(exc)[:500]
        return base

    content_type = response.headers.get("Content-Type", "")
    if "text/html" not in content_type and "application/xhtml" not in content_type and response.text.lstrip()[:20].lower().find("<html") == -1:
        base["extraction_status"] = "unsupported_content_type"
        base["extraction_error"] = content_type[:200]
        return base

    soup = BeautifulSoup(response.text, "html.parser")
    json_ld = _extract_json_ld(soup)
    base.update({k: v for k, v in json_ld.items() if v})

    text = _extract_visible_text(soup)
    lower_text = text.lower()

    if not base["source_email_found"]:
        base["source_email_found"] = _first(EMAIL_RE.findall(text))
    if not base["source_phone_found"]:
        base["source_phone_found"] = _first(PHONE_RE.findall(text))

    canonical = soup.find("link", rel=lambda value: value and "canonical" in value)
    if canonical and canonical.get("href") and not base["source_website_found"]:
        base["source_website_found"] = urljoin(url, canonical["href"])

    found_facilities = []
    for keyword in FACILITY_KEYWORDS:
        if keyword.lower() in lower_text and keyword.lower() not in [f.lower() for f in found_facilities]:
            found_facilities.append(keyword)
    base["source_facilities_text"] = ", ".join(found_facilities[:30])

    base["source_opening_text"] = _text_excerpt_around(text, OPENING_KEYWORDS)
    prices = PRICE_RE.findall(text)
    base["source_price_text"] = ", ".join(dict.fromkeys(prices[:20]))

    # Keep a compact text excerpt for debugging and for later LLM-based extraction if needed.
    profile_keywords = []
    if profile_context:
        for key in ["name", "city", "zipcode", "address_house_number"]:
            value = profile_context.get(key)
            if value:
                profile_keywords.append(str(value))
    base["source_page_text_excerpt"] = _text_excerpt_around(text, profile_keywords or FACILITY_KEYWORDS)[:2000]
    base["extraction_status"] = "success"
    return base
