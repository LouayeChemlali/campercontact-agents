from __future__ import annotations

# checks whether a URL belongs to one of the trusted camping source domains

from urllib.parse import urlparse

from .config import ALLOWED_SOURCE_DOMAINS


def _normalise_domain(domain: str) -> str:
    domain = (domain or "").strip().lower()
    domain = domain.split(":")[0]
    domain = domain.replace("https://", "").replace("http://", "")
    domain = domain.strip("/")
    domain = domain.replace("*.", "")

    if domain.startswith("www."):
        domain = domain[4:]

    return domain


def _allowed_domains() -> list[str]:
    return [
        _normalise_domain(domain)
        for domain in ALLOWED_SOURCE_DOMAINS.split(",")
        if domain.strip()
    ]


def is_allowed_source_url(url: str) -> bool:
    """Return True only if the URL belongs to one of the allowed source domains."""
    if not url:
        return False

    parsed = urlparse(url)
    candidate_domain = _normalise_domain(parsed.netloc)

    if not candidate_domain:
        return False

    for allowed_domain in _allowed_domains():
        if candidate_domain == allowed_domain:
            return True

        # Allow subdomains, e.g. nl.campspace.com under campspace.com
        if candidate_domain.endswith("." + allowed_domain):
            return True

    return False
