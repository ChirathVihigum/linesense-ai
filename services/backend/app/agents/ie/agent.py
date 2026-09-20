"""The industrial engineering (IE) agent.

``assess_line_capability`` answers "what limits each compatible line's output
on this style?" — the bottleneck operation, the line's units per hour, this
model's balance index, and how that compares with the SAM-based capacity the
plan assumes.

Every number comes from ``app.domain.ie`` applied to the run snapshot's
per-line analysis, so a finding and the IE screens can never disagree.

**The agent never reasons about an individual worker.** Cycle observations
reach it already aggregated per operation (sample count, median, staffing);
operator aliases are not in the snapshot, are never requested by a tool, and
must never appear in a summary, finding or action.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from app.agents.base import (
    AgentContext,
    AgentTool,
    Assessment,
    BaseAgent,
    ToolError,
    ToolResult,
)
from app.agents.quantities import decimal_str
from app.domain.ie.calc import LineBalanceResult, line_balance
from app.domain.ie.service import MIN_SAMPLES
from app.orchestration.protocol import (
    DataQuality,
    EvidenceRef,
    Finding,
    Metric,
    RecommendedAction,
)
from app.orchestration.snapshot import SnapshotData, SnapshotLine
from app.retrieval.agent_tool import make_search_documents_tool

ASSESS_TASK_TYPE = "assess_line_capability"
DOCUMENT_SEARCH_DEFAULT_QUERY = "bottleneck escalation line balancing"
# A line whose modelled throughput falls below this share of its SAM-based
# capacity is flagged: the plan assumes the SAM figure.
CAPACITY_WARNING_RATIO = Decimal("0.9")
SUGGESTION_NOTE = (
    "Suggestion only — a supervisor decides. This is about the operation's method, "
    "layout and staffing level, never about an individual operator."
)
_UNITS_PER_HOUR = "units_per_hour"
_SECONDS = "seconds"
_PERCENT = "percent"


# --------------------------------------------------------------------------
# The snapshot's per-line analysis (written by app.domain.ie.service)
# --------------------------------------------------------------------------


class _Lenient(BaseModel):
    # A snapshot written by an older or newer build may carry a key this
    # agent does not know about; the analysis it does know is still usable.
    model_config = ConfigDict(extra="ignore")


class OperationAnalysisView(_Lenient):
    operation_id: uuid.UUID
    code: str
    name: str
    sam_minutes: Decimal
    sample_count: int
    representative_seconds: Decimal | None
    parallel_operators: int
    effective_seconds: Decimal | None
    insufficient_samples: bool


class LineBalanceView(_Lenient):
    effective_cycles: list[Decimal]
    bottleneck_index: int
    bottleneck_effective_seconds: Decimal
    units_per_hour: Decimal
    balance_index_percent: Decimal

    def to_result(self) -> LineBalanceResult:
        return LineBalanceResult(
            effective_cycles=tuple(self.effective_cycles),
            bottleneck_index=self.bottleneck_index,
            bottleneck_effective_seconds=self.bottleneck_effective_seconds,
            units_per_hour=self.units_per_hour,
            balance_index_percent=self.balance_index_percent,
        )


class LineStyleAnalysisView(_Lenient):
    line_id: uuid.UUID
    style_id: uuid.UUID
    operations: list[OperationAnalysisView]
    balance: LineBalanceView | None
    observed_units_per_hour: Decimal | None
    sam_units_per_hour: Decimal | None
    assumptions: list[str]
    limitations: list[str]
    data_versions: dict[str, Any]


@dataclass(frozen=True)
class _LineView:
    """One compatible line's analysis, reduced to what the agent reports."""

    line: SnapshotLine
    analysis: LineStyleAnalysisView
    measured: list[OperationAnalysisView]
    unmeasured: list[OperationAnalysisView]
    balance: LineBalanceResult | None

    @property
    def code(self) -> str:
        return self.line.code

    @property
    def bottleneck(self) -> OperationAnalysisView | None:
        if self.balance is None or not self.measured:
            return None
        return self.measured[self.balance.bottleneck_index]

    @property
    def partial(self) -> bool:
        """The balance covers only the operations that have enough samples."""
        return bool(self.unmeasured)


def _line_view(line: SnapshotLine, analysis: LineStyleAnalysisView) -> _LineView:
    measured = [op for op in analysis.operations if op.effective_seconds is not None]
    unmeasured = [op for op in analysis.operations if op.effective_seconds is None]
    if analysis.balance is not None:
        # Every operation was measured: reuse the stored result verbatim.
        balance: LineBalanceResult | None = analysis.balance.to_result()
    elif measured:
        # Partial coverage: the same domain function over the measured subset.
        balance = line_balance(
            [op.effective_seconds for op in measured if op.effective_seconds is not None]
        )
    else:
        balance = None
    return _LineView(
        line=line, analysis=analysis, measured=measured, unmeasured=unmeasured, balance=balance
    )


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------


def _line_evidence(view: _LineView) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=f"ev-line-{view.code}",
        kind="record",
        record_type="line",
        record_id=view.line.line_id,
        description=(
            f"Line {view.code} ({view.line.name}): {view.line.operator_count} operator "
            f"positions staffed for this style."
        ),
    )


def _operation_evidence(view: _LineView, operation: OperationAnalysisView) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=f"ev-op-{view.code}-{operation.code}",
        kind="record",
        record_type="style_operation",
        record_id=operation.operation_id,
        description=(
            f"{operation.code} ({operation.name}) on {view.code}: {operation.sam_minutes} SAM "
            f"minutes, staffed with {operation.parallel_operators} parallel operator "
            f"position(s), {operation.sample_count} cycle observation(s) considered."
        ),
    )


def _sample_note(operation: OperationAnalysisView) -> str:
    median = operation.representative_seconds
    effective = operation.effective_seconds
    return (
        f"{operation.code}: {operation.sample_count} sample(s)"
        + ("" if median is None else f", median {decimal_str(median)} s")
        + f", {operation.parallel_operators} operator position(s)"
        + ("" if effective is None else f", effective {decimal_str(effective)} s")
    )


def _calculation_evidence(view: _LineView) -> EvidenceRef:
    samples = ", ".join(_sample_note(operation) for operation in view.analysis.operations)
    balance = view.balance
    derivation = (
        "no operation has enough samples, so no cycle could be derived"
        if balance is None
        else (
            f"bottleneck effective cycle = max(effective) = "
            f"{decimal_str(balance.bottleneck_effective_seconds)} s; units_per_hour = 3600 / "
            f"{decimal_str(balance.bottleneck_effective_seconds)} = "
            f"{decimal_str(balance.units_per_hour)}; balance_index = sum(effective) / "
            f"({len(balance.effective_cycles)} x "
            f"{decimal_str(balance.bottleneck_effective_seconds)}) = "
            f"{decimal_str(balance.balance_index_percent)}%"
        )
    )
    return EvidenceRef(
        evidence_id=f"ev-calc-{view.code}",
        kind="calculation",
        description=(
            f"Line {view.code}: {samples}. {derivation}. A representative cycle needs at "
            f"least {MIN_SAMPLES} observations; the median is used. Assumptions: "
            f"{'; '.join(view.analysis.assumptions)}."
        ),
    )


# --------------------------------------------------------------------------
# Tool inputs (defaults bound to this run's own lines)
# --------------------------------------------------------------------------


def _line_input(name: str, default_code: str) -> type[BaseModel]:
    fields: dict[str, Any] = {
        "line_code": (
            str,
            Field(default=default_code, description="A compatible line code in this analysis."),
        )
    }
    return create_model(name, **fields)


def _operation_input(name: str, default_line: str, default_operation: str) -> type[BaseModel]:
    fields: dict[str, Any] = {
        "line_code": (
            str,
            Field(default=default_line, description="A compatible line code in this analysis."),
        ),
        "operation_code": (
            str,
            Field(
                default=default_operation,
                description="An operation code in this style's routing.",
            ),
        ),
    }
    return create_model(name, **fields)


# --------------------------------------------------------------------------
# The agent
# --------------------------------------------------------------------------


class IEAgent(BaseAgent):
    name = "ie"
    prompt_version = "ie-v1"
    goal = (
        "Report what limits each compatible line's output on this style — the bottleneck "
        "operation, its effective cycle and the line's units per hour — and where the "
        "method or staffing of an operation deserves a review. Never judge a person."
    )

    # ------------------------------------------------------------ assessment

    async def assess(self, ctx: AgentContext) -> Assessment:
        data = ctx.snapshot
        builder = _IEAssessment(data)
        views = _views(data)

        for view in views:
            builder.add_line(view)
        for line in _unanalysed_lines(data):
            builder.no_observations(line)

        builder.summary = self._summary(data, views)
        return builder.build()

    def _summary(self, data: SnapshotData, views: list[_LineView]) -> str:
        if not views:
            return (
                f"Order {data.order.external_ref}: no compatible line has cycle observations "
                f"for style {data.order.style_code}, so no line capability could be computed."
            )
        head = (
            f"Order {data.order.external_ref}, style {data.order.style_code}: "
            f"{len(views)} line(s) analysed."
        )
        parts: list[str] = []
        for view in views:
            bottleneck = view.bottleneck
            if bottleneck is None or view.balance is None:
                parts.append(f"{view.code} has no operation with enough samples")
                continue
            parts.append(
                f"{view.code} is limited by {bottleneck.code} at "
                f"{decimal_str(view.balance.bottleneck_effective_seconds)} s effective cycle "
                f"({decimal_str(view.balance.units_per_hour)} units/hour)"
            )
        return f"{head} " + "; ".join(parts) + "."

    # ----------------------------------------------------------------- tools

    def tools(self, ctx: AgentContext) -> list[AgentTool]:
        views = _views(ctx.snapshot)
        default_line = views[0].code if views else ""
        bottleneck = views[0].bottleneck if views else None
        default_operation = bottleneck.code if bottleneck is not None else ""
        return [
            AgentTool(
                name="get_line_analysis",
                description=(
                    "Per-operation cycle data for one compatible line: sample count, median "
                    "and effective cycle seconds, plus the line's bottleneck and throughput. "
                    "Aggregated per operation; never per operator."
                ),
                input_model=_line_input("LineAnalysisInput", default_line),
                handler=_tool_line_analysis,
            ),
            AgentTool(
                name="get_operation_statistics",
                description=(
                    "Cycle-time statistics for one operation on one line: observation count "
                    "and median seconds only. No operator-level data is available."
                ),
                input_model=_operation_input(
                    "OperationStatisticsInput", default_line, default_operation
                ),
                handler=_tool_operation_statistics,
            ),
            AgentTool(
                name="compare_observed_vs_standard",
                description=(
                    "This line's modelled units per hour against its SAM-based capacity and "
                    "its measured output."
                ),
                input_model=_line_input("CompareObservedInput", default_line),
                handler=_tool_compare_observed_vs_standard,
            ),
            make_search_documents_tool(default_query=DOCUMENT_SEARCH_DEFAULT_QUERY),
        ]


def _views(data: SnapshotData) -> list[_LineView]:
    """The analysed compatible lines, in line-code order."""
    by_id = {str(line.line_id): line for line in data.lines}
    views: list[_LineView] = []
    for line_id, payload in data.ie.items():
        line = by_id.get(line_id)
        if line is None:  # pragma: no cover - the snapshot only analyses its own lines
            continue
        views.append(_line_view(line, LineStyleAnalysisView.model_validate(payload)))
    return sorted(views, key=lambda view: view.code)


def _unanalysed_lines(data: SnapshotData) -> list[SnapshotLine]:
    return sorted(
        (line for line in data.lines if line.compatible and str(line.line_id) not in data.ie),
        key=lambda line: line.code,
    )


# --------------------------------------------------------------------------
# Assessment assembly
# --------------------------------------------------------------------------


class _IEAssessment:
    def __init__(self, data: SnapshotData) -> None:
        self.data = data
        self.summary = ""
        self.findings: list[Finding] = []
        self.metrics: list[Metric] = []
        self.actions: list[RecommendedAction] = []
        self.evidence: dict[str, EvidenceRef] = {}
        self.warnings: list[str] = []
        self.notes: list[str] = []
        self.missing: list[str] = []
        self.complete = True

    # ------------------------------------------------------------- primitives

    def add_evidence(self, ref: EvidenceRef) -> str:
        self.evidence.setdefault(ref.evidence_id, ref)
        return ref.evidence_id

    def finding(
        self,
        *,
        finding_id: str,
        severity: Literal["info", "warning", "critical"],
        code: str,
        message: str,
        evidence_ids: list[str],
    ) -> None:
        self.findings.append(
            Finding(
                finding_id=finding_id,
                severity=severity,
                code=code,
                message=message,
                evidence_ids=evidence_ids,
                source="deterministic",
            )
        )

    def metric(
        self, name: str, value: Decimal | None, unit: str, *, note: str | None = None
    ) -> None:
        self.metrics.append(Metric(name=name, value=value, unit=unit, note=note))

    # ----------------------------------------------------------------- pieces

    def add_line(self, view: _LineView) -> None:
        line_evidence = self.add_evidence(_line_evidence(view))
        calculation = self.add_evidence(_calculation_evidence(view))
        balance = view.balance
        bottleneck = view.bottleneck
        partial_note = (
            f"Derived from the {len(view.measured)} of {len(view.analysis.operations)} "
            "operations that have enough cycle observations."
            if view.partial
            else None
        )

        self.metric(
            f"units_per_hour:{view.code}",
            balance.units_per_hour if balance is not None else None,
            _UNITS_PER_HOUR,
            note=partial_note,
        )
        self.metric(
            f"balance_index:{view.code}",
            balance.balance_index_percent if balance is not None else None,
            _PERCENT,
            note=partial_note,
        )
        self.metric(
            f"bottleneck_seconds:{view.code}",
            balance.bottleneck_effective_seconds if balance is not None else None,
            _SECONDS,
            note=partial_note,
        )
        self.metric(
            f"sam_units_per_hour:{view.code}",
            view.analysis.sam_units_per_hour,
            _UNITS_PER_HOUR,
            note=(
                None
                if view.analysis.sam_units_per_hour is not None
                else "The style has no SAM minutes, so no standard capacity could be computed."
            ),
        )

        if balance is None or bottleneck is None:
            self.complete = False
            self.missing.append(view.code)
            self.notes.append(
                f"Line {view.code} has no operation with at least {MIN_SAMPLES} cycle observations."
            )
        else:
            operation_evidence = self.add_evidence(_operation_evidence(view, bottleneck))
            ids = [line_evidence, operation_evidence, calculation]
            self.finding(
                finding_id=f"ie-bottleneck-{view.code}",
                severity="warning",
                code="BOTTLENECK_OPERATION",
                message=(
                    f"On line {view.code}, {bottleneck.code} ({bottleneck.name}) is the "
                    f"bottleneck: an effective cycle of "
                    f"{decimal_str(balance.bottleneck_effective_seconds)} s "
                    f"({decimal_str(bottleneck.representative_seconds or Decimal(0))} s median "
                    f"over {bottleneck.sample_count} observation(s) across "
                    f"{bottleneck.parallel_operators} operator position(s)) limits the line to "
                    f"{decimal_str(balance.units_per_hour)} units/hour, with a balance index of "
                    f"{decimal_str(balance.balance_index_percent)}%."
                ),
                evidence_ids=ids,
            )
            self._capacity(view, balance, bottleneck, ids)
            self._review_action(view, balance, bottleneck, ids)

        if view.unmeasured:
            self.complete = False
            self.missing.append(view.code)
            codes = ", ".join(operation.code for operation in view.unmeasured)
            self.notes.append(f"Line {view.code}: {codes} have fewer than {MIN_SAMPLES} samples.")
            self.finding(
                finding_id=f"ie-samples-{view.code}",
                severity="info",
                code="INSUFFICIENT_SAMPLES",
                message=(
                    f"On line {view.code}, {codes} have fewer than {MIN_SAMPLES} recent cycle "
                    f"observations, so they are excluded from the line's balance; the figures "
                    f"cover the other {len(view.measured)} operation(s)."
                ),
                evidence_ids=[line_evidence, calculation],
            )

    def _capacity(
        self,
        view: _LineView,
        balance: LineBalanceResult,
        bottleneck: OperationAnalysisView,
        ids: list[str],
    ) -> None:
        standard = view.analysis.sam_units_per_hour
        if standard is None or balance.units_per_hour >= CAPACITY_WARNING_RATIO * standard:
            return
        self.finding(
            finding_id=f"ie-capacity-{view.code}",
            severity="warning",
            code="LINE_CAPACITY_BELOW_PLAN",
            message=(
                f"Line {view.code} models {decimal_str(balance.units_per_hour)} units/hour "
                f"against a SAM-based {decimal_str(standard)} units/hour — below the "
                f"{decimal_str(CAPACITY_WARNING_RATIO * 100)}% the plan assumes. "
                f"{bottleneck.code} is the constraint."
            ),
            evidence_ids=ids,
        )

    def _review_action(
        self,
        view: _LineView,
        balance: LineBalanceResult,
        bottleneck: OperationAnalysisView,
        ids: list[str],
    ) -> None:
        self.actions.append(
            RecommendedAction(
                action_id=f"act-ie-review-{view.code}-{bottleneck.code}",
                kind="IE_REVIEW",
                summary=(
                    f"Review method and staffing at {bottleneck.code} on {view.code} "
                    f"(effective cycle "
                    f"{decimal_str(balance.bottleneck_effective_seconds)} s)."
                ),
                payload={
                    "line_code": view.code,
                    "operation_code": bottleneck.code,
                    "effective_cycle_seconds": decimal_str(balance.bottleneck_effective_seconds),
                    "units_per_hour": decimal_str(balance.units_per_hour),
                    "parallel_operators": bottleneck.parallel_operators,
                    "note": SUGGESTION_NOTE,
                },
                evidence_ids=ids,
                rank=len(self.actions),
                source="deterministic",
            )
        )

    def no_observations(self, line: SnapshotLine) -> None:
        self.complete = False
        self.missing.append(line.code)
        message = (
            f"Line {line.code} can run this style but has no recent cycle observations, so "
            "its capability is unknown."
        )
        self.notes.append(message)
        self.finding(
            finding_id=f"ie-no-observations-{line.code}",
            severity="info",
            code="NO_OBSERVATIONS",
            message=message,
            evidence_ids=[
                self.add_evidence(
                    EvidenceRef(
                        evidence_id=f"ev-line-{line.code}",
                        kind="record",
                        record_type="line",
                        record_id=line.line_id,
                        description=(
                            f"Line {line.code} ({line.name}): {line.operator_count} operator "
                            "positions; no cycle observations for this style."
                        ),
                    )
                )
            ],
        )

    def build(self) -> Assessment:
        return Assessment(
            summary=self.summary,
            findings=self.findings,
            metrics=self.metrics,
            recommended_actions=self.actions,
            evidence_refs=list(self.evidence.values()),
            warnings=self.warnings,
            data_quality=DataQuality(
                complete=self.complete, missing=sorted(set(self.missing)), notes=self.notes
            ),
        )


# --------------------------------------------------------------------------
# Tool handlers (snapshot only; aggregated per operation, never per operator)
# --------------------------------------------------------------------------


def _view_by_code(ctx: AgentContext, line_code: str) -> _LineView:
    views = _views(ctx.snapshot)
    view = next((candidate for candidate in views if candidate.code == line_code), None)
    if view is None:
        known = ", ".join(candidate.code for candidate in views) or "none"
        raise ToolError(
            f"No cycle analysis for line {line_code!r} is in this run's snapshot; "
            f"analysed lines: {known}."
        )
    return view


def _operation_row(operation: OperationAnalysisView) -> dict[str, Any]:
    return {
        "operation_code": operation.code,
        "operation_name": operation.name,
        "sam_minutes": decimal_str(operation.sam_minutes),
        "sample_count": operation.sample_count,
        "median_seconds": (
            None
            if operation.representative_seconds is None
            else decimal_str(operation.representative_seconds)
        ),
        "parallel_operators": operation.parallel_operators,
        "effective_seconds": (
            None
            if operation.effective_seconds is None
            else decimal_str(operation.effective_seconds)
        ),
        "insufficient_samples": operation.insufficient_samples,
    }


async def _tool_line_analysis(ctx: AgentContext, arguments: Any) -> ToolResult:
    view = _view_by_code(ctx, arguments.line_code)
    balance = view.balance
    bottleneck = view.bottleneck
    return ToolResult(
        data={
            "line_code": view.code,
            "style_code": ctx.snapshot.order.style_code,
            "operator_positions": view.line.operator_count,
            "operations": [_operation_row(operation) for operation in view.analysis.operations],
            "bottleneck_operation_code": bottleneck.code if bottleneck is not None else None,
            "bottleneck_effective_seconds": (
                None if balance is None else decimal_str(balance.bottleneck_effective_seconds)
            ),
            "units_per_hour": None if balance is None else decimal_str(balance.units_per_hour),
            "balance_index_percent": (
                None if balance is None else decimal_str(balance.balance_index_percent)
            ),
            "assumptions": view.analysis.assumptions,
            "limitations": view.analysis.limitations,
        },
        evidence=[_line_evidence(view), _calculation_evidence(view)],
    )


async def _tool_operation_statistics(ctx: AgentContext, arguments: Any) -> ToolResult:
    view = _view_by_code(ctx, arguments.line_code)
    operation = next(
        (row for row in view.analysis.operations if row.code == arguments.operation_code), None
    )
    if operation is None:
        known = ", ".join(row.code for row in view.analysis.operations) or "none"
        raise ToolError(
            f"Unknown operation {arguments.operation_code!r} on line {view.code}; "
            f"this style has: {known}."
        )
    return ToolResult(
        data={
            "line_code": view.code,
            **_operation_row(operation),
            "note": (
                "The run snapshot records the observation count and the median only; the "
                "observed range is not retained, and no per-operator data exists."
            ),
        },
        evidence=[_operation_evidence(view, operation)],
    )


async def _tool_compare_observed_vs_standard(ctx: AgentContext, arguments: Any) -> ToolResult:
    view = _view_by_code(ctx, arguments.line_code)
    balance = view.balance
    standard = view.analysis.sam_units_per_hour
    modelled = balance.units_per_hour if balance is not None else None
    ratio = (
        None
        if modelled is None or standard is None or standard == 0
        else decimal_str(modelled / standard * 100)
    )
    return ToolResult(
        data={
            "line_code": view.code,
            "modelled_units_per_hour": None if modelled is None else decimal_str(modelled),
            "sam_units_per_hour": None if standard is None else decimal_str(standard),
            "measured_units_per_hour": (
                None
                if view.analysis.observed_units_per_hour is None
                else decimal_str(view.analysis.observed_units_per_hour)
            ),
            "modelled_share_of_standard_percent": ratio,
            "warning_threshold_percent": decimal_str(CAPACITY_WARNING_RATIO * 100),
        },
        evidence=[_line_evidence(view), _calculation_evidence(view)],
    )


__all__ = ["ASSESS_TASK_TYPE", "IEAgent"]
