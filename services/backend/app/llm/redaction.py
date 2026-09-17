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
-- phone-shaped by digit count and punctuation alone).

To keep dates/timestamps untouched, this module finds their spans in the
*original* text first and then only accepts a phone-candidate match that
does not overlap any of those spans -- a position-based approach, not a
textual one. An earlier version of this module replaced protected spans
with NUL-wrapped placeholder text and restored it afterward; that is
unsafe against untrusted input (agent tool output, user-entered notes) that
happens to already contain a NUL byte followed by digits and another NUL --
such input either collided with an unrelated protected span (silent
corruption) or indexed past the end of the protected list (a crash). Since
tool output is exactly the kind of text this module redacts, "the input
never contains our sentinel" is not a safe assumption, so this module never
rewrites the text it is scanning; it only ever slices the original string.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}")
_API_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]+")
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)

# ISO 8601 dates and datetimes: 2026-09-17, 2026-09-17T08:30:00Z,
# 2026-09-17 08:30, 2026-09-17T08:30:00+05:30. Every quantifier in this
# pattern (and in `_BARE_TIME_RE`/`_PHONE_CANDIDATE_RE` below) is fixed-width
# or bounded, and each optional segment is gated by a literal character
# (`-`, `T`/` `, `:`) that must appear first, so a failed match at any
# position fails in O(1) rather than backtracking -- this module never scans
# with an unbounded quantifier over untrusted input (see `_EMAIL_RE` above,
# which is bounded for the same reason: an unbounded local-part/domain would
# make a long run of non-matching characters, e.g. attacker-supplied text
# with no "@" at all, cost O(n^2) instead of O(n)).
_ISO_DATETIME_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
)
# A bare "HH:MM(:SS)" not already consumed as part of a date above.
_BARE_TIME_RE = re.compile(r"(?<!\d)\d{2}:\d{2}(?::\d{2})?(?!\d)")

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


def _protected_spans(text: str) -> list[tuple[int, int]]:
    """Character-offset spans of ISO dates/datetimes and bare times in
    ``text``. A phone candidate overlapping any of these is not redacted."""
    spans = [match.span() for match in _ISO_DATETIME_RE.finditer(text)]
    spans.extend(match.span() for match in _BARE_TIME_RE.finditer(text))
    return spans


def _overlaps_any(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < span_end and span_start < end for span_start, span_end in spans)


def redact_text(text: str) -> str:
    """Replace emails, API keys, bearer tokens, and phone-like sequences."""
    text = _API_KEY_RE.sub(REDACTED, text)
    text = _BEARER_RE.sub(REDACTED, text)
    text = _EMAIL_RE.sub(REDACTED, text)

    protected = _protected_spans(text)

    pieces: list[str] = []
    cursor = 0
    for match in _PHONE_CANDIDATE_RE.finditer(text):
        start, end = match.span()
        if start < cursor:
            continue  # overlaps a phone match already redacted
        if _overlaps_any(start, end, protected):
            continue  # part of a date/time, not a phone number
        if not _is_phone_like(match.group(0)):
            continue
        pieces.append(text[cursor:start])
        pieces.append(REDACTED)
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


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
