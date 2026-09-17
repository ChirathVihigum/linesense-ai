"""Canonical request hashing for Idempotency-Key handling (no database)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.idempotency.service import request_hash


def test_key_order_does_not_matter() -> None:
    assert request_hash({"a": 1, "b": [1, {"c": 2, "d": 3}]}) == request_hash(
        {"b": [1, {"d": 3, "c": 2}], "a": 1}
    )


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (Decimal("1"), "1"),
        (Decimal("1"), 1),
        (1, "1"),
        (1, True),
        (1, 1.0),
        (None, "None"),
        ({"x": Decimal("2.50")}, {"x": "2.50"}),
        (uuid.UUID(int=1), str(uuid.UUID(int=1))),
        (date(2026, 9, 17), "2026-09-17"),
        (datetime(2026, 9, 17, tzinfo=UTC), "2026-09-17T00:00:00+00:00"),
        ({"__decimal__": "1"}, Decimal("1")),
        (["decimal", "1"], Decimal("1")),
    ],
)
def test_distinct_types_hash_differently(left: object, right: object) -> None:
    assert request_hash(left) != request_hash(right)


def test_lists_and_tuples_are_the_same_array() -> None:
    assert request_hash([1, 2]) == request_hash((1, 2))


def test_equal_typed_values_hash_equal() -> None:
    payload = {
        "quantity": Decimal("12.5"),
        "order_id": uuid.UUID(int=7),
        "due": date(2026, 12, 1),
        "at": datetime(2026, 9, 17, 8, 30, tzinfo=UTC),
    }
    assert request_hash(payload) == request_hash(dict(payload))


@pytest.mark.parametrize("value", [object(), {1: "non-string key"}, {1, 2}, b"bytes"])
def test_unsupported_values_are_rejected(value: object) -> None:
    with pytest.raises(TypeError):
        request_hash(value)
