"""The production planning agent.

Round 0 (``propose_allocation``) offers a ranked set of allocation options for
the order's remaining units; round 1 (``revise_allocation``) re-proposes them
with the RM agent's material limit made binding.

Every option is produced by ``app.domain.planning.calc.plan_earliest_slots``
over the run snapshot's capacity slots: the agent chooses *which* constraints
to try, never what a plan works out to.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.base import (
    AgentContext,
    AgentTool,
    Assessment,
    BaseAgent,
    ToolError,
    ToolResult,
)
from app.agents.quantities import decimal_str, quantize_quantity
from app.domain.planning.calc import (
    AllocationPlan,
    SlotCapacity,
    plan_earliest_slots,
    required_standard_minutes,
    utilization,
)
from app.orchestration.protocol import (
    AgentResult,
    DataQuality,
    EvidenceRef,
    Finding,
    Metric,
    RecommendedAction,
)
from app.orchestration.snapshot import SnapshotData, SnapshotSlot

PROPOSE_TASK_TYPE = "propose_allocation"
REVISE_TASK_TYPE = "revise_allocation"

FULL_EARLIEST = "FULL_EARLIEST"
MATERIAL_LIMITED = "MATERIAL_LIMITED"
SINGLE_LINE = "SINGLE_LINE"
SIMULATED = "SIMULATED"

COVERABLE_UNITS_METRIC = "coverable_units"
IE_RISK_FINDING_CODE = "LINE_CAPACITY_BELOW_PLAN"
OVERCOMMIT_THRESHOLD = Decimal("0.95")
_UNITS = "units"
_MINUTES = "standard_minutes"
_USABLE_STATUSES = ("SUCCEEDED", "DEGRADED")


# --------------------------------------------------------------------------
# Candidate options
# --------------------------------------------------------------------------


@dataclass
class _Option:
    code: str
    plan: AllocationPlan
    line_codes: tuple[str, ...]
    ie_risk: bool = False
    action_id: str = ""
    evidence_ids: list[str] = field(default_factory=list)

    @property
    def is_full(self) -> bool:
        return self.plan.unscheduled_units <= 0

    @property
    def fingerprint(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (str(allocation.slot_id), decimal_str(allocation.units))
            for allocation in self.plan.allocations
        )


def _slot_capacities(data: SnapshotData) -> list[SlotCapacity]:
    return [
        SlotCapacity(
            slot_id=slot.slot_id,
            line_id=slot.line_id,
            slot_date=slot.slot_date,
            shift_code=slot.shift_code,
            available_operator_minutes=slot.available_operator_minutes,
            planned_efficiency=slot.planned_efficiency,
            allocated_standard_minutes=slot.allocated_standard_minutes,
        )
        for slot in data.slots
    ]


def _remaining_minutes(data: SnapshotData, line_id: uuid.UUID) -> Decimal:
    return sum(
        (
            capacity.remaining_standard_minutes
            for capacity, slot in zip(_slot_capacities(data), data.slots, strict=True)
            if slot.line_id == line_id and data.as_of_date <= slot.slot_date <= data.order.due_date
        ),
        Decimal(0),
    )


def _plan(
    data: SnapshotData,
    *,
    line_ids: frozenset[uuid.UUID],
    max_units: Decimal | None = None,
) -> AllocationPlan:
    return plan_earliest_slots(
        units=data.order.remaining_units,
        sam_minutes_per_unit=data.sam_total_minutes,
        slots=_slot_capacities(data),
        earliest_date=data.as_of_date,
        due_date=data.order.due_date,
        compatible_line_ids=line_ids,
        max_units=max_units,
    )


def _option(data: SnapshotData, code: str, plan: AllocationPlan) -> _Option:
    codes = {slot.line_id: slot.line_code for slot in data.slots}
    used = sorted({codes.get(allocation.line_id, "?") for allocation in plan.allocations})
    return _Option(code=code, plan=plan, line_codes=tuple(used))


def _rank_key(option: _Option, *, prefer: str | None) -> tuple[Any, ...]:
    return (
        0 if prefer is not None and option.code == prefer else 1,
        0 if option.is_full else 1,
        option.plan.finish_date or date.max,
        len(option.line_codes),
        1 if option.ie_risk else 0,
        option.code,
    )


# --------------------------------------------------------------------------
# Dependency helpers
# --------------------------------------------------------------------------


def _usable(result: AgentResult | None) -> AgentResult | None:
    return result if result is not None and result.status in _USABLE_STATUSES else None


def _coverable_units(rm: AgentResult | None) -> Decimal | None:
    if rm is None:
        return None
    for metric in rm.metrics:
        if metric.name == COVERABLE_UNITS_METRIC:
            return metric.value
    return None


def _risky_line_codes(ie: AgentResult | None, data: SnapshotData) -> set[str]:
    """Line codes the IE agent flagged with ``LINE_CAPACITY_BELOW_PLAN``.

    The IE result names its lines either through ``line`` record evidence or,
    failing that, by their code in the message; both are matched against the
    snapshot so nothing outside this run's lines is ever flagged.
    """
    if ie is None:
        return set()
    by_id = {line.line_id: line.code for line in data.lines}
    evidence = {ref.evidence_id: ref for ref in ie.evidence_refs}
    risky: set[str] = set()
    for finding in ie.findings:
        if finding.code != IE_RISK_FINDING_CODE:
            continue
        for evidence_id in finding.evidence_ids:
            ref = evidence.get(evidence_id)
            if ref is not None and ref.record_type == "line" and ref.record_id in by_id:
                risky.add(by_id[ref.record_id])
        risky.update(line.code for line in data.lines if line.code in finding.message.split())
    return risky


# --------------------------------------------------------------------------
# Tool inputs
# --------------------------------------------------------------------------


class DependencyFindingsInput(BaseModel):
    agent: Literal["rm", "ie"] = Field(
        default="rm", description="Which dependency's findings to read."
    )


class CompatibleLinesInput(BaseModel):
    """No arguments."""


class RemainingCapacityInput(BaseModel):
    line_code: str | None = Field(
        default=None, description="One line code, or null for every compatible line."
    )


class SimulateAllocationInput(BaseModel):
    max_units: int | None = Field(
        default=None, description="Cap the plan at this many units (e.g. a material limit)."
    )
    line_codes: list[str] | None = Field(
        default=None, description="Restrict the plan to these compatible line codes."
    )


# --------------------------------------------------------------------------
# The agent
# --------------------------------------------------------------------------


class PlanningAgent(BaseAgent):
    name = "planning"
    prompt_version = "planning-v1"
    goal = (
        "Propose how to allocate the order's remaining units to capacity slots before "
        "its due date, honouring the material limit the RM agent reported."
    )

    async def assess(self, ctx: AgentContext) -> Assessment:
        data = ctx.snapshot
        revise = ctx.task_type == REVISE_TASK_TYPE
        builder = _PlanningAssessment(data)

        rm = _usable(ctx.dependency_results.get("rm"))
        ie = _usable(ctx.dependency_results.get("ie"))
        if rm is None:
            builder.missing("rm", mandatory=revise)
        if ie is None:
            builder.missing("ie", mandatory=False)

        compatible = [line for line in data.lines if line.compatible]
        required = (
            required_standard_minutes(data.order.remaining_units, data.sam_total_minutes)
            if data.sam_total_minutes > 0
            else Decimal(0)
        )
        builder.metric("required_standard_minutes", required, _MINUTES)

        if not compatible or data.sam_total_minutes <= 0:
            builder.no_compatible_line()
            builder.metric("allocated_units", Decimal(0), _UNITS)
            builder.metric("unscheduled_units", Decimal(data.order.remaining_units), _UNITS)
            builder.summary = (
                f"Order {data.order.external_ref}: no active line can run style "
                f"{data.order.style_code}, so no allocation can be proposed."
            )
            return builder.build()

        coverable = _coverable_units(rm)
        options = self._options(data, compatible, coverable)
        risky = _risky_line_codes(ie, data)
        for option in options:
            option.ie_risk = any(code in risky for code in option.line_codes)

        prefer = MATERIAL_LIMITED if revise else None
        options.sort(key=lambda option: _rank_key(option, prefer=prefer))
        builder.add_options(options)

        best = options[0]
        builder.metric("allocated_units", best.plan.allocated_units, _UNITS)
        builder.metric("unscheduled_units", best.plan.unscheduled_units, _UNITS)
        builder.overcommitted(best)
        if all(option.plan.unscheduled_units > 0 for option in options):
            builder.unscheduled(best)
        for option in options:
            if option.ie_risk:
                builder.ie_risk(option, risky)
        if rm is not None:
            builder.material_constraint(rm)
            if revise:
                builder.revised_for_material(
                    best, rm, ctx.dependency_results.get("planning_r0"), coverable
                )
        builder.summary = self._summary(data, best, options, revise)
        return builder.build()

    def _options(
        self,
        data: SnapshotData,
        compatible: list[Any],
        coverable: Decimal | None,
    ) -> list[_Option]:
        all_ids = frozenset(line.line_id for line in compatible)
        options = [_option(data, FULL_EARLIEST, _plan(data, line_ids=all_ids))]
        if coverable is not None:
            options.append(
                _option(data, MATERIAL_LIMITED, _plan(data, line_ids=all_ids, max_units=coverable))
            )
        best_line = max(
            compatible,
            key=lambda line: (_remaining_minutes(data, line.line_id), line.code),
            default=None,
        )
        if best_line is not None:
            options.append(
                _option(data, SINGLE_LINE, _plan(data, line_ids=frozenset({best_line.line_id})))
            )
        return _deduplicate(options)

    def _summary(
        self, data: SnapshotData, best: _Option, options: list[_Option], revise: bool
    ) -> str:
        lead = "Revised plan" if revise else "Plan"
        finish = best.plan.finish_date.isoformat() if best.plan.finish_date else "no scheduled date"
        head = (
            f"{lead} for order {data.order.external_ref}: option {best.code} allocates "
            f"{decimal_str(best.plan.allocated_units)} of {data.order.remaining_units} "
            f"remaining units across {len(best.plan.allocations)} slot(s) on "
            f"{', '.join(best.line_codes) or 'no line'}, finishing {finish} against a due "
            f"date of {data.order.due_date.isoformat()}."
        )
        if best.plan.unscheduled_units > 0:
            head += (
                f" {decimal_str(best.plan.unscheduled_units)} units stay unscheduled "
                f"({best.plan.unscheduled_reason})."
            )
        return f"{head} {len(options)} option(s) were considered."

    # ----------------------------------------------------------------- tools

    def tools(self, ctx: AgentContext) -> list[AgentTool]:  # noqa: ARG002
        return [
            AgentTool(
                name="get_dependency_findings",
                description="The findings and metrics another agent reported for this run.",
                input_model=DependencyFindingsInput,
                handler=_tool_dependency_findings,
            ),
            AgentTool(
                name="list_compatible_lines",
                description="Active lines and whether they can run this order's style.",
                input_model=CompatibleLinesInput,
                handler=_tool_compatible_lines,
            ),
            AgentTool(
                name="get_remaining_capacity",
                description=("Remaining standard minutes per line between today and the due date."),
                input_model=RemainingCapacityInput,
                handler=_tool_remaining_capacity,
            ),
            AgentTool(
                name="simulate_allocation",
                description=(
                    "Run the earliest-slot planner with a unit cap and/or a subset of "
                    "compatible lines, and add the result as a new candidate action you "
                    "may then select in submit_assessment."
                ),
                input_model=SimulateAllocationInput,
                handler=_tool_simulate_allocation,
            ),
        ]


def _deduplicate(options: list[_Option]) -> list[_Option]:
    seen: set[tuple[tuple[str, str], ...]] = set()
    unique: list[_Option] = []
    for option in options:
        if option.fingerprint in seen:
            continue
        seen.add(option.fingerprint)
        unique.append(option)
    return unique


# --------------------------------------------------------------------------
# Assessment assembly
# --------------------------------------------------------------------------


class _PlanningAssessment:
    def __init__(self, data: SnapshotData) -> None:
        self.data = data
        self.summary = ""
        self.findings: list[Finding] = []
        self.metrics: list[Metric] = []
        self.actions: list[RecommendedAction] = []
        self.evidence: dict[str, EvidenceRef] = {}
        self.warnings: list[str] = []
        self.notes: list[str] = []
        self.missing_inputs: list[str] = []
        self.complete = True
        self.add_evidence(
            EvidenceRef(
                evidence_id="ev-order",
                kind="record",
                record_type="order",
                record_id=data.order.id,
                record_version=data.order.version,
                description=(
                    f"Order {data.order.external_ref}: {data.order.remaining_units} units "
                    f"remaining of {data.order.quantity}, due "
                    f"{data.order.due_date.isoformat()}."
                ),
            )
        )

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

    def metric(self, name: str, value: Decimal | None, unit: str) -> None:
        self.metrics.append(Metric(name=name, value=value, unit=unit))

    # ----------------------------------------------------------------- pieces

    def missing(self, agent: str, *, mandatory: bool) -> None:
        self.complete = False
        self.missing_inputs.append(agent)
        message = f"No usable {agent} result was available for this round" + (
            ", which this revision depends on." if mandatory else "."
        )
        self.notes.append(message)
        self.finding(
            finding_id=f"planning-missing-{agent}",
            severity="warning",
            code="DEPENDENCY_MISSING",
            message=message,
            evidence_ids=["ev-order"],
        )

    def no_compatible_line(self) -> None:
        missing = sorted({skill for line in self.data.lines for skill in line.missing_skills})
        self.finding(
            finding_id="planning-no-line",
            severity="critical",
            code="NO_COMPATIBLE_LINE",
            message=(
                f"No active line can run style {self.data.order.style_code}"
                + (f"; missing skills: {', '.join(missing)}." if missing else ".")
            ),
            evidence_ids=["ev-order"],
        )

    def _slot_evidence(self, option: _Option) -> list[str]:
        by_id = {slot.slot_id: slot for slot in self.data.slots}
        ids = ["ev-order"]
        for allocation in option.plan.allocations:
            slot = by_id.get(allocation.slot_id)
            if slot is None:  # pragma: no cover - plans only use snapshot slots
                continue
            ids.append(self.add_evidence(_slot_evidence_ref(slot)))
        ids.append(self.add_evidence(_plan_evidence_ref(option, self.data)))
        return ids

    def add_options(self, options: list[_Option]) -> None:
        for rank, option in enumerate(options):
            option.action_id = f"act-{option.code.lower().replace('_', '-')}"
            option.evidence_ids = self._slot_evidence(option)
            self.actions.append(
                RecommendedAction(
                    action_id=option.action_id,
                    kind="ALLOCATION",
                    summary=(
                        f"{option.code}: allocate "
                        f"{decimal_str(option.plan.allocated_units)} units on "
                        f"{', '.join(option.line_codes) or 'no line'}"
                        + (
                            f", finishing {option.plan.finish_date.isoformat()}."
                            if option.plan.finish_date
                            else "."
                        )
                    ),
                    payload=allocation_payload(option.code, option.plan, self.data),
                    evidence_ids=option.evidence_ids,
                    rank=rank,
                    source="deterministic",
                )
            )

    def overcommitted(self, option: _Option) -> None:
        by_id = {slot.slot_id: slot for slot in self.data.slots}
        for allocation in option.plan.allocations:
            slot = by_id.get(allocation.slot_id)
            if slot is None:  # pragma: no cover - plans only use snapshot slots
                continue
            capacity = slot.available_operator_minutes * slot.planned_efficiency
            ratio = utilization(
                slot.allocated_standard_minutes + allocation.standard_minutes, capacity
            )
            if ratio is None or ratio <= OVERCOMMIT_THRESHOLD:
                continue
            self.finding(
                finding_id=f"planning-overcommit-{slot.slot_id}",
                severity="warning",
                code="LINE_OVERCOMMITTED",
                message=(
                    f"Line {slot.line_code} on {slot.slot_date.isoformat()} shift "
                    f"{slot.shift_code} would reach "
                    f"{decimal_str(ratio * 100)}% utilization "
                    f"({decimal_str(slot.allocated_standard_minutes + allocation.standard_minutes)}"
                    f" of {decimal_str(capacity)} standard minutes)."
                ),
                evidence_ids=[self.add_evidence(_slot_evidence_ref(slot))],
            )

    def unscheduled(self, option: _Option) -> None:
        self.finding(
            finding_id="planning-unscheduled",
            severity="critical",
            code="UNSCHEDULED_QUANTITY",
            message=(
                f"Every option leaves units unscheduled; the best leaves "
                f"{decimal_str(option.plan.unscheduled_units)} of "
                f"{self.data.order.remaining_units} units unscheduled before "
                f"{self.data.order.due_date.isoformat()} "
                f"({option.plan.unscheduled_reason})."
            ),
            evidence_ids=option.evidence_ids or ["ev-order"],
        )

    def ie_risk(self, option: _Option, risky: set[str]) -> None:
        affected = sorted(code for code in option.line_codes if code in risky)
        self.finding(
            finding_id=f"planning-ie-risk-{option.code}",
            severity="warning",
            code="IE_BOTTLENECK_RISK",
            message=(
                f"Option {option.code} uses line(s) {', '.join(affected)}, which the IE "
                "agent reported as capacity below plan; it is ranked after equivalent "
                "options that avoid them."
            ),
            evidence_ids=option.evidence_ids or ["ev-order"],
        )

    def _rm_evidence(self, rm: AgentResult) -> list[str]:
        """Re-export the RM evidence behind its shortage findings.

        A planning result may only cite its own evidence, so each RM evidence
        id becomes a ``record`` reference to the RM *result* that carries it.
        """
        by_id = {ref.evidence_id: ref for ref in rm.evidence_refs}
        cited: list[str] = []
        for finding in rm.findings:
            if finding.code not in ("MATERIAL_SHORTAGE", "MATERIAL_AT_RISK"):
                continue
            for evidence_id in finding.evidence_ids:
                source = by_id.get(evidence_id)
                cited.append(
                    self.add_evidence(
                        EvidenceRef(
                            evidence_id=f"ev-rm-{evidence_id}",
                            kind="record",
                            record_type="agent_result",
                            record_id=rm.task_id,
                            description=(
                                f"RM result evidence {evidence_id}: "
                                + (source.description if source is not None else finding.message)
                            ),
                        )
                    )
                )
        if not cited:
            cited.append(
                self.add_evidence(
                    EvidenceRef(
                        evidence_id="ev-rm-result",
                        kind="record",
                        record_type="agent_result",
                        record_id=rm.task_id,
                        description=f"RM result: {rm.summary}",
                    )
                )
            )
        return cited

    def material_constraint(self, rm: AgentResult) -> None:
        shortages = [
            finding
            for finding in rm.findings
            if finding.code in ("MATERIAL_SHORTAGE", "MATERIAL_AT_RISK")
        ]
        if not shortages:
            return
        self.finding(
            finding_id="planning-material-constraint",
            severity="info",
            code="MATERIAL_CONSTRAINT_KNOWN",
            message=(
                "The RM agent reported a material constraint: "
                + " ".join(finding.message for finding in shortages)
            ),
            evidence_ids=self._rm_evidence(rm),
        )

    def revised_for_material(
        self,
        best: _Option,
        rm: AgentResult,
        planning_r0: AgentResult | None,
        coverable: Decimal | None,
    ) -> None:
        evidence_ids = self._rm_evidence(rm)
        if planning_r0 is not None:
            evidence_ids.append(
                self.add_evidence(
                    EvidenceRef(
                        evidence_id="ev-planning-r0",
                        kind="record",
                        record_type="agent_result",
                        record_id=planning_r0.task_id,
                        description=f"Round 0 planning result: {planning_r0.summary}",
                    )
                )
            )
        shortages = "; ".join(
            f"{metric.name.split(':', 1)[1]} short {decimal_str(metric.value)} {metric.unit}"
            for metric in rm.metrics
            if metric.name.startswith("shortage:") and metric.value is not None and metric.value > 0
        )
        receipt = _first_open_receipt(self.data)
        if receipt is None:
            receipt_note = "No open receipt is expected for the short material(s)."
        elif receipt[0] > self.data.order.due_date:
            receipt_note = (
                f"The first open receipt is expected on {receipt[0].isoformat()}, after "
                f"the due date {self.data.order.due_date.isoformat()}."
            )
        else:
            receipt_note = f"The first open receipt is expected on {receipt[0].isoformat()}."
        self.finding(
            finding_id="planning-revised",
            severity="warning",
            code="REVISED_FOR_MATERIAL",
            message=(
                f"Revised to {decimal_str(best.plan.allocated_units)} of the "
                f"{self.data.order.remaining_units} units requested because materials "
                f"cover {decimal_str(coverable) if coverable is not None else 'an unknown'}"
                f" units" + (f" ({shortages})" if shortages else "") + f". {receipt_note}"
            ),
            evidence_ids=evidence_ids,
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
                complete=self.complete,
                missing=sorted(set(self.missing_inputs)),
                notes=self.notes,
            ),
        )


def _first_open_receipt(data: SnapshotData) -> tuple[date, Decimal] | None:
    receipts = [
        (receipt.expected_date, receipt.quantity)
        for material in data.materials.values()
        for receipt in material.open_receipts
    ]
    return min(receipts) if receipts else None


def _slot_evidence_ref(slot: SnapshotSlot) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=f"ev-slot-{slot.slot_id}",
        kind="record",
        record_type="capacity_slot",
        record_id=slot.slot_id,
        record_version=slot.version,
        description=(
            f"Line {slot.line_code} on {slot.slot_date.isoformat()} shift "
            f"{slot.shift_code}: {slot.available_operator_minutes} operator minutes at "
            f"{slot.planned_efficiency} planned efficiency, "
            f"{slot.allocated_standard_minutes} standard minutes already allocated."
        ),
    )


def _plan_evidence_ref(option: _Option, data: SnapshotData) -> EvidenceRef:
    plan = option.plan
    return EvidenceRef(
        evidence_id=f"ev-plan-{option.code}",
        kind="calculation",
        description=(
            f"{option.code}: required_standard_minutes = {data.order.remaining_units} units "
            f"x {data.sam_total_minutes} SAM = {plan.required_standard_minutes}; earliest-slot "
            f"allocation between {data.as_of_date.isoformat()} and "
            f"{data.order.due_date.isoformat()} on {', '.join(option.line_codes) or 'no line'} "
            f"places {plan.allocated_units} units in {len(plan.allocations)} slot(s), leaving "
            f"{plan.unscheduled_units} unscheduled ({plan.unscheduled_reason})."
        ),
    )


def allocation_payload(code: str, plan: AllocationPlan, data: SnapshotData) -> dict[str, Any]:
    """The ALLOCATION action payload (every decimal rendered as a string).

    Per-slot units are rounded to six places and the last row absorbs the
    rounding remainder, so the rows a reviewer approves always add up to
    ``allocated_units`` exactly.
    """
    codes = {slot.line_id: slot.line_code for slot in data.slots}
    allocated = quantize_quantity(plan.allocated_units)
    units = [quantize_quantity(allocation.units) for allocation in plan.allocations]
    if units:
        units[-1] += allocated - sum(units, Decimal(0))
    return {
        "option_code": code,
        "allocations": [
            {
                "slot_id": str(allocation.slot_id),
                "line_id": str(allocation.line_id),
                "line_code": codes.get(allocation.line_id, "?"),
                "slot_date": allocation.slot_date.isoformat(),
                "shift_code": allocation.shift_code,
                "standard_minutes": decimal_str(allocation.standard_minutes),
                "units": decimal_str(quantity),
            }
            for allocation, quantity in zip(plan.allocations, units, strict=True)
        ],
        "allocated_units": decimal_str(allocated),
        "unscheduled_units": decimal_str(plan.unscheduled_units),
        "unscheduled_reason": plan.unscheduled_reason,
        "finish_date": plan.finish_date.isoformat() if plan.finish_date else None,
    }


# --------------------------------------------------------------------------
# Tool handlers
# --------------------------------------------------------------------------


async def _tool_dependency_findings(ctx: AgentContext, arguments: Any) -> ToolResult:
    result = ctx.dependency_results.get(arguments.agent)
    if result is None:
        raise ToolError(
            f"No {arguments.agent} result is attached to this task; available: "
            + (", ".join(sorted(ctx.dependency_results)) or "none")
            + "."
        )
    return ToolResult(
        data={
            "agent": result.agent,
            "status": result.status,
            "summary": result.summary,
            "findings": [
                {
                    "code": finding.code,
                    "severity": finding.severity,
                    "message": finding.message,
                }
                for finding in result.findings
            ],
            "metrics": [
                {
                    "name": metric.name,
                    "value": None if metric.value is None else decimal_str(metric.value),
                    "unit": metric.unit,
                }
                for metric in result.metrics
            ],
        },
        evidence=[
            EvidenceRef(
                evidence_id=f"ev-{result.agent}-result",
                kind="record",
                record_type="agent_result",
                record_id=result.task_id,
                description=f"{result.agent} result: {result.summary}",
            )
        ],
    )


async def _tool_compatible_lines(ctx: AgentContext, arguments: Any) -> ToolResult:  # noqa: ARG001
    data = ctx.snapshot
    return ToolResult(
        data={
            "style_code": data.order.style_code,
            "lines": [
                {
                    "line_code": line.code,
                    "operator_count": line.operator_count,
                    "compatible": line.compatible,
                    "missing_skills": line.missing_skills,
                    "remaining_standard_minutes": decimal_str(
                        _remaining_minutes(data, line.line_id)
                    ),
                }
                for line in data.lines
            ],
        },
        evidence=[],
    )


def _line_by_code(ctx: AgentContext, code: str) -> Any:
    line = next((line for line in ctx.snapshot.lines if line.code == code), None)
    if line is None:
        known = ", ".join(line.code for line in ctx.snapshot.lines) or "none"
        raise ToolError(f"Unknown line code {code!r}; this factory has: {known}.")
    if not line.compatible:
        raise ToolError(
            f"Line {code} cannot run style {ctx.snapshot.order.style_code} "
            f"(missing skills: {', '.join(line.missing_skills) or 'unknown'})."
        )
    return line


async def _tool_remaining_capacity(ctx: AgentContext, arguments: Any) -> ToolResult:
    data = ctx.snapshot
    lines = (
        [_line_by_code(ctx, arguments.line_code)]
        if arguments.line_code is not None
        else [line for line in data.lines if line.compatible]
    )
    line_ids = {line.line_id for line in lines}
    slots = [
        slot
        for slot in data.slots
        if slot.line_id in line_ids and data.as_of_date <= slot.slot_date <= data.order.due_date
    ]
    total = sum((_remaining_minutes(data, line.line_id) for line in lines), Decimal(0))
    return ToolResult(
        data={
            "line_code": arguments.line_code,
            "from_date": data.as_of_date.isoformat(),
            "to_date": data.order.due_date.isoformat(),
            "remaining_standard_minutes": decimal_str(total),
            "slots": [
                {
                    "line_code": slot.line_code,
                    "slot_date": slot.slot_date.isoformat(),
                    "shift_code": slot.shift_code,
                    "capacity_standard_minutes": decimal_str(
                        slot.available_operator_minutes * slot.planned_efficiency
                    ),
                    "allocated_standard_minutes": decimal_str(slot.allocated_standard_minutes),
                }
                for slot in slots
            ],
        },
        evidence=[_slot_evidence_ref(slot) for slot in slots],
    )


async def _tool_simulate_allocation(ctx: AgentContext, arguments: Any) -> ToolResult:
    assessment = ctx.assessment
    if assessment is None:  # pragma: no cover - set before any tool runs
        raise ToolError("The assessment is not available yet.")
    data = ctx.snapshot
    if arguments.line_codes:
        lines = [_line_by_code(ctx, code) for code in arguments.line_codes]
    else:
        lines = [line for line in data.lines if line.compatible]
    if not lines:
        raise ToolError(f"No active line can run style {data.order.style_code}.")
    if arguments.max_units is not None and arguments.max_units < 0:
        raise ToolError("max_units must be zero or more.")

    plan = _plan(
        data,
        line_ids=frozenset(line.line_id for line in lines),
        max_units=None if arguments.max_units is None else Decimal(arguments.max_units),
    )
    option = _option(data, SIMULATED, plan)
    index = (
        sum(
            1
            for action in assessment.recommended_actions
            if action.kind == "ALLOCATION" and str(action.payload.get("option_code")) == SIMULATED
        )
        + 1
    )
    option.action_id = f"act-sim-{index}"

    by_id = {slot.slot_id: slot for slot in data.slots}
    existing = {ref.evidence_id for ref in assessment.evidence_refs}
    evidence_ids = ["ev-order"]
    new_refs: list[EvidenceRef] = []
    for allocation in plan.allocations:
        slot = by_id[allocation.slot_id]
        ref = _slot_evidence_ref(slot)
        if ref.evidence_id in existing:
            evidence_ids.append(ref.evidence_id)
            continue
        # A slot no other option used gets a simulation-scoped id, so an
        # investigative tool called later can never emit the same id twice.
        ref = ref.model_copy(update={"evidence_id": f"ev-sim{index}-slot-{slot.slot_id}"})
        existing.add(ref.evidence_id)
        evidence_ids.append(ref.evidence_id)
        new_refs.append(ref)
    plan_ref = _plan_evidence_ref(option, data).model_copy(
        update={"evidence_id": f"ev-plan-{SIMULATED}-{index}"}
    )
    evidence_ids.append(plan_ref.evidence_id)
    new_refs.append(plan_ref)

    assessment.evidence_refs.extend(new_refs)
    assessment.recommended_actions.append(
        RecommendedAction(
            action_id=option.action_id,
            kind="ALLOCATION",
            summary=(
                f"SIMULATED: allocate {decimal_str(plan.allocated_units)} units on "
                f"{', '.join(option.line_codes) or 'no line'}"
                + (f", finishing {plan.finish_date.isoformat()}." if plan.finish_date else ".")
            ),
            payload=allocation_payload(SIMULATED, plan, data),
            evidence_ids=evidence_ids,
            rank=len(assessment.recommended_actions),
            source="deterministic",
        )
    )
    return ToolResult(
        data={
            "action_id": option.action_id,
            "option_code": SIMULATED,
            **allocation_payload(SIMULATED, plan, data),
        },
        evidence=[],
    )


__all__ = [
    "MATERIAL_LIMITED",
    "PROPOSE_TASK_TYPE",
    "REVISE_TASK_TYPE",
    "PlanningAgent",
    "allocation_payload",
]
