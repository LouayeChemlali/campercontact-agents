# regex helpers for pulling emails and phone numbers out of raw text, not actively used in v1 but kept for future extraction

import re

_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_PATTERN = re.compile(r"(?:\+?\d[\d\s\-().]{6,}\d)")


def extract_email(text: str) -> str | None:
    """Extract first email address found in text. Not used in v1."""
    if not text:
        return None
    match = _EMAIL_PATTERN.search(text)
    return match.group(0) if match else None


def extract_phone(text: str) -> str | None:
    """Extract first phone number found in text. Not used in v1."""
    if not text:
        return None
    match = _PHONE_PATTERN.search(text)
    return match.group(0).strip() if match else None
