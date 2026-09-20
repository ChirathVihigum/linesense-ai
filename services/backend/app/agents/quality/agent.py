"""The quality agent.

``assess_quality_status`` answers "what is this order's quality position, and
may it ship?" — inspections against the versioned policy, active holds,
releases, and the deterministic shipment gates.

**The deterministic facts always win.** Shipment eligibility is computed by
``app.domain.quality.calc.shipment_eligibility`` when the run snapshot is
built; this agent reports that verdict and never recomputes, softens or
overrides it. A model summary claiming an order is ready to ship changes
nothing: the findings, the ``shipment_eligible`` metric and the order report
all keep the calculated answer.

Like the IE agent, this agent never reasons about an individual worker: a
defect belongs to an operation, never to a person.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agents.base import (
    AgentContext,
    AgentTool,
    Assessment,
    BaseAgent,
    ToolResult,
)
from app.agents.quantities import decimal_str
from app.domain.quality.calc import defective_rate, defects_per_hundred_units
from app.domain.vocab import InspectionResult, InspectionType
from app.orchestration.protocol import (
    DataQuality,
    EvidenceRef,
    Finding,
    Metric,
    RecommendedAction,
)
from app.orchestration.snapshot import SnapshotData

ASSESS_TASK_TYPE = "assess_quality_status"
NOT_INSPECTED_MESSAGE = "Quality pending — no inspection recorded; this is not a pass."
HOLD_SUGGESTION_NOTE = (
    "Suggestion only — a release requires the quality release command, an explicit "
    "reason and the right role. This agent cannot release anything."
)
SHIPMENT_SOURCE = "Calculated from records"
_RATE = "fraction"
_DEFECTS_PER_HUNDRED = "defects_per_100_units"
_DEFECTS = "defects"
_BOOLEAN = "boolean"


# --------------------------------------------------------------------------
# The snapshot's quality facts
# --------------------------------------------------------------------------


class _Lenient(BaseModel):
    # Snapshots written by an older build may lack a key this agent added.
    model_config = ConfigDict(extra="ignore")


class DefectView(_Lenient):
    defect_code: str
    severity: str
    count: int
    operation_id: uuid.UUID | None = None


class InspectionView(_Lenient):
    id: uuid.UUID
    inspection_type: str
    inspected_units: int
    defective_units: int
    result: str
    policy_version_id: uuid.UUID
    line_id: uuid.UUID | None = None
    inspected_at: datetime
    defects: list[DefectView] = Field(default_factory=list)

    @property
    def total_defects(self) -> int:
        return sum(defect.count for defect in self.defects)


class HoldView(_Lenient):
    id: uuid.UUID
    reason: str
    inspection_id: uuid.UUID | None = None
    created_at: datetime


class PolicyView(_Lenient):
    id: uuid.UUID
    code: str
    version_no: int
    is_demo: bool
    rules: dict[str, Any]


def _inspections(data: SnapshotData) -> list[InspectionView]:
    """Every inspection in the snapshot, newest first."""
    rows = [InspectionView.model_validate(row) for row in data.quality.inspections]
    return sorted(rows, key=lambda row: (row.inspected_at, str(row.id)), reverse=True)


def _policy(data: SnapshotData) -> PolicyView | None:
    payload = data.quality.policy
    return None if payload is None else PolicyView.model_validate(payload)


def _holds(data: SnapshotData) -> list[HoldView]:
    return [HoldView.model_validate(row) for row in data.quality.active_holds]


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------


def _order_evidence(data: SnapshotData) -> EvidenceRef:
    return EvidenceRef(
        evidence_id="ev-order",
        kind="record",
        record_type="order",
        record_id=data.order.id,
        record_version=data.order.version,
        description=(
            f"Order {data.order.external_ref}: production state "
            f"{data.order.production_state}, quality state {data.quality.quality_state}."
        ),
    )


def _policy_evidence(policy: PolicyView) -> EvidenceRef:
    return EvidenceRef(
        evidence_id="ev-policy",
        kind="record",
        record_type="quality_policy",
        record_id=policy.id,
        record_version=policy.version_no,
        description=(
            f"Quality policy {policy.code} v{policy.version_no}"
            + (" (demo policy)" if policy.is_demo else "")
            + f": {policy.rules}."
        ),
    )


def _inspection_evidence(inspection: InspectionView) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=f"ev-inspection-{inspection.id}",
        kind="record",
        record_type="inspection",
        record_id=inspection.id,
        description=(
            f"{inspection.inspection_type} inspection on "
            f"{inspection.inspected_at.isoformat()}: {inspection.defective_units} defective of "
            f"{inspection.inspected_units} inspected, result {inspection.result}."
        ),
    )


def _hold_evidence(hold: HoldView) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=f"ev-hold-{hold.id}",
        kind="record",
        record_type="quality_hold",
        record_id=hold.id,
        description=(f"Active quality hold placed {hold.created_at.isoformat()}: {hold.reason}."),
    )


def _shipment_evidence(data: SnapshotData) -> EvidenceRef:
    shipment = data.quality.shipment
    reasons = ", ".join(shipment.reasons) or "none"
    return EvidenceRef(
        evidence_id="ev-calc-shipment",
        kind="calculation",
        description=(
            f"Shipment eligibility ({SHIPMENT_SOURCE}): every gate must hold — a known "
            f"policy, production complete, packing complete, each required inspection type "
            f"passed, no active hold, and a valid release. eligible="
            f"{str(shipment.eligible).lower()}; unmet gates: {reasons}."
        ),
    )


# --------------------------------------------------------------------------
# Tool inputs
# --------------------------------------------------------------------------


class InspectionsInput(BaseModel):
    """No arguments: every inspection recorded for this order."""


class PolicyRulesInput(BaseModel):
    """No arguments: the policy version this order is judged against."""


class DefectBreakdownInput(BaseModel):
    group_by: Literal["defect_code", "operation"] = Field(
        default="defect_code", description="How to group the defect counts."
    )


# --------------------------------------------------------------------------
# The agent
# --------------------------------------------------------------------------


class QualityAgent(BaseAgent):
    name = "quality"
    prompt_version = "quality-v1"
    goal = (
        "Report this order's quality position — inspections against the versioned policy, "
        "active holds and whether the calculated shipment gates are met — without ever "
        "declaring an order shippable yourself."
    )

    # ------------------------------------------------------------ assessment

    async def assess(self, ctx: AgentContext) -> Assessment:
        data = ctx.snapshot
        builder = _QualityAssessment(data)
        policy = _policy(data)
        inspections = _inspections(data)
        holds = _holds(data)

        builder.add_evidence(_order_evidence(data))
        builder.policy(policy)
        builder.inspections(inspections, policy)
        builder.holds(holds)
        builder.shipment()
        builder.summary = self._summary(data, inspections, holds, policy)
        return builder.build()

    def _summary(
        self,
        data: SnapshotData,
        inspections: list[InspectionView],
        holds: list[HoldView],
        policy: PolicyView | None,
    ) -> str:
        shipment = data.quality.shipment
        head = (
            f"Order {data.order.external_ref}: quality state "
            f"{data.quality.quality_state}, {len(inspections)} inspection(s) recorded, "
            f"{len(holds)} active hold(s)."
        )
        verdict = (
            "The calculated shipment gates are all met."
            if shipment.eligible
            else "The order is not eligible to ship: " + ", ".join(shipment.reasons) + "."
        )
        policy_note = (
            "No approved quality policy applies to this order."
            if policy is None
            else f"Judged against policy {policy.code} v{policy.version_no}"
            + (" (a demo policy)." if policy.is_demo else ".")
        )
        return f"{head} {verdict} {policy_note}"

    # ----------------------------------------------------------------- tools

    def tools(self, ctx: AgentContext) -> list[AgentTool]:  # noqa: ARG002
        return [
            AgentTool(
                name="get_inspections",
                description=(
                    "Every inspection recorded for this order: type, units inspected, "
                    "defective units, result and defect counts."
                ),
                input_model=InspectionsInput,
                handler=_tool_inspections,
            ),
            AgentTool(
                name="get_policy_rules",
                description=(
                    "The versioned quality policy this order is judged against, with its "
                    "thresholds and whether it is a demo policy."
                ),
                input_model=PolicyRulesInput,
                handler=_tool_policy_rules,
            ),
            AgentTool(
                name="get_defect_breakdown",
                description=(
                    "Defect counts for this order grouped by defect code or by operation. "
                    "No operator-level data exists."
                ),
                input_model=DefectBreakdownInput,
                handler=_tool_defect_breakdown,
            ),
        ]


# --------------------------------------------------------------------------
# Assessment assembly
# --------------------------------------------------------------------------


class _QualityAssessment:
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

    def policy(self, policy: PolicyView | None) -> None:
        if policy is None:
            self.complete = False
            self.missing.append("quality_policy")
            self.notes.append("No approved quality policy applies to this order.")
            self.finding(
                finding_id="quality-policy-missing",
                severity="critical",
                code="POLICY_MISSING",
                message=(
                    "No approved quality policy version applies to this order, so no "
                    "inspection can be dispositioned and the order cannot ship."
                ),
                evidence_ids=["ev-order"],
            )
            return
        evidence_id = self.add_evidence(_policy_evidence(policy))
        if policy.is_demo:
            self.finding(
                finding_id="quality-demo-policy",
                severity="warning",
                code="DEMO_POLICY",
                message=(
                    f"Policy {policy.code} v{policy.version_no} is a demo policy: its "
                    "thresholds are illustrative and are not a customer's AQL."
                ),
                evidence_ids=[evidence_id],
            )

    def inspections(self, inspections: list[InspectionView], policy: PolicyView | None) -> None:
        if not inspections:
            self.finding(
                finding_id="quality-not-inspected",
                severity="info",
                code="NOT_INSPECTED",
                message=NOT_INSPECTED_MESSAGE,
                evidence_ids=["ev-order"],
            )
            return

        defects_by_code: dict[str, int] = {}
        for inspection in inspections:
            self.add_evidence(_inspection_evidence(inspection))
            rate = defective_rate(inspection.defective_units, inspection.inspected_units)
            dhu = defects_per_hundred_units(inspection.total_defects, inspection.inspected_units)
            note = (
                None
                if inspection.inspected_units
                else "No units were inspected, so the rate is unknown (never zero)."
            )
            self.metric(f"defective_rate:{inspection.id}", rate, _RATE, note=note)
            self.metric(f"dhu:{inspection.id}", dhu, _DEFECTS_PER_HUNDRED, note=note)
            for defect in inspection.defects:
                defects_by_code[defect.defect_code] = (
                    defects_by_code.get(defect.defect_code, 0) + defect.count
                )
        for code in sorted(defects_by_code):
            self.metric(f"defect_count:{code}", Decimal(defects_by_code[code]), _DEFECTS)

        failed = next(
            (
                inspection
                for inspection in inspections
                if inspection.inspection_type == InspectionType.FINAL.value
            ),
            None,
        )
        if failed is not None and failed.result == InspectionResult.FAIL.value:
            ids = [self.add_evidence(_inspection_evidence(failed)), "ev-order"]
            if policy is not None:
                ids.append("ev-policy")
            self.finding(
                finding_id=f"quality-failed-{failed.id}",
                severity="critical",
                code="INSPECTION_FAILED",
                message=(
                    f"The latest FINAL inspection ({failed.inspected_at.isoformat()}) failed: "
                    f"{failed.defective_units} defective of {failed.inspected_units} inspected"
                    + (
                        f", against policy {policy.code} v{policy.version_no}."
                        if policy is not None
                        else "."
                    )
                ),
                evidence_ids=ids,
            )

    def holds(self, holds: list[HoldView]) -> None:
        for hold in holds:
            evidence_id = self.add_evidence(_hold_evidence(hold))
            self.finding(
                finding_id=f"quality-hold-{hold.id}",
                severity="critical",
                code="ACTIVE_HOLD",
                message=(
                    f"An active quality hold placed on {hold.created_at.isoformat()} blocks "
                    f"this order: {hold.reason}."
                ),
                evidence_ids=[evidence_id, "ev-order"],
            )
            self.actions.append(
                RecommendedAction(
                    action_id=f"act-quality-hold-{hold.id}",
                    kind="QUALITY_HOLD_REVIEW",
                    summary=(
                        f"Suggest a quality review of the hold placed on "
                        f"{hold.created_at.isoformat()} ({hold.reason})."
                    ),
                    payload={
                        "hold_id": str(hold.id),
                        "reason": hold.reason,
                        "inspection_id": (
                            None if hold.inspection_id is None else str(hold.inspection_id)
                        ),
                        "note": HOLD_SUGGESTION_NOTE,
                    },
                    evidence_ids=[evidence_id],
                    rank=len(self.actions),
                    source="deterministic",
                )
            )

    def shipment(self) -> None:
        """The deterministic verdict, reported verbatim and never recomputed."""
        shipment = self.data.quality.shipment
        evidence_id = self.add_evidence(_shipment_evidence(self.data))
        self.metric(
            "shipment_eligible",
            Decimal(1) if shipment.eligible else Decimal(0),
            _BOOLEAN,
            note=SHIPMENT_SOURCE,
        )
        if shipment.eligible:
            self.finding(
                finding_id="quality-shipment-eligible",
                severity="info",
                code="SHIPMENT_ELIGIBLE",
                message=(
                    "Every calculated shipment gate is met: the policy is known, production "
                    "and packing are complete, the required inspections passed, no hold is "
                    "active and a valid release exists."
                ),
                evidence_ids=[evidence_id, "ev-order"],
            )
            return
        self.finding(
            finding_id="quality-shipment-ineligible",
            severity="warning",
            code="SHIPMENT_INELIGIBLE",
            message=(
                "The order is not eligible to ship. Unmet gates: "
                + (", ".join(shipment.reasons) or "unknown")
                + f". ({SHIPMENT_SOURCE}.)"
            ),
            evidence_ids=[evidence_id, "ev-order"],
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
# Tool handlers (snapshot only)
# --------------------------------------------------------------------------


async def _tool_inspections(ctx: AgentContext, arguments: Any) -> ToolResult:  # noqa: ARG001
    data = ctx.snapshot
    inspections = _inspections(data)
    return ToolResult(
        data={
            "quality_state": data.quality.quality_state,
            "inspections": [
                {
                    "inspection_id": str(inspection.id),
                    "inspection_type": inspection.inspection_type,
                    "inspected_units": inspection.inspected_units,
                    "defective_units": inspection.defective_units,
                    "result": inspection.result,
                    "inspected_at": inspection.inspected_at.isoformat(),
                    "defective_rate": _optional_decimal(
                        defective_rate(inspection.defective_units, inspection.inspected_units)
                    ),
                    "dhu": _optional_decimal(
                        defects_per_hundred_units(
                            inspection.total_defects, inspection.inspected_units
                        )
                    ),
                }
                for inspection in inspections
            ],
        },
        evidence=[_inspection_evidence(inspection) for inspection in inspections],
    )


async def _tool_policy_rules(ctx: AgentContext, arguments: Any) -> ToolResult:  # noqa: ARG001
    policy = _policy(ctx.snapshot)
    if policy is None:
        return ToolResult(
            data={
                "policy": None,
                "note": (
                    "No approved quality policy version applies to this order; nothing can "
                    "be dispositioned."
                ),
            },
            evidence=[],
        )
    return ToolResult(
        data={
            "code": policy.code,
            "version_no": policy.version_no,
            "is_demo": policy.is_demo,
            "rules": policy.rules,
        },
        evidence=[_policy_evidence(policy)],
    )


async def _tool_defect_breakdown(ctx: AgentContext, arguments: Any) -> ToolResult:
    data = ctx.snapshot
    inspections = _inspections(data)
    operation_codes = {operation.operation_id: operation.code for operation in data.operations}
    counts: dict[str, int] = {}
    for inspection in inspections:
        for defect in inspection.defects:
            if arguments.group_by == "operation":
                key = (
                    operation_codes.get(defect.operation_id, "UNASSIGNED")
                    if defect.operation_id is not None
                    else "UNASSIGNED"
                )
            else:
                key = defect.defect_code
            counts[key] = counts.get(key, 0) + defect.count
    return ToolResult(
        data={
            "group_by": arguments.group_by,
            "groups": [{"key": key, "count": counts[key]} for key in sorted(counts)],
            "total_defects": sum(counts.values()),
            "note": "Defects belong to operations and inspections, never to a person.",
        },
        evidence=[_inspection_evidence(inspection) for inspection in inspections],
    )


def _optional_decimal(value: Decimal | None) -> str | None:
    return None if value is None else decimal_str(value)


__all__ = ["ASSESS_TASK_TYPE", "NOT_INSPECTED_MESSAGE", "QualityAgent"]
