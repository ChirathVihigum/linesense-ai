"""Unit tests for `app.domain.orders.import_csv`.

These exercise only the scenarios that never need a database (header,
encoding, size/row-count limits, malformed CSV fields, per-field format,
formula-like cells, and the in-file duplicate/date checks that run before
any lookup). `_UnusedSession` actively proves those paths never touch the
database (it raises if any query method is called); `_resolve_valid_row` is
exercised directly, against an in-memory `_Lookups` snapshot, for the two
checks ("unknown customer", "duplicate ref") that sit right at the DB
boundary. Full customer/style/BOM resolution against a real database is
covered by `tests/integration/test_import_api.py`.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, NoReturn

from app.domain.orders.import_csv import (
    EXPECTED_HEADER,
    FILE_LEVEL_ROW,
    MAX_ROWS,
    RowError,
    _Lookups,
    _resolve_valid_row,
    validate_csv,
)

TODAY = date(2026, 1, 1)
ORG_ID = uuid.uuid4()
HEADER_LINE = ",".join(EXPECTED_HEADER)
VALID_ROW = "PO-1,CUST-1,STY-1,10,2099-01-01,3"
EMPTY_LOOKUPS = _Lookups(
    customer_ids_by_code={},
    style_ids_by_code={},
    active_bom_by_style_id={},
    existing_refs=frozenset(),
)


class _UnusedSession:
    """A stand-in proving `validate_csv` never touches the database when
    every row fails before reaching the database-facing checks."""

    async def execute(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError("validate_csv should not query the database for this input")

    async def scalars(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError("validate_csv should not query the database for this input")


def _csv(*lines: str) -> bytes:
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


async def test_header_mismatch_is_a_file_level_error() -> None:
    raw = _csv("wrong,header,here", VALID_ROW)
    result = await validate_csv(_UnusedSession(), raw, organization_id=ORG_ID, today=TODAY)  # type: ignore[arg-type]
    assert not result.ok
    assert result.errors[0].row_number == FILE_LEVEL_ROW
    assert "Header must be exactly" in result.errors[0].message


async def test_bad_date_format() -> None:
    raw = _csv(HEADER_LINE, "PO-1,CUST-1,STY-1,10,not-a-date,3")
    result = await validate_csv(_UnusedSession(), raw, organization_id=ORG_ID, today=TODAY)  # type: ignore[arg-type]
    assert result.errors == [
        RowError(2, "due_date", "due_date must be an ISO 8601 date (YYYY-MM-DD).")
    ]


def test_past_date_is_rejected() -> None:
    error = _resolve_valid_row(
        today=TODAY,
        row_number=2,
        external_ref="PO-1",
        customer_code="CUST-1",
        style_code="STY-1",
        quantity=10,
        due_date=date(2020, 1, 1),
        priority=3,
        seen_refs={},
        lookups=EMPTY_LOOKUPS,
    )
    assert error == RowError(2, "due_date", "Due date must not be in the past.")


async def test_bad_priority() -> None:
    raw = _csv(HEADER_LINE, "PO-1,CUST-1,STY-1,10,2099-01-01,9")
    result = await validate_csv(_UnusedSession(), raw, organization_id=ORG_ID, today=TODAY)  # type: ignore[arg-type]
    assert result.errors == [RowError(2, "priority", "priority must be between 1 and 5.")]


def test_unknown_customer() -> None:
    error = _resolve_valid_row(
        today=TODAY,
        row_number=2,
        external_ref="PO-1",
        customer_code="CUST-1",
        style_code="STY-1",
        quantity=10,
        due_date=date(2099, 1, 1),
        priority=3,
        seen_refs={},
        lookups=EMPTY_LOOKUPS,
    )
    assert error == RowError(2, "customer_code", "Unknown customer code.")


def test_duplicate_ref_in_file() -> None:
    error = _resolve_valid_row(
        today=TODAY,
        row_number=3,
        external_ref="PO-1",
        customer_code="CUST-1",
        style_code="STY-1",
        quantity=10,
        due_date=date(2099, 1, 1),
        priority=3,
        seen_refs={"PO-1": 2},
        lookups=EMPTY_LOOKUPS,
    )
    assert error == RowError(
        3, "external_ref", "Duplicate external_ref in file (first seen on row 2)."
    )


async def test_formula_like_cell_is_rejected() -> None:
    raw = _csv(HEADER_LINE, "=cmd|'/calc',CUST-1,STY-1,10,2099-01-01,3")
    result = await validate_csv(_UnusedSession(), raw, organization_id=ORG_ID, today=TODAY)  # type: ignore[arg-type]
    assert result.errors == [RowError(2, "external_ref", "Formula-like values are not accepted.")]


async def test_formula_like_preview_is_neutralized() -> None:
    raw = _csv(HEADER_LINE, "=cmd|'/calc',CUST-1,STY-1,10,2099-01-01,3")
    result = await validate_csv(_UnusedSession(), raw, organization_id=ORG_ID, today=TODAY)  # type: ignore[arg-type]
    assert result.preview[0]["external_ref"] == "'=cmd|'/calc'"


async def test_space_prefixed_formula_cell_is_rejected() -> None:
    # A leading space hides the formula prefix from a check that only looks
    # at the raw first character; a check that only looks at the *stripped*
    # value would in turn miss a raw leading tab/CR (see the test above).
    # Both the raw and the stripped form must be checked.
    raw = _csv(HEADER_LINE, " =cmd|'/calc',CUST-1,STY-1,10,2099-01-01,3")
    result = await validate_csv(_UnusedSession(), raw, organization_id=ORG_ID, today=TODAY)  # type: ignore[arg-type]
    assert result.errors == [RowError(2, "external_ref", "Formula-like values are not accepted.")]


async def test_every_formula_like_field_in_a_row_is_neutralized_in_preview() -> None:
    # Two formula-like cells in the same row: both must come back neutralized
    # in the preview, not just the first one that triggers the RowError.
    raw = _csv(HEADER_LINE, "=cmd,+CUST-1,STY-1,10,2099-01-01,3")
    result = await validate_csv(_UnusedSession(), raw, organization_id=ORG_ID, today=TODAY)  # type: ignore[arg-type]
    assert result.preview[0]["external_ref"] == "'=cmd"
    assert result.preview[0]["customer_code"] == "'+CUST-1"


async def test_tab_prefixed_cell_is_rejected_as_formula() -> None:
    # A leading TAB (or CR) is itself a formula-injection vector some
    # spreadsheet tools recognize; the check must run before `.strip()`
    # removes it, or it would never match.
    raw = _csv(HEADER_LINE, "PO-1,\tCUST-1,STY-1,10,2099-01-01,3")
    result = await validate_csv(_UnusedSession(), raw, organization_id=ORG_ID, today=TODAY)  # type: ignore[arg-type]
    assert result.errors == [RowError(2, "customer_code", "Formula-like values are not accepted.")]


async def test_row_numbers_follow_reader_line_num_across_multiline_quoted_fields() -> None:
    # A quoted field that itself contains a CRLF spans two physical lines;
    # counting logical records (`enumerate`) instead of the reader's own
    # `line_num` would report the *next* row as one line too early.
    raw = _csv(
        HEADER_LINE,
        '"PO-1\r\nSTILL-ROW-2",CUST-1,STY-1,10,2099-01-01,3',
        "PO-2,CUST-1,STY-1,bad,2099-01-01,3",
    )
    result = await validate_csv(_UnusedSession(), raw, organization_id=ORG_ID, today=TODAY)  # type: ignore[arg-type]
    # Row 1 (the multiline external_ref, which fails the external_ref
    # pattern) ends on physical line 3; row 2 (bad quantity) is on line 4.
    assert [error.row_number for error in result.errors] == [3, 4]
    assert result.errors[0].field == "external_ref"
    assert result.errors[1].field == "quantity"


async def test_more_than_max_rows_is_rejected() -> None:
    rows = [f"PO-{i},CUST-1,STY-1,10,2099-01-01,3" for i in range(MAX_ROWS + 1)]
    raw = _csv(HEADER_LINE, *rows)
    result = await validate_csv(_UnusedSession(), raw, organization_id=ORG_ID, today=TODAY)  # type: ignore[arg-type]
    assert result.errors[0].row_number == FILE_LEVEL_ROW
    assert "more than" in result.errors[0].message
    assert result.row_count == MAX_ROWS + 1


async def test_non_utf8_file_is_rejected() -> None:
    raw = "PO-\xe9,CUST-1,STY-1,10,2099-01-01,3".encode("latin-1")
    result = await validate_csv(_UnusedSession(), raw, organization_id=ORG_ID, today=TODAY)  # type: ignore[arg-type]
    assert result.errors == [RowError(FILE_LEVEL_ROW, None, "File must be UTF-8 encoded.")]


async def test_missing_required_field() -> None:
    raw = _csv(HEADER_LINE, ",CUST-1,STY-1,10,2099-01-01,3")
    result = await validate_csv(_UnusedSession(), raw, organization_id=ORG_ID, today=TODAY)  # type: ignore[arg-type]
    assert result.errors == [RowError(2, "external_ref", "external_ref is required.")]


async def test_oversized_field_is_a_file_level_error() -> None:
    # Python's csv module raises `_csv.Error` once a field exceeds its
    # 131072-character field-size limit; this must become a clean rejected
    # batch, not an unhandled exception.
    huge_field = "A" * 200_000
    raw = _csv(HEADER_LINE, f"{huge_field},CUST-1,STY-1,10,2099-01-01,3")
    result = await validate_csv(_UnusedSession(), raw, organization_id=ORG_ID, today=TODAY)  # type: ignore[arg-type]
    assert result.errors[0].row_number == FILE_LEVEL_ROW
    assert "Could not parse the CSV file" in result.errors[0].message


async def test_row_numbers_survive_blank_lines() -> None:
    # Both rows fail structural parsing (never reach the database-facing
    # lookups), so `_UnusedSession` staying untouched is itself part of what
    # this test proves.
    raw = _csv(
        HEADER_LINE,
        "PO-1,CUST-1,STY-1,bad,2099-01-01,3",
        "",
        "PO-2,CUST-1,STY-1,10,not-a-date,3",
    )
    result = await validate_csv(_UnusedSession(), raw, organization_id=ORG_ID, today=TODAY)  # type: ignore[arg-type]
    # Line 2 = first data row (bad quantity), line 3 = the blank line
    # (consumes a number but produces no row), line 4 = the row with the
    # bad date.
    assert result.row_count == 2
    assert [error.row_number for error in result.errors] == [2, 4]
