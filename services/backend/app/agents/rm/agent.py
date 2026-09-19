"""The raw-material (RM) agent.

Round 0 (``assess_material_readiness``) answers "can this order be made from
the stock we have?"; round 1 (``validate_plan_materials``) answers "does the
plan the planning agent selected have its materials?" and proposes the
reservation that would hold them.

Every business number comes from ``app.domain.inventory.calc`` applied to the
run snapshot. The agent never queries the database and never recomputes a
formula of its own, so a finding and the inventory screens can never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model

from app.agents.base import (
    AgentContext,
    AgentTool,
    Assessment,
    BaseAgent,
    ToolError,
    ToolResult,
)
from app.agents.quantities import decimal_str
from app.domain.inventory.calc import (
    UnsupportedUnitConversion,
    available_now,
    average_daily_consumption,
    convert_quantity,
    coverable_units,
    coverage_days,
    gross_demand,
    material_state,
    projected_balance,
    reorder_point,
    shortage,
)
from app.domain.inventory.queries import CONSUMPTION_WINDOW_DAYS
from app.domain.rounding import round_up_to_pack
from app.orchestration.protocol import (
    AgentResult,
    DataQuality,
    EvidenceRef,
    Finding,
    Metric,
    RecommendedAction,
)
from app.orchestration.snapshot import SnapshotBomLine, SnapshotData, SnapshotMaterial

ASSESS_TASK_TYPE = "assess_material_readiness"
VALIDATE_TASK_TYPE = "validate_plan_materials"
SUGGESTION_NOTE = "Suggestion only — no purchase order is created"
_UNITS = "units"
_DAYS = "days"


# --------------------------------------------------------------------------
# Per-material deterministic assessment
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _MaterialView:
    """One BOM line's numbers, all produced by ``app.domain.inventory.calc``."""

    line: SnapshotBomLine
    material: SnapshotMaterial
    per_unit: Decimal
    demand: Decimal
    available: Decimal
    shortage: Decimal
    projected_at_due: Decimal
    projected_shortage_at_due: Decimal
    daily_consumption: Decimal
    coverage_days: Decimal | None
    reorder_point: Decimal
    coverable_units: Decimal
    state: str
    data_complete: bool

    @property
    def code(self) -> str:
        return self.line.material_code

    @property
    def unit(self) -> str:
        return self.line.material_unit


def _view(
    line: SnapshotBomLine, material: SnapshotMaterial, data: SnapshotData, units: Decimal
) -> _MaterialView | None:
    """Every number for ``line`` at ``units`` planned units, or ``None`` when
    the BOM unit cannot be converted to the material's stock unit."""
    try:
        per_unit = convert_quantity(line.quantity_per_unit, line.bom_unit, line.material_unit)
    except UnsupportedUnitConversion:
        return None
    demand = gross_demand(units, per_unit, line.wastage_fraction)
    available = available_now(material.on_hand_accepted, material.reserved)
    projected = projected_balance(
        available_now=available,
        receipts=[(receipt.expected_date, receipt.quantity) for receipt in material.open_receipts],
        demand=[(data.as_of_date, demand)],
        at=data.order.due_date,
    )
    projected_shortage = max(Decimal(0), -projected)
    consumption = average_daily_consumption(
        [(issue.date, issue.quantity) for issue in material.issues_14d],
        CONSUMPTION_WINDOW_DAYS,
        data.as_of_date,
    )
    complete = material.balance_id is not None
    return _MaterialView(
        line=line,
        material=material,
        per_unit=per_unit,
        demand=demand,
        available=available,
        shortage=shortage(available, demand),
        projected_at_due=projected,
        projected_shortage_at_due=projected_shortage,
        daily_consumption=consumption,
        coverage_days=coverage_days(available, consumption),
        reorder_point=reorder_point(consumption, line.lead_time_days, line.safety_stock),
        coverable_units=coverable_units(available, per_unit, line.wastage_fraction),
        state=material_state(
            shortage(available, demand), projected_shortage, data_complete=complete
        ).value,
        data_complete=complete,
    )


def _receipts_by(view: _MaterialView, at: date) -> list[Any]:
    return [receipt for receipt in view.material.open_receipts if receipt.expected_date <= at]


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------


def _bom_evidence(line: SnapshotBomLine) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=f"ev-bom-{line.material_code}",
        kind="record",
        record_type="bom_line",
        record_id=line.bom_line_id,
        description=(
            f"BOM line for {line.material_code}: {line.quantity_per_unit} {line.bom_unit} "
            f"per unit with {line.wastage_fraction} wastage."
        ),
    )


def _balance_evidence(line: SnapshotBomLine, material: SnapshotMaterial) -> EvidenceRef | None:
    if material.balance_id is None:
        return None
    return EvidenceRef(
        evidence_id=f"ev-bal-{line.material_code}",
        kind="record",
        record_type="material_balance",
        record_id=material.balance_id,
        record_version=material.balance_version,
        description=(
            f"{line.material_code} stock balance: {material.on_hand_accepted} "
            f"{line.material_unit} accepted on hand, {material.reserved} "
            f"{line.material_unit} reserved."
        ),
    )


def _receipt_evidence(line: SnapshotBomLine, material: SnapshotMaterial) -> list[EvidenceRef]:
    return [
        EvidenceRef(
            evidence_id=f"ev-rcpt-{line.material_code}-{index}",
            kind="record",
            record_type="expected_receipt",
            record_id=receipt.id,
            description=(
                f"Open receipt of {receipt.quantity} {line.material_unit} of "
                f"{line.material_code} expected on {receipt.expected_date.isoformat()}."
            ),
        )
        for index, receipt in enumerate(material.open_receipts, start=1)
    ]


def _calculation_evidence(view: _MaterialView, data: SnapshotData, units: Decimal) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=f"ev-calc-{view.code}",
        kind="calculation",
        description=(
            f"{view.code}: gross_demand = {units} units x {view.per_unit} {view.unit}/unit "
            f"x (1 + {view.line.wastage_fraction}) = {view.demand} {view.unit}; "
            f"available_now = {view.material.on_hand_accepted} - {view.material.reserved} "
            f"= {view.available} {view.unit}; shortage = max(0, {view.demand} - "
            f"{view.available}) = {view.shortage} {view.unit}; projected balance at "
            f"{data.order.due_date.isoformat()} = {view.projected_at_due} {view.unit}; "
            f"coverable_units = floor({view.available} / ({view.per_unit} x "
            f"(1 + {view.line.wastage_fraction}))) = {view.coverable_units} units."
        ),
    )


# --------------------------------------------------------------------------
# Tool input models (defaults are bound to the run's own BOM)
# --------------------------------------------------------------------------


class BomDemandInput(BaseModel):
    """No arguments: the whole BOM demand for the order's remaining units."""


def _material_input(name: str, default_code: str, *, with_days: bool = False) -> type[BaseModel]:
    fields: dict[str, Any] = {
        "material_code": (
            str,
            Field(default=default_code, description="A material code from the order's BOM."),
        )
    }
    if with_days:
        fields["days"] = (
            Literal[7, 14],
            Field(default=CONSUMPTION_WINDOW_DAYS, description="Consumption window in days."),
        )
    return create_model(name, **fields)


# --------------------------------------------------------------------------
# The agent
# --------------------------------------------------------------------------


class RMAgent(BaseAgent):
    name = "rm"
    prompt_version = "rm-v1"
    goal = (
        "Report whether the order's materials are available, with the numbers and "
        "records behind every claim, and suggest replenishment where they are short."
    )

    # ------------------------------------------------------------ assessment

    async def assess(self, ctx: AgentContext) -> Assessment:
        if ctx.task_type == VALIDATE_TASK_TYPE:
            return self._validate_plan(ctx)
        return self._assess_readiness(ctx)

    def _assess_readiness(self, ctx: AgentContext) -> Assessment:
        data = ctx.snapshot
        units = Decimal(data.order.remaining_units)
        builder = _AssessmentBuilder(data)
        coverables: list[Decimal] = []
        short_views: list[_MaterialView] = []

        for line in data.bom.lines:
            material = data.materials.get(str(line.material_id))
            builder.add_evidence(_bom_evidence(line))
            if material is None:
                builder.incomplete(
                    code="MATERIAL_BALANCE_MISSING",
                    severity="critical",
                    message=f"No stock record for {line.material_code} is in the snapshot.",
                    evidence_ids=[f"ev-bom-{line.material_code}"],
                    missing=line.material_code,
                )
                continue
            view = _view(line, material, data, units)
            if view is None:
                builder.incomplete(
                    code="UNIT_CONVERSION_MISSING",
                    severity="critical",
                    message=(
                        f"{line.material_code} is excluded: no approved conversion from "
                        f"{line.bom_unit} (BOM) to {line.material_unit} (stock)."
                    ),
                    evidence_ids=[f"ev-bom-{line.material_code}"],
                    missing=line.material_code,
                )
                continue
            builder.add_material(view, units)
            coverables.append(view.coverable_units)
            if view.shortage > 0:
                short_views.append(view)

        builder.metric(
            "coverable_units",
            min(coverables) if coverables else units,
            _UNITS,
            note=(
                None
                if coverables
                else "No BOM material limits this order; the full quantity is coverable."
            ),
        )
        builder.replenishment(short_views, data)
        builder.summary = self._readiness_summary(data, units, coverables, short_views)
        return builder.build()

    def _readiness_summary(
        self,
        data: SnapshotData,
        units: Decimal,
        coverables: list[Decimal],
        short_views: list[_MaterialView],
    ) -> str:
        coverable = min(coverables) if coverables else units
        head = (
            f"Order {data.order.external_ref}: {len(short_views)} of "
            f"{len(data.bom.lines)} BOM materials are short for {units} remaining units."
        )
        if not short_views:
            return f"{head} Stock covers the full quantity."
        detail = "; ".join(
            f"{view.code} is short {decimal_str(view.shortage)} {view.unit} "
            f"(demand {decimal_str(view.demand)} {view.unit} against "
            f"{decimal_str(view.available)} {view.unit} available)"
            for view in short_views
        )
        return f"{head} {detail}. Materials cover {decimal_str(coverable)} of {units} units."

    # ------------------------------------------------------------- round one

    def _validate_plan(self, ctx: AgentContext) -> Assessment:
        data = ctx.snapshot
        builder = _AssessmentBuilder(data)
        allocated = _allocated_units(ctx.dependency_results.get("planning"))
        if allocated is None:
            builder.data_quality_missing.append("planning")
            builder.complete = False
            builder.notes.append("No planning allocation was available to validate.")
            builder.finding(
                finding_id="rm-plan-missing",
                severity="warning",
                code="PLAN_INPUT_MISSING",
                message=(
                    "The planning agent produced no usable allocation, so no materials "
                    "were reserved."
                ),
                evidence_ids=[],
            )
            builder.summary = (
                f"Order {data.order.external_ref}: no plan was available to validate, "
                "so no reservation is proposed."
            )
            return builder.build()

        reservations: list[dict[str, str]] = []
        unreserved: list[dict[str, str]] = []
        evidence_ids: list[str] = []
        for line in data.bom.lines:
            material = data.materials.get(str(line.material_id))
            builder.add_evidence(_bom_evidence(line))
            if material is None:
                builder.incomplete(
                    code="MATERIAL_BALANCE_MISSING",
                    severity="critical",
                    message=f"No stock record for {line.material_code} is in the snapshot.",
                    evidence_ids=[f"ev-bom-{line.material_code}"],
                    missing=line.material_code,
                )
                continue
            view = _view(line, material, data, allocated)
            if view is None:
                builder.incomplete(
                    code="UNIT_CONVERSION_MISSING",
                    severity="critical",
                    message=(
                        f"{line.material_code} is excluded: no approved conversion from "
                        f"{line.bom_unit} (BOM) to {line.material_unit} (stock)."
                    ),
                    evidence_ids=[f"ev-bom-{line.material_code}"],
                    missing=line.material_code,
                )
                continue
            cited = builder.add_plan_material(view, allocated)
            evidence_ids.extend(cited)
            quantity = min(view.demand, view.available)
            if quantity > 0 and material.balance_id is not None:
                reservations.append(
                    {
                        "material_id": str(line.material_id),
                        "material_code": view.code,
                        "balance_id": str(material.balance_id),
                        "quantity": decimal_str(quantity),
                        "unit": view.unit,
                    }
                )
            if view.demand > quantity:
                unreserved.append(
                    {
                        "material_code": view.code,
                        "quantity": decimal_str(view.demand - quantity),
                        "unit": view.unit,
                    }
                )

        if reservations:
            builder.actions.append(
                RecommendedAction(
                    action_id="act-reserve",
                    kind="RESERVATION",
                    summary=(
                        f"Reserve materials for {decimal_str(allocated)} units: "
                        + ", ".join(
                            f"{row['quantity']} {row['unit']} of {row['material_code']}"
                            for row in reservations
                        )
                        + "."
                    ),
                    payload={"reservations": reservations, "unreserved": unreserved},
                    evidence_ids=sorted(set(evidence_ids)),
                    rank=0,
                    source="deterministic",
                )
            )
        builder.summary = (
            f"Order {data.order.external_ref}: the planned {decimal_str(allocated)} units "
            + (
                "are fully covered by stock on hand."
                if not unreserved
                else "are short "
                + ", ".join(
                    f"{row['quantity']} {row['unit']} of {row['material_code']}"
                    for row in unreserved
                )
                + "."
            )
        )
        return builder.build()

    # ----------------------------------------------------------------- tools

    def tools(self, ctx: AgentContext) -> list[AgentTool]:
        lines = ctx.snapshot.bom.lines
        default_code = lines[0].material_code if lines else ""
        return [
            AgentTool(
                name="get_material_position",
                description=(
                    "Stock position of one BOM material in this run's snapshot: accepted "
                    "on hand, reserved, available now, demand and shortage."
                ),
                input_model=_material_input("MaterialPositionInput", default_code),
                handler=_tool_material_position,
            ),
            AgentTool(
                name="get_expected_receipts",
                description="Open expected receipts for one BOM material, earliest first.",
                input_model=_material_input("ExpectedReceiptsInput", default_code),
                handler=_tool_expected_receipts,
            ),
            AgentTool(
                name="get_consumption_history",
                description=(
                    "Daily issue history for one BOM material over the last 7 or 14 days, "
                    "with the average daily consumption."
                ),
                input_model=_material_input(
                    "ConsumptionHistoryInput", default_code, with_days=True
                ),
                handler=_tool_consumption_history,
            ),
            AgentTool(
                name="get_bom_demand",
                description="The order's BOM with the gross demand of every line.",
                input_model=BomDemandInput,
                handler=_tool_bom_demand,
            ),
        ]


# --------------------------------------------------------------------------
# Assessment assembly
# --------------------------------------------------------------------------


class _AssessmentBuilder:
    def __init__(self, data: SnapshotData) -> None:
        self.data = data
        self.summary = ""
        self.findings: list[Finding] = []
        self.metrics: list[Metric] = []
        self.actions: list[RecommendedAction] = []
        self.evidence: dict[str, EvidenceRef] = {}
        self.warnings: list[str] = []
        self.notes: list[str] = []
        self.data_quality_missing: list[str] = []
        self.complete = True

    def add_evidence(self, ref: EvidenceRef | None) -> str | None:
        if ref is None:
            return None
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

    def incomplete(
        self,
        *,
        code: str,
        severity: Literal["info", "warning", "critical"],
        message: str,
        evidence_ids: list[str],
        missing: str,
    ) -> None:
        self.complete = False
        self.data_quality_missing.append(missing)
        self.notes.append(message)
        self.finding(
            finding_id=f"rm-{code.lower()}-{missing}",
            severity=severity,
            code=code,
            message=message,
            evidence_ids=evidence_ids,
        )

    def _material_evidence_ids(self, view: _MaterialView, units: Decimal) -> list[str]:
        ids = [
            self.add_evidence(_bom_evidence(view.line)),
            self.add_evidence(_balance_evidence(view.line, view.material)),
            self.add_evidence(_calculation_evidence(view, self.data, units)),
        ]
        ids.extend(self.add_evidence(ref) for ref in _receipt_evidence(view.line, view.material))
        return [evidence_id for evidence_id in ids if evidence_id is not None]

    def add_material(self, view: _MaterialView, units: Decimal) -> None:
        ids = self._material_evidence_ids(view, units)
        code = view.code
        self.metric(f"gross_demand:{code}", view.demand, view.unit)
        self.metric(f"available_now:{code}", view.available, view.unit)
        self.metric(f"shortage:{code}", view.shortage, view.unit)
        self.metric(
            f"coverage_days:{code}",
            view.coverage_days,
            _DAYS,
            note=(
                None
                if view.coverage_days is not None
                else "No issues recorded in the last 14 days."
            ),
        )
        if not view.data_complete:
            self.complete = False
            self.notes.append(f"{code} has no stock balance record; treated as zero on hand.")
            self.data_quality_missing.append(code)
        if view.shortage > 0 and view.projected_shortage_at_due > 0:
            self.finding(
                finding_id=f"rm-shortage-{code}",
                severity="critical",
                code="MATERIAL_SHORTAGE",
                message=(
                    f"{code} is short {decimal_str(view.shortage)} {view.unit}: demand "
                    f"{decimal_str(view.demand)} {view.unit} against "
                    f"{decimal_str(view.available)} {view.unit} available, and open "
                    f"receipts do not close the gap by "
                    f"{self.data.order.due_date.isoformat()} (projected "
                    f"{decimal_str(view.projected_at_due)} {view.unit}). "
                    f"Material state: {view.state}."
                ),
                evidence_ids=ids,
            )
        elif view.shortage > 0:
            receipts = _receipts_by(view, self.data.order.due_date)
            self.finding(
                finding_id=f"rm-at-risk-{code}",
                severity="warning",
                code="MATERIAL_AT_RISK",
                message=(
                    f"{code} is short {decimal_str(view.shortage)} {view.unit} today and "
                    f"is only covered by {len(receipts)} expected receipt(s) due on or "
                    f"before {self.data.order.due_date.isoformat()} "
                    f"(projected {decimal_str(view.projected_at_due)} {view.unit}). "
                    f"Material state: {view.state}."
                ),
                evidence_ids=ids,
            )
        if view.available < view.reorder_point:
            self.finding(
                finding_id=f"rm-reorder-{code}",
                severity="warning",
                code="BELOW_REORDER_POINT",
                message=(
                    f"{code} available {decimal_str(view.available)} {view.unit} is below "
                    f"its reorder point {decimal_str(view.reorder_point)} {view.unit} "
                    f"({decimal_str(view.daily_consumption)} {view.unit}/day x "
                    f"{view.line.lead_time_days} days lead time + "
                    f"{decimal_str(view.line.safety_stock)} {view.unit} safety stock)."
                ),
                evidence_ids=ids,
            )
        if view.coverage_days is None:
            self.finding(
                finding_id=f"rm-consumption-{code}",
                severity="info",
                code="CONSUMPTION_UNKNOWN",
                message=(
                    f"No issues of {code} were recorded in the last "
                    f"{CONSUMPTION_WINDOW_DAYS} days, so days of coverage cannot be "
                    "calculated."
                ),
                evidence_ids=ids,
            )

    def add_plan_material(self, view: _MaterialView, allocated: Decimal) -> list[str]:
        ids = self._material_evidence_ids(view, allocated)
        code = view.code
        self.metric(f"gross_demand:{code}", view.demand, view.unit)
        self.metric(f"available_now:{code}", view.available, view.unit)
        self.metric(f"shortage:{code}", view.shortage, view.unit)
        if view.shortage > 0:
            self.finding(
                finding_id=f"rm-plan-short-{code}",
                severity="critical",
                code="PLAN_MATERIAL_SHORT",
                message=(
                    f"The plan needs {decimal_str(view.demand)} {view.unit} of {code} for "
                    f"{decimal_str(allocated)} units but only "
                    f"{decimal_str(view.available)} {view.unit} can be reserved; "
                    f"{decimal_str(view.shortage)} {view.unit} remain uncovered."
                ),
                evidence_ids=ids,
            )
        else:
            self.finding(
                finding_id=f"rm-plan-covered-{code}",
                severity="info",
                code="PLAN_MATERIAL_COVERED",
                message=(
                    f"The plan's {decimal_str(view.demand)} {view.unit} of {code} for "
                    f"{decimal_str(allocated)} units is covered by the "
                    f"{decimal_str(view.available)} {view.unit} available."
                ),
                evidence_ids=ids,
            )
        return ids

    def replenishment(self, short_views: list[_MaterialView], data: SnapshotData) -> None:
        for rank, view in enumerate(short_views):
            ids = self._material_evidence_ids(view, Decimal(data.order.remaining_units))
            needed_by = data.order.due_date - timedelta(days=view.line.lead_time_days)
            quantity = round_up_to_pack(view.shortage, view.line.pack_size)
            self.actions.append(
                RecommendedAction(
                    action_id=f"act-replenish-{view.code}",
                    kind="REPLENISHMENT_SUGGESTION",
                    summary=(
                        f"Suggest replenishing {decimal_str(quantity)} {view.unit} of "
                        f"{view.code} by {needed_by.isoformat()}."
                    ),
                    payload={
                        "material_code": view.code,
                        "suggested_quantity": decimal_str(quantity),
                        "unit": view.unit,
                        "needed_by": needed_by.isoformat(),
                        "note": SUGGESTION_NOTE,
                    },
                    evidence_ids=ids,
                    rank=rank,
                    source="deterministic",
                )
            )
            if needed_by < data.as_of_date:
                self.finding(
                    finding_id=f"rm-lead-time-{view.code}",
                    severity="warning",
                    code="LEAD_TIME_EXCEEDED",
                    message=(
                        f"{view.code} would have to be ordered by {needed_by.isoformat()} "
                        f"({view.line.lead_time_days} days before the due date "
                        f"{data.order.due_date.isoformat()}), which has already passed "
                        f"({data.as_of_date.isoformat()})."
                    ),
                    evidence_ids=ids,
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
                missing=sorted(set(self.data_quality_missing)),
                notes=self.notes,
            ),
        )


def _allocated_units(planning: AgentResult | None) -> Decimal | None:
    """The rank-0 ALLOCATION action's ``allocated_units``, or ``None``."""
    if planning is None or planning.status == "FAILED":
        return None
    actions = sorted(
        (action for action in planning.recommended_actions if action.kind == "ALLOCATION"),
        key=lambda action: action.rank,
    )
    if not actions:
        return None
    try:
        return Decimal(str(actions[0].payload.get("allocated_units", "0")))
    except ArithmeticError:
        return None


# --------------------------------------------------------------------------
# Tool handlers (snapshot only, always with record evidence)
# --------------------------------------------------------------------------


def _lookup(ctx: AgentContext, material_code: str) -> tuple[SnapshotBomLine, SnapshotMaterial]:
    for line in ctx.snapshot.bom.lines:
        if line.material_code == material_code:
            material = ctx.snapshot.materials.get(str(line.material_id))
            if material is None:
                raise ToolError(f"No stock record for {material_code} is in this run's snapshot.")
            return line, material
    known = ", ".join(line.material_code for line in ctx.snapshot.bom.lines) or "none"
    raise ToolError(f"Unknown material code {material_code!r}; this order's BOM has: {known}.")


def _planned_units(ctx: AgentContext) -> Decimal:
    if ctx.task_type == VALIDATE_TASK_TYPE:
        allocated = _allocated_units(ctx.dependency_results.get("planning"))
        if allocated is not None:
            return allocated
    return Decimal(ctx.snapshot.order.remaining_units)


async def _tool_material_position(ctx: AgentContext, arguments: Any) -> ToolResult:
    line, material = _lookup(ctx, arguments.material_code)
    view = _view(line, material, ctx.snapshot, _planned_units(ctx))
    if view is None:
        raise ToolError(
            f"{line.material_code} has no approved conversion from {line.bom_unit} to "
            f"{line.material_unit}, so its position cannot be computed."
        )
    evidence = [_bom_evidence(line)]
    balance = _balance_evidence(line, material)
    if balance is not None:
        evidence.append(balance)
    return ToolResult(
        data={
            "material_code": view.code,
            "unit": view.unit,
            "on_hand_accepted": decimal_str(material.on_hand_accepted),
            "reserved": decimal_str(material.reserved),
            "available_now": decimal_str(view.available),
            "gross_demand": decimal_str(view.demand),
            "shortage": decimal_str(view.shortage),
            "projected_balance_at_due_date": decimal_str(view.projected_at_due),
            "coverable_units": decimal_str(view.coverable_units),
            "reorder_point": decimal_str(view.reorder_point),
            "material_state": view.state,
        },
        evidence=evidence,
    )


async def _tool_expected_receipts(ctx: AgentContext, arguments: Any) -> ToolResult:
    line, material = _lookup(ctx, arguments.material_code)
    return ToolResult(
        data={
            "material_code": line.material_code,
            "unit": line.material_unit,
            "due_date": ctx.snapshot.order.due_date.isoformat(),
            "open_receipts": [
                {
                    "quantity": decimal_str(receipt.quantity),
                    "expected_date": receipt.expected_date.isoformat(),
                    "before_due_date": receipt.expected_date <= ctx.snapshot.order.due_date,
                }
                for receipt in material.open_receipts
            ],
        },
        evidence=_receipt_evidence(line, material),
    )


async def _tool_consumption_history(ctx: AgentContext, arguments: Any) -> ToolResult:
    line, material = _lookup(ctx, arguments.material_code)
    days = int(arguments.days)
    issues = [(issue.date, issue.quantity) for issue in material.issues_14d]
    average = average_daily_consumption(issues, days, ctx.snapshot.as_of_date)
    cutoff = ctx.snapshot.as_of_date - timedelta(days=days)
    evidence = [_bom_evidence(line)]
    balance = _balance_evidence(line, material)
    if balance is not None:
        evidence.append(balance)
    return ToolResult(
        data={
            "material_code": line.material_code,
            "unit": line.material_unit,
            "window_days": days,
            "average_daily_consumption": decimal_str(average),
            "issues": [
                {"date": when.isoformat(), "quantity": decimal_str(quantity)}
                for when, quantity in issues
                if cutoff < when <= ctx.snapshot.as_of_date
            ],
        },
        evidence=evidence,
    )


async def _tool_bom_demand(ctx: AgentContext, arguments: Any) -> ToolResult:  # noqa: ARG001
    units = _planned_units(ctx)
    rows: list[dict[str, Any]] = []
    evidence: list[EvidenceRef] = []
    for line in ctx.snapshot.bom.lines:
        evidence.append(_bom_evidence(line))
        material = ctx.snapshot.materials.get(str(line.material_id))
        view = _view(line, material, ctx.snapshot, units) if material is not None else None
        rows.append(
            {
                "material_code": line.material_code,
                "unit": line.material_unit,
                "quantity_per_unit": decimal_str(line.quantity_per_unit),
                "bom_unit": line.bom_unit,
                "wastage_fraction": decimal_str(line.wastage_fraction),
                "gross_demand": decimal_str(view.demand) if view else None,
                "shortage": decimal_str(view.shortage) if view else None,
            }
        )
    return ToolResult(data={"planned_units": decimal_str(units), "lines": rows}, evidence=evidence)


__all__ = ["ASSESS_TASK_TYPE", "VALIDATE_TASK_TYPE", "RMAgent"]
