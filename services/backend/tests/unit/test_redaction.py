"""Redaction of secrets/PII before anything reaches an LLM provider."""

from __future__ import annotations

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
