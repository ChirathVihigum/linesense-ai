from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.middleware import trace_id_var
from app.audit.service import REDACTED, audit_denied, record_audit
from app.db.models import AuditEvent
from tests.factories import make_factory

pytestmark = pytest.mark.integration


async def test_record_audit_persists_all_fields(db_session: AsyncSession) -> None:
    factory = await make_factory(db_session)
    run_id = uuid.uuid4()
    event = await record_audit(
        db_session,
        organization_id=factory.organization_id,
        factory_id=factory.id,
        actor_type="USER",
        actor_id="user-123",
        action="order.create",
        target_type="order",
        target_id="order-9",
        outcome="SUCCESS",
        reason="created from test",
        trace_id="trace-abc",
        run_id=run_id,
        before={"quantity": 1},
        after={"quantity": 2, "nested": [{"note": "ok"}]},
    )
    await db_session.commit()

    stored = await db_session.scalar(select(AuditEvent).where(AuditEvent.id == event.id))
    assert stored is not None
    assert stored.organization_id == factory.organization_id
    assert stored.factory_id == factory.id
    assert stored.actor_type == "USER"
    assert stored.actor_id == "user-123"
    assert stored.action == "order.create"
    assert stored.target_type == "order"
    assert stored.target_id == "order-9"
    assert stored.outcome == "SUCCESS"
    assert stored.reason == "created from test"
    assert stored.trace_id == "trace-abc"
    assert stored.run_id == run_id
    assert stored.before == {"quantity": 1}
    assert stored.after == {"quantity": 2, "nested": [{"note": "ok"}]}
    assert stored.created_at is not None


async def test_sensitive_keys_are_redacted(db_session: AsyncSession) -> None:
    factory = await make_factory(db_session)
    await record_audit(
        db_session,
        organization_id=factory.organization_id,
        factory_id=None,
        actor_type="SYSTEM",
        actor_id="system",
        action="settings.update",
        target_type="settings",
        target_id="1",
        outcome="SUCCESS",
        before={"Password": "hunter2", "name": "x", "list": [{"api_secret": "s"}]},
        after={
            "csrf_token": "abc",
            "nested": {"ACCESS_TOKEN": "t", "keep": 1, "Authorization": "Bearer x"},
            "clientSecret": "c",
            "api-key": "k",
            "token": "raw",
            "cookie": "ls_session=1",
        },
    )
    await db_session.commit()

    stored = await db_session.scalar(select(AuditEvent))
    assert stored is not None
    assert stored.before == {"Password": REDACTED, "name": "x", "list": [{"api_secret": REDACTED}]}
    assert stored.after == {
        "csrf_token": REDACTED,
        "nested": {"ACCESS_TOKEN": REDACTED, "keep": 1, "Authorization": REDACTED},
        "clientSecret": REDACTED,
        "api-key": REDACTED,
        "token": REDACTED,
        "cookie": REDACTED,
    }
    assert REDACTED == "[REDACTED]"


async def test_token_usage_counters_are_not_redacted(db_session: AsyncSession) -> None:
    factory = await make_factory(db_session)
    usage = {
        "input_tokens": 120,
        "output_tokens": 30,
        "max_tokens": 1024,
        "token_count": 150,
        "tokens_used": 150,
        "token_budget": 200000,
        "password_policy": "strong",
    }
    event = await record_audit(
        db_session,
        organization_id=factory.organization_id,
        factory_id=factory.id,
        actor_type="SYSTEM",
        actor_id="worker",
        action="analysis.model_call",
        target_type="analysis_run",
        target_id="r-1",
        outcome="SUCCESS",
        after={"usage": usage},
    )
    assert event.after == {"usage": usage}


async def test_trace_id_defaults_to_request_context(db_session: AsyncSession) -> None:
    factory = await make_factory(db_session)
    token = trace_id_var.set("ctx-trace")
    try:
        event = await record_audit(
            db_session,
            organization_id=factory.organization_id,
            factory_id=factory.id,
            actor_type="USER",
            actor_id="u",
            action="a",
            target_type="t",
            target_id="1",
            outcome="SUCCESS",
        )
    finally:
        trace_id_var.reset(token)
    assert event.trace_id == "ctx-trace"


async def test_audit_denied_commits_independently(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as setup:
        factory = await make_factory(setup)
        await setup.commit()

    user_id = uuid.uuid4()
    # The caller's own transaction is rolled back (as a denied request's would be);
    # the DENIED audit event must survive.
    async with session_factory() as request_session:
        await audit_denied(
            session_factory,
            organization_id=factory.organization_id,
            factory_id=factory.id,
            actor_id=str(user_id),
            action="order.dispatch",
            target_type="order",
            target_id="o-1",
            reason="missing permission order:dispatch",
        )
        await request_session.rollback()

    async with session_factory() as check:
        stored = await check.scalar(select(AuditEvent))
    assert stored is not None
    assert stored.outcome == "DENIED"
    assert stored.actor_type == "USER"
    assert stored.actor_id == str(user_id)
    assert stored.reason == "missing permission order:dispatch"
