"""The IE agent's deterministic assessment (no model, no database).

Every case is built from the demo scenario's own cycle observations, so the
numbers asserted here are the ones the IE screens show.
"""

from __future__ import annotations

import statistics
import uuid
from decimal import Decimal
from typing import Any

from app.agents.base import Assessment
from app.agents.ie import IEAgent
from app.domain.ie.service import ASSUMPTIONS
from app.orchestration.snapshot import SnapshotLine, SnapshotOperation
from app.seed import scenario as demo
from tests.helpers.agents import agent_context, snapshot_data

L2_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2")
L9_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa9")
STYLE_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
OPERATION_IDS = {
    code: uuid.UUID(f"cccccccc-cccc-4ccc-8ccc-cccccccccc{index:02d}")
    for index, (_, code, _, _, _, _) in enumerate(demo.DEMO_STYLE_OPERATIONS, start=1)
}
ALIAS_CODE = "KTN-OP-001"
# 20 operators x 60 minutes / 6.7 SAM minutes at efficiency 1.
SAM_UNITS_PER_HOUR = Decimal("179.104477611940298507462686567")


def _operations() -> list[SnapshotOperation]:
    return [
        SnapshotOperation(
            operation_id=OPERATION_IDS[code],
            sequence=sequence,
            code=code,
            name=name,
            sam_minutes=sam,
            skill_code=skill,
        )
        for sequence, code, name, sam, skill, _machine in demo.DEMO_STYLE_OPERATIONS
    ]


def _operation_analysis(code: str, *, samples: int = 5) -> dict[str, Any]:
    observed = demo.DEMO_IE_OBSERVED_SECONDS_BY_OPERATION[code][:samples]
    parallel = demo.DEMO_IE_PARALLEL_OPERATORS_BY_OPERATION[code]
    _sequence, _code, name, sam, _skill, _machine = next(
        row for row in demo.DEMO_STYLE_OPERATIONS if row[1] == code
    )
    enough = samples >= 3
    representative = Decimal(statistics.median(observed)) if enough else None
    return {
        "operation_id": str(OPERATION_IDS[code]),
        "code": code,
        "name": name,
        "sam_minutes": str(sam),
        "sample_count": samples,
        "representative_seconds": str(representative) if representative is not None else None,
        "parallel_operators": parallel,
        "effective_seconds": (
            str(representative / parallel) if representative is not None else None
        ),
        "insufficient_samples": not enough,
    }


def _analysis(*, short_sampled: str | None = None) -> dict[str, Any]:
    """The demo line/style analysis, optionally with one under-sampled operation."""
    operations = [
        _operation_analysis(code, samples=1 if code == short_sampled else 5)
        for _, code, _, _, _, _ in demo.DEMO_STYLE_OPERATIONS
    ]
    effective = [
        Decimal(operation["effective_seconds"])
        for operation in operations
        if operation["effective_seconds"] is not None
    ]
    balance: dict[str, Any] | None = None
    if short_sampled is None:
        bottleneck = max(effective)
        balance = {
            "effective_cycles": [str(value) for value in effective],
            "bottleneck_index": effective.index(bottleneck),
            "bottleneck_effective_seconds": str(bottleneck),
            "units_per_hour": str(Decimal(3600) / bottleneck),
            "balance_index_percent": str(
                sum(effective, Decimal(0)) / (Decimal(len(effective)) * bottleneck) * 100
            ),
        }
    return {
        "line_id": str(L2_ID),
        "style_id": str(STYLE_ID),
        "operations": operations,
        "balance": balance,
        "observed_units_per_hour": "52",
        "sam_units_per_hour": str(SAM_UNITS_PER_HOUR),
        "assumptions": list(ASSUMPTIONS),
        "limitations": [],
        "data_versions": {"cycle_observations_considered": 35, "line_operator_count": 20},
    }


def _line(line_id: uuid.UUID, code: str) -> SnapshotLine:
    return SnapshotLine(
        line_id=line_id,
        code=code,
        name=f"Line {code}",
        operator_count=20,
        compatible=True,
        missing_skills=[],
    )


def _snapshot(**overrides: Any):  # noqa: ANN202
    defaults: dict[str, Any] = {
        "operations": _operations(),
        "sam_total_minutes": demo.DEMO_STYLE_SAM_TOTAL,
        "lines": [_line(L2_ID, demo.DEMO_IE_LINE_CODE)],
        "ie": {str(L2_ID): _analysis()},
    }
    defaults.update(overrides)
    return snapshot_data(**defaults)


async def _assess(**overrides: Any) -> Assessment:
    ctx = agent_context(llm=None, task_type="assess_line_capability", **overrides)
    return await IEAgent().assess(ctx)


def _codes(assessment: Assessment) -> list[str]:
    return [finding.code for finding in assessment.findings]


def _metric(assessment: Assessment, name: str) -> Decimal | None:
    return next(metric.value for metric in assessment.metrics if metric.name == name)


def _texts(assessment: Assessment) -> list[str]:
    return [
        assessment.summary,
        *(finding.message for finding in assessment.findings),
        *(action.summary for action in assessment.recommended_actions),
        *(str(action.payload) for action in assessment.recommended_actions),
        *(evidence.description for evidence in assessment.evidence_refs),
    ]


# --------------------------------------------------------------------------


async def test_the_demo_line_reports_its_bottleneck_operation() -> None:
    assessment = await _assess(snapshot=_snapshot())

    assert "BOTTLENECK_OPERATION" in _codes(assessment)
    bottleneck = next(f for f in assessment.findings if f.code == "BOTTLENECK_OPERATION")
    assert demo.DEMO_IE_BOTTLENECK_OPERATION_CODE in bottleneck.message
    assert bottleneck.severity == "warning"

    line = demo.DEMO_IE_LINE_CODE
    seconds = _metric(assessment, f"bottleneck_seconds:{line}")
    assert seconds is not None
    assert seconds >= demo.DEMO_IE_EXPECTED_BOTTLENECK_SECONDS_LOW
    assert seconds <= demo.DEMO_IE_EXPECTED_BOTTLENECK_SECONDS_HIGH
    assert _metric(assessment, f"units_per_hour:{line}") == Decimal(60)
    balance_index = _metric(assessment, f"balance_index:{line}")
    assert balance_index is not None
    assert Decimal(65) < balance_index < Decimal(67)
    assert _metric(assessment, f"sam_units_per_hour:{line}") == SAM_UNITS_PER_HOUR
    assert assessment.data_quality.complete is True


async def test_a_line_below_its_sam_capacity_is_flagged_for_review() -> None:
    assessment = await _assess(snapshot=_snapshot())

    assert "LINE_CAPACITY_BELOW_PLAN" in _codes(assessment)
    capacity = next(f for f in assessment.findings if f.code == "LINE_CAPACITY_BELOW_PLAN")
    # The planning agent recognises the risky line through record evidence.
    line_refs = [
        ref
        for ref in assessment.evidence_refs
        if ref.evidence_id in capacity.evidence_ids and ref.record_type == "line"
    ]
    assert [ref.record_id for ref in line_refs] == [L2_ID]

    actions = [a for a in assessment.recommended_actions if a.kind == "IE_REVIEW"]
    assert len(actions) == 1
    action = actions[0]
    assert demo.DEMO_IE_BOTTLENECK_OPERATION_CODE in action.summary
    assert demo.DEMO_IE_LINE_CODE in action.summary
    assert action.payload["operation_code"] == demo.DEMO_IE_BOTTLENECK_OPERATION_CODE
    assert action.source == "deterministic"


async def test_operations_without_enough_samples_are_reported_not_guessed() -> None:
    analysis = _analysis(short_sampled="OP-02")
    assessment = await _assess(snapshot=_snapshot(ie={str(L2_ID): analysis}))

    assert "INSUFFICIENT_SAMPLES" in _codes(assessment)
    finding = next(f for f in assessment.findings if f.code == "INSUFFICIENT_SAMPLES")
    assert finding.severity == "info"
    assert "OP-02" in finding.message
    assert assessment.data_quality.complete is False
    # The bottleneck is still the worst *measured* operation.
    assert _metric(assessment, f"units_per_hour:{demo.DEMO_IE_LINE_CODE}") == Decimal(60)


async def test_a_compatible_line_without_observations_is_reported_as_unknown() -> None:
    assessment = await _assess(snapshot=_snapshot(lines=[_line(L2_ID, "L2"), _line(L9_ID, "L9")]))

    assert "NO_OBSERVATIONS" in _codes(assessment)
    finding = next(f for f in assessment.findings if f.code == "NO_OBSERVATIONS")
    assert finding.severity == "info"
    assert "L9" in finding.message
    assert assessment.data_quality.complete is False
    assert "L9" in assessment.data_quality.missing


async def test_no_finding_action_or_summary_mentions_an_individual_operator() -> None:
    assessment = await _assess(snapshot=_snapshot())

    for text in _texts(assessment):
        assert "KTN-OP-" not in text
        assert ALIAS_CODE not in text
        assert "operator_alias" not in text
