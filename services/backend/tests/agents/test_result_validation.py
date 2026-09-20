"""Server-side validation of agent results (backend-contracts.md section 6)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AgentTask,
    AnalysisRun,
    Chunk,
    Document,
    DocumentVersion,
    Organization,
    RunSnapshot,
)
from app.domain.vocab import DocumentType, DocumentVersionStatus, TaskStatus
from app.orchestration.protocol import (
    AgentResult,
    DataQuality,
    EvidenceRef,
    ExecutionMetadata,
    Finding,
    Metric,
    RecommendedAction,
)
from app.orchestration.snapshot import SnapshotData
from app.orchestration.validation import validate_result
from tests.factories import make_factory, make_line, make_org, make_slot, utcnow
from tests.helpers.agents import make_requester, make_run_with_snapshot
from tests.helpers.inventory import make_material_order

pytestmark = pytest.mark.integration


async def _fixture(session: AsyncSession) -> tuple[AnalysisRun, RunSnapshot, AgentTask, Any]:
    """A run whose snapshot has one material balance and one capacity slot."""
    from app.db.models import LineCapability, MaterialBalance

    organization = await make_org(session)
    factory = await make_factory(session, organization=organization)
    material, order = await make_material_order(session, organization, factory)
    balance = MaterialBalance(
        organization_id=organization.id,
        factory_id=factory.id,
        material_id=material.id,
        on_hand_accepted=Decimal("1500"),
        reserved=Decimal("400"),
    )
    session.add(balance)
    line = await make_line(session, organization=organization, factory=factory)
    session.add(LineCapability(line_id=line.id, skill_code="SEW"))
    await session.flush()
    await make_slot(session, line=line)
    requester = await make_requester(session, organization, factory)
    run, snapshot = await make_run_with_snapshot(session, order=order, requested_by=requester)
    task = AgentTask(
        id=uuid.uuid4(),
        run_id=run.id,
        organization_id=run.organization_id,
        factory_id=run.factory_id,
        message_id=uuid.uuid4(),
        sender="orchestrator",
        recipient="rm",
        task_type="assess_material_readiness",
        round=0,
        idempotency_key=f"{run.id}:rm:{run.snapshot_id}:round-0",
        status=TaskStatus.RUNNING.value,
        envelope={},
        deadline_at=run.deadline_at,
    )
    session.add(task)
    await session.flush()
    return run, snapshot, task, balance


def _result(task: AgentTask, snapshot: RunSnapshot, **overrides: Any) -> AgentResult:
    now = utcnow()
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "task_id": task.id,
        "agent": task.recipient,
        "status": "SUCCEEDED",
        "summary": "All good.",
        "summary_source": "deterministic",
        "findings": [],
        "metrics": [],
        "recommended_actions": [],
        "evidence_refs": [],
        "warnings": [],
        "input_versions": dict(snapshot.input_versions),
        "data_quality": DataQuality(complete=True, missing=[], notes=[]),
        "execution_metadata": ExecutionMetadata(
            provider="fixture",
            model="fixture-scripted-v1",
            model_calls=1,
            tool_calls=[],
            input_tokens=10,
            output_tokens=5,
            prompt_version="rm-v1",
            degraded=False,
            degraded_reason=None,
            started_at=now,
            completed_at=now,
        ),
    }
    payload.update(overrides)
    return AgentResult.model_validate(payload)


def _finding(*evidence_ids: str) -> Finding:
    return Finding(
        finding_id="rm-1",
        severity="info",
        code="TEST",
        message="test finding",
        evidence_ids=list(evidence_ids),
        source="deterministic",
    )


async def test_minimal_result_is_valid(db_session: AsyncSession) -> None:
    run, snapshot, task, _balance = await _fixture(db_session)
    assert await validate_result(db_session, run, snapshot, _result(task, snapshot)) == []


async def test_cited_evidence_must_exist(db_session: AsyncSession) -> None:
    run, snapshot, task, _balance = await _fixture(db_session)
    result = _result(task, snapshot, findings=[_finding("ev-missing")])
    problems = await validate_result(db_session, run, snapshot, result)
    assert any("unknown evidence id" in problem for problem in problems)


async def test_duplicate_evidence_ids_are_rejected(db_session: AsyncSession) -> None:
    run, snapshot, task, _balance = await _fixture(db_session)
    evidence = EvidenceRef(evidence_id="ev-1", kind="calculation", description="x = 1")
    result = _result(task, snapshot, evidence_refs=[evidence, evidence])
    problems = await validate_result(db_session, run, snapshot, result)
    assert any("declared twice" in problem for problem in problems)


async def test_record_evidence_must_exist_in_scope(db_session: AsyncSession) -> None:
    run, snapshot, task, balance = await _fixture(db_session)
    in_scope = EvidenceRef(
        evidence_id="ev-1",
        kind="record",
        record_type="material_balance",
        record_id=balance.id,
        record_version=balance.version,
        description="M01 balance",
    )
    assert (
        await validate_result(
            db_session, run, snapshot, _result(task, snapshot, evidence_refs=[in_scope])
        )
        == []
    )

    foreign = in_scope.model_copy(update={"record_id": uuid.uuid4()})
    problems = await validate_result(
        db_session, run, snapshot, _result(task, snapshot, evidence_refs=[foreign])
    )
    assert any("unknown or out of scope" in problem for problem in problems)

    incomplete = EvidenceRef(evidence_id="ev-1", kind="record", description="no reference")
    problems = await validate_result(
        db_session, run, snapshot, _result(task, snapshot, evidence_refs=[incomplete])
    )
    assert any("record_type and record_id" in problem for problem in problems)

    unknown_type = in_scope.model_copy(update={"record_type": "shell_command"})
    problems = await validate_result(
        db_session, run, snapshot, _result(task, snapshot, evidence_refs=[unknown_type])
    )
    assert problems != []


async def test_document_evidence_must_be_readable_and_in_scope(
    db_session: AsyncSession,
) -> None:
    run, snapshot, task, _balance = await _fixture(db_session)

    async def _document(**overrides: Any) -> tuple[Document, DocumentVersion]:
        defaults: dict[str, Any] = {
            "organization_id": run.organization_id,
            "factory_id": None,
            "slug": f"doc-{uuid.uuid4().hex[:8]}",
            "title": "Quality policy",
            "doc_type": DocumentType.QUALITY_POLICY.value,
        }
        defaults.update(overrides)
        document = Document(**defaults)
        db_session.add(document)
        await db_session.flush()
        version = DocumentVersion(
            document_id=document.id,
            version_no=1,
            status=DocumentVersionStatus.ACTIVE.value,
            sha256="0" * 64,
            storage_key=f"documents/{document.id}",
            media_type="application/pdf",
            size_bytes=1024,
        )
        db_session.add(version)
        await db_session.flush()
        return document, version

    document, version = await _document()
    evidence = EvidenceRef(
        evidence_id="ev-doc",
        kind="document",
        document_id=document.id,
        document_version_id=version.id,
        page_number=2,
        description="Sampling rules, page 2",
    )
    assert (
        await validate_result(
            db_session, run, snapshot, _result(task, snapshot, evidence_refs=[evidence])
        )
        == []
    )

    version.status = DocumentVersionStatus.QUARANTINE.value
    await db_session.flush()
    problems = await validate_result(
        db_session, run, snapshot, _result(task, snapshot, evidence_refs=[evidence])
    )
    assert any("not readable" in problem for problem in problems)
    version.status = DocumentVersionStatus.ACTIVE.value
    await db_session.flush()

    other_organization = await make_org(db_session)
    foreign_org_document, foreign_version = await _document(organization_id=other_organization.id)
    foreign_evidence = evidence.model_copy(
        update={"document_id": foreign_org_document.id, "document_version_id": foreign_version.id}
    )
    problems = await validate_result(
        db_session, run, snapshot, _result(task, snapshot, evidence_refs=[foreign_evidence])
    )
    assert any("outside the run's organization" in problem for problem in problems)

    sibling_factory = await make_factory(
        db_session, organization=await db_session.get(Organization, run.organization_id)
    )
    other_factory_document, other_factory_version = await _document(factory_id=sibling_factory.id)
    problems = await validate_result(
        db_session,
        run,
        snapshot,
        _result(
            task,
            snapshot,
            evidence_refs=[
                evidence.model_copy(
                    update={
                        "document_id": other_factory_document.id,
                        "document_version_id": other_factory_version.id,
                    }
                )
            ],
        ),
    )
    assert problems != []

    chunk = Chunk(
        document_version_id=version.id,
        organization_id=run.organization_id,
        factory_id=None,
        chunk_index=0,
        text="Sampling rules",
        token_count=3,
    )
    db_session.add(chunk)
    await db_session.flush()
    good_chunk = evidence.model_copy(update={"chunk_id": chunk.id})
    assert (
        await validate_result(
            db_session, run, snapshot, _result(task, snapshot, evidence_refs=[good_chunk])
        )
        == []
    )
    bad_chunk = evidence.model_copy(update={"chunk_id": uuid.uuid4()})
    problems = await validate_result(
        db_session, run, snapshot, _result(task, snapshot, evidence_refs=[bad_chunk])
    )
    assert any("chunk does not belong" in problem for problem in problems)


async def test_action_payloads_may_only_name_snapshot_rows(db_session: AsyncSession) -> None:
    run, snapshot, task, balance = await _fixture(db_session)
    data = SnapshotData.model_validate(snapshot.data)
    slot_id = next(iter(data.slot_ids()))

    def _action(payload: dict[str, Any]) -> RecommendedAction:
        return RecommendedAction(
            action_id="act-1",
            kind="ALLOCATION_AND_RESERVATION",
            summary="Allocate and reserve.",
            payload=payload,
            evidence_ids=[],
            rank=0,
            source="deterministic",
        )

    valid = _action(
        {
            "allocations": [{"slot_id": str(slot_id), "units": "100"}],
            "reservations": [{"balance_id": str(balance.id), "quantity": "120"}],
        }
    )
    assert (
        await validate_result(
            db_session, run, snapshot, _result(task, snapshot, recommended_actions=[valid])
        )
        == []
    )

    invented = _action(
        {
            "allocations": [{"slot_id": str(uuid.uuid4()), "units": "100"}],
            "reservations": [{"balance_id": str(uuid.uuid4()), "quantity": "120"}],
        }
    )
    problems = await validate_result(
        db_session, run, snapshot, _result(task, snapshot, recommended_actions=[invented])
    )
    assert any("is not in the run snapshot" in problem for problem in problems)
    assert len(problems) == 2


async def test_metrics_must_be_finite(db_session: AsyncSession) -> None:
    run, snapshot, task, _balance = await _fixture(db_session)
    # The protocol model already refuses a non-finite metric...
    with pytest.raises(ValidationError):
        Metric(name="coverable_units", value=Decimal("NaN"), unit="units")
    # ...and validation refuses one that bypassed parsing (defence in depth).
    result = _result(task, snapshot)
    result.metrics = [
        Metric.model_construct(name="coverable_units", value=Decimal("NaN"), unit="units")
    ]
    problems = await validate_result(db_session, run, snapshot, result)
    assert any("must be finite" in problem for problem in problems)


async def test_agent_task_and_versions_must_match(db_session: AsyncSession) -> None:
    run, snapshot, task, _balance = await _fixture(db_session)

    wrong_agent = _result(task, snapshot, agent="planning")
    problems = await validate_result(db_session, run, snapshot, wrong_agent)
    assert any("is not the task's recipient" in problem for problem in problems)

    other_run, other_snapshot = await make_run_with_snapshot(db_session)
    foreign_task = _result(task, snapshot, task_id=uuid.uuid4())
    problems = await validate_result(db_session, run, snapshot, foreign_task)
    assert any("does not belong to run" in problem for problem in problems)

    stale = _result(task, snapshot, input_versions={"order": {str(uuid.uuid4()): 99}})
    problems = await validate_result(db_session, run, snapshot, stale)
    assert any("input_versions" in problem for problem in problems)
    assert other_run.id != run.id and other_snapshot.id != snapshot.id


# --------------------------------------------------------------------------
# bom_line evidence resolves its organization through its BOM version
# --------------------------------------------------------------------------


async def _bom_line_of(session: AsyncSession, run: AnalysisRun) -> Any:
    from sqlalchemy import select

    from app.db.models import BomLine, Order

    bom_version_id = await session.scalar(
        select(Order.bom_version_id).where(Order.id == run.order_id)
    )
    line = await session.scalar(select(BomLine).where(BomLine.bom_version_id == bom_version_id))
    assert line is not None
    return line


def _bom_evidence(record_id: uuid.UUID) -> EvidenceRef:
    return EvidenceRef(
        evidence_id="ev-bom",
        kind="record",
        record_type="bom_line",
        record_id=record_id,
        description="BOM line for M01",
    )


async def test_bom_line_evidence_of_the_run_s_own_order_is_accepted(
    db_session: AsyncSession,
) -> None:
    run, snapshot, task, _balance = await _fixture(db_session)
    line = await _bom_line_of(db_session, run)

    result = _result(task, snapshot, evidence_refs=[_bom_evidence(line.id)])
    assert await validate_result(db_session, run, snapshot, result) == []


async def test_bom_line_evidence_from_another_organization_is_rejected(
    db_session: AsyncSession,
) -> None:
    from app.db.models import BomLine, BomVersion
    from tests.factories import make_material, make_style_with_operations

    run, snapshot, task, _balance = await _fixture(db_session)

    # A BOM line whose *style* (and therefore organization) is somebody else's.
    other_org = await make_org(db_session)
    other_style = await make_style_with_operations(db_session, organization=other_org)
    other_material = await make_material(db_session, organization=other_org)
    other_version = BomVersion(style_id=other_style.id, version_no=1, is_active=True)
    db_session.add(other_version)
    await db_session.flush()
    foreign_line = BomLine(
        bom_version_id=other_version.id,
        material_id=other_material.id,
        quantity_per_unit=Decimal("1"),
        unit=other_material.unit,
        wastage_fraction=Decimal("0"),
    )
    db_session.add(foreign_line)
    await db_session.flush()

    result = _result(task, snapshot, evidence_refs=[_bom_evidence(foreign_line.id)])
    problems = await validate_result(db_session, run, snapshot, result)
    assert any("unknown or out of scope" in problem for problem in problems)


async def test_unknown_bom_line_evidence_is_rejected(db_session: AsyncSession) -> None:
    run, snapshot, task, _balance = await _fixture(db_session)

    result = _result(task, snapshot, evidence_refs=[_bom_evidence(uuid.uuid4())])
    problems = await validate_result(db_session, run, snapshot, result)
    assert any("unknown or out of scope" in problem for problem in problems)


async def test_bom_line_with_an_unresolvable_bom_version_is_rejected(
    db_session: AsyncSession,
) -> None:
    """A BOM line whose ``BomVersion`` cannot be loaded resolves to no style.

    A foreign key makes this unreachable through the database, so the branch is
    exercised directly: before the Task 13 fix this whole code path raised
    ``AttributeError`` (``BomLine`` has no ``style_id``) instead of returning a
    verdict, which killed the agent job rather than rejecting the evidence.
    """
    from app.db.models import BomLine
    from app.orchestration.validation import _record_exists_in_scope

    run, _snapshot, _task, _balance = await _fixture(db_session)
    orphan = BomLine(
        id=uuid.uuid4(),
        bom_version_id=uuid.uuid4(),  # no such BOM version
        material_id=uuid.uuid4(),
        quantity_per_unit=Decimal("1"),
        unit="m",
        wastage_fraction=Decimal("0"),
    )

    class _StubSession:
        """Returns ``orphan`` for the BOM line and ``None`` for anything else."""

        async def get(self, model: Any, record_id: uuid.UUID) -> Any:
            return orphan if model is BomLine else None

    stub: Any = _StubSession()
    assert await _record_exists_in_scope(stub, run, "bom_line", orphan.id) is False
