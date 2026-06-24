from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import urlparse


def parse_vertex_results(search_response: Dict[str, Any], query: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    results = search_response.get("results", []) or []

    for rank, item in enumerate(results, start=1):
        document = item.get("document", {}) or {}
        derived = document.get("derivedStructData", {}) or {}

        source_url = derived.get("link")
        if not source_url:
            continue

        source_domain = urlparse(source_url).netloc.replace("www.", "")
        page_title = derived.get("title", "") or ""

        snippet = ""
        snippets = derived.get("snippets", []) or []
        if snippets and isinstance(snippets, list):
            snippet = snippets[0].get("snippet", "") or ""

        rows.append(
            {
                "query_used": query,
                "source_url": source_url,
                "source_domain": source_domain,
                "page_title": page_title,
                "snippet": snippet,
                "search_rank": rank,
            }
        )

    return rows


def deduplicate_candidates(candidates: List[Dict[str, Any]], max_urls: int) -> List[Dict[str, Any]]:
    seen = set()
    unique = []
    for candidate in candidates:
        url = candidate.get("source_url")
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(candidate)
        if len(unique) >= max_urls:
            break
    return unique
