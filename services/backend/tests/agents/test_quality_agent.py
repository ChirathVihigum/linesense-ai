"""The quality agent: deterministic facts decide, the model only explains."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.agents.base import Assessment
from app.agents.quality import QualityAgent
from app.llm import FixtureLLMClient, FixtureRequest, LLMResponse, LLMToolCall
from app.orchestration.snapshot import SnapshotQuality, SnapshotShipment
from app.orchestration.synthesis import build_order_report
from tests.helpers.agents import agent_context, run_row, snapshot_data, task_row

POLICY_ID = uuid.UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
INSPECTION_ID = uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
HOLD_ID = uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
MODEL_CLAIM = "All clear — ready to ship"


def _policy(*, is_demo: bool = True) -> dict[str, Any]:
    return {
        "id": str(POLICY_ID),
        "code": "QP-DEMO",
        "version_no": 1,
        "is_demo": is_demo,
        "rules": {
            "sample_size": 20,
            "max_defective_units": 2,
            "max_critical_defects": 0,
            "required_inspection_types": ["INLINE", "FINAL"],
        },
    }


def _inspection(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "id": str(INSPECTION_ID),
        "inspection_type": "FINAL",
        "inspected_units": 50,
        "defective_units": 7,
        "result": "FAIL",
        "policy_version_id": str(POLICY_ID),
        "line_id": None,
        "inspected_at": "2026-09-18T04:00:00+00:00",
        "defects": [
            {"defect_code": "BROKEN_STITCH", "severity": "MAJOR", "count": 5, "operation_id": None},
            {"defect_code": "STAIN", "severity": "MINOR", "count": 3, "operation_id": None},
        ],
    }
    defaults.update(overrides)
    return defaults


def _quality(**overrides: Any) -> SnapshotQuality:
    defaults: dict[str, Any] = {
        "policy": _policy(),
        "inspections": [],
        "active_holds": [],
        "releases": [],
        "shipment": SnapshotShipment(
            eligible=False,
            reasons=["PRODUCTION_NOT_COMPLETE", "INSPECTION_MISSING:FINAL", "NO_QUALITY_RELEASE"],
        ),
        "quality_state": "NOT_INSPECTED",
    }
    defaults.update(overrides)
    return SnapshotQuality(**defaults)


def _snapshot(**overrides: Any):  # noqa: ANN202
    return snapshot_data(quality=_quality(**overrides))


async def _assess(**overrides: Any) -> Assessment:
    ctx = agent_context(llm=None, task_type="assess_quality_status", **overrides)
    return await QualityAgent().assess(ctx)


def _codes(assessment: Assessment) -> list[str]:
    return [finding.code for finding in assessment.findings]


def _severity(assessment: Assessment, code: str) -> str:
    return next(finding.severity for finding in assessment.findings if finding.code == code)


def _metric(assessment: Assessment, name: str) -> Decimal | None:
    return next(metric.value for metric in assessment.metrics if metric.name == name)


# --------------------------------------------------------------------------


async def test_an_uninspected_order_is_pending_not_passed() -> None:
    assessment = await _assess(snapshot=_snapshot())

    assert "NOT_INSPECTED" in _codes(assessment)
    finding = next(f for f in assessment.findings if f.code == "NOT_INSPECTED")
    assert finding.severity == "info"
    assert finding.message == ("Quality pending — no inspection recorded; this is not a pass.")
    assert "SHIPMENT_INELIGIBLE" in _codes(assessment)
    ineligible = next(f for f in assessment.findings if f.code == "SHIPMENT_INELIGIBLE")
    assert "INSPECTION_MISSING:FINAL" in ineligible.message


async def test_an_active_hold_is_critical_and_only_suggests_a_review() -> None:
    assessment = await _assess(
        snapshot=_snapshot(
            active_holds=[
                {
                    "id": str(HOLD_ID),
                    "reason": "Sleeve seam failures on the final audit",
                    "inspection_id": str(INSPECTION_ID),
                    "created_at": "2026-09-18T05:00:00+00:00",
                }
            ],
            inspections=[_inspection()],
            quality_state="HOLD",
        )
    )

    assert _severity(assessment, "ACTIVE_HOLD") == "critical"
    actions = [a for a in assessment.recommended_actions if a.kind == "QUALITY_HOLD_REVIEW"]
    assert len(actions) == 1
    assert actions[0].payload["hold_id"] == str(HOLD_ID)
    assert "release" not in actions[0].summary.lower() or "suggest" in actions[0].summary.lower()


async def test_the_demo_policy_is_flagged_as_a_demo() -> None:
    assessment = await _assess(snapshot=_snapshot())
    assert _severity(assessment, "DEMO_POLICY") == "warning"


async def test_a_missing_policy_is_critical_and_blocks_shipment() -> None:
    assessment = await _assess(
        snapshot=_snapshot(
            policy=None,
            shipment=SnapshotShipment(eligible=False, reasons=["POLICY_UNKNOWN"]),
        )
    )

    assert _severity(assessment, "POLICY_MISSING") == "critical"
    assert "SHIPMENT_INELIGIBLE" in _codes(assessment)
    assert assessment.data_quality.complete is False


async def test_a_failed_final_inspection_is_critical_with_its_rates() -> None:
    assessment = await _assess(
        snapshot=_snapshot(inspections=[_inspection()], quality_state="PENDING")
    )

    assert _severity(assessment, "INSPECTION_FAILED") == "critical"
    assert _metric(assessment, f"defective_rate:{INSPECTION_ID}") == Decimal(7) / Decimal(50)
    assert _metric(assessment, f"dhu:{INSPECTION_ID}") == Decimal(8) / Decimal(50) * 100
    assert _metric(assessment, "defect_count:BROKEN_STITCH") == Decimal(5)


async def test_rates_are_unknown_when_nothing_was_inspected() -> None:
    assessment = await _assess(
        snapshot=_snapshot(
            inspections=[
                _inspection(
                    inspected_units=0,
                    defective_units=0,
                    result="INSUFFICIENT_SAMPLE",
                    defects=[],
                )
            ]
        )
    )

    assert _metric(assessment, f"defective_rate:{INSPECTION_ID}") is None
    assert _metric(assessment, f"dhu:{INSPECTION_ID}") is None


# --------------------------------------------------------------------------
# The model may not change a quality fact
# --------------------------------------------------------------------------


@pytest.fixture
def budget(monkeypatch: pytest.MonkeyPatch) -> None:
    async def reserve(session_factory: Any, run_id: Any) -> bool:
        return True

    async def usage(
        session_factory: Any, run_id: Any, *, input_tokens: int, output_tokens: int
    ) -> None:
        return None

    monkeypatch.setattr("app.agents.base.reserve_model_call", reserve)
    monkeypatch.setattr("app.agents.base.record_usage", usage)


def _overclaiming_script(request: FixtureRequest) -> LLMResponse:
    """A model that submits an unfounded all-clear on its first turn."""
    return LLMResponse(
        text=None,
        tool_calls=[
            LLMToolCall(
                id="call-1",
                name="submit_assessment",
                arguments={
                    "summary": MODEL_CLAIM,
                    "selected_action_id": None,
                    "action_rationale": None,
                    "finding_notes": [],
                    "revision_note": None,
                    "cited_evidence_ids": [],
                },
            )
        ],
        stop_reason="tool_use",
        input_tokens=10,
        output_tokens=5,
        provider="fixture",
        model=FixtureLLMClient.model,
        request_id=None,
        raw_content=None,
    )


async def test_a_model_all_clear_never_makes_the_shipment_eligible(budget: None) -> None:
    snapshot = _snapshot(
        active_holds=[
            {
                "id": str(HOLD_ID),
                "reason": "Sleeve seam failures on the final audit",
                "inspection_id": str(INSPECTION_ID),
                "created_at": "2026-09-18T05:00:00+00:00",
            }
        ],
        inspections=[_inspection()],
        quality_state="HOLD",
    )
    task = task_row(recipient="quality")
    ctx = agent_context(
        llm=FixtureLLMClient(_overclaiming_script),
        task_type="assess_quality_status",
        task_id=task.id,
        snapshot=snapshot,
    )

    result = await QualityAgent().run(ctx)

    assert result.status == "SUCCEEDED"
    assert result.summary == MODEL_CLAIM
    assert result.summary_source == "model"
    # Every deterministic fact survives the model's claim.
    codes = [finding.code for finding in result.findings]
    assert "ACTIVE_HOLD" in codes
    assert "SHIPMENT_INELIGIBLE" in codes
    assert "SHIPMENT_ELIGIBLE" not in codes
    eligible = next(metric for metric in result.metrics if metric.name == "shipment_eligible")
    assert eligible.value == Decimal(0)

    report = build_order_report(
        run_row(order_id=snapshot.order.id),
        snapshot,
        {task.id: result},
        None,
        tasks=[task],
    )
    assert report.shipment.eligible is False
    assert report.shipment.source == "Calculated from records"
    assert MODEL_CLAIM in [summary.summary for summary in report.agent_summaries]
    assert report.states.quality == "HOLD"
