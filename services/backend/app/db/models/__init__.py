"""Every ORM model, imported here so ``Base.metadata`` is fully populated.

Import order follows the contract's own table grouping (identity, demand,
capacity, inventory, industrial engineering, quality, documents, workflow,
decisions, operations). Cross-group foreign keys (e.g. ``allocations ->
recommendations``) do not need that order to be a strict dependency graph:
SQLAlchemy's metadata sorts tables by their actual foreign keys when emitting
DDL, and the two genuine cycles (``analysis_runs`` <-> ``run_snapshots``,
``quality_holds`` -> ``quality_releases``) use ``use_alter=True``.
"""

from __future__ import annotations

from app.db.models.capacity import Allocation, Line, LineCapability, LineCapacitySlot
from app.db.models.decisions import Approval, AuditEvent, Recommendation
from app.db.models.demand import (
    BomLine,
    BomVersion,
    Customer,
    Material,
    Order,
    Style,
    StyleOperation,
)
from app.db.models.documents import Chunk, Document, DocumentAcl, DocumentVersion
from app.db.models.identity import (
    Factory,
    Membership,
    Organization,
    RoleAssignment,
    SessionRecord,
    User,
)
from app.db.models.ie import (
    CycleObservation,
    LineMeasurement,
    OperationStaffing,
    OperatorAlias,
    SkillRecord,
)
from app.db.models.inventory import (
    ExpectedReceipt,
    MaterialBalance,
    MaterialLot,
    Reservation,
    StockMovement,
)
from app.db.models.operations import (
    IdempotencyKey,
    ImportBatch,
    ImportRowError,
    Note,
    Notification,
)
from app.db.models.quality import (
    DefectObservation,
    Inspection,
    QualityHold,
    QualityPolicyVersion,
    QualityRelease,
)
from app.db.models.workflow import (
    AgentResultRecord,
    AgentTask,
    AnalysisRun,
    Job,
    RunEvent,
    RunSnapshot,
)

__all__ = [
    "Allocation",
    "AgentResultRecord",
    "AgentTask",
    "AnalysisRun",
    "Approval",
    "AuditEvent",
    "BomLine",
    "BomVersion",
    "Chunk",
    "Customer",
    "CycleObservation",
    "DefectObservation",
    "Document",
    "DocumentAcl",
    "DocumentVersion",
    "ExpectedReceipt",
    "Factory",
    "IdempotencyKey",
    "ImportBatch",
    "ImportRowError",
    "Inspection",
    "Job",
    "Line",
    "LineCapability",
    "LineCapacitySlot",
    "LineMeasurement",
    "Material",
    "MaterialBalance",
    "MaterialLot",
    "Membership",
    "Note",
    "Notification",
    "OperationStaffing",
    "OperatorAlias",
    "Order",
    "Organization",
    "QualityHold",
    "QualityPolicyVersion",
    "QualityRelease",
    "Recommendation",
    "Reservation",
    "RoleAssignment",
    "RunEvent",
    "RunSnapshot",
    "SessionRecord",
    "SkillRecord",
    "StockMovement",
    "Style",
    "StyleOperation",
    "User",
]
