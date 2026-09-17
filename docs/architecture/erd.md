# Entity-relationship diagram

This is a hand-drawn `mermaid erDiagram` of every table in
[`backend-contracts.md`](./backend-contracts.md) section 2 (50 tables,
matching `app/db/models/` and `migrations/versions/0001_initial_schema.py`
exactly — see `tests/integration/test_schema.py::test_all_contract_tables_exist`).

To keep the diagram readable, "actor" foreign keys that point at `users`
purely for attribution (`created_by`, `approved_by`, `inspected_by`,
`decided_by`, `released_by`, `requested_by`, `recorded_by`, `proposer_user_id`,
`applied_by`, `author_id`, ...) are **omitted** as relationship lines; they
are documented column-by-column in `backend-contracts.md` section 2 instead.
Every tenant-scoping relationship (`organization_id`, and the composite
`(organization_id, factory_id) -> factories`) is also omitted as a drawn
edge for the same reason — nearly every table has one, and drawing all of
them from every table to `organizations`/`factories` would make the diagram
unreadable. What remains below is the *business* graph: how orders, BOMs,
capacity, inventory, quality, documents, workflow, and decisions relate to
each other.

```mermaid
erDiagram
    %% ---- Identity ----
    ORGANIZATIONS ||--o{ FACTORIES : "has"
    ORGANIZATIONS ||--o{ MEMBERSHIPS : "has"
    USERS ||--o{ MEMBERSHIPS : "holds"
    MEMBERSHIPS ||--o{ ROLE_ASSIGNMENTS : "grants"
    FACTORIES |o--o{ ROLE_ASSIGNMENTS : "scopes (nullable = org-wide)"
    USERS ||--o{ SESSIONS : "logs in via"

    ORGANIZATIONS {
        uuid id PK
        text slug UK
    }
    FACTORIES {
        uuid id PK
        uuid organization_id FK
        text code
    }
    USERS {
        uuid id PK
        text issuer
        text subject
    }
    MEMBERSHIPS {
        uuid id PK
        uuid organization_id FK
        uuid user_id FK
    }
    ROLE_ASSIGNMENTS {
        uuid id PK
        uuid membership_id FK
        uuid factory_id FK "nullable"
        text role
    }
    SESSIONS {
        uuid id PK
        uuid user_id FK
        text token_hash UK
    }

    %% ---- Demand ----
    CUSTOMERS ||--o{ ORDERS : "places"
    STYLES ||--o{ STYLE_OPERATIONS : "sequences"
    STYLES ||--o{ BOM_VERSIONS : "versions"
    BOM_VERSIONS ||--o{ BOM_LINES : "lines"
    MATERIALS ||--o{ BOM_LINES : "consumed as"
    STYLES ||--o{ ORDERS : "produced as"
    BOM_VERSIONS ||--o{ ORDERS : "priced by"

    STYLES {
        uuid id PK
        uuid organization_id FK
        text code
    }
    STYLE_OPERATIONS {
        uuid id PK
        uuid style_id FK
        int sequence
    }
    MATERIALS {
        uuid id PK
        uuid organization_id FK
        text code
        text unit
    }
    BOM_VERSIONS {
        uuid id PK
        uuid style_id FK
        int version_no
        bool is_active
    }
    BOM_LINES {
        uuid id PK
        uuid bom_version_id FK
        uuid material_id FK
    }
    CUSTOMERS {
        uuid id PK
        uuid organization_id FK
    }
    ORDERS {
        uuid id PK
        uuid organization_id FK
        uuid factory_id FK
        uuid customer_id FK
        uuid style_id FK
        uuid bom_version_id FK
        text production_state
        text material_state
        text quality_state
    }

    %% ---- Capacity ----
    LINES ||--o{ LINE_CAPABILITIES : "can perform"
    LINES ||--o{ LINE_CAPACITY_SLOTS : "scheduled by shift"
    ORDERS ||--o{ ALLOCATIONS : "allocated via"
    LINE_CAPACITY_SLOTS ||--o{ ALLOCATIONS : "consumed by"
    RECOMMENDATIONS |o--o{ ALLOCATIONS : "may originate from"

    LINES {
        uuid id PK
        uuid organization_id FK
        uuid factory_id FK
        text code
    }
    LINE_CAPABILITIES {
        uuid id PK
        uuid line_id FK
        text skill_code
    }
    LINE_CAPACITY_SLOTS {
        uuid id PK
        uuid line_id FK
        date slot_date
        text shift_code
    }
    ALLOCATIONS {
        uuid id PK
        uuid order_id FK
        uuid slot_id FK
        uuid recommendation_id FK "nullable"
        text status
    }

    %% ---- Inventory ----
    MATERIALS ||--o{ MATERIAL_LOTS : "received as"
    MATERIALS ||--o{ STOCK_MOVEMENTS : "moves"
    MATERIAL_LOTS |o--o{ STOCK_MOVEMENTS : "traces to (nullable)"
    STOCK_MOVEMENTS |o--o{ STOCK_MOVEMENTS : "corrects (self, nullable)"
    ORDERS |o--o{ STOCK_MOVEMENTS : "issued for (nullable)"
    MATERIALS ||--o{ MATERIAL_BALANCES : "summarized as"
    MATERIALS ||--o{ RESERVATIONS : "reserved as"
    ORDERS ||--o{ RESERVATIONS : "reserves for"
    RECOMMENDATIONS |o--o{ RESERVATIONS : "may originate from"
    MATERIALS ||--o{ EXPECTED_RECEIPTS : "expected as"

    MATERIAL_LOTS {
        uuid id PK
        uuid material_id FK
        text lot_code
        text status
    }
    STOCK_MOVEMENTS {
        uuid id PK
        uuid material_id FK
        uuid lot_id FK "nullable"
        uuid corrects_movement_id FK "nullable"
        uuid order_id FK "nullable"
        text movement_type
        numeric quantity
    }
    MATERIAL_BALANCES {
        uuid id PK
        uuid material_id FK
        numeric on_hand_accepted
        numeric reserved
    }
    RESERVATIONS {
        uuid id PK
        uuid material_id FK
        uuid order_id FK
        uuid recommendation_id FK "nullable"
        text status
    }
    EXPECTED_RECEIPTS {
        uuid id PK
        uuid material_id FK
        text status
    }

    %% ---- Industrial engineering ----
    LINES |o--o{ OPERATOR_ALIASES : "assigned to (nullable)"
    OPERATOR_ALIASES ||--o{ SKILL_RECORDS : "has skills"
    LINES ||--o{ OPERATION_STAFFING : "staffs"
    STYLES ||--o{ OPERATION_STAFFING : "for style"
    STYLE_OPERATIONS ||--o{ OPERATION_STAFFING : "for operation"
    LINES ||--o{ CYCLE_OBSERVATIONS : "observed on"
    STYLES ||--o{ CYCLE_OBSERVATIONS : "observed for"
    STYLE_OPERATIONS ||--o{ CYCLE_OBSERVATIONS : "observed at"
    OPERATOR_ALIASES ||--o{ CYCLE_OBSERVATIONS : "performed by"
    LINES ||--o{ LINE_MEASUREMENTS : "measured on"
    STYLES ||--o{ LINE_MEASUREMENTS : "measured for"

    OPERATOR_ALIASES {
        uuid id PK
        uuid factory_id FK
        text alias_code
        uuid line_id FK "nullable"
    }
    SKILL_RECORDS {
        uuid id PK
        uuid operator_alias_id FK
        text skill_code
        int level
    }
    OPERATION_STAFFING {
        uuid id PK
        uuid line_id FK
        uuid style_id FK
        uuid operation_id FK
    }
    CYCLE_OBSERVATIONS {
        uuid id PK
        uuid line_id FK
        uuid style_id FK
        uuid operation_id FK
        uuid operator_alias_id FK
        numeric observed_seconds
    }
    LINE_MEASUREMENTS {
        uuid id PK
        uuid line_id FK
        uuid style_id FK
        date measured_on
    }

    %% ---- Quality ----
    ORDERS ||--o{ INSPECTIONS : "inspected via"
    LINES |o--o{ INSPECTIONS : "inline at (nullable)"
    QUALITY_POLICY_VERSIONS ||--o{ INSPECTIONS : "graded against"
    INSPECTIONS ||--o{ DEFECT_OBSERVATIONS : "records"
    STYLE_OPERATIONS |o--o{ DEFECT_OBSERVATIONS : "attributed to (nullable)"
    ORDERS ||--o{ QUALITY_HOLDS : "held by"
    INSPECTIONS |o--o{ QUALITY_HOLDS : "triggered by (nullable)"
    QUALITY_RELEASES |o--o{ QUALITY_HOLDS : "released via (nullable)"
    ORDERS ||--o{ QUALITY_RELEASES : "released for"
    INSPECTIONS ||--o{ QUALITY_RELEASES : "based on"
    QUALITY_POLICY_VERSIONS ||--o{ QUALITY_RELEASES : "under policy"

    QUALITY_POLICY_VERSIONS {
        uuid id PK
        uuid organization_id FK
        text code
        int version_no
        text status
    }
    INSPECTIONS {
        uuid id PK
        uuid order_id FK
        uuid line_id FK "nullable"
        uuid policy_version_id FK
        text inspection_type
        text result
    }
    DEFECT_OBSERVATIONS {
        uuid id PK
        uuid inspection_id FK
        uuid operation_id FK "nullable"
        text severity
    }
    QUALITY_HOLDS {
        uuid id PK
        uuid order_id FK
        uuid inspection_id FK "nullable"
        uuid release_id FK "nullable, use_alter"
        text status
    }
    QUALITY_RELEASES {
        uuid id PK
        uuid order_id FK
        uuid inspection_id FK
        uuid policy_version_id FK
    }

    %% ---- Documents and retrieval ----
    FACTORIES |o--o{ DOCUMENTS : "scoped to (nullable = org-wide)"
    DOCUMENTS ||--o{ DOCUMENT_VERSIONS : "versions"
    DOCUMENTS ||--o{ DOCUMENT_ACL : "restricted by"
    DOCUMENT_VERSIONS ||--o{ CHUNKS : "chunked into"

    DOCUMENTS {
        uuid id PK
        uuid organization_id FK
        uuid factory_id FK "nullable"
        text slug UK
        text doc_type
    }
    DOCUMENT_VERSIONS {
        uuid id PK
        uuid document_id FK
        int version_no
        text status
    }
    DOCUMENT_ACL {
        uuid id PK
        uuid document_id FK
        text role
    }
    CHUNKS {
        uuid id PK
        uuid document_version_id FK
        int chunk_index
        tsvector tsv "generated"
        vector embedding "384-dim, nullable"
    }

    %% ---- Workflow ----
    ORDERS ||--o{ ANALYSIS_RUNS : "analyzed by"
    ANALYSIS_RUNS |o--|| RUN_SNAPSHOTS : "pins (use_alter, nullable)"
    RUN_SNAPSHOTS }o--|| ANALYSIS_RUNS : "captured for"
    ANALYSIS_RUNS ||--o{ AGENT_TASKS : "dispatches"
    AGENT_TASKS |o--o{ AGENT_TASKS : "replans (self, nullable)"
    AGENT_TASKS ||--|| AGENT_RESULTS : "answered by"
    ANALYSIS_RUNS ||--o{ AGENT_RESULTS : "collects"
    ANALYSIS_RUNS ||--o{ RUN_EVENTS : "logs"

    ANALYSIS_RUNS {
        uuid id PK
        uuid order_id FK
        uuid snapshot_id FK "nullable, use_alter"
        text status
    }
    RUN_SNAPSHOTS {
        uuid id PK
        uuid run_id FK
        uuid order_id FK
    }
    AGENT_TASKS {
        uuid id PK
        uuid run_id FK
        uuid parent_task_id FK "nullable"
        text recipient
        text status
    }
    AGENT_RESULTS {
        uuid id PK
        uuid task_id FK UK
        uuid run_id FK
        text status
    }
    RUN_EVENTS {
        bigint id PK
        uuid run_id FK
        text event_type
    }
    JOBS {
        uuid id PK
        text queue
        text dedupe_key UK "nullable"
        text status
    }

    %% ---- Decisions and operations ----
    ORDERS ||--o{ RECOMMENDATIONS : "proposes for"
    ANALYSIS_RUNS ||--o{ RECOMMENDATIONS : "produced by"
    RECOMMENDATIONS ||--|| APPROVALS : "decided via"
    IMPORT_BATCHES ||--o{ IMPORT_ERRORS : "reports"

    RECOMMENDATIONS {
        uuid id PK
        uuid order_id FK
        uuid run_id FK
        text kind
        text status
    }
    APPROVALS {
        uuid id PK
        uuid recommendation_id FK UK
        text decision
    }
    AUDIT_EVENTS {
        bigint id PK
        uuid organization_id "no FK: append-only"
        uuid factory_id "no FK, nullable"
        text actor_type
        text outcome
    }
    IMPORT_BATCHES {
        uuid id PK
        uuid organization_id FK
        text kind
        text status
    }
    IMPORT_ERRORS {
        uuid id PK
        uuid batch_id FK
        int row_number
    }
    IDEMPOTENCY_KEYS {
        uuid id PK
        uuid organization_id FK
        text operation
        text key
    }
    NOTIFICATIONS {
        uuid id PK
        uuid organization_id FK
        uuid factory_id FK
        uuid user_id FK "nullable"
    }
    NOTES {
        uuid id PK
        uuid organization_id FK
        uuid factory_id FK
        uuid author_id FK
    }
```

## Notes on tables with no drawn relationship

- **`AUDIT_EVENTS`** is deliberately disconnected: it carries plain
  `organization_id`/`factory_id`/`run_id` columns with **no foreign keys at
  all**, so the append-only log survives deletion of the rows it describes.
- **`JOBS`** has no foreign keys (it is a generic durable-job queue keyed by
  `(queue, job_type, payload)`), so it is listed above without relationship
  edges.
- **`IDEMPOTENCY_KEYS`** only references `organizations` (via the omitted
  tenant-scoping edges described above); it has no other foreign keys.

## Tenant scoping (omitted above for readability)

Every table with both `organization_id` and `factory_id` NOT NULL has a
composite foreign key `(organization_id, factory_id) -> factories
(organization_id, id)`, so a row can never reference a factory belonging to
a different organization
(`tests/integration/test_schema.py::test_cross_org_factory_reference_rejected`).
`documents` and `chunks` use the same composite foreign key with a nullable
`factory_id` (Postgres `MATCH SIMPLE`, the default, only enforces it when
`factory_id` is non-null). `role_assignments.factory_id` is the one
exception: it is a plain foreign key to `factories.id` because
`role_assignments` has no `organization_id` column of its own (the
organization is reached via `membership_id -> memberships.organization_id`).
