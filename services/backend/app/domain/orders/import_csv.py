"""CSV order import: parsing, per-row validation, and preview building.

`validate_csv` parses uploaded bytes and validates every row (existing
customer/style codes, an active BOM, uniqueness) without writing anything;
the `imports/orders` upload route uses it to build a `VALIDATED`/`REJECTED`
`import_batches` preview. Every customer/style/BOM/existing-external_ref
lookup is done once per file in bulk (`_load_lookups`), not once per row, so
the query count stays constant regardless of row count.

The raw CSV bytes are never stored (backend-contracts.md requirement), so
the commit route cannot re-parse the original file. Instead, the upload
route stores each validated row's normalized fields (see
`ValidRow.to_storage`) in `import_batches.preview`, and the commit route
calls `revalidate_rows` on that stored data to re-run the *database*-facing
checks (codes still resolve, BOM still active, no new duplicate) inside the
commit transaction, exactly as the contract requires ("re-validates all
rows inside the transaction").
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BomVersion, Customer, Order, Style

MAX_FILE_BYTES = 1_048_576
MAX_ROWS = 2_000
EXPECTED_HEADER: tuple[str, ...] = (
    "external_ref",
    "customer_code",
    "style_code",
    "quantity",
    "due_date",
    "priority",
)
TEMPLATE_CSV = (
    "external_ref,customer_code,style_code,quantity,due_date,priority\r\n"
    "PO-1001,CUST-001,STY-001,500,2026-12-31,3\r\n"
)

# The header itself is physical line 1, so a data row's "row_number" is its
# physical line number in the file (a blank line still consumes a line
# number even though it produces no row, so numbers never drift).
FILE_LEVEL_ROW = 0
_EXTERNAL_REF_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]*$")
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_FORMULA_MESSAGE = "Formula-like values are not accepted."


@dataclass(frozen=True)
class RowError:
    row_number: int
    field: str | None
    message: str


@dataclass(frozen=True)
class ValidRow:
    row_number: int
    external_ref: str
    customer_code: str
    customer_id: uuid.UUID
    style_code: str
    style_id: uuid.UUID
    bom_version_id: uuid.UUID
    quantity: int
    due_date: date
    priority: int

    def to_storage(self) -> dict[str, Any]:
        """JSON-safe fields needed by `revalidate_rows` at commit time."""
        return {
            "row_number": self.row_number,
            "external_ref": self.external_ref,
            "customer_code": self.customer_code,
            "style_code": self.style_code,
            "quantity": self.quantity,
            "due_date": self.due_date.isoformat(),
            "priority": self.priority,
        }


@dataclass(frozen=True)
class ImportValidationResult:
    row_count: int
    valid_rows: list[ValidRow]
    errors: list[RowError]
    preview: list[dict[str, Any]]

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class _Lookups:
    """Every organization-scoped fact `_resolve_valid_row` needs, loaded
    once per file/commit instead of once per row."""

    customer_ids_by_code: dict[str, uuid.UUID]
    style_ids_by_code: dict[str, uuid.UUID]
    active_bom_by_style_id: dict[uuid.UUID, uuid.UUID]
    existing_refs: frozenset[str]


def _is_formula_like(value: str) -> bool:
    """True if ``value`` is formula-like either as given or once whitespace
    is stripped -- a *leading-tab/CR* injection vector only shows up
    unstripped, while a *space-padded* formula (`" =cmd"`) only shows up
    once the padding is removed, so both forms must be checked or one of
    the two escapes detection."""
    if not value:
        return False
    if value[0] in _FORMULA_PREFIXES:
        return True
    stripped = value.strip()
    return bool(stripped) and stripped[0] in _FORMULA_PREFIXES


def _neutralize(value: str) -> str:
    """A formula-like value made safe to store/display (spreadsheet apps'
    own convention: a leading `'` forces text, never a formula)."""
    return f"'{value}" if _is_formula_like(value) else value


def _file_level_result(message: str, *, row_count: int = 0) -> ImportValidationResult:
    return ImportValidationResult(
        row_count=row_count,
        valid_rows=[],
        errors=[RowError(FILE_LEVEL_ROW, None, message)],
        preview=[],
    )


def _decode(raw_bytes: bytes) -> str | None:
    try:
        return raw_bytes.decode("utf-8-sig")  # tolerates a UTF-8 BOM
    except UnicodeDecodeError:
        return None


def _parse_row_fields(
    raw_row: list[str], *, row_number: int
) -> tuple[dict[str, Any], RowError | None]:
    """Normalize one CSV row; returns `(preview_row, None)` or `(preview_row, error)`.

    `preview_row` always has string/plain values suitable for a JSON preview,
    even when validation fails, so a rejected row can still be shown — any
    formula-like raw value is neutralized before it is ever stored. The
    formula check runs on the *raw*, unstripped cell (a leading tab or
    carriage return is itself the formula-injection vector some spreadsheet
    tools recognize, and `.strip()` would otherwise remove it first).
    """
    if len(raw_row) != len(EXPECTED_HEADER):
        return (
            dict.fromkeys(EXPECTED_HEADER, ""),
            RowError(
                row_number, None, f"Expected {len(EXPECTED_HEADER)} columns, found {len(raw_row)}."
            ),
        )

    unstripped = dict(zip(EXPECTED_HEADER, raw_row, strict=True))
    formula_fields = [field for field in EXPECTED_HEADER if _is_formula_like(unstripped[field])]

    raw = {field: value.strip() for field, value in unstripped.items()}
    formula_fields_set = set(formula_fields)
    preview: dict[str, Any] = {
        field: (_neutralize(unstripped[field]) if field in formula_fields_set else value)
        for field, value in raw.items()
    }

    if formula_fields:
        return preview, RowError(row_number, formula_fields[0], _FORMULA_MESSAGE)

    if not raw["external_ref"]:
        return preview, RowError(row_number, "external_ref", "external_ref is required.")
    if not (3 <= len(raw["external_ref"]) <= 40) or not _EXTERNAL_REF_RE.match(raw["external_ref"]):
        return preview, RowError(
            row_number,
            "external_ref",
            "external_ref must be 3-40 characters matching ^[A-Z0-9][A-Z0-9-]*$.",
        )
    if not raw["customer_code"]:
        return preview, RowError(row_number, "customer_code", "customer_code is required.")
    if not raw["style_code"]:
        return preview, RowError(row_number, "style_code", "style_code is required.")

    if not raw["quantity"]:
        return preview, RowError(row_number, "quantity", "quantity is required.")
    try:
        quantity = int(raw["quantity"])
    except ValueError:
        return preview, RowError(row_number, "quantity", "quantity must be an integer.")
    if not (1 <= quantity <= 1_000_000):
        return preview, RowError(
            row_number, "quantity", "quantity must be between 1 and 1,000,000."
        )

    if not raw["due_date"]:
        return preview, RowError(row_number, "due_date", "due_date is required.")
    try:
        due_date = date.fromisoformat(raw["due_date"])
    except ValueError:
        return preview, RowError(
            row_number, "due_date", "due_date must be an ISO 8601 date (YYYY-MM-DD)."
        )

    priority_raw = raw["priority"]
    if not priority_raw:
        priority = 3
    else:
        try:
            priority = int(priority_raw)
        except ValueError:
            return preview, RowError(row_number, "priority", "priority must be an integer.")
        if not (1 <= priority <= 5):
            return preview, RowError(row_number, "priority", "priority must be between 1 and 5.")

    preview.update(
        {
            "external_ref": raw["external_ref"],
            "quantity": quantity,
            "due_date": due_date.isoformat(),
            "priority": priority,
        }
    )
    return preview, None


def _resolve_valid_row(
    *,
    today: date,
    row_number: int,
    external_ref: str,
    customer_code: str,
    style_code: str,
    quantity: int,
    due_date: date,
    priority: int,
    seen_refs: dict[str, int],
    lookups: _Lookups,
) -> ValidRow | RowError:
    """The database-facing checks shared by first validation and re-validation
    at commit, against a `_Lookups` snapshot already loaded in bulk."""
    if external_ref in seen_refs:
        return RowError(
            row_number,
            "external_ref",
            f"Duplicate external_ref in file (first seen on row {seen_refs[external_ref]}).",
        )
    seen_refs[external_ref] = row_number

    if due_date < today:
        return RowError(row_number, "due_date", "Due date must not be in the past.")

    customer_id = lookups.customer_ids_by_code.get(customer_code)
    if customer_id is None:
        return RowError(row_number, "customer_code", "Unknown customer code.")

    style_id = lookups.style_ids_by_code.get(style_code)
    if style_id is None:
        return RowError(row_number, "style_code", "Unknown style code.")

    bom_version_id = lookups.active_bom_by_style_id.get(style_id)
    if bom_version_id is None:
        return RowError(row_number, "style_code", "Style has no active bill of materials.")

    if external_ref in lookups.existing_refs:
        return RowError(
            row_number, "external_ref", "An order with this external reference already exists."
        )

    return ValidRow(
        row_number=row_number,
        external_ref=external_ref,
        customer_code=customer_code,
        customer_id=customer_id,
        style_code=style_code,
        style_id=style_id,
        bom_version_id=bom_version_id,
        quantity=quantity,
        due_date=due_date,
        priority=priority,
    )


async def _load_lookups(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    customer_codes: set[str],
    style_codes: set[str],
    external_refs: set[str],
) -> _Lookups:
    customer_ids_by_code: dict[str, uuid.UUID] = {}
    if customer_codes:
        customer_rows = await session.execute(
            select(Customer.code, Customer.id).where(
                Customer.organization_id == organization_id,
                Customer.code.in_(customer_codes),
            )
        )
        customer_ids_by_code = {code: customer_id for code, customer_id in customer_rows}

    style_ids_by_code: dict[str, uuid.UUID] = {}
    if style_codes:
        style_rows = await session.execute(
            select(Style.code, Style.id).where(
                Style.organization_id == organization_id, Style.code.in_(style_codes)
            )
        )
        style_ids_by_code = {code: style_id for code, style_id in style_rows}

    active_bom_by_style_id: dict[uuid.UUID, uuid.UUID] = {}
    if style_ids_by_code:
        bom_rows = await session.execute(
            select(BomVersion.style_id, BomVersion.id).where(
                BomVersion.style_id.in_(style_ids_by_code.values()),
                BomVersion.is_active.is_(True),
            )
        )
        active_bom_by_style_id = {style_id: bom_id for style_id, bom_id in bom_rows}

    existing_refs: frozenset[str] = frozenset()
    if external_refs:
        existing_refs = frozenset(
            (
                await session.scalars(
                    select(Order.external_ref).where(
                        Order.organization_id == organization_id,
                        Order.external_ref.in_(external_refs),
                    )
                )
            ).all()
        )

    return _Lookups(
        customer_ids_by_code=customer_ids_by_code,
        style_ids_by_code=style_ids_by_code,
        active_bom_by_style_id=active_bom_by_style_id,
        existing_refs=existing_refs,
    )


async def validate_csv(
    session: AsyncSession,
    raw_bytes: bytes,
    *,
    organization_id: uuid.UUID,
    today: date,
) -> ImportValidationResult:
    """Parse and validate an orders CSV upload against ``organization_id``.

    Never raises for content problems (bad header/encoding/size/row count,
    a malformed CSV field, or any row-level issue): every problem becomes a
    `RowError` on the returned result so the caller can build a
    `VALIDATED`/`REJECTED` batch.
    """
    if len(raw_bytes) > MAX_FILE_BYTES:
        return _file_level_result("File exceeds the 1 MB limit.")

    text = _decode(raw_bytes)
    if text is None:
        return _file_level_result("File must be UTF-8 encoded.")

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return _file_level_result("File is empty.")
    except csv.Error as exc:
        return _file_level_result(f"Could not parse the CSV file: {exc}")

    if tuple(cell.strip() for cell in header) != EXPECTED_HEADER:
        return _file_level_result(f"Header must be exactly: {','.join(EXPECTED_HEADER)}")

    # Physical line numbers, taken from the reader's own `line_num` (the
    # count of physical lines consumed so far) rather than counting logical
    # records: a quoted field that itself contains a newline spans more than
    # one physical line, and `enumerate()` over logical records would drift
    # out of sync with the file's real line numbers as soon as one appears.
    # A blank line still consumes a number even though it produces no row,
    # so numbers never drift there either. `csv.Error` here (e.g. a field
    # over Python's 128 KiB field-size limit) becomes a file-level error
    # rather than an unhandled exception.
    physical_rows: list[tuple[int, list[str]]] = []
    try:
        for row in reader:
            if row:
                physical_rows.append((reader.line_num, row))
    except csv.Error as exc:
        return _file_level_result(f"Could not parse the CSV file: {exc}")

    if len(physical_rows) > MAX_ROWS:
        return _file_level_result(
            f"File has more than {MAX_ROWS} data rows.", row_count=len(physical_rows)
        )

    parsed: list[tuple[int, dict[str, Any], RowError | None]] = []
    customer_codes: set[str] = set()
    style_codes: set[str] = set()
    external_refs: set[str] = set()
    for row_number, raw_row in physical_rows:
        normalized, error = _parse_row_fields(raw_row, row_number=row_number)
        parsed.append((row_number, normalized, error))
        if error is None:
            customer_codes.add(normalized["customer_code"])
            style_codes.add(normalized["style_code"])
            external_refs.add(normalized["external_ref"])

    lookups = await _load_lookups(
        session,
        organization_id,
        customer_codes=customer_codes,
        style_codes=style_codes,
        external_refs=external_refs,
    )

    errors: list[RowError] = []
    valid_rows: list[ValidRow] = []
    preview: list[dict[str, Any]] = []
    seen_refs: dict[str, int] = {}

    for row_number, normalized, error in parsed:
        if len(preview) < 20:
            preview.append(normalized)
        if error is not None:
            errors.append(error)
            continue

        resolved = _resolve_valid_row(
            today=today,
            row_number=row_number,
            external_ref=normalized["external_ref"],
            customer_code=normalized["customer_code"],
            style_code=normalized["style_code"],
            quantity=normalized["quantity"],
            due_date=date.fromisoformat(normalized["due_date"]),
            priority=normalized["priority"],
            seen_refs=seen_refs,
            lookups=lookups,
        )
        if isinstance(resolved, RowError):
            errors.append(resolved)
        else:
            valid_rows.append(resolved)

    return ImportValidationResult(
        row_count=len(physical_rows), valid_rows=valid_rows, errors=errors, preview=preview
    )


async def revalidate_rows(
    session: AsyncSession,
    stored_rows: list[dict[str, Any]],
    *,
    organization_id: uuid.UUID,
    today: date,
) -> ImportValidationResult:
    """Re-run the database-facing checks for rows stored by an earlier `validate_csv` call.

    Used by the commit route so a batch validated earlier is re-checked
    against the database's current state inside the commit transaction
    (codes still resolve, the BOM is still active, no new duplicate
    external_ref was created meanwhile).
    """
    customer_codes = {row["customer_code"] for row in stored_rows}
    style_codes = {row["style_code"] for row in stored_rows}
    external_refs = {row["external_ref"] for row in stored_rows}
    lookups = await _load_lookups(
        session,
        organization_id,
        customer_codes=customer_codes,
        style_codes=style_codes,
        external_refs=external_refs,
    )

    errors: list[RowError] = []
    valid_rows: list[ValidRow] = []
    seen_refs: dict[str, int] = {}
    for row in stored_rows:
        resolved = _resolve_valid_row(
            today=today,
            row_number=row["row_number"],
            external_ref=row["external_ref"],
            customer_code=row["customer_code"],
            style_code=row["style_code"],
            quantity=row["quantity"],
            due_date=date.fromisoformat(row["due_date"]),
            priority=row["priority"],
            seen_refs=seen_refs,
            lookups=lookups,
        )
        if isinstance(resolved, RowError):
            errors.append(resolved)
        else:
            valid_rows.append(resolved)
    return ImportValidationResult(
        row_count=len(stored_rows), valid_rows=valid_rows, errors=errors, preview=stored_rows[:20]
    )
