"""Redaction of secrets/PII before anything reaches an LLM provider."""

from __future__ import annotations

import time

from app.llm.redaction import REDACTED, redact_payload, redact_text


def test_redacts_email_addresses() -> None:
    assert redact_text("contact planner@demo.test for details") == f"contact {REDACTED} for details"


def test_redacts_phone_like_sequences() -> None:
    assert redact_text("call +94 77 123 4567 now") == f"call {REDACTED} now"
    assert redact_text("reach 077-123-4567") == f"reach {REDACTED}"


def test_does_not_redact_short_digit_runs() -> None:
    # Fewer than 9 digits: not phone-like.
    assert redact_text("order quantity 1234") == "order quantity 1234"


def test_redacts_anthropic_api_keys() -> None:
    text = "key=sk-ant-abc123XYZ_-token more text"
    assert redact_text(text) == f"key={REDACTED} more text"


def test_redacts_bearer_tokens() -> None:
    text = "Authorization: Bearer abcDEF123.token-value"
    assert redact_text(text) == f"Authorization: {REDACTED}"


def test_operator_aliases_are_not_redacted() -> None:
    # Pseudonymous alias codes (backend-contracts.md section 2) must survive.
    text = "operator KTN-OP-017 recorded the cycle"
    assert redact_text(text) == text


def test_redact_payload_recurses_through_nested_structures() -> None:
    payload = {
        "note": "email planner@demo.test",
        "items": [
            {"phone": "+94771234567"},
            "sk-ant-secrettoken123",
        ],
        "count": 3,
        "flag": True,
        "nothing": None,
    }
    redacted = redact_payload(payload)

    assert redacted["note"] == f"email {REDACTED}"
    assert redacted["items"][0]["phone"] == REDACTED
    assert redacted["items"][1] == REDACTED
    assert redacted["count"] == 3
    assert redacted["flag"] is True
    assert redacted["nothing"] is None


def test_redact_payload_preserves_tuple_type() -> None:
    result = redact_payload(("planner@demo.test", 42))
    assert result == (REDACTED, 42)
    assert isinstance(result, tuple)


def test_bare_digit_runs_survive() -> None:
    # 9-12 bare digits with no separators and no leading "+" are not
    # phone-like per backend-contracts.md section 8 (separators required).
    assert redact_text("order quantity 123456789") == "order quantity 123456789"
    assert redact_text("PO number 9876543210") == "PO number 9876543210"
    assert redact_text("reference 123456789012") == "reference 123456789012"


def test_order_references_survive() -> None:
    assert redact_text("see PO-KTN-0001 for details") == "see PO-KTN-0001 for details"
    assert redact_text("linked to PO-DEMO-001") == "linked to PO-DEMO-001"


def test_uuids_survive() -> None:
    text = "run id c1468894-4b3a-4a5d-941f-fb9eb3c6a2c3 failed"
    assert redact_text(text) == text


def test_iso_dates_and_datetimes_survive() -> None:
    assert redact_text("due 2026-09-17") == "due 2026-09-17"
    assert redact_text("logged at 2026-09-17T08:30:00Z") == "logged at 2026-09-17T08:30:00Z"


def test_decimal_numbers_survive() -> None:
    assert redact_text("total 1099.98") == "total 1099.98"
    assert redact_text("balance 12000.00") == "balance 12000.00"


def test_punctuated_phone_numbers_are_still_redacted() -> None:
    assert redact_text("call +94 77 123 4567 now") == f"call {REDACTED} now"
    assert redact_text("call (011) 234-5678 now") == f"call {REDACTED} now"
    assert redact_text("call 077.123.4567 now") == f"call {REDACTED} now"


def test_date_and_time_combinations_survive() -> None:
    # Regression: a date's "4-2-2" digit grouping, or a date plus an
    # adjacent time, is structurally indistinguishable from a phone number
    # by digit count and punctuation alone -- dates/times are masked before
    # phone detection runs specifically to avoid this.
    assert redact_text("2026-09-17 08:30") == "2026-09-17 08:30"
    assert redact_text("2026-09-17T08:30:00+05:30") == "2026-09-17T08:30:00+05:30"
    assert redact_text("2026-09-17 2026-09-22") == "2026-09-17 2026-09-22"
    assert redact_text("2026-09-17 to 2026-09-22") == "2026-09-17 to 2026-09-22"
    assert redact_text("due 2026-09-22, 1,260.000 m") == "due 2026-09-22, 1,260.000 m"
    assert (
        redact_text("slots 2026-09-17 A and 2026-09-18 B") == "slots 2026-09-17 A and 2026-09-18 B"
    )


def test_phone_number_redacted_alongside_a_surviving_date() -> None:
    text = "Call +94 77 123 4567 before 2026-09-17 08:30"
    assert redact_text(text) == f"Call {REDACTED} before 2026-09-17 08:30"


def test_nul_placeholder_shaped_input_does_not_crash_or_corrupt() -> None:
    # Regression: an earlier implementation masked protected date/time spans
    # with NUL-wrapped placeholder text and restored it afterward. Untrusted
    # input (tool output, user notes) that happens to already contain that
    # exact shape either crashed (index past the end of the protected list)
    # or silently substituted unrelated protected content. This module must
    # never rewrite the text it scans -- only slice the original string --
    # so input shaped like the old placeholder scheme is inert, ordinary text.
    assert redact_text("call \x000\x00 me") == "call \x000\x00 me"
    assert redact_text("\x0099\x00 and 2026-09-17") == "\x0099\x00 and 2026-09-17"
    assert (
        redact_text("value \x005\x00 plus phone 077-123-4567")
        == f"value \x005\x00 plus phone {REDACTED}"
    )


def test_nul_placeholder_shaped_input_is_not_replaced_by_unrelated_content() -> None:
    # The specific silent-corruption case: a "\x00<N>\x00" span in the input
    # must never be swapped for the Nth protected date/time span found
    # elsewhere in the same text.
    text = "ORIGINAL[\x000\x00]FIELD then a real date 2026-09-17 appears"
    assert redact_text(text) == text


def test_redact_text_is_fast_on_pathological_inputs() -> None:
    # A long run of digits/separators with no valid phone/email anywhere,
    # and a long run of "@" characters with no valid email anywhere. Both
    # used to take ~15s under an unbounded-quantifier email regex (O(n^2)
    # backtracking); each must now complete in well under a second.
    digits_and_separators = "9" * 200_000
    start = time.perf_counter()
    result = redact_text(digits_and_separators)
    elapsed = time.perf_counter() - start
    assert result == digits_and_separators
    assert elapsed < 1.0, f"redact_text on 200k digits took {elapsed:.3f}s"

    many_at_signs = "a@" * 100_000
    start = time.perf_counter()
    result = redact_text(many_at_signs)
    elapsed = time.perf_counter() - start
    assert result == many_at_signs
    assert elapsed < 1.0, f"redact_text on 200k '@' chars took {elapsed:.3f}s"
