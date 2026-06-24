from __future__ import annotations

from typing import Any, Dict

import requests

from .auth import get_access_token
from .config import SEARCH_API_VERSION, SEARCH_TIMEOUT_SECONDS, SERVING_CONFIG


def search_vertex_ai_search(query: str, page_size: int = 10) -> Dict[str, Any]:
    token = get_access_token()
    url = f"https://discoveryengine.googleapis.com/{SEARCH_API_VERSION}/{SERVING_CONFIG}:search"

    payload = {
        "query": query,
        "pageSize": page_size,
        "queryExpansionSpec": {"condition": "DISABLED"},
        "spellCorrectionSpec": {"mode": "AUTO"},
        "languageCode": "en-US",
        "userInfo": {"timeZone": "Europe/Amsterdam"},
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    response = requests.post(url, headers=headers, json=payload, timeout=SEARCH_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()
