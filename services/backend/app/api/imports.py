"""CSV order import routes (backend-contracts.md section 5; task-7-brief.md).

The raw upload is parsed in memory and never stored; see
`app.domain.orders.import_csv` for how the commit route re-validates a
batch's rows without the original file.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any, cast

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_principal, require_idempotency_key
from app.api.errors import AppError
from app.api.orders import (
    audit_denial_from_error,
    idempotent_finish,
    idempotent_start,
    request_trace_id,
)
from app.audit.service import record_audit
from app.auth.policy import Principal
from app.auth.scope import load_scoped
from app.db.models import Factory, ImportBatch, ImportRowError, Order
from app.db.session import get_db_session
from app.domain import clock
from app.domain.orders.import_csv import MAX_FILE_BYTES, TEMPLATE_CSV, revalidate_rows, validate_csv
from app.domain.vocab import (
    ActorType,
    AuditOutcome,
    ImportBatchStatus,
    MaterialState,
    OrderSource,
    ProductionState,
    QualityState,
)

router = APIRouter(prefix="/api/v1", tags=["imports"])

IMPORT_KIND = "orders"
ALREADY_IMPORTED_MESSAGE = "This file was already imported."


class ImportRowErrorOut(BaseModel):
    row_number: int
    field: str | None
    message: str


class ImportBatchOut(BaseModel):
    batch_id: uuid.UUID
    status: str
    row_count: int
    errors: list[ImportRowErrorOut]
    preview: list[dict[str, Any]]
    created_at: datetime
    committed_at: datetime | None


async def _to_batch_out(session: AsyncSession, batch: ImportBatch) -> ImportBatchOut:
    error_rows = (
        await session.scalars(
            select(ImportRowError)
            .where(ImportRowError.batch_id == batch.id)
            .order_by(ImportRowError.row_number)
        )
    ).all()
    preview_data = batch.preview if isinstance(batch.preview, dict) else {}
    display = preview_data.get("display", [])
    return ImportBatchOut(
        batch_id=batch.id,
        status=batch.status,
        row_count=batch.row_count,
        errors=[
            ImportRowErrorOut(row_number=row.row_number, field=row.field, message=row.message)
            for row in error_rows
        ],
        preview=display,
        created_at=batch.created_at,
        committed_at=batch.committed_at,
    )


async def _already_committed(
    session: AsyncSession, *, organization_id: uuid.UUID, file_sha256: str
) -> bool:
    existing = await session.scalar(
        select(ImportBatch.id).where(
            ImportBatch.organization_id == organization_id,
            ImportBatch.kind == IMPORT_KIND,
            ImportBatch.file_sha256 == file_sha256,
            ImportBatch.status == ImportBatchStatus.COMMITTED.value,
        )
    )
    return existing is not None


@router.get("/imports/templates/orders.csv", response_class=PlainTextResponse)
async def orders_csv_template(principal: Principal = Depends(get_principal)) -> PlainTextResponse:
    return PlainTextResponse(content=TEMPLATE_CSV, media_type="text/csv")


@router.post(
    "/factories/{factory_id}/imports/orders", response_model=ImportBatchOut, status_code=201
)
async def upload_orders_csv(
    factory_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    try:
        factory = await load_scoped(session, Factory, factory_id, principal, "order:import")
    except AppError as exc:
        await audit_denial_from_error(
            request,
            principal,
            exc,
            factory_id=factory_id,
            action="order.import.validate",
            target_type="import_batch",
            target_id=str(factory_id),
        )
        raise

    # Read at most one byte past the limit rather than the whole upload, so
    # an oversized file is never fully buffered into memory.
    raw_bytes = await file.read(MAX_FILE_BYTES + 1)
    if len(raw_bytes) > MAX_FILE_BYTES:
        raise AppError(413, "PAYLOAD_TOO_LARGE", "File exceeds the 1 MB limit.")
    file_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    early = await idempotent_start(
        session,
        principal,
        operation="order:import",
        key=idempotency_key,
        request_payload={"factory_id": str(factory_id), "file_sha256": file_sha256},
    )
    if early is not None:
        return early

    if await _already_committed(
        session, organization_id=principal.organization_id, file_sha256=file_sha256
    ):
        raise AppError(409, "CONFLICT", ALREADY_IMPORTED_MESSAGE)

    result = await validate_csv(
        session,
        raw_bytes,
        organization_id=principal.organization_id,
        today=clock.today_in(factory.timezone),
    )
    status_value = (
        ImportBatchStatus.VALIDATED.value if result.ok else ImportBatchStatus.REJECTED.value
    )

    batch = ImportBatch(
        organization_id=principal.organization_id,
        factory_id=factory.id,
        kind=IMPORT_KIND,
        status=status_value,
        file_sha256=file_sha256,
        row_count=result.row_count,
        preview={
            "display": result.preview,
            "valid_rows": [row.to_storage() for row in result.valid_rows],
        },
        created_by=principal.user_id,
    )
    session.add(batch)
    await session.flush()
    for error in result.errors:
        session.add(
            ImportRowError(
                batch_id=batch.id,
                row_number=error.row_number,
                field=error.field,
                message=error.message,
            )
        )
    await session.flush()

    await record_audit(
        session,
        organization_id=principal.organization_id,
        factory_id=factory.id,
        actor_type=ActorType.USER.value,
        actor_id=str(principal.user_id),
        action="order.import.validate",
        target_type="import_batch",
        target_id=str(batch.id),
        outcome=AuditOutcome.SUCCESS.value,
        trace_id=request_trace_id(request),
        after={
            "status": status_value,
            "row_count": result.row_count,
            "error_count": len(result.errors),
        },
    )

    body = await _to_batch_out(session, batch)
    return await idempotent_finish(
        session,
        principal,
        operation="order:import",
        key=idempotency_key,
        status_code=201,
        body=body,
    )


@router.get("/imports/{batch_id}", response_model=ImportBatchOut)
async def get_import_batch(
    batch_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> ImportBatchOut:
    batch = await load_scoped(session, ImportBatch, batch_id, principal, "order:import")
    return await _to_batch_out(session, batch)


@router.post("/imports/{batch_id}/commit", response_model=ImportBatchOut)
async def commit_import(
    batch_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    try:
        batch = await load_scoped(session, ImportBatch, batch_id, principal, "order:import")
    except AppError as exc:
        factory_id = await session.scalar(
            select(ImportBatch.factory_id).where(ImportBatch.id == batch_id)
        )
        await audit_denial_from_error(
            request,
            principal,
            exc,
            factory_id=factory_id,
            action="order.import.commit",
            target_type="import_batch",
            target_id=str(batch_id),
        )
        raise

    early = await idempotent_start(
        session,
        principal,
        operation="order:import:commit",
        key=idempotency_key,
        request_payload={"batch_id": str(batch_id)},
    )
    if early is not None:
        return early

    # Lock the batch row so two concurrent commits of the *same* batch
    # serialize: the loser waits here, then sees status == COMMITTED below
    # and gets a clean 409 instead of racing on the orders it would insert.
    batch = (
        await session.scalars(
            select(ImportBatch)
            .where(ImportBatch.id == batch.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one()

    if batch.status != ImportBatchStatus.VALIDATED.value:
        raise AppError(409, "CONFLICT", f"Import batch is {batch.status}, not VALIDATED.")
    if await _already_committed(
        session, organization_id=principal.organization_id, file_sha256=batch.file_sha256
    ):
        raise AppError(409, "CONFLICT", ALREADY_IMPORTED_MESSAGE)

    factory = await session.get(Factory, batch.factory_id)
    if factory is None:
        raise RuntimeError("import batch references a missing factory")

    preview_data = batch.preview if isinstance(batch.preview, dict) else {}
    stored_rows = cast("list[dict[str, Any]]", preview_data.get("valid_rows", []))
    result = await revalidate_rows(
        session,
        stored_rows,
        organization_id=principal.organization_id,
        today=clock.today_in(factory.timezone),
    )
    if not result.ok:
        raise AppError(
            409,
            "CONFLICT",
            "One or more rows are no longer valid; no orders were created.",
            field_errors=[
                {"field": f"row {error.row_number}: {error.field}", "message": error.message}
                for error in result.errors
            ],
        )

    for row in result.valid_rows:
        session.add(
            Order(
                organization_id=principal.organization_id,
                factory_id=factory.id,
                customer_id=row.customer_id,
                style_id=row.style_id,
                bom_version_id=row.bom_version_id,
                external_ref=row.external_ref,
                quantity=row.quantity,
                due_date=row.due_date,
                priority=row.priority,
                production_state=ProductionState.DRAFT.value,
                material_state=MaterialState.UNKNOWN.value,
                quality_state=QualityState.NOT_INSPECTED.value,
                source=OrderSource.CSV_IMPORT.value,
                created_by=principal.user_id,
            )
        )
    created = len(result.valid_rows)
    batch.status = ImportBatchStatus.COMMITTED.value
    batch.committed_at = clock.utcnow()
    try:
        await session.flush()
    except IntegrityError as exc:
        # A concurrent commit (of a *different* batch of the same file, or a
        # concurrent order create) slipped in between `revalidate_rows` and
        # this flush; the unique constraints (external_ref, and the
        # committed-file-hash partial index) are the actual race-free guard.
        raise AppError(
            409,
            "CONFLICT",
            "One or more orders could not be created; a conflicting order or import was "
            "committed concurrently. No orders were created.",
        ) from exc

    await record_audit(
        session,
        organization_id=principal.organization_id,
        factory_id=factory.id,
        actor_type=ActorType.USER.value,
        actor_id=str(principal.user_id),
        action="order.import.commit",
        target_type="import_batch",
        target_id=str(batch.id),
        outcome=AuditOutcome.SUCCESS.value,
        trace_id=request_trace_id(request),
        after={"status": batch.status, "orders_created": created},
    )

    body = await _to_batch_out(session, batch)
    return await idempotent_finish(
        session,
        principal,
        operation="order:import:commit",
        key=idempotency_key,
        status_code=200,
        body=body,
    )
