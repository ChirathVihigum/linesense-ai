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
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
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


def _tagged(value: Any) -> Any:
    """Encode ``value`` as ``[type, value]`` so distinct types never collide.

    Without tags, ``Decimal("1")``, ``"1"`` and ``1`` would be indistinguishable
    once serialized (and a user dict could imitate any ad-hoc type marker).
    """
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", value]
    if isinstance(value, float):
        return ["float", value]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, Decimal):
        return ["decimal", str(value)]
    if isinstance(value, uuid.UUID):
        return ["uuid", str(value)]
    if isinstance(value, datetime):
        return ["datetime", value.isoformat()]
    if isinstance(value, date):
        return ["date", value.isoformat()]
    if isinstance(value, dict):
        items: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"request payload keys must be str, got {type(key).__name__}")
            items[key] = _tagged(item)
        return ["object", items]
    if isinstance(value, list | tuple):
        return ["array", [_tagged(item) for item in value]]
    raise TypeError(f"unsupported request payload value of type {type(value).__name__}")


def request_hash(payload: Any) -> str:
    """SHA-256 of the canonical, type-tagged JSON encoding of ``payload``."""
    canonical = json.dumps(_tagged(payload), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _scope(organization_id: uuid.UUID, actor_id: str, operation: str, key: str) -> list[Any]:
    return [
        IdempotencyKey.organization_id == organization_id,
        IdempotencyKey.actor_id == actor_id,
        IdempotencyKey.operation == operation,
        IdempotencyKey.key == key,
    ]


_MAX_CLAIM_ATTEMPTS = 3


async def _insert_pending(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    actor_id: str,
    operation: str,
    key: str,
    digest: str,
    now: datetime,
) -> bool:
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
    return inserted is not None


async def _lock_existing(
    session: AsyncSession, *, organization_id: uuid.UUID, actor_id: str, operation: str, key: str
) -> IdempotencyKey | None:
    return (
        await session.execute(
            select(IdempotencyKey)
            .where(*_scope(organization_id, actor_id, operation, key))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


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
    # Python clock: ``expires_at`` only needs coarse accuracy. The reconciler
    # deletes keys one hour after expiry (``PURGE_GRACE``), which absorbs any
    # realistic skew between this host and the database clock.
    now = datetime.now(tz=UTC)
    row: IdempotencyKey | None = None
    for _ in range(_MAX_CLAIM_ATTEMPTS):
        inserted = await _insert_pending(
            session,
            organization_id=organization_id,
            actor_id=actor_id,
            operation=operation,
            key=key,
            digest=digest,
            now=now,
        )
        if inserted:
            return None
        # The conflicting row can vanish (purged by the reconciler) between
        # the INSERT and this SELECT; then simply try the INSERT again.
        row = await _lock_existing(
            session,
            organization_id=organization_id,
            actor_id=actor_id,
            operation=operation,
            key=key,
        )
        if row is not None:
            break
    if row is None:
        raise RuntimeError("idempotency key kept disappearing while being claimed")

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
