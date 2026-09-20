"""Redaction of secrets/PII before anything reaches an LLM provider."""

from __future__ import annotations

import time
import unicodedata

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


def test_redacts_nfd_decomposed_email_local_part() -> None:
    # "josé@example.com" with the "é" as NFD (base "e" + combining acute
    # accent U+0301) rather than the precomposed NFC codepoint. A word-char
    # scan limited to `str.isalnum()` stops at the combining mark, one
    # character short of "@", and previously dropped the whole match.
    nfd_email = unicodedata.normalize("NFD", "josé@example.com")
    assert nfd_email != "josé@example.com"  # sanity: decomposition actually happened
    assert redact_text(f"contact {nfd_email} for details") == f"contact {REDACTED} for details"


def test_redacts_nfd_decomposed_email_domain_label() -> None:
    # The accent lands in the domain's final label instead of the local part.
    nfd_email = unicodedata.normalize("NFD", "contact@café.example")
    assert nfd_email != "contact@café.example"
    assert redact_text(f"see {nfd_email} now") == f"see {REDACTED} now"


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


def test_ordinary_email_is_redacted() -> None:
    text = "contact ops.lead@factory.example.com today"
    assert redact_text(text) == f"contact {REDACTED} today"


def test_over_long_local_part_is_redacted_in_full() -> None:
    # Regression: a length-bounded email regex matched only the last 64
    # local-part characters, leaking the rest ("reach a[REDACTED] now").
    for length in (65, 100, 1_000):
        text = f"reach {'a' * length}@example.com now"
        assert redact_text(text) == f"reach {REDACTED} now", length


def test_over_long_domain_is_redacted_in_full() -> None:
    domain = ".".join(["aaaa"] * 60) + ".com"  # ~300 characters
    assert len(domain) >= 300
    text = f"mail user@{domain} now"
    assert redact_text(text) == f"mail {REDACTED} now"


def test_email_adjacent_to_punctuation() -> None:
    assert redact_text("see (a.b@c.io) here") == f"see ({REDACTED}) here"
    # The sentence-ending dot is not part of the domain and must survive.
    assert redact_text("write to a.b@c.io.") == f"write to {REDACTED}."
    assert redact_text("write to a.b@c.io-") == f"write to {REDACTED}-"
    assert redact_text("mail a.b@c.io, then") == f"mail {REDACTED}, then"


def test_dotless_host_is_not_treated_as_email() -> None:
    # Deliberate choice: without a dotted domain ending in a >=2-letter
    # label, "@" text is far more often a handle/mention/host than a
    # routable address, so "user@localhost" is left alone.
    assert redact_text("ssh user@localhost now") == "ssh user@localhost now"
    assert redact_text("ping @c.io now") == "ping @c.io now"
    assert redact_text("version a@b.c1 now") == "version a@b.c1 now"


def test_chained_at_signs_are_redacted_in_full() -> None:
    assert redact_text("x a@b@c.io y") == f"x {REDACTED} y"
    assert redact_text("x a@b.io@c.io y") == f"x {REDACTED} y"
    assert redact_text("x a@b.io@ y") == f"x {REDACTED} y"


def test_email_redaction_leaves_dates_and_phones_behaviour_intact() -> None:
    text = "mail a@b.io or call 077-123-4567 by 2026-09-17 08:30."
    assert redact_text(text) == f"mail {REDACTED} or call {REDACTED} by 2026-09-17 08:30."


def test_redact_text_is_linear_on_long_emails_and_mixed_text() -> None:
    long_local = "a" * 200_000 + "@example.com"
    start = time.perf_counter()
    assert redact_text(long_local) == REDACTED
    assert time.perf_counter() - start < 1.0

    long_domain = "u@" + "a." * 100_000 + "com"
    start = time.perf_counter()
    assert redact_text(long_domain) == REDACTED
    assert time.perf_counter() - start < 1.0

    dotted_chain = "aa.aa@" * 40_000
    start = time.perf_counter()
    redact_text(dotted_chain)
    assert time.perf_counter() - start < 1.0

    sentence = (
        "Operator KTN-OP-017 (ops.lead@factory.example.com, +94 77 123 4567) "
        "logged PO-KTN-0001 at 2026-09-17 08:30; total 1099.98, key sk-ant-abc. "
    )
    mixed = sentence * (200_000 // len(sentence) + 1)
    start = time.perf_counter()
    result = redact_text(mixed)
    elapsed = time.perf_counter() - start
    assert "@" not in result
    assert "123 4567" not in result
    assert "2026-09-17 08:30" in result
    assert elapsed < 1.0, f"redact_text on 200k mixed text took {elapsed:.3f}s"


def test_email_followed_by_digits_is_redacted_via_longest_valid_domain_prefix() -> None:
    # Regression: the greedy domain run swallowed trailing digits, so the
    # whole candidate failed the "alphabetic final label" check and the
    # address leaked. The longest valid domain prefix is redacted instead.
    assert redact_text("x@y.io2026-09-17") == f"{REDACTED}2026-09-17"
    assert redact_text("a@b.co.uk2026 trailing") == f"{REDACTED}2026 trailing"
    assert (
        redact_text("a@sub.factory-01.example.com9 trailing digit") == f"{REDACTED}9 trailing digit"
    )
    assert redact_text("a@b.io0771234567") == f"{REDACTED}0771234567"


def test_repeated_emails_glued_to_dates_are_all_redacted_quickly() -> None:
    chunk = "contact ops@factory.io2026-09-17 "
    text = chunk * (200_000 // len(chunk) + 1)
    start = time.perf_counter()
    result = redact_text(text)
    elapsed = time.perf_counter() - start
    assert "@" not in result
    assert "ops" not in result
    assert result == f"contact {REDACTED}2026-09-17 " * (200_000 // len(chunk) + 1)
    assert elapsed < 1.0, f"redact_text on 200k glued emails took {elapsed:.3f}s"


def test_unicode_emails_are_redacted_in_full() -> None:
    assert redact_text("mail héllo@example.com now") == f"mail {REDACTED} now"
    assert redact_text("mail user@héllo.com now") == f"mail {REDACTED} now"
    assert redact_text("mail 用户@例子.公司 now") == f"mail {REDACTED} now"


def test_empty_domain_labels_are_not_valid() -> None:
    assert redact_text("mail a@.io now") == "mail a@.io now"


def test_numeric_uuid_shaped_tokens_survive() -> None:
    text = "id 11111111-2222-3333-4444-555555555555 done"
    assert redact_text(text) == text
    assert redact_text("11111111-2222-3333-4444-555555555555") == (
        "11111111-2222-3333-4444-555555555555"
    )
