"""Unit tests for `app.domain.orders.import_csv`.

These exercise only the scenarios that never need a database (header,
encoding, size/row-count limits, per-field format, formula-like cells, and
the in-file duplicate/date checks that run before any lookup). A fake
session whose `.scalar()` always reports "not found" proves those paths
never touch the database; `_resolve_valid_row` is also exercised directly
for the two checks ("unknown customer", "duplicate ref") that sit right at
the DB boundary. Full customer/style/BOM resolution against a real database
is covered by `tests/integration/test_import_api.py`.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from app.domain.orders.import_csv import (
    EXPECTED_HEADER,
    FILE_LEVEL_ROW,
    MAX_ROWS,
    RowError,
    _resolve_valid_row,
    validate_csv,
)

TODAY = date(2026, 1, 1)
ORG_ID = uuid.uuid4()
HEADER_LINE = ",".join(EXPECTED_HEADER)
VALID_ROW = "PO-1,CUST-1,STY-1,10,2099-01-01,3"


class _AlwaysNoneSession:
    """A fake session whose `.scalar()` always reports "not found"."""

    async def scalar(self, statement: Any) -> None:
        return None


def _csv(*lines: str) -> bytes:
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


async def test_header_mismatch_is_a_file_level_error() -> None:
    raw = _csv("wrong,header,here", VALID_ROW)
    result = await validate_csv(_AlwaysNoneSession(), raw, organization_id=ORG_ID, today=TODAY)
    assert not result.ok
    assert result.errors[0].row_number == FILE_LEVEL_ROW
    assert "Header must be exactly" in result.errors[0].message


async def test_bad_date_format() -> None:
    raw = _csv(HEADER_LINE, "PO-1,CUST-1,STY-1,10,not-a-date,3")
    result = await validate_csv(_AlwaysNoneSession(), raw, organization_id=ORG_ID, today=TODAY)
    assert result.errors == [
        RowError(1, "due_date", "due_date must be an ISO 8601 date (YYYY-MM-DD).")
    ]


async def test_past_date_is_rejected() -> None:
    error = await _resolve_valid_row(
        _AlwaysNoneSession(),
        organization_id=ORG_ID,
        today=TODAY,
        row_number=1,
        external_ref="PO-1",
        customer_code="CUST-1",
        style_code="STY-1",
        quantity=10,
        due_date=date(2020, 1, 1),
        priority=3,
        seen_refs={},
    )
    assert error == RowError(1, "due_date", "Due date must not be in the past.")


async def test_bad_priority() -> None:
    raw = _csv(HEADER_LINE, "PO-1,CUST-1,STY-1,10,2099-01-01,9")
    result = await validate_csv(_AlwaysNoneSession(), raw, organization_id=ORG_ID, today=TODAY)
    assert result.errors == [RowError(1, "priority", "priority must be between 1 and 5.")]


async def test_unknown_customer() -> None:
    error = await _resolve_valid_row(
        _AlwaysNoneSession(),
        organization_id=ORG_ID,
        today=TODAY,
        row_number=1,
        external_ref="PO-1",
        customer_code="CUST-1",
        style_code="STY-1",
        quantity=10,
        due_date=date(2099, 1, 1),
        priority=3,
        seen_refs={},
    )
    assert error == RowError(1, "customer_code", "Unknown customer code.")


async def test_duplicate_ref_in_file() -> None:
    error = await _resolve_valid_row(
        _AlwaysNoneSession(),
        organization_id=ORG_ID,
        today=TODAY,
        row_number=2,
        external_ref="PO-1",
        customer_code="CUST-1",
        style_code="STY-1",
        quantity=10,
        due_date=date(2099, 1, 1),
        priority=3,
        seen_refs={"PO-1": 1},
    )
    assert error == RowError(
        2, "external_ref", "Duplicate external_ref in file (first seen on row 1)."
    )


async def test_formula_like_cell_is_rejected() -> None:
    raw = _csv(HEADER_LINE, "=cmd|'/calc',CUST-1,STY-1,10,2099-01-01,3")
    result = await validate_csv(_AlwaysNoneSession(), raw, organization_id=ORG_ID, today=TODAY)
    assert result.errors == [RowError(1, "external_ref", "Formula-like values are not accepted.")]


async def test_more_than_max_rows_is_rejected() -> None:
    rows = [f"PO-{i},CUST-1,STY-1,10,2099-01-01,3" for i in range(MAX_ROWS + 1)]
    raw = _csv(HEADER_LINE, *rows)
    result = await validate_csv(_AlwaysNoneSession(), raw, organization_id=ORG_ID, today=TODAY)
    assert result.errors[0].row_number == FILE_LEVEL_ROW
    assert "more than" in result.errors[0].message
    assert result.row_count == MAX_ROWS + 1


async def test_non_utf8_file_is_rejected() -> None:
    raw = "PO-\xe9,CUST-1,STY-1,10,2099-01-01,3".encode("latin-1")
    result = await validate_csv(_AlwaysNoneSession(), raw, organization_id=ORG_ID, today=TODAY)
    assert result.errors == [RowError(FILE_LEVEL_ROW, None, "File must be UTF-8 encoded.")]


async def test_missing_required_field() -> None:
    raw = _csv(HEADER_LINE, ",CUST-1,STY-1,10,2099-01-01,3")
    result = await validate_csv(_AlwaysNoneSession(), raw, organization_id=ORG_ID, today=TODAY)
    assert result.errors == [RowError(1, "external_ref", "external_ref is required.")]
