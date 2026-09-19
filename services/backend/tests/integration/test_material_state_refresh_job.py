"""Integration test for `app.jobs.handlers.handle_refresh_material_states`
(task-7-brief.md fix round 1, controller addition).

`app.domain.orders.service.transition_order` enqueues this job (rather than
recomputing other orders' `material_state` inline) when cancelling an order
releases reservations; see `test_orders_api.py`'s
`test_cancel_releases_allocations_and_reservations` for the enqueue side.
This test exercises the handler itself: claiming the job and running it
actually updates `orders.material_state`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import BomLine, BomVersion, Order, Style
from app.jobs.handlers import MAINTENANCE_REFRESH_MATERIAL_STATES, handle_refresh_material_states
from app.jobs.queue import claim, enqueue
from app.jobs.worker import JobContext
from app.settings import Settings
from tests.factories import make_balance, make_factory, make_material, make_order, make_org

pytestmark = pytest.mark.integration


async def test_handler_updates_material_state(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    async with session_factory() as setup:
        org = await make_org(setup)
        factory = await make_factory(setup, organization=org)
        material = await make_material(setup, organization=org, unit="m")
        balance = await make_balance(
            setup,
            organization=org,
            factory=factory,
            material=material,
            on_hand_accepted=0,
            reserved=0,
        )

        # A style whose one BOM line demands more of `material` than the
        # factory has on hand, so `recompute_material_states` moves the
        # order to `SHORTAGE` — proving the handler actually ran, not just
        # that it didn't error.
        style = Style(
            organization_id=org.id,
            code=f"STY-{uuid.uuid4().hex[:8]}",
            name="shortage style",
            product_type="knit-top",
        )
        setup.add(style)
        await setup.flush()
        bom_version = BomVersion(style_id=style.id, version_no=1, is_active=True)
        setup.add(bom_version)
        await setup.flush()
        setup.add(
            BomLine(
                bom_version_id=bom_version.id,
                material_id=material.id,
                quantity_per_unit=1,
                unit="m",
                wastage_fraction=0,
            )
        )
        await setup.flush()

        order = await make_order(
            setup,
            organization=org,
            factory=factory,
            style=style,
            bom_version=bom_version,
            quantity=10,
        )

        job_id = await enqueue(
            setup,
            queue="maintenance",
            job_type=MAINTENANCE_REFRESH_MATERIAL_STATES,
            payload={"factory_id": str(balance.factory_id), "material_ids": [str(material.id)]},
        )
        assert job_id is not None
        await setup.commit()
        order_id = order.id

    claimed = await claim(
        session_factory, queues=["maintenance"], worker_id="test-worker", lease_seconds=30
    )
    assert claimed is not None
    assert claimed.job_type == MAINTENANCE_REFRESH_MATERIAL_STATES

    ctx = JobContext(
        job=claimed, session_factory=session_factory, settings=settings, worker_id="test-worker"
    )
    await handle_refresh_material_states(ctx)

    async with session_factory() as check:
        refreshed = await check.get(Order, order_id)
    assert refreshed is not None
    assert refreshed.material_state == "SHORTAGE"
