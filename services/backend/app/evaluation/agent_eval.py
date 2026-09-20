"""Agent section (task-19-brief.md requirement 6).

**This is not a live-LLM evaluation.** Every "model" call in this section
goes through ``app.llm.fixture_client.FixtureLLMClient`` with
``default_fixture_script`` — a deterministic test double that always picks
the deterministically-best candidate action and cites the available
evidence. It exercises the bounded agent loop, the orchestrator's task
graph, and the deterministic domain calculations end to end; it says
nothing about how a real model would reason, phrase a summary, or handle
an ambiguous case. See ``docs/evaluation/methodology.md``.

For each of ``--scenarios`` synthetic variants (seeded RNG over one
material's on-hand quantity and one line's capacity minutes, each built
fresh via ``tests.factories`` so scenarios never share state), three
approaches are compared against ground truth re-derived from the exact
values written to the database (never from the a-priori random draw, so a
rounding step can never quietly mislabel a scenario):

* (a) deterministic baseline -- the Task 4 calc functions only. Its
  accuracy against ground truth is close to 100% by construction (ground
  truth is computed with the same functions); it is reported as the
  "ceiling" the agent flow should reach, not an independent check of the
  formulas.
* (b) single-agent baseline -- the planning agent alone, in-memory
  (``dependency_results={}``, no database). By design it never sees the
  RM agent's material assessment, so it can never propose a
  material-limited option.
* (c) the real four-agent flow -- RM/IE/quality dispatched, planning
  proposes, and (when the top plan overcommits material) one targeted
  replan, driven end to end through the real job queue and worker
  (``tests.helpers.worker.drain``) against a fresh ``AnalysisRun``.
"""

from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    BomLine,
    Customer,
    Factory,
    LineCapability,
    Organization,
    StyleOperation,
)
from app.domain.inventory.calc import available_now, gross_demand, shortage
from app.domain.planning.calc import available_standard_minutes, required_standard_minutes
from app.domain.vocab import ProductionState
from app.jobs.queue import enqueue
from app.llm import FixtureLLMClient, default_fixture_script
from app.orchestration.executor import ORCHESTRATOR_ADVANCE_JOB
from app.orchestration.protocol import AgentResult
from app.orchestration.snapshot import (
    SnapshotBom,
    SnapshotBomLine,
    SnapshotData,
    SnapshotLine,
    SnapshotMaterial,
    SnapshotOrder,
    SnapshotQuality,
    SnapshotShipment,
    SnapshotSlot,
)
from app.retrieval.search import RetrievalScope, get_citation
from app.settings import Settings

RNG_SEED = 20260917
EFFICIENCY = Decimal("0.75")
QUANTITY = 500
SAM_TOTAL_MINUTES = Decimal("6.7")
BOM_QTY_PER_UNIT = Decimal("1.2")
WASTAGE_FRACTION = Decimal("0.05")
RESERVED = Decimal("0")
BLOCKER_SEVERITIES = ("warning", "critical")


@dataclass(frozen=True)
class ScenarioValues:
    on_hand_accepted: Decimal
    capacity_minutes: Decimal
    material_conflict_truth: bool
    capacity_sufficient_truth: bool


def _generate_scenario_values(index: int) -> ScenarioValues:
    rng = random.Random(RNG_SEED + index)  # noqa: S311 - deterministic synthetic test data, not crypto
    want_conflict = rng.random() < 0.5
    want_capacity_ok = rng.random() < 0.5

    demand = gross_demand(Decimal(QUANTITY), BOM_QTY_PER_UNIT, WASTAGE_FRACTION)
    available_target = demand * (Decimal("0.6") if want_conflict else Decimal("1.4"))
    on_hand_accepted = (available_target + RESERVED).quantize(Decimal("0.0001"))

    required_minutes = required_standard_minutes(QUANTITY, SAM_TOTAL_MINUTES)
    total_target_minutes = (required_minutes / EFFICIENCY) * (
        Decimal("0.6") if not want_capacity_ok else Decimal("1.4")
    )
    capacity_minutes = total_target_minutes.quantize(Decimal("0.01"))

    # Ground truth is re-derived from the exact values above (the same
    # domain functions the deterministic baseline uses), never trusted
    # from the a-priori `want_*` draw, so a quantize() rounding step can
    # never silently mislabel a scenario.
    actual_available = available_now(on_hand_accepted, RESERVED)
    material_conflict_truth = shortage(actual_available, demand) > 0
    actual_capacity = available_standard_minutes([capacity_minutes], EFFICIENCY)
    capacity_sufficient_truth = actual_capacity >= required_minutes

    return ScenarioValues(
        on_hand_accepted=on_hand_accepted,
        capacity_minutes=capacity_minutes,
        material_conflict_truth=material_conflict_truth,
        capacity_sufficient_truth=capacity_sufficient_truth,
    )


def _deterministic_baseline(values: ScenarioValues) -> dict[str, bool]:
    """(a): re-run the same Task 4 functions the ground truth used."""
    demand = gross_demand(Decimal(QUANTITY), BOM_QTY_PER_UNIT, WASTAGE_FRACTION)
    available = available_now(values.on_hand_accepted, RESERVED)
    material_conflict = shortage(available, demand) > 0
    required_minutes = required_standard_minutes(QUANTITY, SAM_TOTAL_MINUTES)
    capacity = available_standard_minutes([values.capacity_minutes], EFFICIENCY)
    return {
        "material_conflict_pred": material_conflict,
        "capacity_sufficient_pred": capacity >= required_minutes,
    }


def _material_conflict_predicted(result: AgentResult | None) -> bool:
    if result is None or result.status == "FAILED":
        return False
    return any(
        isinstance(action.payload, dict) and action.payload.get("option_code") == "MATERIAL_LIMITED"
        for action in result.recommended_actions
    )


def _capacity_sufficient_predicted(result: AgentResult | None) -> bool | None:
    if result is None or result.status == "FAILED":
        return None
    unscheduled = next((m.value for m in result.metrics if m.name == "unscheduled_units"), None)
    if unscheduled is None:
        return None
    return unscheduled == 0


def _build_synthetic_snapshot(
    *,
    values: ScenarioValues,
    order_id: uuid.UUID,
    style_id: uuid.UUID,
    material_id: uuid.UUID,
    line_id: uuid.UUID,
    due_date: date,
    slot_date: date,
    as_of_date: date,
    external_ref: str,
) -> SnapshotData:
    return SnapshotData(
        order=SnapshotOrder(
            id=order_id,
            version=1,
            external_ref=external_ref,
            customer_code="EVAL",
            style_id=style_id,
            style_code="EVAL-STY",
            quantity=QUANTITY,
            produced_units=0,
            remaining_units=QUANTITY,
            due_date=due_date,
            priority=1,
            production_state=ProductionState.VALIDATED.value,
            factory_timezone="Asia/Colombo",
        ),
        as_of_date=as_of_date,
        bom=SnapshotBom(
            bom_version_id=uuid.uuid4(),
            version_no=1,
            lines=[
                SnapshotBomLine(
                    bom_line_id=uuid.uuid4(),
                    material_id=material_id,
                    material_code="EVAL-MAT",
                    material_name="Eval Material",
                    material_unit="m",
                    bom_unit="m",
                    quantity_per_unit=BOM_QTY_PER_UNIT,
                    wastage_fraction=WASTAGE_FRACTION,
                    safety_stock=Decimal("0"),
                    lead_time_days=7,
                    pack_size=None,
                )
            ],
        ),
        materials={
            str(material_id): SnapshotMaterial(
                balance_id=uuid.uuid4(),
                balance_version=1,
                on_hand_accepted=values.on_hand_accepted,
                reserved=RESERVED,
                open_receipts=[],
                issues_14d=[],
            )
        },
        operations=[],
        sam_total_minutes=SAM_TOTAL_MINUTES,
        lines=[
            SnapshotLine(
                line_id=line_id,
                code="EVAL-L1",
                name="Eval Line 1",
                operator_count=20,
                compatible=True,
                missing_skills=[],
            )
        ],
        slots=[
            SnapshotSlot(
                slot_id=uuid.uuid4(),
                line_id=line_id,
                line_code="EVAL-L1",
                slot_date=slot_date,
                shift_code="A",
                available_operator_minutes=values.capacity_minutes,
                planned_efficiency=EFFICIENCY,
                allocated_standard_minutes=Decimal("0"),
                version=1,
            )
        ],
        ie={},
        quality=SnapshotQuality(
            policy=None,
            inspections=[],
            active_holds=[],
            releases=[],
            shipment=SnapshotShipment(eligible=False, reasons=["POLICY_UNKNOWN"]),
            quality_state="NOT_INSPECTED",
        ),
    )


async def _run_single_agent_baseline(values: ScenarioValues) -> dict[str, Any]:
    """(b): the planning agent alone, fully in-memory.

    ``BaseAgent._run_loop`` reserves every model call against the run's DB
    budget before making it (global-constraints.md: "reserved atomically
    in the DB before each call") even when no investigative tool needs a
    session -- so a truly database-free baseline has to stub that
    reservation out, exactly as ``tests/agents/test_document_tool.py``'s
    ``_in_memory_run_budget`` fixture does for the same reason.
    """
    from typing import cast
    from unittest.mock import patch

    from app.agents.planning import PlanningAgent
    from tests.helpers.agents import agent_context

    today = date.today()
    snapshot = _build_synthetic_snapshot(
        values=values,
        order_id=uuid.uuid4(),
        style_id=uuid.uuid4(),
        material_id=uuid.uuid4(),
        line_id=uuid.uuid4(),
        due_date=today + timedelta(days=14),
        slot_date=today + timedelta(days=7),
        as_of_date=today,
        external_ref="EVAL-SINGLE",
    )
    ctx = agent_context(
        llm=FixtureLLMClient(default_fixture_script),
        snapshot=snapshot,
        dependency_results={},
        task_type="propose_allocation",
        requester_roles=frozenset({"planner"}),
        session_factory=cast("Any", None),
    )

    async def _reserve(*_args: Any, **_kwargs: Any) -> bool:
        return True

    async def _record(*_args: Any, **_kwargs: Any) -> None:
        return None

    started = time.perf_counter()
    with (
        patch("app.agents.base.reserve_model_call", _reserve),
        patch("app.agents.base.record_usage", _record),
    ):
        result = await PlanningAgent().run(ctx)
    elapsed = time.perf_counter() - started
    return {
        "material_conflict_pred": _material_conflict_predicted(result),
        "capacity_sufficient_pred": _capacity_sufficient_predicted(result),
        "model_calls": result.execution_metadata.model_calls,
        "wall_clock_seconds": elapsed,
        "status": result.status,
    }


async def _build_scenario_order(
    session: AsyncSession,
    *,
    organization: Organization,
    factory: Factory,
    values: ScenarioValues,
    index: int,
    customer_code: str = "EVAL",
) -> Any:
    from tests.factories import (
        make_balance,
        make_line,
        make_material,
        make_order,
        make_slot,
        make_style_with_operations,
    )

    today = date.today()
    style = await make_style_with_operations(session, organization, operation_count=1)
    operation = (
        await session.scalars(sa.select(StyleOperation).where(StyleOperation.style_id == style.id))
    ).one()
    operation.sam_minutes = SAM_TOTAL_MINUTES
    operation.skill_code = "EVAL"
    material = await make_material(session, organization, code=f"EVAL-MAT-{index}")
    customer = Customer(
        organization_id=organization.id, code=f"{customer_code}-{index}", name="Eval Customer"
    )
    session.add(customer)
    await session.flush()
    order = await make_order(
        session,
        organization=organization,
        factory=factory,
        style=style,
        customer=customer,
        external_ref=f"PO-EVAL-{index:04d}",
        quantity=QUANTITY,
        due_date=today + timedelta(days=14),
        production_state=ProductionState.VALIDATED.value,
    )
    session.add(
        BomLine(
            bom_version_id=order.bom_version_id,
            material_id=material.id,
            quantity_per_unit=BOM_QTY_PER_UNIT,
            unit="m",
            wastage_fraction=WASTAGE_FRACTION,
        )
    )
    await make_balance(
        session,
        organization=organization,
        factory=factory,
        material=material,
        on_hand_accepted=values.on_hand_accepted,
        reserved=RESERVED,
    )
    line = await make_line(session, organization=organization, factory=factory, operator_count=20)
    session.add(LineCapability(line_id=line.id, skill_code="EVAL"))
    await session.flush()
    await make_slot(
        session,
        line=line,
        slot_date=today + timedelta(days=7),
        shift_code="A",
        available_operator_minutes=values.capacity_minutes,
        planned_efficiency=EFFICIENCY,
    )
    await session.commit()
    return order


async def _run_four_agent_flow(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    transport: httpx.AsyncBaseTransport,
    *,
    organization: Organization,
    factory: Factory,
    values: ScenarioValues,
    index: int,
    customer_code: str = "EVAL",
    order: Any | None = None,
) -> dict[str, Any]:
    """(c): the real orchestrator + worker + job queue, fixture provider.

    ``order`` lets a caller (the fairness section) reuse the exact same
    order across two runs -- ``recommendations.proposal_hash`` is computed
    over a proposal that embeds ``order_id`` (see
    ``app.orchestration.recommendations.create_recommendation``), so two
    *different* orders can never hash identically even with identical
    business content; only re-running the same order (with its customer
    changed in between) is a meaningful "identical except the customer"
    comparison.
    """
    from tests.helpers.agents import make_requester, make_run_with_snapshot
    from tests.helpers.worker import drain

    if order is None:
        async with session_factory() as session:
            order = await _build_scenario_order(
                session,
                organization=organization,
                factory=factory,
                values=values,
                index=index,
                customer_code=customer_code,
            )

    async with session_factory() as session:
        requester = await make_requester(session, organization, factory, role="planner")
        # A run must carry a real snapshot (built from the order just written) or
        # the orchestrator refuses it outright ("no snapshot to reason about") --
        # `make_run_with_snapshot` is `tests.factories.make_run` plus exactly that.
        run, _snapshot = await make_run_with_snapshot(session, order=order, requested_by=requester)
        await enqueue(
            session,
            queue="orchestrator",
            job_type=ORCHESTRATOR_ADVANCE_JOB,
            payload={"run_id": str(run.id)},
            dedupe_key=f"eval-run:{run.id}:start",
        )
        run_id = run.id
        await session.commit()

    started = time.perf_counter()
    for _ in range(6):  # a handful of drain passes settles the replan/round-1 chain
        processed = await drain(session_factory, settings, transport=transport)
        if processed == 0:
            break
    elapsed = time.perf_counter() - started

    async with session_factory() as session:
        results = await _load_results(session, run_id)
        if not results:
            raise RuntimeError(
                f"four-agent flow for run {run_id} produced no agent results at all "
                "(the orchestrator job likely failed outright -- check worker logs)"
            )
        recommendation = (
            await session.execute(
                sa.text(
                    "SELECT proposal, proposal_hash FROM recommendations WHERE run_id = :run_id "
                    "ORDER BY created_at DESC LIMIT 1"
                ).bindparams(run_id=run_id)
            )
        ).first()
        scope = RetrievalScope(
            organization_id=organization.id, factory_id=factory.id, roles=frozenset({"planner"})
        )
        citation_valid, citation_total = await _check_citations(
            session, scope=scope, results=results, run_id=run_id
        )

    rm_result = results.get("rm")
    planning_result = results.get("planning")
    coverable = None
    if rm_result is not None:
        coverable = next((m.value for m in rm_result.metrics if m.name == "coverable_units"), None)
    material_conflict_pred = coverable is not None and coverable < Decimal(QUANTITY)

    respects_material_coverage: bool | None = None
    if recommendation is not None and coverable is not None:
        proposal = recommendation[0] if isinstance(recommendation[0], dict) else {}
        allocated = proposal.get("allocated_units")
        try:
            respects_material_coverage = (
                allocated is not None and Decimal(str(allocated)) <= coverable
            )
        except Exception:  # noqa: BLE001 - a malformed proposal is reported, never crashes the run
            respects_material_coverage = None

    total_model_calls = sum(r.execution_metadata.model_calls for r in results.values())
    evidence_per_blocker = _evidence_per_blocker(results.values())

    return {
        "material_conflict_pred": material_conflict_pred,
        "capacity_sufficient_pred": _capacity_sufficient_predicted(planning_result),
        "respects_material_coverage": respects_material_coverage,
        "recommendation_created": recommendation is not None,
        "evidence_refs_per_blocker": evidence_per_blocker,
        "citation_valid": citation_valid,
        "citation_total": citation_total,
        "model_calls": total_model_calls,
        "wall_clock_seconds": elapsed,
        "agents_ran": sorted(results.keys()),
        "recommendation_proposal_hash": recommendation[1] if recommendation is not None else None,
    }


async def _load_results(session: AsyncSession, run_id: uuid.UUID) -> dict[str, AgentResult]:
    from app.db.models import AgentResultRecord, AgentTask

    rows = (
        await session.execute(
            sa.select(AgentTask.recipient, AgentTask.round, AgentResultRecord.payload)
            .join(AgentResultRecord, AgentResultRecord.task_id == AgentTask.id)
            .where(AgentTask.run_id == run_id)
            .order_by(AgentTask.round)
        )
    ).all()
    # Keep the highest-round result per recipient (the final one for that agent).
    results: dict[str, AgentResult] = {}
    for recipient, _round, payload in rows:
        results[recipient] = AgentResult.model_validate(payload)
    return results


def _evidence_per_blocker(results: Any) -> float | None:
    counts = []
    for result in results:
        for finding in result.findings:
            if finding.severity in BLOCKER_SEVERITIES:
                counts.append(len(finding.evidence_ids))
    if not counts:
        return None
    return sum(counts) / len(counts)


async def _check_citations(
    session: AsyncSession,
    *,
    scope: RetrievalScope,
    results: dict[str, AgentResult],
    run_id: uuid.UUID,
) -> tuple[int, int]:
    valid = 0
    total = 0
    for result in results.values():
        for evidence in result.evidence_refs:
            if evidence.chunk_id is None:
                continue
            total += 1
            citation = await get_citation(session, scope, evidence.chunk_id, run_id=run_id)
            if citation is not None:
                valid += 1
    return valid, total


async def run_agent_eval(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    transport: httpx.AsyncBaseTransport,
    *,
    scenarios: int,
) -> dict[str, Any]:
    from tests.factories import make_factory, make_org

    async with session_factory() as session:
        organization = await make_org(session, name="Eval Org (agent scenarios)")
        factory = await make_factory(session, organization=organization, code="EVL")
        await session.commit()

    scenario_reports: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index in range(scenarios):
        values = _generate_scenario_values(index)
        baseline_a = _deterministic_baseline(values)
        try:
            baseline_b = await _run_single_agent_baseline(values)
        except Exception as exc:  # noqa: BLE001 - recorded honestly, never crashes the harness
            errors.append({"scenario": index, "stage": "single_agent_baseline", "error": str(exc)})
            continue
        try:
            flow_c = await _run_four_agent_flow(
                session_factory,
                settings,
                transport,
                organization=organization,
                factory=factory,
                values=values,
                index=index,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append({"scenario": index, "stage": "four_agent_flow", "error": str(exc)})
            continue

        scenario_reports.append(
            {
                "scenario": index,
                "ground_truth": {
                    "material_conflict": values.material_conflict_truth,
                    "capacity_sufficient": values.capacity_sufficient_truth,
                },
                "deterministic_baseline": baseline_a,
                "single_agent_baseline": baseline_b,
                "four_agent_flow": flow_c,
            }
        )

    def _accuracy(key: str, approach: str) -> float | None:
        pairs = [
            (r["ground_truth"][key], r[approach].get(f"{key}_pred"))
            for r in scenario_reports
            if r[approach].get(f"{key}_pred") is not None
        ]
        if not pairs:
            return None
        correct = sum(1 for truth, pred in pairs if truth == pred)
        return correct / len(pairs)

    return {
        "scenarios_requested": scenarios,
        "scenarios_completed": len(scenario_reports),
        "errors": errors,
        "scenarios": scenario_reports,
        "accuracy": {
            "material_conflict": {
                "deterministic_baseline": _accuracy("material_conflict", "deterministic_baseline"),
                "single_agent_baseline": _accuracy("material_conflict", "single_agent_baseline"),
                "four_agent_flow": _accuracy("material_conflict", "four_agent_flow"),
            },
            "capacity_sufficient": {
                "deterministic_baseline": _accuracy(
                    "capacity_sufficient", "deterministic_baseline"
                ),
                "single_agent_baseline": _accuracy("capacity_sufficient", "single_agent_baseline"),
                "four_agent_flow": _accuracy("capacity_sufficient", "four_agent_flow"),
            },
        },
        "single_agent_baseline_note": (
            "By design (task-19-brief.md requirement 6), the single-agent baseline never "
            "receives the RM agent's material assessment, so its material_conflict_pred is "
            "always False -- it cannot detect a material conflict, ever, regardless of the "
            "scenario. This is expected, not a bug."
        ),
        "not_a_live_llm_evaluation": True,
    }
