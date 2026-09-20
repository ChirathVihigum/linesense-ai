"""The canonical per-order report.

One evidence-backed answer to "where does this order stand?", assembled at
finalization from **deterministic state only**:

* production, quality and shipment come from the run snapshot's own facts,
* the material state is the worst of the RM round-0 materials,
* blockers are the agents' critical findings, deterministic ones first,
* a model's words appear only inside ``agent_summaries``, labelled with the
  provider that produced them — never as a state, a number or a verdict.

The report is appended to the run as a ``run.report`` event and served by
``GET /runs/{id}`` and the order detail's ``latest_report``.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from app.db.models import AgentTask, AnalysisRun, Recommendation
from app.domain.clock import utcnow
from app.domain.inventory.readiness import worst_material_state
from app.domain.vocab import GeneratedBy, MaterialState, TaskStatus
from app.orchestration.protocol import AgentResult
from app.orchestration.snapshot import SnapshotData

SHIPMENT_SOURCE = "Calculated from records"
TASK_FAILED_REASON = "TASK_FAILED"
TASK_CANCELLED_REASON = "TASK_CANCELLED"
NO_RESULT_SUMMARY = "This agent produced no result."

# How a recommendation's provenance is shown next to it.
SOURCE_LABELS = {
    GeneratedBy.MODEL.value: "AI-assisted explanation; every number is calculated",
    GeneratedBy.DETERMINISTIC.value: "Deterministic proposal; AI explanation unavailable",
}

# The RM finding codes that carry a material's deterministic state.
#
# Deferred (review round 1, item 6): these codes are emitted by
# ``app.agents.rm.agent`` from ``app.domain.inventory.calc.material_state`` and
# mapped back here, so the pairing lives in two places. Collapsing it needs the
# RM agent to carry the state on the finding itself (a protocol change), which
# is out of scope for this task.
_MATERIAL_STATE_BY_FINDING: dict[str, MaterialState] = {
    "MATERIAL_SHORTAGE": MaterialState.SHORTAGE,
    "PLAN_MATERIAL_SHORT": MaterialState.SHORTAGE,
    "MATERIAL_AT_RISK": MaterialState.AT_RISK,
    "MATERIAL_BALANCE_MISSING": MaterialState.UNKNOWN,
    "UNIT_CONVERSION_MISSING": MaterialState.UNKNOWN,
}


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


class ReportOrder(BaseModel):
    id: uuid.UUID
    external_ref: str
    due_date: date


class ReportStates(BaseModel):
    production: str
    material: str
    quality: str
    analysis: str


class ReportShipment(BaseModel):
    eligible: bool
    reasons: list[str]
    source: str = SHIPMENT_SOURCE


class ReportBlocker(BaseModel):
    code: str
    message: str
    agent: str
    severity: str
    evidence_ids: list[str]
    source: str


class ReportAgentSummary(BaseModel):
    agent: str
    status: str
    summary: str
    summary_source: str
    provider: str
    model: str
    degraded_reason: str | None
    # Not part of the minimum contract, but what makes a warning (a bottleneck,
    # a demo policy) visible in the report without promoting it to a blocker.
    finding_codes: list[str]


class ReportRecommendation(BaseModel):
    id: uuid.UUID
    status: str
    kind: str
    source_label: str


class OrderReport(BaseModel):
    order: ReportOrder
    states: ReportStates
    shipment: ReportShipment
    blockers: list[ReportBlocker]
    agent_summaries: list[ReportAgentSummary]
    recommendation: ReportRecommendation | None
    evidence: list[dict[str, Any]]
    degraded: bool
    degraded_reasons: list[str]
    generated_at: datetime


# --------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------


def _material_state(
    tasks: Sequence[AgentTask], results: Mapping[uuid.UUID, AgentResult]
) -> MaterialState:
    """The worst state the RM agent's round-0 findings describe.

    Round 1 validates one *plan*, not the order's materials, so it never
    changes what the order detail and this report agree the material state is.
    """
    rm0 = next(
        (task for task in tasks if task.recipient == "rm" and task.round == 0),
        None,
    )
    result = results.get(rm0.id) if rm0 is not None else None
    if result is None or result.status == "FAILED":
        return MaterialState.UNKNOWN
    states = [
        _MATERIAL_STATE_BY_FINDING[finding.code]
        for finding in result.findings
        if finding.source == "deterministic" and finding.code in _MATERIAL_STATE_BY_FINDING
    ]
    if not states:
        return MaterialState.UNKNOWN if not result.data_quality.complete else MaterialState.READY
    return worst_material_state(states)


def _blockers(
    tasks: Sequence[AgentTask], results: Mapping[uuid.UUID, AgentResult]
) -> list[ReportBlocker]:
    blockers: list[ReportBlocker] = []
    for task in tasks:
        result = results.get(task.id)
        if result is None:
            continue
        blockers.extend(
            ReportBlocker(
                code=finding.code,
                message=finding.message,
                agent=result.agent,
                severity=finding.severity,
                evidence_ids=list(finding.evidence_ids),
                source=finding.source,
            )
            for finding in result.findings
            if finding.severity == "critical"
        )
    # Deterministic blockers first; within each group the dispatch order stands.
    return sorted(blockers, key=lambda blocker: blocker.source != "deterministic")


def _degraded_reason(run: AnalysisRun, task: AgentTask, result: AgentResult | None) -> str | None:
    if result is None:
        if task.status == TaskStatus.CANCELLED.value:
            # A task the run itself stopped (deadline, cancellation): report the
            # run's own reason so the report and ``analysis_runs.error_code``
            # never disagree.
            return run.error_code or TASK_CANCELLED_REASON
        return TASK_FAILED_REASON
    if result.status == "FAILED" or task.status == TaskStatus.FAILED.value:
        return (
            result.execution_metadata.degraded_reason
            or (result.error_code.value if result.error_code else None)
            or TASK_FAILED_REASON
        )
    if result.execution_metadata.degraded:
        return (
            result.execution_metadata.degraded_reason
            or (result.error_code.value if result.error_code else None)
            or result.status
        )
    return None


def _agent_summaries(
    run: AnalysisRun, tasks: Sequence[AgentTask], results: Mapping[uuid.UUID, AgentResult]
) -> list[ReportAgentSummary]:
    summaries: list[ReportAgentSummary] = []
    for task in tasks:
        result = results.get(task.id)
        metadata = result.execution_metadata if result is not None else None
        summaries.append(
            ReportAgentSummary(
                agent=task.recipient,
                status=result.status if result is not None else task.status,
                summary=result.summary if result is not None else NO_RESULT_SUMMARY,
                summary_source=result.summary_source if result is not None else "deterministic",
                provider=metadata.provider if metadata is not None else run.llm_provider,
                model=metadata.model if metadata is not None else run.llm_model,
                degraded_reason=_degraded_reason(run, task, result),
                finding_codes=(
                    [finding.code for finding in result.findings] if result is not None else []
                ),
            )
        )
    return summaries


def _evidence(
    tasks: Sequence[AgentTask], results: Mapping[uuid.UUID, AgentResult]
) -> list[dict[str, Any]]:
    """Every evidence reference the run produced, once per agent."""
    seen: set[tuple[str, str]] = set()
    items: list[dict[str, Any]] = []
    for task in tasks:
        result = results.get(task.id)
        if result is None:
            continue
        for ref in result.evidence_refs:
            key = (result.agent, ref.evidence_id)
            if key in seen:
                continue
            seen.add(key)
            items.append({"agent": result.agent, **ref.model_dump(mode="json")})
    return items


def build_order_report(
    run: AnalysisRun,
    snapshot: SnapshotData,
    results_by_task: Mapping[uuid.UUID, AgentResult],
    recommendation: Recommendation | None,
    *,
    tasks: Sequence[AgentTask],
) -> OrderReport:
    """The run's canonical report, built from deterministic state only."""
    summaries = _agent_summaries(run, tasks, results_by_task)
    reasons = sorted({summary.degraded_reason for summary in summaries if summary.degraded_reason})
    return OrderReport(
        order=ReportOrder(
            id=snapshot.order.id,
            external_ref=snapshot.order.external_ref,
            due_date=snapshot.order.due_date,
        ),
        states=ReportStates(
            production=snapshot.order.production_state,
            material=_material_state(tasks, results_by_task).value,
            quality=snapshot.quality.quality_state,
            analysis=run.status,
        ),
        shipment=ReportShipment(
            eligible=snapshot.quality.shipment.eligible,
            reasons=list(snapshot.quality.shipment.reasons),
            source=SHIPMENT_SOURCE,
        ),
        blockers=_blockers(tasks, results_by_task),
        agent_summaries=summaries,
        recommendation=(
            None
            if recommendation is None
            else ReportRecommendation(
                id=recommendation.id,
                status=recommendation.status,
                kind=recommendation.kind,
                source_label=SOURCE_LABELS.get(
                    recommendation.generated_by, recommendation.generated_by
                ),
            )
        ),
        evidence=_evidence(tasks, results_by_task),
        degraded=bool(reasons),
        degraded_reasons=reasons,
        generated_at=utcnow(),
    )


__all__ = ["OrderReport", "build_order_report"]
