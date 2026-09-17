"""Schema-level integration tests: tables, grants, and check constraints.

These exercise the real migrated ``linesense_test`` database (never SQLite
or mocks), per `docs/architecture/backend-contracts.md` section 2 and the
Task 3 brief.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BomVersion, Chunk, Document, DocumentVersion, Job, Line
from tests import factories

pytestmark = pytest.mark.integration

CONTRACT_TABLES = {
    # Identity
    "organizations",
    "factories",
    "users",
    "memberships",
    "role_assignments",
    "sessions",
    # Demand
    "customers",
    "styles",
    "style_operations",
    "materials",
    "bom_versions",
    "bom_lines",
    "orders",
    # Capacity
    "lines",
    "line_capabilities",
    "line_capacity_slots",
    "allocations",
    # Inventory
    "material_lots",
    "stock_movements",
    "material_balances",
    "reservations",
    "expected_receipts",
    # Industrial engineering
    "operator_aliases",
    "skill_records",
    "operation_staffing",
    "cycle_observations",
    "line_measurements",
    # Quality
    "quality_policy_versions",
    "inspections",
    "defect_observations",
    "quality_holds",
    "quality_releases",
    # Documents and retrieval
    "documents",
    "document_versions",
    "document_acl",
    "chunks",
    # Workflow
    "analysis_runs",
    "run_snapshots",
    "agent_tasks",
    "agent_results",
    "run_events",
    "jobs",
    # Decisions and operations
    "recommendations",
    "approvals",
    "audit_events",
    "import_batches",
    "import_errors",
    "idempotency_keys",
    "notifications",
    "notes",
}


async def test_all_contract_tables_exist(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    )
    actual = {row[0] for row in result.all()}
    assert actual == CONTRACT_TABLES | {"alembic_version"}


async def test_app_role_cannot_update_or_delete_audit_events(db_session: AsyncSession) -> None:
    org_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO audit_events (organization_id, actor_type, actor_id, action, "
            "target_type, target_id, outcome) VALUES "
            "(:org_id, 'SYSTEM', 'seed', 'seed.action', 'seed', 'seed', 'SUCCESS')"
        ),
        {"org_id": org_id},
    )
    await db_session.commit()

    with pytest.raises(ProgrammingError) as exc_info:
        await db_session.execute(text("UPDATE audit_events SET reason = 'x'"))
    assert "InsufficientPrivilege" in type(exc_info.value.orig).__name__
    await db_session.rollback()

    with pytest.raises(ProgrammingError) as exc_info:
        await db_session.execute(text("DELETE FROM audit_events"))
    assert "InsufficientPrivilege" in type(exc_info.value.orig).__name__
    await db_session.rollback()


async def test_app_role_cannot_create_table(db_session: AsyncSession) -> None:
    with pytest.raises(ProgrammingError) as exc_info:
        await db_session.execute(text("CREATE TABLE x (id int)"))
    assert "InsufficientPrivilege" in type(exc_info.value.orig).__name__
    await db_session.rollback()


async def test_order_quantity_must_be_positive(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError):
        await factories.make_order(db_session, quantity=0)


async def test_user_identity_unique(db_session: AsyncSession) -> None:
    await factories.make_user(db_session, issuer="dev", subject="dup-subject")
    with pytest.raises(IntegrityError):
        await factories.make_user(db_session, issuer="dev", subject="dup-subject")


async def test_cross_org_factory_reference_rejected(db_session: AsyncSession) -> None:
    org_a = await factories.make_org(db_session)
    factory_a1 = await factories.make_factory(db_session, organization=org_a)
    org_b = await factories.make_org(db_session)

    db_session.add(
        Line(
            organization_id=org_b.id,
            factory_id=factory_a1.id,
            code="LINE-X",
            name="Cross-org line",
            operator_count=10,
            is_active=True,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_slot_cannot_be_oversubscribed(db_session: AsyncSession) -> None:
    slot = await factories.make_slot(
        db_session, available_operator_minutes=100, planned_efficiency=0.5
    )
    slot.allocated_standard_minutes = 51
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_balance_reserved_cannot_exceed_on_hand(db_session: AsyncSession) -> None:
    balance = await factories.make_balance(db_session, on_hand_accepted=10, reserved=0)
    balance.reserved = 11
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_single_active_bom_per_style(db_session: AsyncSession) -> None:
    style = await factories.make_style_with_operations(db_session)
    db_session.add(BomVersion(style_id=style.id, version_no=1, is_active=True))
    await db_session.flush()
    db_session.add(BomVersion(style_id=style.id, version_no=2, is_active=True))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_chunk_tsv_generated(db_session: AsyncSession) -> None:
    org = await factories.make_org(db_session)
    document = Document(
        organization_id=org.id,
        slug="needle-breakage-sop",
        title="Needle breakage SOP",
        doc_type="SOP",
    )
    db_session.add(document)
    await db_session.flush()

    document_version = DocumentVersion(
        document_id=document.id,
        version_no=1,
        status="ACTIVE",
        sha256="0" * 64,
        storage_key="docs/needle.pdf",
        media_type="application/pdf",
        size_bytes=100,
    )
    db_session.add(document_version)
    await db_session.flush()

    chunk = Chunk(
        document_version_id=document_version.id,
        organization_id=org.id,
        chunk_index=0,
        text="Follow the needle breakage procedure before restarting the line.",
        token_count=10,
    )
    db_session.add(chunk)
    await db_session.flush()

    result = await db_session.execute(
        text("SELECT tsv @@ plainto_tsquery('english', 'needle') FROM chunks WHERE id = :id"),
        {"id": chunk.id},
    )
    assert result.scalar_one() is True


async def test_job_dedupe_key_unique(db_session: AsyncSession) -> None:
    db_session.add(
        Job(
            queue="orchestrator",
            job_type="run_analysis",
            payload={},
            status="READY",
            dedupe_key="dupe-key",
        )
    )
    await db_session.flush()
    db_session.add(
        Job(
            queue="orchestrator",
            job_type="run_analysis",
            payload={},
            status="READY",
            dedupe_key="dupe-key",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
