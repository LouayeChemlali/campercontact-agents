from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

from .config import FETCH_TIMEOUT_SECONDS, RESPECT_ROBOTS_TXT, USER_AGENT


@lru_cache(maxsize=256)
def _load_robot_parser(base_url: str) -> RobotFileParser:
    parser = RobotFileParser()
    robots_url = base_url.rstrip("/") + "/robots.txt"
    parser.set_url(robots_url)
    try:
        response = requests.get(robots_url, timeout=FETCH_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})
        if response.status_code >= 400:
            # If robots.txt is unavailable, robotparser treats it as allowed after parse([]).
            parser.parse([])
        else:
            parser.parse(response.text.splitlines())
    except Exception:
        parser.parse([])
    return parser


def can_fetch(url: str) -> bool:
    if not RESPECT_ROBOTS_TXT:
        return True
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return False
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    parser = _load_robot_parser(base_url)
    return parser.can_fetch(USER_AGENT, url)
