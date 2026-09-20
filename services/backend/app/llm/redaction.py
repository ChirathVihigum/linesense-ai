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

import bisect
import re
import unicodedata
from typing import Any

REDACTED = "[REDACTED]"

# Emails are found by a linear scan (`_email_spans`), not a regex: an
# unbounded regex backtracks quadratically on long non-matching runs, and a
# length-bounded one matches only a suffix/prefix of an over-long address,
# leaking the rest. Neither character class admits "@", so the per-"@"
# expansions below never cross another "@" and total work is O(n).
#
# Character classes (Unicode-aware, so non-ASCII addresses are covered whole):
# - local part: any Unicode letter/digit (``str.isalnum()``), any Unicode
#   combining mark (category ``Mn``/``Mc``/``Me``: see ``_is_combining_mark``),
#   or one of ``._%+-``;
# - domain: the same, plus one of ``.-``.
#
# A NFD-decomposed address (e.g. "josé@x.com" normalized to
# "josé@x.com", the accent as a standalone combining codepoint) has a
# combining mark as its last local-part character or inside a domain label.
# ``"́".isalnum()`` is ``False``, so without this, the local/domain scan
# stopped one character short of "@"/the label boundary and the span-building
# check in ``_email_spans`` (``start < at`` / a non-empty label) then dropped
# the match's decomposed tail entirely -- silently leaking part of the
# address. Treating combining marks as word characters keeps the whole
# decomposed grapheme cluster inside the local/domain run.
_EMAIL_LOCAL_PUNCT = frozenset("._%+-")
_EMAIL_DOMAIN_PUNCT = frozenset(".-")
_API_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]+")
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)

# ISO 8601 dates and datetimes: 2026-09-17, 2026-09-17T08:30:00Z,
# 2026-09-17 08:30, 2026-09-17T08:30:00+05:30. Every quantifier in this
# pattern (and in `_BARE_TIME_RE`/`_PHONE_CANDIDATE_RE` below) is fixed-width
# or bounded, and each optional segment is gated by a literal character
# (`-`, `T`/` `, `:`) that must appear first, so a failed match at any
# position fails in O(1) rather than backtracking -- this module never scans
# with an unbounded, backtracking quantifier over untrusted input (emails are
# handled by a linear scan above for the same reason).
_ISO_DATETIME_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
)
# A bare "HH:MM(:SS)" not already consumed as part of a date above.
_BARE_TIME_RE = re.compile(r"(?<!\d)\d{2}:\d{2}(?::\d{2})?(?!\d)")
# An 8-4-4-4-12 hex UUID. An all-digit one (``11111111-2222-...``) otherwise
# contains a phone-shaped "4-4-4" run; fixed-width, so O(1) per position.
_UUID_RE = re.compile(
    r"(?<![0-9A-Za-z])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{12}(?![0-9A-Za-z])"
)

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


def _is_combining_mark(char: str) -> bool:
    """Whether ``char`` is a Unicode combining mark (category ``Mn``/``Mc``/``Me``).

    NFD-normalised text spells an accented letter as a base letter followed
    by one of these; ``str.isalnum()`` is ``False`` for them on their own, so
    a scan that only checked ``isalnum()``/``isalpha()`` would stop inside a
    decomposed grapheme cluster (see the module docstring and the comment
    above ``_EMAIL_LOCAL_PUNCT``).
    """
    return unicodedata.category(char)[0] == "M"


def _is_word_char(char: str) -> bool:
    return char.isalnum() or _is_combining_mark(char)


def _is_local_char(char: str) -> bool:
    return _is_word_char(char) or char in _EMAIL_LOCAL_PUNCT


def _is_domain_char(char: str) -> bool:
    return _is_word_char(char) or char in _EMAIL_DOMAIN_PUNCT


def _expand_left(text: str, index: int, floor: int) -> int:
    """First index of the local-part run ending just before ``index``."""
    while index > floor and _is_local_char(text[index - 1]):
        index -= 1
    return index


def _expand_right(text: str, index: int) -> int:
    """End index (exclusive) of the domain run starting at ``index``, with
    trailing dots/hyphens (e.g. a sentence-ending period) excluded. Used for
    chained ``@domain`` pieces, which are swallowed whole."""
    length = len(text)
    end = index
    while end < length and _is_domain_char(text[end]):
        end += 1
    while end > index and text[end - 1] in _EMAIL_DOMAIN_PUNCT:
        end -= 1
    return end


def _valid_domain_end(text: str, index: int) -> int:
    """End (exclusive) of the longest valid domain starting at ``index``, or
    ``index`` itself if there is none.

    A valid domain is a prefix of the domain-character run starting at
    ``index`` that contains at least one dot, has no empty labels, and ends
    in a final label (after the last dot) of at least two letters
    (``str.isalpha()``, so Unicode letters count) that is not followed by
    another letter. Taking the *longest valid prefix* rather than rejecting
    the whole run means trailing junk glued onto an address
    (``x@y.io2026-09-17``, ``a@b.co.uk2026``) does not hide the address.
    Dotless hosts (``user@localhost``) are deliberately never valid: they are
    far more often handles or host names. One pass over the run: O(run).
    """
    length = len(text)
    best = index
    label_start = index
    seen_dot = False
    alpha_run = True  # every char of the current label so far is a letter
    position = index
    while position < length:
        char = text[position]
        if char == ".":
            if position == label_start:
                break  # empty label: no longer prefix can be valid
            seen_dot = True
            label_start = position + 1
            alpha_run = True
        elif char.isalpha() or _is_combining_mark(char):
            pass
        elif _is_domain_char(char):
            alpha_run = False
        else:
            break
        position += 1
        if (
            seen_dot
            and alpha_run
            and position - label_start >= 2
            and (
                position == length
                or not (text[position].isalpha() or _is_combining_mark(text[position]))
            )
        ):
            best = position
    return best


def _email_spans(text: str) -> list[tuple[int, int]]:
    """Non-overlapping, ordered spans of every email-shaped token, each
    covering the whole token regardless of length. Chains such as
    ``a@b@c.io`` are covered in full. Runs in O(len(text)): each "@"
    expands only up to its neighbouring "@", and chain extensions only cover
    text not yet claimed by an earlier span."""
    spans: list[tuple[int, int]] = []
    cursor = 0  # end of the last accepted span
    at = text.find("@")
    while at != -1:
        if at >= cursor:
            start = _expand_left(text, at, cursor)
            end = _valid_domain_end(text, at + 1)
            if start < at and end > at + 1:
                # Extend over chained "local@" pieces to the left ...
                while start > cursor and text[start - 1] == "@":
                    start = _expand_left(text, start - 1, cursor)
                # ... and over chained "@domain" pieces to the right.
                while end < len(text) and text[end] == "@":
                    end = max(end + 1, _expand_right(text, end + 1))
                spans.append((start, end))
                cursor = end
        at = text.find("@", at + 1)
    return spans


def _redact_emails(text: str) -> str:
    pieces: list[str] = []
    cursor = 0
    for start, end in _email_spans(text):
        pieces.append(text[cursor:start])
        pieces.append(REDACTED)
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _protected_spans(text: str) -> list[tuple[int, int]]:
    """Sorted, disjoint character-offset spans of ISO dates/datetimes, bare
    times, and UUIDs in ``text``. A phone candidate overlapping any of these
    is not redacted."""
    raw = [match.span() for match in _ISO_DATETIME_RE.finditer(text)]
    raw.extend(match.span() for match in _BARE_TIME_RE.finditer(text))
    raw.extend(match.span() for match in _UUID_RE.finditer(text))
    raw.sort()
    merged: list[tuple[int, int]] = []
    for start, end in raw:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _overlaps_any(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    """``spans`` is sorted and disjoint (see `_protected_spans`), so a binary
    search finds the only span that can overlap: O(log n) per candidate."""
    # ``index`` is the first span starting after ``start``: the span before
    # it may reach into the candidate, and it may itself start inside it.
    index = bisect.bisect_right(spans, start, key=lambda span: span[0])
    if index > 0 and spans[index - 1][1] > start:
        return True
    return index < len(spans) and spans[index][0] < end


def redact_text(text: str) -> str:
    """Replace emails, API keys, bearer tokens, and phone-like sequences."""
    text = _API_KEY_RE.sub(REDACTED, text)
    text = _BEARER_RE.sub(REDACTED, text)
    text = _redact_emails(text)

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
