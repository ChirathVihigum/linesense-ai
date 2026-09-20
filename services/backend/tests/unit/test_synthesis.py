"""The canonical per-order report: built from deterministic state, never model text."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from app.orchestration.protocol import AgentResult
from app.orchestration.snapshot import SnapshotQuality, SnapshotShipment
from app.orchestration.synthesis import build_order_report
from tests.helpers.agents import run_row, snapshot_data, task_row

STAMP = "2026-09-20T00:00:00+00:00"


def _result(
    agent: str,
    task_id: uuid.UUID,
    *,
    status: str = "SUCCEEDED",
    summary_source: str = "model",
    findings: list[dict[str, Any]] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    degraded_reason: str | None = None,
) -> AgentResult:
    return AgentResult.model_validate(
        {
            "schema_version": "1.0",
            "task_id": str(task_id),
            "agent": agent,
            "status": status,
            "summary": f"{agent} says something.",
            "summary_source": summary_source,
            "findings": findings or [],
            "metrics": [],
            "recommended_actions": [],
            "evidence_refs": evidence or [],
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
                "degraded": degraded_reason is not None,
                "degraded_reason": degraded_reason,
                "started_at": STAMP,
                "completed_at": STAMP,
            },
            "error_code": None,
        }
    )


def _finding(code: str, severity: str, *, source: str = "deterministic") -> dict[str, Any]:
    return {
        "finding_id": f"f-{code.lower()}-{source}",
        "severity": severity,
        "code": code,
        "message": f"{code} happened.",
        "evidence_ids": [],
        "source": source,
    }


def _evidence(evidence_id: str) -> dict[str, Any]:
    return {"evidence_id": evidence_id, "kind": "calculation", "description": "a calculation"}


def _quality_snapshot(**overrides: Any):  # noqa: ANN202
    defaults: dict[str, Any] = {
        "policy": None,
        "inspections": [],
        "active_holds": [],
        "releases": [],
        "shipment": SnapshotShipment(eligible=False, reasons=["POLICY_UNKNOWN"]),
        "quality_state": "NOT_INSPECTED",
    }
    defaults.update(overrides)
    return SnapshotQuality(**defaults)


# --------------------------------------------------------------------------


def test_blockers_are_the_critical_findings_deterministic_first() -> None:
    rm_task = task_row(recipient="rm")
    quality_task = task_row(recipient="quality")
    snapshot = snapshot_data()
    results = {
        rm_task.id: _result(
            "rm",
            rm_task.id,
            findings=[
                _finding("MATERIAL_SHORTAGE", "critical"),
                _finding("BELOW_REORDER_POINT", "warning"),
                _finding("MODEL_NOTE", "critical", source="model"),
            ],
        ),
        quality_task.id: _result(
            "quality", quality_task.id, findings=[_finding("POLICY_MISSING", "critical")]
        ),
    }

    report = build_order_report(
        run_row(order_id=snapshot.order.id),
        snapshot,
        results,
        None,
        tasks=[rm_task, quality_task],
    )

    assert [blocker.code for blocker in report.blockers] == [
        "MATERIAL_SHORTAGE",
        "POLICY_MISSING",
        "MODEL_NOTE",
    ]
    assert [blocker.source for blocker in report.blockers] == [
        "deterministic",
        "deterministic",
        "model",
    ]
    assert {blocker.agent for blocker in report.blockers} == {"rm", "quality"}
    assert report.recommendation is None


def test_degraded_reasons_are_aggregated_once_per_reason() -> None:
    rm_task = task_row(recipient="rm", status="SUCCEEDED")
    ie_task = task_row(recipient="ie", status="SUCCEEDED")
    failed_task = task_row(recipient="quality", status="FAILED")
    snapshot = snapshot_data()
    results = {
        rm_task.id: _result(
            "rm",
            rm_task.id,
            status="DEGRADED",
            summary_source="deterministic",
            degraded_reason="PROVIDER_UNAVAILABLE",
        ),
        ie_task.id: _result(
            "ie",
            ie_task.id,
            status="DEGRADED",
            summary_source="deterministic",
            degraded_reason="PROVIDER_UNAVAILABLE",
        ),
    }

    report = build_order_report(
        run_row(order_id=snapshot.order.id),
        snapshot,
        results,
        None,
        tasks=[rm_task, ie_task, failed_task],
    )

    assert report.degraded is True
    assert report.degraded_reasons == ["PROVIDER_UNAVAILABLE", "TASK_FAILED"]
    assert [summary.agent for summary in report.agent_summaries] == ["rm", "ie", "quality"]
    assert all(summary.summary_source == "deterministic" for summary in report.agent_summaries)
    missing = next(s for s in report.agent_summaries if s.agent == "quality")
    assert missing.status == "FAILED"
    assert missing.provider == "fixture"


def test_the_material_state_is_the_worst_of_the_round_zero_materials() -> None:
    snapshot = snapshot_data()

    def state_for(*codes: str) -> str:
        task = task_row(recipient="rm", round_=0)
        results = {
            task.id: _result("rm", task.id, findings=[_finding(code, "critical") for code in codes])
        }
        report = build_order_report(
            run_row(order_id=snapshot.order.id), snapshot, results, None, tasks=[task]
        )
        return report.states.material

    assert state_for() == "READY"
    assert state_for("MATERIAL_AT_RISK") == "AT_RISK"
    assert state_for("MATERIAL_AT_RISK", "MATERIAL_SHORTAGE") == "SHORTAGE"
    assert state_for("MATERIAL_SHORTAGE", "MATERIAL_BALANCE_MISSING") == "UNKNOWN"


def test_a_later_rm_round_never_overrides_the_round_zero_material_state() -> None:
    snapshot = snapshot_data()
    rm0 = task_row(recipient="rm", round_=0)
    rm1 = task_row(recipient="rm", round_=1)
    results = {
        rm0.id: _result("rm", rm0.id, findings=[_finding("MATERIAL_SHORTAGE", "critical")]),
        rm1.id: _result("rm", rm1.id, findings=[]),
    }

    report = build_order_report(
        run_row(order_id=snapshot.order.id), snapshot, results, None, tasks=[rm0, rm1]
    )
    assert report.states.material == "SHORTAGE"


def test_quality_and_shipment_come_from_the_snapshot_not_the_agents() -> None:
    snapshot = snapshot_data(
        quality=_quality_snapshot(
            quality_state="HOLD",
            shipment=SnapshotShipment(eligible=False, reasons=["ACTIVE_QUALITY_HOLD"]),
        )
    )
    task = task_row(recipient="quality")
    results = {task.id: _result("quality", task.id)}

    report = build_order_report(
        run_row(order_id=snapshot.order.id), snapshot, results, None, tasks=[task]
    )

    assert report.states.quality == "HOLD"
    assert report.shipment.eligible is False
    assert report.shipment.reasons == ["ACTIVE_QUALITY_HOLD"]
    assert report.shipment.source == "Calculated from records"
    assert report.states.production == snapshot.order.production_state
    assert report.order.external_ref == snapshot.order.external_ref
    assert report.order.due_date == snapshot.order.due_date
    assert isinstance(report.order.due_date, date)


def test_evidence_is_unique_per_agent() -> None:
    rm_task = task_row(recipient="rm")
    ie_task = task_row(recipient="ie")
    snapshot = snapshot_data()
    results = {
        rm_task.id: _result("rm", rm_task.id, evidence=[_evidence("ev-1"), _evidence("ev-1")]),
        ie_task.id: _result("ie", ie_task.id, evidence=[_evidence("ev-1")]),
    }

    report = build_order_report(
        run_row(order_id=snapshot.order.id), snapshot, results, None, tasks=[rm_task, ie_task]
    )

    assert [(item["agent"], item["evidence_id"]) for item in report.evidence] == [
        ("rm", "ev-1"),
        ("ie", "ev-1"),
    ]
