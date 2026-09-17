"""CSV order import: parsing, per-row validation, and preview building.

`validate_csv` parses uploaded bytes and validates every row (existing
customer/style codes, an active BOM, uniqueness) without writing anything;
the `imports/orders` upload route uses it to build a `VALIDATED`/`REJECTED`
`import_batches` preview.

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


def _is_formula_like(value: str) -> bool:
    return bool(value) and value[0] in _FORMULA_PREFIXES


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
    even when validation fails, so a rejected row can still be shown.
    """
    if len(raw_row) != len(EXPECTED_HEADER):
        return (
            dict.fromkeys(EXPECTED_HEADER, ""),
            RowError(
                row_number, None, f"Expected {len(EXPECTED_HEADER)} columns, found {len(raw_row)}."
            ),
        )

    raw = dict(zip(EXPECTED_HEADER, (cell.strip() for cell in raw_row), strict=True))
    preview: dict[str, Any] = dict(raw)

    for field in EXPECTED_HEADER:
        if _is_formula_like(raw[field]):
            return preview, RowError(row_number, field, _FORMULA_MESSAGE)

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


async def _resolve_valid_row(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    today: date,
    row_number: int,
    external_ref: str,
    customer_code: str,
    style_code: str,
    quantity: int,
    due_date: date,
    priority: int,
    seen_refs: dict[str, int],
) -> ValidRow | RowError:
    """The database-facing checks shared by first validation and re-validation at commit."""
    if external_ref in seen_refs:
        return RowError(
            row_number,
            "external_ref",
            f"Duplicate external_ref in file (first seen on row {seen_refs[external_ref]}).",
        )
    seen_refs[external_ref] = row_number

    if due_date < today:
        return RowError(row_number, "due_date", "Due date must not be in the past.")

    customer = await session.scalar(
        select(Customer).where(
            Customer.organization_id == organization_id, Customer.code == customer_code
        )
    )
    if customer is None:
        return RowError(row_number, "customer_code", "Unknown customer code.")

    style = await session.scalar(
        select(Style).where(Style.organization_id == organization_id, Style.code == style_code)
    )
    if style is None:
        return RowError(row_number, "style_code", "Unknown style code.")

    bom_version = await session.scalar(
        select(BomVersion).where(BomVersion.style_id == style.id, BomVersion.is_active.is_(True))
    )
    if bom_version is None:
        return RowError(row_number, "style_code", "Style has no active bill of materials.")

    existing = await session.scalar(
        select(Order.id).where(
            Order.organization_id == organization_id, Order.external_ref == external_ref
        )
    )
    if existing is not None:
        return RowError(
            row_number, "external_ref", "An order with this external reference already exists."
        )

    return ValidRow(
        row_number=row_number,
        external_ref=external_ref,
        customer_code=customer_code,
        customer_id=customer.id,
        style_code=style_code,
        style_id=style.id,
        bom_version_id=bom_version.id,
        quantity=quantity,
        due_date=due_date,
        priority=priority,
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
    or any row-level issue): every problem becomes a `RowError` on the
    returned result so the caller can build a `VALIDATED`/`REJECTED` batch.
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

    if tuple(cell.strip() for cell in header) != EXPECTED_HEADER:
        return _file_level_result(f"Header must be exactly: {','.join(EXPECTED_HEADER)}")

    raw_rows = [row for row in reader if row]
    if len(raw_rows) > MAX_ROWS:
        return _file_level_result(
            f"File has more than {MAX_ROWS} data rows.", row_count=len(raw_rows)
        )

    errors: list[RowError] = []
    valid_rows: list[ValidRow] = []
    preview: list[dict[str, Any]] = []
    seen_refs: dict[str, int] = {}

    for row_number, raw_row in enumerate(raw_rows, start=1):
        normalized, error = _parse_row_fields(raw_row, row_number=row_number)
        if len(preview) < 20:
            preview.append(normalized)
        if error is not None:
            errors.append(error)
            continue

        resolved = await _resolve_valid_row(
            session,
            organization_id=organization_id,
            today=today,
            row_number=row_number,
            external_ref=normalized["external_ref"],
            customer_code=normalized["customer_code"],
            style_code=normalized["style_code"],
            quantity=normalized["quantity"],
            due_date=date.fromisoformat(normalized["due_date"]),
            priority=normalized["priority"],
            seen_refs=seen_refs,
        )
        if isinstance(resolved, RowError):
            errors.append(resolved)
        else:
            valid_rows.append(resolved)

    return ImportValidationResult(
        row_count=len(raw_rows), valid_rows=valid_rows, errors=errors, preview=preview
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
    errors: list[RowError] = []
    valid_rows: list[ValidRow] = []
    seen_refs: dict[str, int] = {}
    for row in stored_rows:
        resolved = await _resolve_valid_row(
            session,
            organization_id=organization_id,
            today=today,
            row_number=row["row_number"],
            external_ref=row["external_ref"],
            customer_code=row["customer_code"],
            style_code=row["style_code"],
            quantity=row["quantity"],
            due_date=date.fromisoformat(row["due_date"]),
            priority=row["priority"],
            seen_refs=seen_refs,
        )
        if isinstance(resolved, RowError):
            errors.append(resolved)
        else:
            valid_rows.append(resolved)
    return ImportValidationResult(
        row_count=len(stored_rows), valid_rows=valid_rows, errors=errors, preview=stored_rows[:20]
    )
