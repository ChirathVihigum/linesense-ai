# LineSense AI: requirements

This document is the requirements baseline for IT 3041 Phase 0. It refines
`LINESENSE_IMPLEMENTATION_PLAN.md` (the spec) into numbered, testable
requirements. When code and this document disagree, fix the code or update
this document in the same commit with a stated reason (same rule as
[`docs/architecture/backend-contracts.md`](architecture/backend-contracts.md)).

## 1. Product statement

LineSense AI is a factory operations decision-support application for
apparel manufacturing. It lets a supervisor answer: **can this order finish
on time, what is blocking it, what evidence supports that conclusion, and
what should we do next?** It preserves the four functions from the supplied
architecture diagram — planning/allocation, supermarket/raw materials (RM),
industrial engineering (IE)/cycle time, and quality — as bounded, evidence-
producing AI agents behind deterministic domain calculations, human
approval workflows, and enforced access control. It is a decision-support
and recommendation system: it does not operate machinery, place real
purchase orders, or authorize physical shipments (spec §1).

## 2. Users and roles

Roles and their allowed scope are defined authoritatively in
[`backend-contracts.md`](architecture/backend-contracts.md) §4 and
enforced by `app/auth/policy.py`. Summary:

| Role | Primary responsibility |
|---|---|
| `org_admin` | Membership/configuration administration; no automatic business authority |
| `supervisor` | Factory-wide review; approves planning/material proposals |
| `planner` | Creates/imports orders; proposes allocations |
| `storekeeper` | Records/verifies material receipts, issues, corrections |
| `ie_engineer` | Records cycle observations; validates IE assumptions |
| `quality_manager` | Records inspections; places holds; records releases |
| `viewer` | Read-only access to authorized dashboards and evidence |

Roles are additive and explicit; `org_admin` does not implicitly gain
business authority (e.g. quality release). An approver can never approve
their own proposal (`SELF_APPROVAL_DENIED`, 403). At least two users with
different roles are seeded so the demo can show separation of duties
(`backend-contracts.md` §9).

## 3. Assumptions

Carried from spec §1, plus assumptions specific to this build environment:

- A 3–4-student team works Week 3 through Week 10 (assignment-relative
  weeks); viva is Week 11.
- The manufacturing domain has not been confirmed against the lecturer's
  approved domain list (not supplied); the official report template has
  also not been supplied. Both gaps are tracked, not silently assumed away.
- The "26-customer order book" is the size of the demonstration dataset,
  not an application limit.
- Initial inputs are synthetic CSV records and text-based PDF/Markdown
  SOPs. Live ERP/MES access, real worker records, and scanned-document OCR
  are later integrations, out of scope for this build (§8).
- One demonstration organization contains two factories (`KTN`, `BYG`) so
  access boundaries can be tested; organization scoping remains in the
  schema for future customers.
- English is the initial UI/document language; timestamps are stored in
  UTC and displayed in the factory's configured timezone (default
  `Asia/Colombo`).
- The system recommends actions; it never operates machinery, places real
  purchase orders, or authorizes physical shipments.
- **This development environment has no Docker, no Java, and no Anthropic
  API key.** Consequences: identity uses a development-only OIDC provider
  instead of a Keycloak container ([ADR-0004](adr/0004-oidc-server-sessions-and-dev-idp.md));
  the Compose/Keycloak path is a planned deliverable of a later deployment
  task (not yet written), to be validated statically only, never run,
  in this environment ([ADR-0008](adr/0008-local-environment-without-docker.md)); the LLM
  boundary is exercised only through the deterministic `fixture` provider,
  never a live Anthropic call, until a key is provisioned
  ([ADR-0006](adr/0006-llm-boundary-and-fixture-provider.md)). No live-LLM
  or live-Keycloak success is ever claimed in this environment.
- No official report template or lecturer-approved domain list has been
  supplied; both are treated as external blockers to note when the
  assessment package (Phase 6) is prepared, not fabricated in the meantime.

## 4. Functional requirements

Each requirement lists acceptance criteria that later phases must satisfy;
"Where implemented" names the planned module per
`LINESENSE_IMPLEMENTATION_PLAN.md` §4's repository layout. Requirements
marked with a later phase are documented now and implemented then — this
is a Phase 0 documentation task, not an implementation task.

### Orders and import

**REQ-01 — Order creation and CSV import.** A `planner`/`supervisor` can
create an order manually or import a batch of orders from CSV, scoped to
one factory, one style, one active BOM version, with a due date and
priority.
*Acceptance criteria:* invalid rows are reported individually
(`import_errors`) without partially committing the batch; a dry-run preview
is available before commit; duplicate `external_ref` within an
organization is rejected; committed imports are idempotent on
`(organization_id, kind, file_sha256)`.
*Where implemented:* `app/domain/orders/`, `app/ingestion/`.

**REQ-02 — Order lifecycle state transitions.** Production state moves
only through `DRAFT → VALIDATED → PLANNED → IN_PRODUCTION →
PRODUCTION_COMPLETE → DISPATCHED`, with explicit permitted cancellation
transitions, defined in one central policy table with actor permissions,
preconditions, and audit requirements.
*Acceptance criteria:* no arbitrary `PATCH status` endpoint exists; every
transition is audited; an invalid transition returns `422`/`409` per the
transition table, never a silent no-op.
*Where implemented:* `app/domain/orders/transitions.py`.

### Four domain agents

**REQ-03 — Planning agent.** Given an order and validated RM/IE results,
propose a ranked, feasible allocation (or explain why none exists), using
deterministic earliest-due-date allocation with documented tie-breaking.
*Acceptance criteria:* proposals never oversubscribe a capacity slot;
an infeasible order is left unscheduled with a stated reason, never
silently dropped; at least one real model-mediated tool selection or
revision based on another agent's result is demonstrated (spec §6).
*Where implemented:* `app/agents/planning/`, `app/domain/planning/`.

**REQ-04 — RM (materials) agent.** Assess material readiness for an order:
time-phased shortages, coverage, and a replenishment proposal, using
current accepted balances/reservations/receipts and retrieved SOP
evidence.
*Acceptance criteria:* never double-counts reservations already excluded
from `available_now`; missing/zero consumption yields an explained
unknown, not a computed zero; every claim is evidence-linked.
*Where implemented:* `app/agents/rm/`, `app/domain/inventory/`.

**REQ-05 — IE (cycle time) agent.** Identify bottleneck operations,
estimate line throughput, and note sample-size/assumption limitations.
*Acceptance criteria:* labels the line balance index as this model's
metric, not a universal KPI; never mixes seconds and minutes or applies
efficiency twice.
*Where implemented:* `app/agents/ie/`, `app/domain/ie/`.

**REQ-06 — Quality agent.** Evaluate policy eligibility against an
approved, versioned quality policy; report hold reasons and defect trends
with a cited explanation.
*Acceptance criteria:* unknown policy or zero inspections never yields an
automatic pass; the model explains a rule result but never invents its
thresholds.
*Where implemented:* `app/agents/quality/`, `app/domain/quality/`.

### Agent protocol and orchestration

**REQ-07 — Versioned agent task protocol.** Agents communicate through the
custom HTTP/JSON protocol defined in
[`backend-contracts.md`](architecture/backend-contracts.md) §6
(`schema_version "1.0"`), never described as A2A or MCP.
*Acceptance criteria:* `POST /internal/v1/agent-tasks` is idempotent on
`idempotency_key`; envelope scope/agent/task/dependencies are
server-verified against the loaded run, never trusted from the JSON body
alone; invalid results are rejected with `INVALID_AGENT_OUTPUT`.
*Where implemented:* `app/orchestration/protocol.py`, `validation.py`.

**REQ-08 — Durable orchestration and recovery.** Runs survive a worker
crash; leases are fenced; retries are bounded; stale snapshots are
rejected; a run exits with a clear reason when a limit (tool calls, model
calls, replans, deadline) is reached.
*Acceptance criteria:* an old worker cannot overwrite a result after its
task is reassigned; a periodic reconciliation job finds stalled eligible
runs; delivery is at-least-once and documented as such, never
exactly-once.
*Where implemented:* `app/jobs/queue.py`, `app/orchestration/`.

### Recommendations, approvals, and quality lifecycle

**REQ-09 — Recommendation and approval inbox.** Proposed actions from
agents enter an approval inbox; an authorized approver reviews a diff,
source versions, impact, and expiry before deciding.
*Acceptance criteria:* self-approval is denied (403
`SELF_APPROVAL_DENIED`); expired/stale proposals are rejected (409); one
`approvals` row per `recommendation`.
*Where implemented:* `app/domain/approvals/`.

**REQ-10 — Transactional apply.** Applying an approval locks affected
capacity-slot and material-balance rows in deterministic (sorted id)
order, re-reads current values, revalidates constraints, and commits the
update, audit event, and any follow-up job atomically.
*Acceptance criteria:* two concurrent applications against the same
constrained resource never both fully succeed; a changed input since
proposal invalidates the proposal and requires fresh analysis.
*Where implemented:* `app/domain/approvals/apply.py`.

**REQ-11 — Quality hold, release, and shipment eligibility.** `shipment_ready`
is a derived value (production complete, required current inspections
satisfied, no active hold, packing/quantity records complete, authorized
release recorded) — never an LLM-selected label, never stored as
independent truth.
*Acceptance criteria:* a positive AI explanation attempt never overrides
an active hold or missing inspection; dispatch remains a separate,
human-recorded event.
*Where implemented:* `app/domain/quality/calc.py` (`shipment_eligibility`).

### Documents, retrieval, and evidence

**REQ-12 — Document pipeline.** Upload → quarantine → validate type/size →
scan → extract text with page/section boundaries → normalize →
version/hash → chunk → embed → activate.
*Acceptance criteria:* encrypted/scanned PDFs are rejected with a helpful
message, not silently mis-parsed; originals are never served from a public
path; storage keys are never derived directly from uploaded filenames.
*Where implemented:* `app/ingestion/`, `app/retrieval/`.

**REQ-13 — Hybrid retrieval with citations.** SOP/document retrieval
combines lexical (`tsvector`/GIN) and vector (`pgvector`) search with
reciprocal-rank fusion, filtered by organization/factory/ACL/active-version
before any content reaches a model.
*Acceptance criteria:* every rendered citation resolves to authorized,
versioned evidence; authorization is rechecked when a citation is opened;
Recall@5 is measured against a held-out question set (target in §5).
*Where implemented:* `app/retrieval/`.

### NLP

**REQ-14 — Entity extraction.** Extract order/line/style/material/
operation/defect-category references from supervisor notes, grounded in
authorized master data (dictionary/entity-rule matching first).
*Acceptance criteria:* unknown/ambiguous mentions require resolution, never
silent record creation; extraction never grants authority to act on a
mentioned record; measured on a held-out labeled set (target in §5).
*Where implemented:* `app/nlp/extraction.py`.

**REQ-15 — Note classification.** Classify notes into
planning/materials/IE/quality/unknown, with a documented baseline and
confusion matrix.
*Acceptance criteria:* macro F1 and per-class support are reported, not
just an aggregate accuracy figure (target in §5).
*Where implemented:* `app/nlp/classification.py`.

**REQ-16 — Grounded summarization.** Summarize approved findings with
references; canonical structured status is computed first, an optional
model-generated explanation is layered second and never substitutes for
it.
*Acceptance criteria:* every summary distinguishes deterministic content
from model-generated content (`AgentResult.summary_source`).
*Where implemented:* `app/nlp/summarization.py`, `app/agents/*/`.

### Audit, notifications, dashboards

**REQ-17 — Audit trail.** Every privileged write, denial, import,
proposal, approval, application, quality release, and policy/version
change is recorded via `app/audit/service.record_audit` in the same
transaction as the write.
*Acceptance criteria:* the audit table is append-only for the application
role (`SELECT, INSERT` only); denied privileged writes are audited with
outcome `DENIED`; tokens and unnecessary PII are never recorded.
*Where implemented:* `app/audit/`.

**REQ-18 — In-app notifications.** Notify relevant users/roles of material
shortages, quality holds, and completed analyses inside the application;
no outbound email/SMS in this build (spec §6: "Notifications in the
assignment are in-app records").
*Acceptance criteria:* a notification links back to its source record/run;
notifications are scoped by organization/factory/role like every other
resource.
*Where implemented:* `app/domain/notifications/`.

**REQ-19 — Dashboards and screens.** Implement the screens listed in spec
§9 (Operations overview, Orders, Order detail, Planning board, Materials,
IE, Quality, Knowledge base, Approval inbox, Imports, Administration) with
loading/empty/validation/permission-denied/stale-data/degraded-AI/
server-error states as applicable.
*Acceptance criteria:* every AI-derived value shows its actual status
source ("Calculated from records" / "AI recommendation" / "Pending human
approval" / "Approved by [role/user] at [time]"); demo fixtures are never
shown as live ERP data.
*Where implemented:* `apps/web/src/features/`.

### Authentication, authorization, and security

**REQ-20 — Authentication and authorization.** OIDC authorization-code +
PKCE login; opaque server sessions; role/factory-scoped permission checks
on every access path (API, worker tools, retrieval, exports, audit views).
*Acceptance criteria:* inaccessible resources return `404`; an accessible
resource without the required permission returns `403`; cross-factory
access is denied in negative tests.
*Where implemented:* `app/auth/`.

**REQ-21 — Application security controls.** CSRF double-check (token +
Origin), validated/scanned uploads, idempotency keys on side-effecting
requests, parameterized queries, no arbitrary SQL/shell/URL tool access for
agents.
*Acceptance criteria:* a request missing/mismatching `X-CSRF-Token` or
`Origin` is rejected; a malicious upload fixture is rejected before
processing; a replayed idempotency key with a changed payload returns
`409 IDEMPOTENCY_KEY_REUSED`.
*Where implemented:* `app/api/`, `app/ingestion/`.

## 5. Non-functional requirements

**NFR-01 Security.** TLS in deployment; secrets in environment/secret
store only, never logged; production startup fails on missing required
security configuration ([`backend-contracts.md`](architecture/backend-contracts.md)
§1). See spec §11 threat table for the full control list.

**NFR-02 Tenancy.** Every tenant-owned record carries `organization_id`;
plant-specific records also carry `factory_id`; all access paths enforce
scope through shared policy functions. Row-level security is a **documented
pre-pilot requirement, deferred** for the assignment build: it is required
before any real multi-organization pilot (spec §11), but the assignment's
demo scope (one organization, two factories) is covered by application-
level scope checks (`app/auth/scope.py`) plus negative access tests; RLS
policies, a non-owner/non-superuser application role check, and pool-reuse
tenant-context tests are tracked as follow-up work, not silently dropped.

**NFR-03 Reliability.** At-least-once job delivery with lease fencing;
bounded retries with backoff; crash recovery via lease expiry; degraded
(not fabricated) results on provider/dependency outage. See spec §7.

**NFR-04 Performance (targets, not guarantees).** Measured on named
hardware/model settings against the seeded evaluation dataset (spec §13):
non-AI API p95 under 500 ms at 20 concurrent users; analysis acknowledgment
p95 under 1 second; a four-agent run usually under 60 seconds with a
120-second overall deadline. These are targets to evaluate against, not
results claimed in advance.

**NFR-05 Accessibility.** Keyboard-accessible controls, labels, chart
summaries, sufficient contrast, and icons/text alongside color on every
screen (spec §9); desktop-first responsive layout.

## 6. Assignment traceability

The first three columns are a verbatim reproduction of
`LINESENSE_IMPLEMENTATION_PLAN.md` §2 (including its assessment marks
where the brief states them); the fourth column, "Where implemented", is
added here and names the planned module(s) or document:

| Brief requirement | Concrete implementation | Evidence for assessment | Where implemented |
|---|---|---|---|
| At least two interacting intelligent agents | Four domain agents exchanging typed tasks/results through an orchestrated protocol | Run trace showing planning reacting to RM and IE findings | `app/agents/`, `app/orchestration/` |
| LLM use | Tool selection, exception interpretation, grounded action explanations | Redacted real-provider run with model and prompt versions | `app/llm/`, `app/agents/*/` |
| NLP | Entity extraction for order/style/line/material/defect references; exception classification and summarization | Labeled extraction/classification dataset and measured scores | `app/nlp/` |
| Information retrieval | Authorized SOP retrieval using lexical plus vector search, with citations | Lexical/vector/hybrid comparison and Recall@5 | `app/retrieval/` |
| Security | OIDC login, server sessions, resource authorization, validated uploads, encrypted transport | Negative access tests, threat model, scan results | `app/auth/`, `app/api/`, `app/ingestion/` |
| Defined agent protocol | Versioned JSON messages over private HTTP with durable task IDs | OpenAPI, schemas, sequence diagram, retry demo | `app/orchestration/protocol.py` |
| Responsible AI | Evidence, abstention, worker privacy, review of consequential actions | Model/data cards, adversarial tests, fairness limitations | `app/agents/*/`, `docs/adr/0006-*` |
| Commercialization | Target factories, proposed tiers, cost model, deployment options | Pricing assumptions and pilot measurement plan | `LINESENSE_IMPLEMENTATION_PLAN.md` §16 (assessment package, Phase 6) |
| Week 6 mid evaluation: 20 marks | Architecture, roles/communication, progress demo, RAI check, business pitch | Slides and a working two-agent vertical slice | Phase 2 exit gate |
| Week 10 Gen AI video: 25 marks | 3–5-minute explanation using a generative-video tool | Final video with accurate claims and identifiable synthetic scenes | Phase 6 |
| Week 10 report: 30 marks | Design, methodology, RAI, commercialization, evaluation | Official template populated with actual results | Phase 6 |
| Week 10 repository: 5 marks | Setup, usage, contributors, tests, documentation | Reproducible README and repository | This document, `README.md`, `docs/development-guide.md` |
| Week 11 viva: 20 marks | Each member explains code, decisions, protocols, results | Contribution log and individual rehearsals | Contribution log (`README.md` Contributors section) |

## 7. Explicit out-of-scope list

Reproduced from spec §14's cut-if-time-slips list, plus items the plan
places outside this build entirely:

- Drag-and-drop planning UI.
- Server-sent events (SSE) for progress updates (start with polling every
  2–5 seconds; add SSE later only if needed).
- OCR for scanned/encrypted PDFs (reject with a helpful message instead).
- External notifications (email/SMS) — in-app only (REQ-18).
- Optimization solvers for allocation beyond the deterministic
  earliest-due-date baseline, unless the baseline demonstrably fails.
- Advanced analytics beyond the specified dashboard charts.
- Live ERP/MES connectors — synthetic CSV/PDF/Markdown inputs only; an
  adapter *interface* is documented (spec §10) but not connected to a real
  system.
- Purchasing / real supplier communication — RM agent proposes
  replenishment; it never places a real purchase order.
- Machine control / autonomous shipment authorization — the system
  recommends; humans dispatch and authorize.
- Polished/production exports beyond what evidence and audit require.
- Fine-tuning, a second orchestration framework, Kafka, Kubernetes, and a
  separate vector database service ([ADR-0001](adr/0001-modular-monolith-and-worker.md),
  [ADR-0002](adr/0002-postgres-pgvector-single-store.md)).
