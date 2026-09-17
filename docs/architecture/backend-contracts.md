# LineSense backend contracts

This document is the binding interface contract shared by all implementation tasks. It refines
`LINESENSE_IMPLEMENTATION_PLAN.md` (the spec). When code and this document disagree, fix the code
or update this document in the same commit with a stated reason.

## 1. Repository and runtime conventions

- Backend lives in `services/backend/`, Python package `app`, tests in `services/backend/tests/`.
  Run backend commands from `services/backend/` with `uv run ...`. Python 3.12.
- Async SQLAlchemy 2.0 (`sqlalchemy.ext.asyncio`) with the psycopg 3 driver:
  URLs look like `postgresql+psycopg://user:pass@127.0.0.1:55432/dbname`.
- Local PostgreSQL 16 + pgvector 0.8.6 runs as a **project-local cluster**: data dir
  `.local/pgdata`, port `55432`, socket dir `.local/pgrun`, managed by `scripts/dev-db.sh`.
  Never touch any other cluster.
- Database roles: `linesense_owner` (owns schema, runs migrations) and `linesense_app` (runtime:
  DML only, no DDL). Databases: `linesense_dev` and `linesense_test`. Extensions `vector` and
  `pg_trgm` are created by the bootstrap script as the cluster superuser.
- Settings come from environment variables with prefix `LS_` (see `.env.example`), loaded by
  `app.settings.get_settings()` (pydantic-settings, cached). `LS_ENVIRONMENT` is one of
  `development | test | production`. Production startup fails if any security setting is missing
  or uses a development default.
- All timestamps are `timestamptz` stored in UTC. Business dates (`due_date`, `slot_date`) are
  `date` in the factory's timezone (default `Asia/Colombo`).
- Quantities are `Numeric`/`Decimal`, never float. Units are explicit columns.
- Identifiers are UUIDv4 generated in Python (`uuid.uuid4`).
- Mutable business records have an integer `version` column (starts at 1, incremented by every
  command that changes the row).
- Logging: `structlog` JSON logs with `trace_id`; never log tokens, cookies, provider keys, full
  prompts, or document text.

## 2. Tables

Column shorthand: `org` = `organization_id uuid not null fk organizations`, `fac` =
`factory_id uuid not null fk factories`, `ts` = `created_at timestamptz not null default now()`.
Every tenant table has `org`; plant tables also have `fac`. Check constraints are named
`ck_<table>_<rule>`.

### Identity
- `organizations(id, name, slug unique, ts)`
- `factories(id, org, code, name, timezone text default 'Asia/Colombo', ts; unique(organization_id, code))`
- `users(id, issuer, subject, email, display_name, is_active bool default true, ts; unique(issuer, subject))`
- `memberships(id, org, user_id fk, is_active bool default true, ts; unique(organization_id, user_id))`
- `role_assignments(id, membership_id fk cascade, factory_id uuid null fk factories, role text, ts;
  unique NULLS NOT DISTINCT (membership_id, factory_id, role); check role in ROLES)`. `factory_id
  IS NULL` means the role applies to every factory in the organization; `NULLS NOT DISTINCT`
  (PostgreSQL 15+) makes two org-wide (`factory_id IS NULL`) rows for the same
  `(membership_id, role)` collide just like two rows naming the same factory would.
- `sessions(id, token_hash text unique, user_id fk, csrf_token text, ts, expires_at, last_seen_at,
  revoked_at null)`. The cookie holds the raw token; only `sha256(token)` hex is stored.

### Demand
- `customers(id, org, code, name, ts; unique(organization_id, code))`
- `styles(id, org, code, name, product_type, ts; unique(organization_id, code))`
- `style_operations(id, style_id fk, sequence int, code, name, sam_minutes numeric(10,4) > 0,
  skill_code text, machine_type text; unique(style_id, sequence))`
- `materials(id, org, code, name, unit text check in ('m','kg','pcs','cone'), safety_stock numeric(14,4) >= 0,
  lead_time_days int >= 0, pack_size numeric(14,4) null > 0, ts; unique(organization_id, code))`
- `bom_versions(id, style_id fk, version_no int, is_active bool, approved_by uuid null fk users,
  approved_at null, ts; unique(style_id, version_no); partial unique index one active per style)`
- `bom_lines(id, bom_version_id fk cascade, material_id fk, quantity_per_unit numeric(14,6) > 0,
  unit text, wastage_fraction numeric(6,4) >= 0 and < 1)`
- `orders(id, org, fac, customer_id fk, style_id fk, bom_version_id fk, external_ref text,
  quantity int > 0, produced_units int >= 0 default 0, packed_units int >= 0 default 0,
  due_date date, priority int 1..5 default 3, production_state text, material_state text,
  quality_state text, source text check in ('manual','csv_import','synthetic_seed'),
  created_by uuid null fk users, version int default 1, ts, updated_at;
  unique(organization_id, external_ref))`

  One order = one style (no order_items table; recorded in ADR-0007).

### Capacity
- `lines(id, org, fac, code, name, operator_count int > 0, is_active bool, ts; unique(factory_id, code))`
- `line_capabilities(id, line_id fk cascade, skill_code text; unique(line_id, skill_code))`.
  A line is compatible with a style when every `style_operations.skill_code` of the style is present.
- `line_capacity_slots(id, org, fac, line_id fk, slot_date date, shift_code text check in ('A','B'),
  available_operator_minutes numeric(12,2) >= 0, planned_efficiency numeric(5,4) > 0 and <= 1,
  allocated_standard_minutes numeric(12,2) >= 0 default 0, version int default 1;
  unique(line_id, slot_date, shift_code);
  check allocated_standard_minutes <= available_operator_minutes * planned_efficiency)`
- `allocations(id, org, fac, order_id fk, slot_id fk, standard_minutes numeric(12,2) > 0,
  units numeric(14,4) > 0, status text check in ('ACTIVE','RELEASED'), recommendation_id null fk,
  created_by uuid null fk users, ts)`

### Inventory
- `material_lots(id, org, fac, material_id fk, lot_code, status text check in
  ('QUARANTINE','ACCEPTED','REJECTED'), received_at, ts; unique(factory_id, lot_code))`
- `stock_movements(id, org, fac, material_id fk, lot_id null fk, movement_type text check in
  ('RECEIPT','ISSUE','CORRECTION'), quantity numeric(14,4) (signed; receipt > 0, issue < 0),
  corrects_movement_id null fk stock_movements, reason text null, order_id null fk,
  created_by uuid null fk users, ts)`. Corrections must reference the corrected movement.
- `material_balances(id, org, fac, material_id fk, on_hand_accepted numeric(14,4) >= 0,
  reserved numeric(14,4) >= 0, version int default 1, updated_at;
  unique(factory_id, material_id); check reserved <= on_hand_accepted)`.
  Maintained in the same transaction as every ledger movement or reservation change; a test asserts
  `on_hand_accepted == sum(movements on ACCEPTED lots)`.
- `reservations(id, org, fac, material_id fk, order_id fk, quantity numeric(14,4) > 0, status text
  check in ('ACTIVE','RELEASED','CONSUMED'), recommendation_id null fk, created_by null fk, ts)`
- `expected_receipts(id, org, fac, material_id fk, quantity numeric(14,4) > 0, expected_date date,
  supplier_ref text, status text check in ('OPEN','RECEIVED','CANCELLED'), ts)`
- Consumption history is derived from `ISSUE` movements (last 14 days); there is no separate table.

### Industrial engineering
- `operator_aliases(id, org, fac, alias_code text, line_id null fk, is_active bool, ts;
  unique(factory_id, alias_code))`. Pseudonymous only: no names or personal attributes anywhere.
- `skill_records(id, operator_alias_id fk cascade, skill_code, level int 1..4; unique(operator_alias_id, skill_code))`
- `operation_staffing(id, org, fac, line_id fk, style_id fk, operation_id fk style_operations,
  parallel_operators int > 0; unique(line_id, style_id, operation_id))`
- `cycle_observations(id, org, fac, line_id fk, style_id fk, operation_id fk, operator_alias_id fk,
  observed_seconds numeric(10,2) > 0, observed_at, recorded_by null fk users,
  is_outlier bool default false, outlier_approved_by null fk users, ts)`
- `line_measurements(id, org, fac, line_id fk, style_id fk, measured_on date, hours numeric(6,2) > 0,
  units_output int >= 0, ts)`

### Quality
- `quality_policy_versions(id, org, code, version_no int, is_demo bool, status text check in
  ('DRAFT','ACTIVE','RETIRED'), rules jsonb, approved_by null fk, approved_at null, ts;
  unique(organization_id, code, version_no))`.
  `rules` = `{"sample_size": int, "max_defective_units": int, "max_critical_defects": int,
  "required_inspection_types": ["FINAL"]}`.
- `inspections(id, org, fac, order_id fk, line_id null fk, inspection_type text check in
  ('INLINE','FINAL'), inspected_units int >= 0, defective_units int >= 0 (<= inspected_units),
  policy_version_id fk, result text check in ('PASS','FAIL','INSUFFICIENT_SAMPLE'),
  inspected_by null fk users, inspected_at, ts)`
- `defect_observations(id, inspection_id fk cascade, defect_code text, severity text check in
  ('MINOR','MAJOR','CRITICAL'), count int > 0, operation_id null fk style_operations)`
- `quality_holds(id, org, fac, order_id fk, inspection_id null fk, reason text, status text check in
  ('ACTIVE','RELEASED'), created_by null fk (null = system rule), ts, released_by null, released_at null,
  release_id null fk quality_releases)`
- `quality_releases(id, org, fac, order_id fk, inspection_id fk, policy_version_id fk,
  released_by fk users, released_at, notes text null)`

### Documents and retrieval
- `documents(id, org, factory_id null fk (null = organization-wide), slug text, title, doc_type text
  check in ('SOP','QUALITY_POLICY','IE_STANDARD','OTHER'), created_by null fk, ts;
  unique(organization_id, slug))`
- `document_versions(id, document_id fk, version_no int, status text check in
  ('QUARANTINE','PROCESSING','ACTIVE','REJECTED','SUPERSEDED'), sha256 text, storage_key text,
  media_type text, size_bytes int, page_count int null, rejection_reason text null,
  created_by null fk, ts, activated_at null; unique(document_id, version_no))`
- `document_acl(id, document_id fk cascade, role text; unique(document_id, role))`. No rows = readable
  by every member with document:read in scope; rows = only those roles.
- `chunks(id, document_version_id fk cascade, org, factory_id null, chunk_index int,
  page_number int null, section text null, text text, token_count int,
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(section,'') || ' ' || text)) STORED,
  embedding vector(384) null, embedding_model text null; unique(document_version_id, chunk_index);
  GIN index on tsv)`

### Workflow
- `analysis_runs(id, org, fac, order_id fk, status text check in ('QUEUED','RUNNING',
  'AWAITING_REVIEW','COMPLETED','DEGRADED','FAILED','CANCELLED'), requested_by fk users,
  idempotency_key text, snapshot_id null, replan_count int default 0, model_calls_used int default 0,
  model_calls_limit int default 12, tokens_used int default 0, token_budget int default 200000,
  llm_provider text, llm_model text, degraded_reason text null, error_code text null,
  trace_id text, deadline_at, started_at null, completed_at null, version int default 1, ts)`
- `run_snapshots(id, run_id fk, org, fac, order_id fk, input_versions jsonb, data jsonb, ts)` — immutable.
- `agent_tasks(id, run_id fk, org, fac, parent_task_id null fk, message_id uuid unique, sender text,
  recipient text check in ('planning','rm','ie','quality'), task_type text, round int,
  idempotency_key text unique, status text check in ('PENDING','RUNNING','SUCCEEDED','FAILED',
  'CANCELLED'), envelope jsonb, attempt int default 0, max_attempts int default 3,
  error_code null, error_detail null, deadline_at, ts, completed_at null)`
- `agent_results(id, task_id unique fk, run_id fk, schema_version text, status text, payload jsonb, ts)`
- `run_events(id bigint identity pk, run_id fk, event_type text, actor text, payload jsonb, ts)`
- `jobs(id, queue text, job_type text, payload jsonb, dedupe_key text null unique, status text check
  in ('READY','LEASED','DONE','FAILED','CANCELLED'), attempt int default 0, max_attempts int default 3,
  available_at timestamptz default now(), leased_until null, lease_token uuid null, worker_id text null,
  heartbeat_at null, last_error text null, ts, completed_at null;
  index(queue, status, available_at))`

### Decisions and operations
- `recommendations(id, org, fac, order_id fk, run_id fk, kind text check in ('ALLOCATION',
  'RESERVATION','ALLOCATION_AND_RESERVATION'), status text check in ('DRAFT','PROPOSED','APPROVED',
  'REJECTED','EXPIRED','APPLIED','SUPERSEDED'), proposal jsonb, proposal_hash text,
  input_versions jsonb, rationale text, evidence_refs jsonb, generated_by text check in
  ('deterministic','model'), proposed_by_agent text, proposer_user_id fk users, expires_at,
  applied_by null fk, applied_at null, superseded_reason text null, version int default 1, ts)`
- `approvals(id, recommendation_id unique fk, decision text check in ('APPROVED','REJECTED'),
  decided_by fk users, reason text, proposal_hash text, decided_at)`
- `audit_events(id bigint identity pk, organization_id uuid, factory_id uuid null, actor_type text
  check in ('USER','SERVICE','SYSTEM'), actor_id text, action text, target_type text, target_id text,
  outcome text check in ('SUCCESS','DENIED','FAILED'), reason text null, trace_id text null,
  run_id uuid null, before jsonb null, after jsonb null, ts)`. `linesense_app` gets only
  `SELECT, INSERT` on this table (append-only for the application).
- `import_batches(id, org, fac, kind text, status text check in ('VALIDATED','COMMITTED','REJECTED'),
  file_sha256 text, row_count int, preview jsonb, created_by fk, ts, committed_at null;
  partial unique(organization_id, kind, file_sha256) where status = 'COMMITTED')`
- `import_errors(id, batch_id fk cascade, row_number int, field text null, message text)`
- `idempotency_keys(id, org, actor_id text, operation text, key text, request_hash text,
  response_status int null, response_body jsonb null, ts, expires_at;
  unique(organization_id, actor_id, operation, key))`
- `notifications(id, org, fac, user_id null fk, role text null, kind text, title, body, link text null,
  ts, read_at null)`
- `notes(id, org, fac, author_id fk users, text text, classification text, entities jsonb, ts)`

Indexes: every `(organization_id, factory_id)` pair; `orders(factory_id, due_date)`;
`stock_movements(factory_id, material_id, created_at)`; `agent_tasks(run_id)`;
`run_events(run_id, id)`; `document_versions(document_id, status)`; `recommendations(factory_id, status)`;
`audit_events(organization_id, created_at)`.

## 3. State vocabularies

- Production: `DRAFT, VALIDATED, PLANNED, IN_PRODUCTION, PRODUCTION_COMPLETE, DISPATCHED, CANCELLED`
- Materials: `UNKNOWN, READY, AT_RISK, SHORTAGE`
- Quality: `NOT_INSPECTED, PENDING, HOLD, RELEASED`
- Analysis run: `QUEUED, RUNNING, AWAITING_REVIEW, COMPLETED, DEGRADED, FAILED, CANCELLED`
- Recommendation: `DRAFT, PROPOSED, APPROVED, REJECTED, EXPIRED, APPLIED, SUPERSEDED`
- `shipment_ready` is computed by `app.domain.quality.calc.shipment_eligibility`, never stored as
  truth and never chosen by a model.

## 4. Roles and permissions (`app/auth/policy.py`)

Roles: `org_admin, supervisor, planner, storekeeper, ie_engineer, quality_manager, viewer`.

| Permission | Roles |
|---|---|
| `order:read`, `analysis:read`, `inventory:read`, `capacity:read`, `ie:read`, `quality:read`, `document:read` | all roles |
| `order:create`, `order:import` | planner, supervisor |
| `order:transition` | planner, supervisor |
| `order:dispatch`, `order:cancel` | supervisor |
| `analysis:run` | planner, supervisor |
| `recommendation:decide`, `recommendation:apply` | supervisor |
| `inventory:write` | storekeeper |
| `ie:write` | ie_engineer |
| `quality:inspect`, `quality:hold`, `quality:release` | quality_manager |
| `document:upload` | org_admin, supervisor, quality_manager, ie_engineer |
| `note:create` | supervisor, planner, storekeeper, ie_engineer, quality_manager |
| `audit:read` | org_admin, supervisor |
| `admin:manage` | org_admin |

`org_admin` gets exactly the permissions listed for it (plus the all-roles read set); no implicit
business authority.

```python
@dataclass(frozen=True)
class Principal:
    user_id: UUID
    organization_id: UUID
    session_id: UUID
    csrf_token: str
    display_name: str
    roles_by_factory: Mapping[UUID | None, frozenset[str]]   # None key = org-wide roles
    def roles_for(self, factory_id: UUID) -> frozenset[str]: ...
    def has(self, permission: str, factory_id: UUID) -> bool: ...
    def factory_ids(self) -> frozenset[UUID]: ...  # factories with any role (org-wide expands via DB)

def require(principal: Principal, permission: str, factory_id: UUID) -> None  # raises AppError 403 FORBIDDEN
```

Resource access helper (`app/auth/scope.py`):
`async def load_scoped(session, model, resource_id, principal, permission) -> model` returns the row
only if `row.organization_id == principal.organization_id` and the principal has `permission` for
`row.factory_id`. A missing row, a row in another organization, or a row in a factory where the
principal holds no role at all raises `AppError(404, "NOT_FOUND")` (never reveal existence); a row in
a factory where the principal holds some role but lacks `permission` raises `AppError(403,
"FORBIDDEN")` (global rule: "missing permission on an accessible scope returns 403"). `Factory` rows
are scoped by their own id; organization-level rows without `factory_id` require `permission` from
any of the principal's roles. `accessible_factory_ids(session, principal, permission)` lists the
organization's factories where `permission` is granted (org-wide roles expand to every factory).
Collection endpoints take `factory_id` in the path and call `load_scoped(session, Factory,
factory_id, principal, permission)` (or `require` once the factory is known to be in the org) first.

## 5. HTTP API conventions

- Public API prefix `/api/v1`; auth routes `/auth/login`, `/auth/callback`, `/auth/logout`;
  health `/api/health/live`, `/api/health/ready`; internal agent routes `/internal/v1/...`.
- Collection routes are factory-scoped: `/api/v1/factories/{factory_id}/orders`, `.../materials`,
  `.../lines`, `.../documents`, `.../recommendations`, etc. Item routes are global and scope-checked:
  `/api/v1/orders/{order_id}`, `/api/v1/runs/{run_id}`, `/api/v1/recommendations/{id}`.
- Error body (all non-2xx):
  `{"error": {"code": str, "message": str, "field_errors": [{"field": str, "message": str}],
  "trace_id": str, "retry_after_seconds": int | null}}`
  raised via `app.api.errors.AppError(status_code, code, message, field_errors=None, retry_after_seconds=None)`.
  Codes: `UNAUTHENTICATED`(401), `FORBIDDEN`(403), `CSRF_FAILED`(403), `SELF_APPROVAL_DENIED`(403),
  `NOT_FOUND`(404), `CONFLICT`(409), `STALE_INPUT`(409), `EXPIRED`(409),
  `IDEMPOTENCY_KEY_REUSED`(409), `INVALID_TRANSITION`(409), `VALIDATION_ERROR`(422),
  `METHOD_NOT_ALLOWED`(405), `PAYLOAD_TOO_LARGE`(413), `UNSUPPORTED_MEDIA_TYPE`(415),
  `RATE_LIMITED`(429), `INTERNAL_ERROR`(500), `SERVICE_UNAVAILABLE`(503).
- Pagination: `?limit=` (1..200, default 50) and `?offset=`; responses `{"items": [...], "total": int,
  "limit": int, "offset": int}`.
- Side-effecting creates/applies/imports/analysis requests require header `Idempotency-Key`
  (8..128 chars). Stored in `idempotency_keys` scoped to (organization, actor, operation, key) with a
  sha256 request hash; a matching retry returns the stored response; a different payload returns
  409 `IDEMPOTENCY_KEY_REUSED`. Retention 24 hours.
- Cookie session: cookie `ls_session` (HttpOnly, SameSite=Lax, `Secure` when
  `LS_ENVIRONMENT=production`), 8-hour absolute lifetime, rotated at login, revoked at logout.
- CSRF: every non-GET/HEAD/OPTIONS request under `/api` and `/auth/logout` requires header
  `X-CSRF-Token` equal to the session's `csrf_token` **and** an `Origin` (or `Referer`) matching
  `LS_PUBLIC_ORIGIN`. `GET /api/v1/me` returns the token.
- Every request gets `trace_id` (from `X-Request-Id` if it is a valid UUID, else new) echoed in the
  `X-Request-Id` response header and error bodies.
- Every write command records an audit event through
  `app.audit.service.record_audit(session, *, organization_id, factory_id, actor_type, actor_id,
  action, target_type, target_id, outcome, reason=None, trace_id=None, run_id=None, before=None,
  after=None)` inside the same transaction. Denied privileged writes are audited with outcome `DENIED`.

## 6. Agent task protocol (`app/orchestration/protocol.py`, schema_version "1.0")

This is a custom, versioned HTTP/JSON protocol. It is **not** A2A or MCP and must never be labelled so.

`POST /internal/v1/agent-tasks` — header `Authorization: Bearer <LS_SERVICE_TOKEN>` (constant-time
compare). Body `TaskEnvelope`; response `202 {"task_id", "run_id", "status"}`; repeated
`idempotency_key` returns the existing task with `200`.
`GET /internal/v1/agent-tasks/{task_id}` — returns `{"task": {...}, "result": AgentResult | null}`.

```python
class InputRef(BaseModel):  type: Literal["order","snapshot","agent_result","material_balance","capacity_slot","policy"]; id: UUID; version: int | None = None
class TaskConstraints(BaseModel): max_tool_calls: int = Field(4, ge=0, le=4); read_only: Literal[True] = True
class TaskEnvelope(BaseModel):
    schema_version: Literal["1.0"]
    message_id: UUID; run_id: UUID; parent_task_id: UUID | None
    organization_id: UUID; factory_id: UUID; order_id: UUID; snapshot_id: UUID
    sender: Literal["orchestrator"]
    recipient: Literal["planning","rm","ie","quality"]
    task_type: str      # must be in ALLOWED_TASK_TYPES[recipient]
    idempotency_key: str  # f"{run_id}:{recipient}:{snapshot_id}:round-{round}"
    round: int = Field(ge=0, le=1)
    deadline_at: datetime
    input_refs: list[InputRef]
    constraints: TaskConstraints
    trace_id: str
ALLOWED_TASK_TYPES = {"rm": {"assess_material_readiness", "validate_plan_materials"},
                      "ie": {"assess_line_capability"},
                      "planning": {"propose_allocation", "revise_allocation"},
                      "quality": {"assess_quality_status"}}

class EvidenceRef(BaseModel):
    evidence_id: str            # stable within the result, e.g. "ev-1"
    kind: Literal["record","document","calculation"]
    record_type: str | None; record_id: UUID | None; record_version: int | None
    document_id: UUID | None; document_version_id: UUID | None; chunk_id: UUID | None
    page_number: int | None; section: str | None
    description: str
class Finding(BaseModel):
    finding_id: str; severity: Literal["info","warning","critical"]; code: str; message: str
    evidence_ids: list[str]; source: Literal["deterministic","model"]
class Metric(BaseModel): name: str; value: Decimal | None; unit: str; note: str | None = None
class RecommendedAction(BaseModel):
    action_id: str; kind: Literal["ALLOCATION","RESERVATION","ALLOCATION_AND_RESERVATION","REPLENISHMENT_SUGGESTION","QUALITY_HOLD_REVIEW","IE_REVIEW"]
    summary: str; payload: dict[str, Any]; evidence_ids: list[str]; rank: int; source: Literal["deterministic","model"]
class DataQuality(BaseModel): complete: bool; missing: list[str]; notes: list[str]
class ExecutionMetadata(BaseModel):
    provider: str; model: str; model_calls: int; tool_calls: list[str]; input_tokens: int; output_tokens: int
    prompt_version: str; degraded: bool; degraded_reason: str | None; started_at: datetime; completed_at: datetime
class AgentResult(BaseModel):
    schema_version: Literal["1.0"]; task_id: UUID; agent: Literal["planning","rm","ie","quality"]
    status: Literal["SUCCEEDED","DEGRADED","FAILED"]
    summary: str; summary_source: Literal["deterministic","model"]
    findings: list[Finding]; metrics: list[Metric]; recommended_actions: list[RecommendedAction]
    evidence_refs: list[EvidenceRef]; warnings: list[str]
    input_versions: dict[str, Any]; data_quality: DataQuality; execution_metadata: ExecutionMetadata
    error_code: AgentErrorCode | None = None
class AgentErrorCode(StrEnum): MISSING_DATA, STALE_INPUT, PROVIDER_UNAVAILABLE, BUDGET_EXCEEDED, INVALID_AGENT_OUTPUT, POLICY_DENIED, DEADLINE_EXCEEDED
RETRYABLE = {PROVIDER_UNAVAILABLE: True, DEADLINE_EXCEEDED: False, everything else: False}
```

Validation (`app/orchestration/validation.py`): every `evidence_ids` entry must exist in
`evidence_refs`; every record/document reference must belong to the run's organization (and factory
or org-wide) and exist; action payloads must reference slot/material ids present in the run snapshot;
numeric metrics must be finite. Failures → `INVALID_AGENT_OUTPUT`.

## 7. Durable jobs (`app/jobs/queue.py`)

Queues: `orchestrator`, `agent`, `document`, `maintenance`. Delivery is at least once.

```python
async def enqueue(session, *, queue: str, job_type: str, payload: dict, dedupe_key: str | None = None,
                  available_at: datetime | None = None, max_attempts: int = 3) -> UUID | None
async def claim(session_factory, *, queues: Sequence[str], worker_id: str, lease_seconds: int) -> ClaimedJob | None
async def heartbeat(session_factory, *, job_id: UUID, lease_token: UUID, lease_seconds: int) -> bool
async def complete(session, *, job_id: UUID, lease_token: UUID) -> bool     # inside caller's transaction; False = fenced out
async def fail(session_factory, *, job_id: UUID, lease_token: UUID, error: str, retryable: bool) -> None
```

A claim selects `READY` jobs with `available_at <= now()` **or** `LEASED` jobs whose `leased_until <
now()`, using `FOR UPDATE SKIP LOCKED`, sets a fresh `lease_token`, increments `attempt`, and
commits. A reclaimed job whose `attempt` would exceed `max_attempts` is marked `FAILED` instead and its
exhaustion handler runs. Retry backoff: `min(2 ** attempt * 2, 60)` seconds plus up to 1 second jitter.

## 8. LLM boundary (`app/llm/`)

```python
class LLMToolSpec(BaseModel): name: str; description: str; input_schema: dict[str, Any]
class LLMToolCall(BaseModel): id: str; name: str; arguments: dict[str, Any]
class LLMResponse(BaseModel):
    text: str | None; tool_calls: list[LLMToolCall]; stop_reason: str
    input_tokens: int; output_tokens: int; provider: str; model: str; request_id: str | None
class LLMClient(Protocol):
    provider: str   # "anthropic" | "fixture"
    model: str
    async def complete(self, *, system: str, messages: list[dict[str, Any]], tools: list[LLMToolSpec],
                       max_tokens: int, timeout_seconds: float) -> LLMResponse
```

`messages` use the Anthropic content-block shape (`text`, `tool_use`, `tool_result`). Errors:
`LLMUnavailableError` (retryable), `LLMRateLimitedError` (retryable, carries retry-after),
`LLMRefusalError`, `LLMInvalidResponseError`. `LLM_PROVIDER=fixture` is a deterministic scripted
client for tests/CI; everything it produces is labelled `provider="fixture"` and the UI shows
"Test fixture — not a live AI model". `LLM_PROVIDER=disabled` produces degraded, deterministic-only
results labelled "AI explanation unavailable".

## 9. Seeded demo identities (development/test only)

Organization `Demo Apparel Group` (slug `demo-apparel`), factories `KTN` (Katunayake Plant) and
`BYG` (Biyagama Plant). Dev IdP subjects are `dev|<local-part>`.

| Email | Role | Factory |
|---|---|---|
| admin@demo.test | org_admin | all (factory_id NULL) |
| supervisor@demo.test | supervisor | KTN |
| supervisor.b@demo.test | supervisor | KTN |
| planner@demo.test | planner | KTN |
| storekeeper@demo.test | storekeeper | KTN |
| ie@demo.test | ie_engineer | KTN |
| quality@demo.test | quality_manager | KTN |
| quality.b@demo.test | quality_manager | KTN |
| viewer@demo.test | viewer | KTN |
| byg.planner@demo.test | planner | BYG |
