"""Grounded order status summary route (task-18-brief.md requirement 4).

Deliberately its own module (not `app/api/orders.py`, which Task 15 owns)
and registered directly in `app/main.py`. Shares
`app.domain.orders.service.latest_order_report` with `order_detail`'s
`latest_report` field, so `GET /orders/{id}.latest_report` and
`GET /orders/{id}/status-summary` always agree on the same report/`stale`
for the same order (see `tests/integration/test_status_summary.py`).

Degrade-gracefully choice (documented, per the task brief: "return 409 or
an empty-summary state — your choice"): when an order has no `run.report`
event at all yet, this route returns 409 `CONFLICT`. An "empty" 200
response would need a fabricated `sentences` list with nothing to cite,
which risks being read as "this order has no issues" rather than "no
analysis has run" — 409 makes the missing precondition explicit to callers
instead.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_principal
from app.api.errors import AppError
from app.api.orders import request_trace_id
from app.api.schemas.summaries import SentenceOut, StatusSummaryOut
from app.audit.service import record_audit
from app.auth.policy import Principal, require
from app.auth.scope import load_scoped
from app.db.models import AuditEvent, Order
from app.db.session import get_db_session
from app.domain.orders.service import latest_order_report
from app.domain.vocab import ActorType, AuditOutcome
from app.llm.factory import build_llm_client
from app.nlp.summarize import DISABLED_LABEL, GroundedSummary, grounded_summary, model_summary

router = APIRouter(prefix="/api/v1", tags=["summaries"])

READ_PERMISSION = "order:read"
MODEL_PERMISSION = "analysis:run"
MODEL_SUMMARY_ACTION = "summary.model_generated"
MODEL_SUMMARY_DAILY_CAP = 10
MODEL_SUMMARY_WINDOW = timedelta(hours=24)


def _to_out(summary: GroundedSummary, *, run_id: uuid.UUID, stale: bool) -> StatusSummaryOut:
    return StatusSummaryOut(
        summary_source=summary.summary_source,
        sentences=[
            SentenceOut(text=sentence.text, evidence_ids=list(sentence.evidence_ids))
            for sentence in summary.sentences
        ],
        report_run_id=run_id,
        stale=stale,
        label=summary.label,
    )


async def _under_daily_cap(
    session: AsyncSession, principal: Principal, order_id: uuid.UUID
) -> bool:
    window_start = datetime.now(UTC) - MODEL_SUMMARY_WINDOW
    count = await session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(
            AuditEvent.organization_id == principal.organization_id,
            AuditEvent.action == MODEL_SUMMARY_ACTION,
            AuditEvent.target_type == "order",
            AuditEvent.target_id == str(order_id),
            AuditEvent.created_at >= window_start,
        )
    )
    return int(count or 0) < MODEL_SUMMARY_DAILY_CAP


@router.get("/orders/{order_id}/status-summary", response_model=StatusSummaryOut)
async def order_status_summary(
    order_id: uuid.UUID,
    request: Request,
    mode: Literal["deterministic", "model"] = Query(default="deterministic"),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> StatusSummaryOut:
    order = await load_scoped(session, Order, order_id, principal, READ_PERMISSION)
    latest = await latest_order_report(session, order)
    if latest is None:
        raise AppError(409, "CONFLICT", "No analysis report is available for this order yet.")
    run_id, report, stale = latest.run_id, latest.payload, latest.stale

    if mode == "deterministic":
        return _to_out(grounded_summary(report), run_id=run_id, stale=stale)

    require(principal, MODEL_PERMISSION, order.factory_id)

    llm = build_llm_client(request.app.state.settings)
    if llm is None:
        # `LS_LLM_PROVIDER=disabled`: no call is ever attempted, so this
        # does not count against the 24h cap or write the model-generated
        # audit event (backend-contracts.md section 8; task-12-brief.md).
        deterministic = grounded_summary(report)
        disabled_summary = GroundedSummary(
            summary_source="deterministic", sentences=deterministic.sentences, label=DISABLED_LABEL
        )
        return _to_out(disabled_summary, run_id=run_id, stale=stale)

    allowed = await _under_daily_cap(session, principal, order.id)

    async def _reserver() -> bool:
        return allowed

    summary = await model_summary(report, llm, _reserver)
    if summary is None:
        raise AppError(
            429,
            "RATE_LIMITED",
            "This order has reached the model summary cap for the last 24 hours.",
            retry_after_seconds=int(MODEL_SUMMARY_WINDOW.total_seconds()),
        )

    # A real call was attempted (accepted as `model` or rejected/unavailable
    # and shown as a labelled deterministic fallback) — either way it
    # consumed the daily cap, so it is recorded regardless of the outcome.
    await record_audit(
        session,
        organization_id=principal.organization_id,
        factory_id=order.factory_id,
        actor_type=ActorType.USER.value,
        actor_id=str(principal.user_id),
        action=MODEL_SUMMARY_ACTION,
        target_type="order",
        target_id=str(order.id),
        outcome=AuditOutcome.SUCCESS.value,
        trace_id=request_trace_id(request),
        run_id=run_id,
    )

    return _to_out(summary, run_id=run_id, stale=stale)
