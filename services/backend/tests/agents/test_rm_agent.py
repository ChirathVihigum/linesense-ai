"""The RM agent's deterministic assessment (rounds 0 and 1), without a model."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.agents.base import Assessment
from app.agents.rm import RMAgent
from app.orchestration.protocol import AgentResult
from app.orchestration.snapshot import (
    SnapshotBom,
    SnapshotBomLine,
    SnapshotIssue,
    SnapshotMaterial,
    SnapshotReceipt,
)
from app.seed import scenario as demo
from tests.helpers.agents import AS_OF, DUE_DATE, agent_context, snapshot_data

MATERIAL_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
BALANCE_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
BOM_LINE_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")


def _bom_line(**overrides: Any) -> SnapshotBomLine:
    defaults: dict[str, Any] = {
        "bom_line_id": BOM_LINE_ID,
        "material_id": MATERIAL_ID,
        "material_code": demo.DEMO_BOM_MATERIAL_CODE,
        "material_name": "Cotton jersey",
        "material_unit": "m",
        "bom_unit": "m",
        "quantity_per_unit": demo.DEMO_BOM_QUANTITY_PER_UNIT,
        "wastage_fraction": demo.DEMO_BOM_WASTAGE_FRACTION,
        "safety_stock": Decimal("100"),
        "lead_time_days": 3,
        "pack_size": Decimal("50"),
    }
    defaults.update(overrides)
    return SnapshotBomLine(**defaults)


def _material(**overrides: Any) -> SnapshotMaterial:
    defaults: dict[str, Any] = {
        "balance_id": BALANCE_ID,
        "balance_version": 7,
        "on_hand_accepted": demo.DEMO_BALANCE_ON_HAND_ACCEPTED,
        "reserved": demo.DEMO_BALANCE_RESERVED,
        "open_receipts": [],
        "issues_14d": [
            SnapshotIssue(date=AS_OF - timedelta(days=offset), quantity=Decimal("90"))
            for offset in range(14)
        ],
    }
    defaults.update(overrides)
    return SnapshotMaterial(**defaults)


def _snapshot(*, line: SnapshotBomLine | None = None, material: SnapshotMaterial | None = None):  # noqa: ANN202
    bom_line = line or _bom_line()
    return snapshot_data(
        bom=SnapshotBom(bom_version_id=uuid.uuid4(), version_no=2, lines=[bom_line]),
        materials={str(bom_line.material_id): material or _material()},
    )


def _codes(assessment: Assessment) -> list[str]:
    return [finding.code for finding in assessment.findings]


def _metric(assessment: Assessment, name: str) -> Decimal | None:
    return next(metric.value for metric in assessment.metrics if metric.name == name)


async def _assess(**ctx_overrides: Any) -> Assessment:
    ctx = agent_context(llm=None, **ctx_overrides)
    return await RMAgent().assess(ctx)


async def test_demo_numbers_produce_a_critical_shortage_with_balance_evidence() -> None:
    assessment = await _assess(snapshot=_snapshot())

    assert "MATERIAL_SHORTAGE" in _codes(assessment)
    shortage = next(f for f in assessment.findings if f.code == "MATERIAL_SHORTAGE")
    assert shortage.severity == "critical"
    assert shortage.source == "deterministic"

    assert _metric(assessment, "available_now:M01") == demo.DEMO_EXPECTED_AVAILABLE
    assert _metric(assessment, "gross_demand:M01") == demo.DEMO_EXPECTED_GROSS_DEMAND
    assert _metric(assessment, "shortage:M01") == demo.DEMO_EXPECTED_SHORTAGE
    assert _metric(assessment, "coverable_units") == demo.DEMO_EXPECTED_COVERABLE_UNITS

    evidence = {ref.evidence_id: ref for ref in assessment.evidence_refs}
    assert set(shortage.evidence_ids) <= set(evidence)
    balance_evidence = next(
        ref for ref in evidence.values() if ref.record_type == "material_balance"
    )
    assert balance_evidence.record_id == BALANCE_ID
    assert balance_evidence.record_version == 7
    assert balance_evidence.evidence_id in shortage.evidence_ids
    assert any(ref.kind == "calculation" for ref in evidence.values())
    assert "1260" in " ".join(ref.description for ref in evidence.values())
    assert assessment.data_quality.complete is True


async def test_receipt_before_due_date_downgrades_the_shortage_to_at_risk() -> None:
    material = _material(
        open_receipts=[
            SnapshotReceipt(id=uuid.uuid4(), quantity=Decimal("500"), expected_date=DUE_DATE)
        ]
    )
    assessment = await _assess(snapshot=_snapshot(material=material))

    assert "MATERIAL_AT_RISK" in _codes(assessment)
    assert "MATERIAL_SHORTAGE" not in _codes(assessment)
    at_risk = next(f for f in assessment.findings if f.code == "MATERIAL_AT_RISK")
    assert at_risk.severity == "warning"
    receipt_evidence = [
        ref for ref in assessment.evidence_refs if ref.record_type == "expected_receipt"
    ]
    assert len(receipt_evidence) == 1
    assert receipt_evidence[0].evidence_id in at_risk.evidence_ids


async def test_zero_consumption_reports_null_coverage_and_an_info_finding() -> None:
    assessment = await _assess(snapshot=_snapshot(material=_material(issues_14d=[])))

    assert _metric(assessment, "coverage_days:M01") is None
    assert "CONSUMPTION_UNKNOWN" in _codes(assessment)
    unknown = next(f for f in assessment.findings if f.code == "CONSUMPTION_UNKNOWN")
    assert unknown.severity == "info"


async def test_unsupported_unit_conversion_excludes_the_material_and_flags_data_quality() -> None:
    assessment = await _assess(snapshot=_snapshot(line=_bom_line(bom_unit="yd")))

    assert "UNIT_CONVERSION_MISSING" in _codes(assessment)
    finding = next(f for f in assessment.findings if f.code == "UNIT_CONVERSION_MISSING")
    assert finding.severity == "critical"
    assert assessment.data_quality.complete is False
    assert not [m for m in assessment.metrics if m.name == "shortage:M01"]
    assert not assessment.recommended_actions


async def test_replenishment_suggestion_rounds_up_to_the_pack_size() -> None:
    assessment = await _assess(snapshot=_snapshot())

    action = next(a for a in assessment.recommended_actions if a.kind == "REPLENISHMENT_SUGGESTION")
    assert action.payload["material_code"] == "M01"
    # shortage 160 m rounded up to the 50 m pack size.
    assert action.payload["suggested_quantity"] == "200"
    assert action.payload["unit"] == "m"
    assert action.payload["needed_by"] == (DUE_DATE - timedelta(days=3)).isoformat()
    assert action.payload["note"] == "Suggestion only — no purchase order is created"
    assert action.source == "deterministic"


async def test_lead_time_exceeded_when_the_order_by_date_is_already_past() -> None:
    assessment = await _assess(snapshot=_snapshot(line=_bom_line(lead_time_days=60)))

    assert "LEAD_TIME_EXCEEDED" in _codes(assessment)
    assert next(f for f in assessment.findings if f.code == "LEAD_TIME_EXCEEDED").severity == (
        "warning"
    )


async def test_below_reorder_point_is_a_warning() -> None:
    # 14 days of 90 m/day -> 90 m/day average, 3 days of lead time, 100 m safety
    # stock -> reorder point 370 m; 200 m on hand is below it.
    material = _material(on_hand_accepted=Decimal("200"), reserved=Decimal("0"))
    assessment = await _assess(snapshot=_snapshot(material=material))

    assert "BELOW_REORDER_POINT" in _codes(assessment)


# --------------------------------------------------------------------------
# Round 1: validate_plan_materials
# --------------------------------------------------------------------------


def _planning_result(allocated_units: str) -> AgentResult:
    now = date.today().isoformat() + "T00:00:00+00:00"  # noqa: DTZ011
    return AgentResult.model_validate(
        {
            "schema_version": "1.0",
            "task_id": str(uuid.uuid4()),
            "agent": "planning",
            "status": "SUCCEEDED",
            "summary": "Allocation proposed.",
            "summary_source": "model",
            "findings": [],
            "metrics": [],
            "recommended_actions": [
                {
                    "action_id": "act-material-limited",
                    "kind": "ALLOCATION",
                    "summary": f"Allocate {allocated_units} units.",
                    "payload": {
                        "option_code": "MATERIAL_LIMITED",
                        "allocations": [],
                        "allocated_units": allocated_units,
                        "unscheduled_units": "0",
                        "unscheduled_reason": None,
                        "finish_date": DUE_DATE.isoformat(),
                    },
                    "evidence_ids": [],
                    "rank": 0,
                    "source": "deterministic",
                }
            ],
            "evidence_refs": [],
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
                "prompt_version": "planning-v1",
                "degraded": False,
                "degraded_reason": None,
                "started_at": now,
                "completed_at": now,
            },
            "error_code": None,
        }
    )


async def _validate(allocated_units: str) -> Assessment:
    return await _assess(
        snapshot=_snapshot(),
        round=1,
        task_type="validate_plan_materials",
        dependency_results={"planning": _planning_result(allocated_units)},
    )


async def test_round_one_reserves_exactly_the_plan_demand_when_stock_covers_it() -> None:
    assessment = await _validate("873")

    action = next(a for a in assessment.recommended_actions if a.kind == "RESERVATION")
    assert action.rank == 0
    assert action.payload["reservations"] == [
        {
            "material_id": str(MATERIAL_ID),
            "material_code": "M01",
            "balance_id": str(BALANCE_ID),
            "quantity": "1099.98",
            "unit": "m",
        }
    ]
    assert action.payload["unreserved"] == []
    assert "PLAN_MATERIAL_COVERED" in _codes(assessment)


async def test_round_one_caps_the_reservation_and_reports_the_remainder() -> None:
    assessment = await _validate("1000")

    action = next(a for a in assessment.recommended_actions if a.kind == "RESERVATION")
    assert action.payload["reservations"][0]["quantity"] == "1100"
    assert action.payload["unreserved"] == [
        {"material_code": "M01", "quantity": "160", "unit": "m"}
    ]
    short = next(f for f in assessment.findings if f.code == "PLAN_MATERIAL_SHORT")
    assert short.severity == "critical"


async def test_round_one_without_a_planning_result_reports_missing_data() -> None:
    assessment = await _assess(
        snapshot=_snapshot(),
        round=1,
        task_type="validate_plan_materials",
        dependency_results={},
    )

    assert assessment.data_quality.complete is False
    assert "planning" in assessment.data_quality.missing
    assert not assessment.recommended_actions


async def test_round_one_omits_the_reservation_when_there_is_nothing_to_reserve() -> None:
    assessment = await _validate("0")

    assert not [a for a in assessment.recommended_actions if a.kind == "RESERVATION"]


@pytest.mark.parametrize("task_type", ["assess_material_readiness", "validate_plan_materials"])
async def test_tool_inputs_default_to_the_first_bom_material(task_type: str) -> None:
    ctx = agent_context(llm=None, snapshot=_snapshot(), task_type=task_type)
    tools = {tool.name: tool for tool in RMAgent().tools(ctx)}

    assert set(tools) == {
        "get_material_position",
        "get_expected_receipts",
        "get_consumption_history",
        "get_bom_demand",
    }
    schema = tools["get_material_position"].input_model.model_json_schema()
    assert schema["properties"]["material_code"]["default"] == "M01"
    history = tools["get_consumption_history"].input_model.model_json_schema()
    assert history["properties"]["days"]["default"] == 14


async def test_material_position_tool_returns_snapshot_numbers_with_record_evidence() -> None:
    ctx = agent_context(llm=None, snapshot=_snapshot())
    agent = RMAgent()
    ctx.assessment = await agent.assess(ctx)
    tool = next(t for t in agent.tools(ctx) if t.name == "get_material_position")

    result = await tool.handler(ctx, tool.input_model())

    assert result.data["available_now"] == "1100"
    assert any(ref.record_type == "material_balance" for ref in result.evidence)


async def test_unknown_material_code_is_a_tool_error() -> None:
    from app.agents.base import ToolError

    ctx = agent_context(llm=None, snapshot=_snapshot())
    agent = RMAgent()
    ctx.assessment = await agent.assess(ctx)
    tool = next(t for t in agent.tools(ctx) if t.name == "get_material_position")

    with pytest.raises(ToolError):
        await tool.handler(ctx, tool.input_model(material_code="NOPE"))
