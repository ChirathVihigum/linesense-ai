"""Server-side validation of an :class:`AgentResult` (backend-contracts.md section 6).

Nothing a model produced is trusted: every evidence id must resolve, every
referenced record must exist inside the run's organization/factory scope,
every document version must be readable, and action payloads may only name
slots and balances that the run's snapshot already contains. Any failure
turns the result into ``INVALID_AGENT_OUTPUT``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AgentTask,
    Allocation,
    AnalysisRun,
    BomLine,
    BomVersion,
    CycleObservation,
    Document,
    DocumentVersion,
    ExpectedReceipt,
    Inspection,
    Line,
    LineCapacitySlot,
    LineMeasurement,
    Material,
    MaterialBalance,
    MaterialLot,
    OperationStaffing,
    Order,
    QualityHold,
    QualityPolicyVersion,
    QualityRelease,
    Reservation,
    RunSnapshot,
    StockMovement,
    Style,
    StyleOperation,
)
from app.db.models import Chunk as ChunkModel
from app.domain.vocab import DocumentVersionStatus
from app.orchestration.protocol import AgentResult, EvidenceRef
from app.orchestration.snapshot import SnapshotData

# Record types an agent may cite, mapped to the model that holds them.
# Tables without their own organization column resolve their owner through a
# parent (see ``_STYLE_SCOPED``).
RECORD_MODELS: dict[str, Any] = {
    "order": Order,
    "material": Material,
    "material_balance": MaterialBalance,
    "material_lot": MaterialLot,
    "stock_movement": StockMovement,
    "expected_receipt": ExpectedReceipt,
    "reservation": Reservation,
    "allocation": Allocation,
    "line": Line,
    "capacity_slot": LineCapacitySlot,
    "cycle_observation": CycleObservation,
    "line_measurement": LineMeasurement,
    "operation_staffing": OperationStaffing,
    "inspection": Inspection,
    "quality_hold": QualityHold,
    "quality_release": QualityRelease,
    "quality_policy": QualityPolicyVersion,
    "style": Style,
}
# Rows whose organization is the owning style's.
_STYLE_SCOPED: dict[str, Any] = {"style_operation": StyleOperation, "bom_line": BomLine}
AGENT_RESULT_RECORD_TYPE = "agent_result"
_ACTIVE_DOCUMENT_STATUSES = (
    DocumentVersionStatus.ACTIVE.value,
    DocumentVersionStatus.SUPERSEDED.value,
)


async def _record_exists_in_scope(
    session: AsyncSession, run: AnalysisRun, record_type: str, record_id: uuid.UUID
) -> bool:
    if record_type == AGENT_RESULT_RECORD_TYPE:
        task = await session.get(AgentTask, record_id)
        return task is not None and task.run_id == run.id
    if record_type in _STYLE_SCOPED:
        model = _STYLE_SCOPED[record_type]
        row = await session.get(model, record_id)
        if row is None:
            return False
        if isinstance(row, BomLine):
            # A BOM line has no style of its own; it belongs to a BOM version.
            bom_version = await session.get(BomVersion, row.bom_version_id)
            if bom_version is None:
                return False
            style_id = bom_version.style_id
        else:
            style_id = row.style_id
        style = await session.get(Style, style_id)
        return style is not None and style.organization_id == run.organization_id
    model = RECORD_MODELS.get(record_type)
    if model is None:
        return False
    row = await session.get(model, record_id)
    if row is None or row.organization_id != run.organization_id:
        return False
    factory_id = getattr(row, "factory_id", None)
    return factory_id is None or factory_id == run.factory_id


async def _validate_document_evidence(
    session: AsyncSession, run: AnalysisRun, evidence: EvidenceRef, errors: list[str]
) -> None:
    where = f"evidence {evidence.evidence_id}"
    if evidence.document_version_id is None:
        errors.append(f"{where}: document evidence must name a document_version_id")
        return
    version = await session.get(DocumentVersion, evidence.document_version_id)
    if version is None or version.status not in _ACTIVE_DOCUMENT_STATUSES:
        errors.append(f"{where}: document version is unknown or not readable")
        return
    document = await session.get(Document, version.document_id)
    if document is None or document.organization_id != run.organization_id:
        errors.append(f"{where}: document is outside the run's organization")
        return
    if document.factory_id is not None and document.factory_id != run.factory_id:
        errors.append(f"{where}: document belongs to another factory")
        return
    if evidence.document_id is not None and evidence.document_id != document.id:
        errors.append(f"{where}: document_id does not match the cited version")
    if evidence.chunk_id is not None:
        chunk = await session.get(ChunkModel, evidence.chunk_id)
        if chunk is None or chunk.document_version_id != version.id:
            errors.append(f"{where}: chunk does not belong to the cited document version")


def _validate_action_payloads(result: AgentResult, data: SnapshotData, errors: list[str]) -> None:
    slot_ids = {str(slot_id) for slot_id in data.slot_ids()}
    balance_ids = {str(balance_id) for balance_id in data.balance_ids()}
    material_ids = set(data.materials)
    for action in result.recommended_actions:
        for allocation in _payload_rows(action.payload, "allocations"):
            slot_id = str(allocation.get("slot_id"))
            if slot_id not in slot_ids:
                errors.append(
                    f"action {action.action_id}: slot {slot_id} is not in the run snapshot"
                )
        for reservation in _payload_rows(action.payload, "reservations"):
            balance_id = str(reservation.get("balance_id"))
            if balance_id not in balance_ids:
                errors.append(
                    f"action {action.action_id}: balance {balance_id} is not in the run snapshot"
                )
            material_id = reservation.get("material_id")
            if material_id is not None and str(material_id) not in material_ids:
                errors.append(
                    f"action {action.action_id}: material {material_id} is not in the run snapshot"
                )


def _payload_rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = payload.get(key)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


async def validate_result(
    session: AsyncSession,
    run: AnalysisRun,
    snapshot: RunSnapshot,
    result: AgentResult,
    *,
    task: AgentTask | None = None,
) -> list[str]:
    """Every rule the result must satisfy; an empty list means it is valid."""
    errors: list[str] = []

    task = task or await session.get(AgentTask, result.task_id)
    if task is None or task.run_id != run.id:
        errors.append(f"task {result.task_id} does not belong to run {run.id}")
    else:
        if result.task_id != task.id:
            errors.append("task_id does not match the executed task")
        if result.agent != task.recipient:
            errors.append(f"agent {result.agent!r} is not the task's recipient {task.recipient!r}")

    if result.input_versions != snapshot.input_versions:
        errors.append("input_versions do not match the run snapshot")

    evidence_by_id: dict[str, EvidenceRef] = {}
    for evidence in result.evidence_refs:
        if evidence.evidence_id in evidence_by_id:
            errors.append(f"evidence {evidence.evidence_id} is declared twice")
        evidence_by_id[evidence.evidence_id] = evidence

    cited: list[tuple[str, list[str]]] = [
        (f"finding {finding.finding_id}", finding.evidence_ids) for finding in result.findings
    ]
    cited.extend(
        (f"action {action.action_id}", action.evidence_ids) for action in result.recommended_actions
    )
    for where, evidence_ids in cited:
        for evidence_id in evidence_ids:
            if evidence_id not in evidence_by_id:
                errors.append(f"{where}: unknown evidence id {evidence_id!r}")

    for evidence in result.evidence_refs:
        if evidence.kind == "record":
            if evidence.record_type is None or evidence.record_id is None:
                errors.append(
                    f"evidence {evidence.evidence_id}: record evidence needs "
                    "record_type and record_id"
                )
            elif not await _record_exists_in_scope(
                session, run, evidence.record_type, evidence.record_id
            ):
                errors.append(
                    f"evidence {evidence.evidence_id}: record "
                    f"{evidence.record_type}/{evidence.record_id} is unknown or out of scope"
                )
        elif evidence.kind == "document":
            await _validate_document_evidence(session, run, evidence, errors)

    for metric in result.metrics:
        if metric.value is not None and not metric.value.is_finite():
            errors.append(f"metric {metric.name}: value must be finite")

    _validate_action_payloads(result, SnapshotData.model_validate(snapshot.data), errors)
    return errors


async def load_snapshot_data(session: AsyncSession, snapshot_id: uuid.UUID) -> SnapshotData:
    """Parse the stored snapshot ``data`` back into :class:`SnapshotData`."""
    row = await session.scalar(select(RunSnapshot).where(RunSnapshot.id == snapshot_id))
    if row is None:
        raise LookupError(f"snapshot {snapshot_id} does not exist")
    return SnapshotData.model_validate(row.data)
