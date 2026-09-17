"""``Idempotency-Key`` handling (backend-contracts.md section 5).

Usage inside a command's transaction::

    stored = await begin(session, organization_id=..., actor_id=..., operation=...,
                         key=key, request_payload=body)
    if stored is not None:
        return JSONResponse(stored.body, status_code=stored.status_code)
    ... perform the command ...
    await finish(session, ..., status_code=201, body=response_body)

``begin`` inserts a pending row with ``INSERT ... ON CONFLICT DO NOTHING``;
a concurrent duplicate blocks on the uncommitted row until the first
transaction ends, then re-selects it and replays the stored response.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.models import IdempotencyKey

RETENTION = timedelta(hours=24)


@dataclass(frozen=True)
class StoredResponse:
    status_code: int
    body: dict[str, Any]


def request_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _scope(organization_id: uuid.UUID, actor_id: str, operation: str, key: str) -> list[Any]:
    return [
        IdempotencyKey.organization_id == organization_id,
        IdempotencyKey.actor_id == actor_id,
        IdempotencyKey.operation == operation,
        IdempotencyKey.key == key,
    ]


async def begin(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    actor_id: str,
    operation: str,
    key: str,
    request_payload: Any,
) -> StoredResponse | None:
    """Claim ``key`` for this request, or return the stored response of a replay.

    Raises 409 ``IDEMPOTENCY_KEY_REUSED`` when the key was used with a
    different payload, and 409 ``CONFLICT`` while the original request is
    still in progress (no stored response yet).
    """
    digest = request_hash(request_payload)
    now = datetime.now(tz=UTC)
    inserted = await session.scalar(
        insert(IdempotencyKey)
        .values(
            id=uuid.uuid4(),
            organization_id=organization_id,
            actor_id=actor_id,
            operation=operation,
            key=key,
            request_hash=digest,
            expires_at=now + RETENTION,
        )
        .on_conflict_do_nothing(index_elements=["organization_id", "actor_id", "operation", "key"])
        .returning(IdempotencyKey.id)
    )
    if inserted is not None:
        return None

    row = (
        await session.execute(
            select(IdempotencyKey)
            .where(*_scope(organization_id, actor_id, operation, key))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()

    if row.expires_at <= now:
        # Past retention: the old key no longer protects anything; start fresh.
        row.request_hash = digest
        row.response_status = None
        row.response_body = None
        row.created_at = now
        row.expires_at = now + RETENTION
        await session.flush()
        return None

    if row.request_hash != digest:
        raise AppError(
            409,
            "IDEMPOTENCY_KEY_REUSED",
            "This Idempotency-Key was already used with a different request.",
        )
    if row.response_status is None or row.response_body is None:
        raise AppError(409, "CONFLICT", "A request with this Idempotency-Key is in progress.")
    return StoredResponse(status_code=row.response_status, body=dict(row.response_body))


async def finish(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    actor_id: str,
    operation: str,
    key: str,
    status_code: int,
    body: dict[str, Any],
) -> None:
    """Store the response for ``key`` (in the command's transaction)."""
    result = await session.execute(
        update(IdempotencyKey)
        .where(*_scope(organization_id, actor_id, operation, key))
        .values(response_status=status_code, response_body=body)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise RuntimeError("finish() called for an idempotency key that begin() did not claim")
