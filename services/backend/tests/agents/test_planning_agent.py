"""The planning agent's deterministic candidate options, ranking and tools."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.agents.base import Assessment, ToolError
from app.agents.planning import PlanningAgent
from app.orchestration.protocol import AgentResult
from app.orchestration.snapshot import (
    SnapshotBom,
    SnapshotBomLine,
    SnapshotLine,
    SnapshotMaterial,
    SnapshotReceipt,
    SnapshotSlot,
)
from tests.helpers.agents import AS_OF, DUE_DATE, agent_context, snapshot_data

LINE_A = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
LINE_B = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")
RM_TASK_ID = uuid.UUID("cccccccc-0000-4000-8000-000000000003")
IE_TASK_ID = uuid.UUID("dddddddd-0000-4000-8000-000000000004")
PLANNING_R0_TASK_ID = uuid.UUID("eeeeeeee-0000-4000-8000-000000000005")

SAM_TOTAL = Decimal("6.7")

SHORT_MATERIAL_ID = uuid.UUID("f0000000-0000-4000-8000-00000000000a")
SPARE_MATERIAL_ID = uuid.UUID("f0000000-0000-4000-8000-00000000000b")
SHORT_RECEIPT_DATE = DUE_DATE + timedelta(days=4)
SPARE_RECEIPT_DATE = AS_OF + timedelta(days=1)


def _bom_line(material_id: uuid.UUID, code: str) -> SnapshotBomLine:
    return SnapshotBomLine(
        bom_line_id=uuid.uuid4(),
        material_id=material_id,
        material_code=code,
        material_name=f"Material {code}",
        material_unit="m",
        bom_unit="m",
        quantity_per_unit=Decimal("1.2"),
        wastage_fraction=Decimal("0.05"),
        safety_stock=Decimal("0"),
        lead_time_days=3,
        pack_size=None,
    )


def _material(receipt_date) -> SnapshotMaterial:  # noqa: ANN001
    return SnapshotMaterial(
        balance_id=uuid.uuid4(),
        balance_version=1,
        on_hand_accepted=Decimal("1500"),
        reserved=Decimal("400"),
        open_receipts=[
            SnapshotReceipt(id=uuid.uuid4(), quantity=Decimal("500"), expected_date=receipt_date)
        ],
        issues_14d=[],
    )


def _two_material_snapshot():  # noqa: ANN202
    """A BOM where the material that is NOT short has the earlier receipt."""
    short_line = _bom_line(SHORT_MATERIAL_ID, "M01")
    spare_line = _bom_line(SPARE_MATERIAL_ID, "M99")
    return _snapshot(
        bom=SnapshotBom(bom_version_id=uuid.uuid4(), version_no=1, lines=[short_line, spare_line]),
        materials={
            str(SHORT_MATERIAL_ID): _material(SHORT_RECEIPT_DATE),
            str(SPARE_MATERIAL_ID): _material(SPARE_RECEIPT_DATE),
        },
    )


def _line(line_id: uuid.UUID, code: str, *, compatible: bool = True) -> SnapshotLine:
    return SnapshotLine(
        line_id=line_id,
        code=code,
        name=f"Line {code}",
        operator_count=20,
        compatible=compatible,
        missing_skills=[] if compatible else ["BH"],
    )


def _slot(
    line_id: uuid.UUID,
    code: str,
    offset: int,
    shift: str,
    minutes: str,
    *,
    allocated: str = "0",
) -> SnapshotSlot:
    return SnapshotSlot(
        slot_id=uuid.uuid4(),
        line_id=line_id,
        line_code=code,
        slot_date=AS_OF + timedelta(days=offset),
        shift_code=shift,
        available_operator_minutes=Decimal(minutes),
        planned_efficiency=Decimal("1"),
        allocated_standard_minutes=Decimal(allocated),
        version=1,
    )


def _snapshot(**overrides: Any):  # noqa: ANN202
    lines = overrides.pop("lines", [_line(LINE_A, "L1"), _line(LINE_B, "L2")])
    slots = overrides.pop(
        "slots",
        [
            _slot(LINE_A, "L1", 0, "A", "4000"),
            _slot(LINE_B, "L2", 0, "A", "4000"),
            _slot(LINE_A, "L1", 1, "A", "4000"),
            _slot(LINE_B, "L2", 1, "A", "4000"),
        ],
    )
    return snapshot_data(lines=lines, slots=slots, sam_total_minutes=SAM_TOTAL, **overrides)


def _agent_result(
    agent: str,
    task_id: uuid.UUID,
    *,
    findings: list[dict[str, Any]] | None = None,
    metrics: list[dict[str, Any]] | None = None,
    actions: list[dict[str, Any]] | None = None,
    status: str = "SUCCEEDED",
    summary: str = "Dependency summary.",
) -> AgentResult:
    stamp = "2026-09-17T00:00:00+00:00"
    return AgentResult.model_validate(
        {
            "schema_version": "1.0",
            "task_id": str(task_id),
            "agent": agent,
            "status": status,
            "summary": summary,
            "summary_source": "deterministic",
            "findings": findings or [],
            "metrics": metrics or [],
            "recommended_actions": actions or [],
            "evidence_refs": [
                {
                    "evidence_id": "ev-bal-M01",
                    "kind": "calculation",
                    "description": "M01 shortage = 160 m",
                }
            ],
            "warnings": [],
            "input_versions": {},
            "data_quality": {"complete": True, "missing": [], "notes": []},
            "execution_metadata": {
                "provider": "fixture",
                "model": "fixture-scripted-v1",
                "model_calls": 1,
                "tool_calls": [],
                "input_tokens": 1,
                "output_tokens": 1,
                "prompt_version": f"{agent}-v1",
                "degraded": False,
                "degraded_reason": None,
                "started_at": stamp,
                "completed_at": stamp,
            },
            "error_code": None,
        }
    )


def _rm_result(coverable: str = "873") -> AgentResult:
    return _agent_result(
        "rm",
        RM_TASK_ID,
        findings=[
            {
                "finding_id": "rm-shortage-M01",
                "severity": "critical",
                "code": "MATERIAL_SHORTAGE",
                "message": "M01 is short 160 m.",
                "evidence_ids": ["ev-bal-M01"],
                "source": "deterministic",
            }
        ],
        metrics=[{"name": "coverable_units", "value": coverable, "unit": "units"}],
        summary="M01 is short 160 m; materials cover 873 units.",
    )


def _ie_result(line_code: str = "L1") -> AgentResult:
    return _agent_result(
        "ie",
        IE_TASK_ID,
        findings=[
            {
                "finding_id": "ie-1",
                "severity": "warning",
                "code": "LINE_CAPACITY_BELOW_PLAN",
                "message": f"Line {line_code} is below the planned output.",
                "evidence_ids": [],
                "source": "deterministic",
            }
        ],
    )


def _codes(assessment: Assessment) -> list[str]:
    return [finding.code for finding in assessment.findings]


def _option_codes(assessment: Assessment) -> list[str]:
    return [
        str(action.payload["option_code"])
        for action in sorted(assessment.recommended_actions, key=lambda a: a.rank)
    ]


async def _assess(**overrides: Any) -> Assessment:
    overrides.setdefault("task_type", "propose_allocation")
    ctx = agent_context(llm=None, **overrides)
    return await PlanningAgent().assess(ctx)


async def test_round_zero_ranks_full_earliest_first_when_capacity_suffices() -> None:
    assessment = await _assess(
        snapshot=_snapshot(), dependency_results={"rm": _rm_result(), "ie": _ie_result("L9")}
    )

    assert _option_codes(assessment)[0] == "FULL_EARLIEST"
    first = sorted(assessment.recommended_actions, key=lambda a: a.rank)[0]
    assert first.kind == "ALLOCATION"
    assert first.payload["allocated_units"] == "1000"
    assert first.payload["unscheduled_units"] == "0"
    assert "MATERIAL_LIMITED" in _option_codes(assessment)
    assert "MATERIAL_CONSTRAINT_KNOWN" in _codes(assessment)
    constraint = next(f for f in assessment.findings if f.code == "MATERIAL_CONSTRAINT_KNOWN")
    cited = {
        ref.evidence_id: ref
        for ref in assessment.evidence_refs
        if ref.evidence_id in constraint.evidence_ids
    }
    assert any(
        ref.kind == "record" and ref.record_type == "agent_result" and ref.record_id == RM_TASK_ID
        for ref in cited.values()
    )
    metrics = {metric.name: metric for metric in assessment.metrics}
    assert metrics["required_standard_minutes"].value == Decimal("6700")
    assert metrics["allocated_units"].value == Decimal("1000")
    assert metrics["unscheduled_units"].value == Decimal("0")
    # The model may rerank the actions, so the numbers say which option they
    # describe.
    assert metrics["allocated_units"].note == (
        "For the deterministically ranked-first option FULL_EARLIEST."
    )
    assert metrics["unscheduled_units"].note == metrics["allocated_units"].note


async def test_round_one_ranks_the_material_limited_option_first() -> None:
    assessment = await _assess(
        round=1,
        task_type="revise_allocation",
        snapshot=_snapshot(),
        dependency_results={
            "rm": _rm_result(),
            "planning_r0": _agent_result("planning", PLANNING_R0_TASK_ID),
        },
    )

    assert _option_codes(assessment)[0] == "MATERIAL_LIMITED"
    first = sorted(assessment.recommended_actions, key=lambda a: a.rank)[0]
    assert first.payload["allocated_units"] == "873"
    revised = next(f for f in assessment.findings if f.code == "REVISED_FOR_MATERIAL")
    assert revised.severity == "warning"
    assert "873" in revised.message
    assert "1000" in revised.message
    assert any(
        ref.record_type == "agent_result" and ref.record_id == PLANNING_R0_TASK_ID
        for ref in assessment.evidence_refs
    )


async def test_revision_quotes_the_short_material_s_receipt_not_another_s() -> None:
    """A healthy material's earlier receipt must never stand in for the short one."""
    rm = _agent_result(
        "rm",
        RM_TASK_ID,
        findings=[
            {
                "finding_id": "rm-shortage-M01",
                "severity": "critical",
                "code": "MATERIAL_SHORTAGE",
                "message": "M01 is short 160 m.",
                "evidence_ids": ["ev-bal-M01"],
                "source": "deterministic",
            }
        ],
        metrics=[
            {"name": "coverable_units", "value": "873", "unit": "units"},
            {"name": "shortage:M01", "value": "160", "unit": "m"},
            {"name": "shortage:M99", "value": "0", "unit": "m"},
        ],
        summary="M01 is short 160 m.",
    )
    assessment = await _assess(
        round=1,
        task_type="revise_allocation",
        snapshot=_two_material_snapshot(),
        dependency_results={
            "rm": rm,
            "planning_r0": _agent_result("planning", PLANNING_R0_TASK_ID),
        },
    )

    revised = next(f for f in assessment.findings if f.code == "REVISED_FOR_MATERIAL")
    assert SHORT_RECEIPT_DATE.isoformat() in revised.message
    assert SPARE_RECEIPT_DATE.isoformat() not in revised.message
    assert "M99" not in revised.message
    assert "first open receipt of M01" in revised.message
    assert "after the due date" in revised.message


async def test_revision_says_so_when_the_short_material_has_no_receipt() -> None:
    snapshot = _two_material_snapshot()
    snapshot.materials[str(SHORT_MATERIAL_ID)].open_receipts = []
    rm = _agent_result(
        "rm",
        RM_TASK_ID,
        metrics=[
            {"name": "coverable_units", "value": "873", "unit": "units"},
            {"name": "shortage:M01", "value": "160", "unit": "m"},
        ],
    )
    assessment = await _assess(
        round=1,
        task_type="revise_allocation",
        snapshot=snapshot,
        dependency_results={"rm": rm},
    )

    revised = next(f for f in assessment.findings if f.code == "REVISED_FOR_MATERIAL")
    assert "No open receipt is expected for the short material(s) (M01)." in revised.message
    assert SPARE_RECEIPT_DATE.isoformat() not in revised.message


async def test_no_compatible_line_is_critical_and_proposes_nothing() -> None:
    assessment = await _assess(
        snapshot=_snapshot(lines=[_line(LINE_A, "L1", compatible=False)], slots=[]),
        dependency_results={"rm": _rm_result()},
    )

    assert "NO_COMPATIBLE_LINE" in _codes(assessment)
    assert next(f for f in assessment.findings if f.code == "NO_COMPATIBLE_LINE").severity == (
        "critical"
    )
    assert assessment.recommended_actions == []


async def test_capacity_short_of_the_order_reports_unscheduled_quantity() -> None:
    assessment = await _assess(
        snapshot=_snapshot(slots=[_slot(LINE_A, "L1", 0, "A", "670")]),
        dependency_results={"rm": _rm_result(), "ie": _ie_result("L9")},
    )

    assert "UNSCHEDULED_QUANTITY" in _codes(assessment)
    first = sorted(assessment.recommended_actions, key=lambda a: a.rank)[0]
    assert first.payload["allocated_units"] == "100"
    assert first.payload["unscheduled_units"] == "900"
    assert first.payload["unscheduled_reason"] == "INSUFFICIENT_CAPACITY_BEFORE_DUE_DATE"


async def test_slots_after_the_due_date_are_never_used() -> None:
    late = _slot(LINE_A, "L1", (DUE_DATE - AS_OF).days + 1, "A", "100000")
    assessment = await _assess(
        snapshot=_snapshot(slots=[_slot(LINE_A, "L1", 0, "A", "670"), late]),
        dependency_results={"rm": _rm_result()},
    )

    first = sorted(assessment.recommended_actions, key=lambda a: a.rank)[0]
    used = {row["slot_id"] for row in first.payload["allocations"]}
    assert str(late.slot_id) not in used
    assert first.payload["unscheduled_units"] == "900"


async def test_ie_bottleneck_risk_pushes_an_option_behind_an_equivalent_one() -> None:
    # Both lines can finish the whole order on the first day. The greedy
    # earliest plan lands on L1 (which carries the IE risk); the single-line
    # option picks L2 (the most remaining capacity) and must outrank it.
    assessment = await _assess(
        snapshot=_snapshot(
            slots=[
                _slot(LINE_A, "L1", 0, "A", "100000"),
                _slot(LINE_B, "L2", 0, "A", "200000"),
            ]
        ),
        dependency_results={"rm": _rm_result(), "ie": _ie_result("L1")},
    )

    assert "IE_BOTTLENECK_RISK" in _codes(assessment)
    risky = next(f for f in assessment.findings if f.code == "IE_BOTTLENECK_RISK")
    assert risky.severity == "warning"
    ranked = sorted(assessment.recommended_actions, key=lambda a: a.rank)
    risky_ids = {
        action.action_id
        for action in ranked
        if any(row["line_code"] == "L1" for row in action.payload["allocations"])
    }
    safe_ids = [action.action_id for action in ranked if action.action_id not in risky_ids]
    assert safe_ids, "an option avoiding the risky line must exist"
    assert ranked[0].action_id in safe_ids


async def test_missing_dependencies_are_reported_in_data_quality() -> None:
    assessment = await _assess(snapshot=_snapshot(), dependency_results={})

    assert set(assessment.data_quality.missing) >= {"rm", "ie"}
    assert assessment.data_quality.complete is False
    assert "DEPENDENCY_MISSING" in _codes(assessment)
    assert "MATERIAL_LIMITED" not in _option_codes(assessment)


async def test_a_failed_rm_result_counts_as_missing() -> None:
    failed = _agent_result("rm", RM_TASK_ID, status="FAILED", summary="RM failed.")
    assessment = await _assess(snapshot=_snapshot(), dependency_results={"rm": failed})

    assert "rm" in assessment.data_quality.missing


async def test_line_overcommitted_warns_per_slot_above_the_threshold() -> None:
    assessment = await _assess(
        snapshot=_snapshot(slots=[_slot(LINE_A, "L1", 0, "A", "7000", allocated="600")]),
        dependency_results={"rm": _rm_result(), "ie": _ie_result("L9")},
    )

    assert "LINE_OVERCOMMITTED" in _codes(assessment)
    overcommit = next(f for f in assessment.findings if f.code == "LINE_OVERCOMMITTED")
    assert overcommit.message.startswith("Under option FULL_EARLIEST, line L1 ")


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


async def _agent_with_assessment(**overrides: Any):  # noqa: ANN202
    overrides.setdefault("task_type", "propose_allocation")
    ctx = agent_context(llm=None, **overrides)
    agent = PlanningAgent()
    ctx.assessment = await agent.assess(ctx)
    return agent, ctx


async def test_simulation_adds_a_selectable_candidate_action() -> None:
    agent, ctx = await _agent_with_assessment(
        snapshot=_snapshot(), dependency_results={"rm": _rm_result()}
    )
    tool = next(t for t in agent.tools(ctx) if t.name == "simulate_allocation")
    assert ctx.assessment is not None
    before = len(ctx.assessment.recommended_actions)

    result = await tool.handler(ctx, tool.input_model(max_units=500, line_codes=["L2"]))

    assert len(ctx.assessment.recommended_actions) == before + 1
    added = ctx.assessment.recommended_actions[-1]
    assert added.payload["option_code"] == "SIMULATED"
    assert added.source == "deterministic"
    assert added.rank == before
    assert added.payload["allocated_units"] == "500"
    assert result.data["action_id"] == added.action_id
    # The action cites only evidence already in the assessment, so a degraded
    # outcome (which drops tool evidence) can never leave a dangling citation.
    assert set(added.evidence_ids) <= {ref.evidence_id for ref in ctx.assessment.evidence_refs}
    # What the simulation discovered comes back as tool evidence, which the
    # loop registers so the model may cite it.
    returned = {ref.evidence_id for ref in result.evidence}
    assert returned
    assert returned.isdisjoint({ref.evidence_id for ref in ctx.assessment.evidence_refs})
    assert set(result.data["new_evidence_ids"]) == returned
    assert any(ref.evidence_id.startswith("ev-plan-SIMULATED-") for ref in result.evidence)


async def test_simulation_with_an_unknown_line_code_is_a_tool_error() -> None:
    agent, ctx = await _agent_with_assessment(
        snapshot=_snapshot(), dependency_results={"rm": _rm_result()}
    )
    tool = next(t for t in agent.tools(ctx) if t.name == "simulate_allocation")

    with pytest.raises(ToolError):
        await tool.handler(ctx, tool.input_model(line_codes=["NOPE"]))


async def test_dependency_findings_tool_reports_the_rm_findings() -> None:
    agent, ctx = await _agent_with_assessment(
        snapshot=_snapshot(), dependency_results={"rm": _rm_result()}
    )
    tool = next(t for t in agent.tools(ctx) if t.name == "get_dependency_findings")

    result = await tool.handler(ctx, tool.input_model())

    assert result.data["agent"] == "rm"
    assert result.data["findings"][0]["code"] == "MATERIAL_SHORTAGE"


async def test_planning_tool_defaults_allow_a_scripted_call() -> None:
    agent, ctx = await _agent_with_assessment(
        snapshot=_snapshot(), dependency_results={"rm": _rm_result()}
    )
    tools = {tool.name: tool for tool in agent.tools(ctx)}

    assert list(tools) == [
        "get_dependency_findings",
        "list_compatible_lines",
        "get_remaining_capacity",
        "simulate_allocation",
    ]
    for tool in tools.values():
        schema = tool.input_model.model_json_schema()
        for name, prop in schema.get("properties", {}).items():
            assert "default" in prop, f"{tool.name}.{name} needs a default"


async def test_remaining_capacity_tool_reports_slot_minutes_with_record_evidence() -> None:
    agent, ctx = await _agent_with_assessment(
        snapshot=_snapshot(), dependency_results={"rm": _rm_result()}
    )
    tool = next(t for t in agent.tools(ctx) if t.name == "get_remaining_capacity")

    result = await tool.handler(ctx, tool.input_model(line_code="L1"))

    assert result.data["line_code"] == "L1"
    assert Decimal(str(result.data["remaining_standard_minutes"])) == Decimal("8000")
    assert all(ref.record_type == "capacity_slot" for ref in result.evidence)


async def test_unknown_line_in_remaining_capacity_is_a_tool_error() -> None:
    agent, ctx = await _agent_with_assessment(
        snapshot=_snapshot(), dependency_results={"rm": _rm_result()}
    )
    tool = next(t for t in agent.tools(ctx) if t.name == "get_remaining_capacity")

    with pytest.raises(ToolError):
        await tool.handler(ctx, tool.input_model(line_code="NOPE"))


def test_due_date_is_after_the_as_of_date() -> None:
    assert isinstance(DUE_DATE, date)
    assert DUE_DATE > AS_OF
