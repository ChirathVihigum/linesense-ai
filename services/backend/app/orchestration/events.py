"""Append-only run events (``run_events``), written in the caller's transaction."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RunEvent


async def append_event(
    session: AsyncSession,
    run_id: uuid.UUID,
    event_type: str,
    actor: str,
    payload: dict[str, Any],
) -> RunEvent:
    """Add a ``run_events`` row and flush it (the caller commits).

    ``payload`` must already be JSON-serialisable (use ``model_dump(mode="json")``).
    """
    event = RunEvent(run_id=run_id, event_type=event_type, actor=actor, payload=payload)
    session.add(event)
    await session.flush()
    return event
