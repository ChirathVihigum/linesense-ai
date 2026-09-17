"""Secret/PII redaction for anything sent to an LLM provider.

Agents run :func:`redact_payload` on tool outputs and prompt context before
including them in a request. Operator aliases (e.g. ``KTN-OP-017``) are
pseudonymous by design (backend-contracts.md section 2, "Industrial
engineering") and are never redacted: the phone-number pattern only matches
runs made of digits and phone punctuation, which an alphanumeric alias never
is.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_API_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]+")
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
# A candidate phone-like run: starts and ends with a digit, with only digits
# and common phone punctuation (space, +, -, ., parens) in between. Letters
# anywhere in the run break the match, so alphanumeric codes are unaffected.
_PHONE_CANDIDATE_RE = re.compile(r"(?<![\w])\+?\d[\d\-.\s()]{6,}\d(?![\w])")


def _digit_count(candidate: str) -> int:
    return sum(1 for char in candidate if char.isdigit())


def redact_text(text: str) -> str:
    """Replace emails, API keys, bearer tokens, and phone-like sequences."""
    text = _API_KEY_RE.sub(REDACTED, text)
    text = _BEARER_RE.sub(REDACTED, text)
    text = _EMAIL_RE.sub(REDACTED, text)

    def _phone_sub(match: re.Match[str]) -> str:
        candidate = match.group(0)
        return REDACTED if _digit_count(candidate) >= 9 else candidate

    return _PHONE_CANDIDATE_RE.sub(_phone_sub, text)


def redact_payload(value: Any) -> Any:
    """Recursively redact every string in ``value`` (dict/list/tuple/scalar)."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: redact_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_payload(item) for item in value)
    return value
