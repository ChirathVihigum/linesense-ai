"""Builders for agent-framework tests: a minimal snapshot and context."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentContext
from app.db.models import AnalysisRun, Order, RunSnapshot, User
from app.llm import LLMClient
from app.orchestration.snapshot import (
    SnapshotBom,
    SnapshotData,
    SnapshotOrder,
    SnapshotQuality,
    SnapshotShipment,
)
from app.settings import Settings

AS_OF = date(2026, 9, 17)
DUE_DATE = date(2026, 9, 25)


def snapshot_data(**overrides: Any) -> SnapshotData:
    """A structurally complete snapshot with no materials, lines or slots."""
    defaults: dict[str, Any] = {
        "order": SnapshotOrder(
            id=uuid.uuid4(),
            version=3,
            external_ref="PO-TEST-001",
            customer_code="C07",
            style_id=uuid.uuid4(),
            style_code="ST-03",
            quantity=1000,
            produced_units=0,
            remaining_units=1000,
            due_date=DUE_DATE,
            priority=2,
            production_state="VALIDATED",
            factory_timezone="Asia/Colombo",
        ),
        "as_of_date": AS_OF,
        "bom": SnapshotBom(bom_version_id=uuid.uuid4(), version_no=1, lines=[]),
        "materials": {},
        "operations": [],
        "sam_total_minutes": Decimal("6.7"),
        "lines": [],
        "slots": [],
        "ie": {},
        "quality": SnapshotQuality(
            policy=None,
            inspections=[],
            active_holds=[],
            releases=[],
            shipment=SnapshotShipment(eligible=False, reasons=["POLICY_UNKNOWN"]),
            quality_state="NOT_INSPECTED",
        ),
    }
    defaults.update(overrides)
    return SnapshotData(**defaults)


def agent_context(
    *,
    llm: LLMClient | None,
    settings: Settings | None = None,
    snapshot: SnapshotData | None = None,
    deadline_seconds: float = 120,
    **overrides: Any,
) -> AgentContext:
    data = snapshot or snapshot_data()
    defaults: dict[str, Any] = {
        "run_id": uuid.uuid4(),
        "task_id": uuid.uuid4(),
        "round": 0,
        "task_type": "assess_material_readiness",
        "organization_id": uuid.uuid4(),
        "factory_id": uuid.uuid4(),
        "order_id": data.order.id,
        "requested_by": uuid.uuid4(),
        "snapshot": data,
        "input_versions": {"order": {str(data.order.id): data.order.version}},
        "dependency_results": {},
        "session_factory": cast("Any", None),
        "llm": llm,
        "settings": settings or Settings(_env_file=None, environment="test"),
        "deadline_at": datetime.now(tz=UTC) + timedelta(seconds=deadline_seconds),
        "requester_roles": frozenset({"planner"}),
    }
    defaults.update(overrides)
    return AgentContext(**defaults)


async def make_run_with_snapshot(
    session: AsyncSession,
    *,
    order: Order | None = None,
    requested_by: User | None = None,
    **run_overrides: Any,
) -> tuple[AnalysisRun, RunSnapshot]:
    """A QUEUED run for ``order`` with a real snapshot built from the database."""
    from app.orchestration.snapshot import build_snapshot
    from tests.factories import make_order, make_run

    order = order or await make_order(session)
    run = await make_run(session, order=order, requested_by=requested_by, **run_overrides)
    data, input_versions = await build_snapshot(session, order)
    snapshot = RunSnapshot(
        id=uuid.uuid4(),
        run_id=run.id,
        organization_id=order.organization_id,
        factory_id=order.factory_id,
        order_id=order.id,
        input_versions=input_versions,
        data=data.model_dump(mode="json"),
    )
    session.add(snapshot)
    await session.flush()
    run.snapshot_id = snapshot.id
    await session.flush()
    return run, snapshot


def envelope_for(
    run: AnalysisRun,
    snapshot: RunSnapshot,
    *,
    recipient: str = "rm",
    task_type: str = "assess_material_readiness",
    round_: int = 0,
    parent_task_id: uuid.UUID | None = None,
    input_refs: list[Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """A dispatch payload for ``run`` (JSON, ready to POST)."""
    from app.orchestration.dispatch import make_envelope
    from app.orchestration.protocol import InputRef

    envelope = make_envelope(
        run,
        recipient=recipient,
        task_type=task_type,
        round=round_,
        parent_task_id=parent_task_id,
        input_refs=(
            input_refs if input_refs is not None else [InputRef(type="snapshot", id=snapshot.id)]
        ),
        deadline_at=run.deadline_at,
    )
    payload = envelope.model_dump(mode="json")
    payload.update(overrides)
    return payload


async def make_requester(
    session: AsyncSession, organization: Any, factory: Any, role: str = "planner"
) -> User:
    """A user with ``role`` on ``factory`` (so ``analysis:run`` re-checks pass)."""
    from app.db.models import Membership, RoleAssignment
    from tests.factories import make_user

    user = await make_user(session)
    membership = Membership(organization_id=organization.id, user_id=user.id)
    session.add(membership)
    await session.flush()
    session.add(RoleAssignment(membership_id=membership.id, factory_id=factory.id, role=role))
    await session.flush()
    return user
