"""Secret/PII redaction for anything sent to an LLM provider.

Agents run :func:`redact_payload` on tool outputs and prompt context before
including them in a request. Operator aliases (e.g. ``KTN-OP-017``) are
pseudonymous by design (backend-contracts.md section 2, "Industrial
engineering") and are never redacted: the phone-number pattern only matches
digit groupings shaped like a phone number, which an alphanumeric alias
never is.

Agent context is full of dates, timestamps, slot dates, and decimal
quantities (due dates, `line_capacity_slots.slot_date`, `cycle_observations
.observed_at`, money/weight amounts, ...). A digit-grouping heuristic loose
enough to catch real phone numbers (``077-123-4567``, ``+94 77 123 4567``)
is, on its own, indistinguishable from an ISO date (``2026-09-17`` is
"4 digits - 2 digits - 2 digits", exactly the shape of a country code plus
two more groups) or a date followed by a time (``2026-09-17 08:30``, whose
leading "date + hour" span alone is 10 digits with hyphen/space separators
-- phone-shaped by digit count and punctuation alone). So dates/timestamps
are matched and set aside *before* phone detection runs, and restored
afterward untouched; phone detection then only ever sees what is left.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_API_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]+")
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)

# ISO 8601 dates and datetimes: 2026-09-17, 2026-09-17T08:30:00Z,
# 2026-09-17 08:30, 2026-09-17T08:30:00+05:30. Matched and temporarily
# replaced by a placeholder (see `_protect_dates_and_times`) so a phone
# candidate can never span into or across one.
_ISO_DATETIME_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
)
# A bare "HH:MM(:SS)" not already consumed as part of a date above.
_BARE_TIME_RE = re.compile(r"(?<!\d)\d{2}:\d{2}(?::\d{2})?(?!\d)")
# Sentinel wrapper for protected spans: NUL is never a digit, phone
# separator, or "\w", so it can never be absorbed into a phone match, and
# it cannot occur in real input text (JSON/text payloads don't carry NULs).
_PLACEHOLDER_RE = re.compile("\x00(\\d+)\x00")

# A phone-shaped digit grouping: optional "+<country code> " (needs its own
# trailing separator), optional "(<area code>) ", then 2-4 groups of 2-4
# digits joined by a single space/hyphen/dot -- e.g. "077-123-4567",
# "+94 77 123 4567", "(011) 234-5678". A compact international number with
# no internal separators at all (e.g. "+94771234567") is matched by the
# second alternative: a leading "+" is a strong, low-false-positive signal
# on its own once the digit count is phone-length.
_PHONE_CANDIDATE_RE = re.compile(
    r"(?<![\w])"
    r"(?:"
    r"(?:\+\d{1,3}[ \-.])?(?:\(\d{2,4}\)[ \-.]?)?\d{2,4}(?:[ \-.]\d{2,4}){1,3}"
    r"|"
    r"\+\d{9,15}"
    r")"
    r"(?![\w])"
)
_PHONE_MIN_DIGITS = 9
_PHONE_MAX_DIGITS = 15


def _digit_count(candidate: str) -> int:
    return sum(1 for char in candidate if char.isdigit())


def _is_phone_like(candidate: str) -> bool:
    """``candidate`` already matched the phone-shaped grouping regex; this
    only bounds the total digit count to a real phone-number length."""
    digits = _digit_count(candidate)
    return _PHONE_MIN_DIGITS <= digits <= _PHONE_MAX_DIGITS


def _protect_dates_and_times(text: str) -> tuple[str, list[str]]:
    """Replace ISO dates/datetimes and bare times with NUL-wrapped indices.

    Returns the rewritten text and the list of original spans to restore
    afterward (index in the list == the number embedded in the placeholder).
    """
    protected: list[str] = []

    def _protect(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"\x00{len(protected) - 1}\x00"

    working = _ISO_DATETIME_RE.sub(_protect, text)
    working = _BARE_TIME_RE.sub(_protect, working)
    return working, protected


def redact_text(text: str) -> str:
    """Replace emails, API keys, bearer tokens, and phone-like sequences."""
    text = _API_KEY_RE.sub(REDACTED, text)
    text = _BEARER_RE.sub(REDACTED, text)
    text = _EMAIL_RE.sub(REDACTED, text)

    working, protected = _protect_dates_and_times(text)

    def _phone_sub(match: re.Match[str]) -> str:
        candidate = match.group(0)
        return REDACTED if _is_phone_like(candidate) else candidate

    working = _PHONE_CANDIDATE_RE.sub(_phone_sub, working)

    def _restore(match: re.Match[str]) -> str:
        return protected[int(match.group(1))]

    return _PLACEHOLDER_RE.sub(_restore, working)


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
