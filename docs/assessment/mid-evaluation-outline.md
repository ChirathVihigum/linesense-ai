# Mid-evaluation outline (Week 6, 20 marks)

The brief's Week 6 checkpoint asks for five things: **architecture**, **agent roles and
communication**, a **progress demo**, a **responsible-AI check**, and a **business pitch** — with
slides and a working **two-agent vertical slice**.

> **Honest framing.** This outline was written from the finished build rather than at the Week 6
> point in the schedule. The two-agent slice it describes genuinely exists and is genuinely
> demonstrable — it is the Phase 2 exit gate, recorded in
> [`docs/IMPLEMENTATION_STATUS.md`](../IMPLEMENTATION_STATUS.md) under the RM/planning agent and
> approval tasks — but presenting this as a snapshot taken in Week 6 would be a fiction. Present it
> as "here is the slice the mid-evaluation gate asked for, and here is what it does."

**Format:** 10 minutes of presentation, 5 minutes of live demo, 5 minutes of questions.

---

## Slides

### 1. Title and team (0:30)
Project name, module, team members and the area each one owns. Pull names and ownership from
[`contribution-log.md`](contribution-log.md) — do not improvise them on the slide.

### 2. The problem, in one sentence (0:45)
A supervisor's question — *will this order ship on time, and if not, why?* — is answered today by
walking between four systems. Name the users: planner, supervisor, storekeeper, IE engineer,
quality manager.

**Do not** claim market research. None was done ([`commercialization.md`](commercialization.md) §9).

### 3. Architecture (2:00)
Show the C4 container view (report figure 2). Talk through:

- One modular-monolith backend + one durable worker. No microservices, no Kafka, no Kubernetes —
  and be ready to say *why*: [ADR-0001](../adr/0001-modular-monolith-and-worker.md).
- PostgreSQL as the single store: domain records, job queue, run traces, audit, and the vector
  index ([ADR-0002](../adr/0002-postgres-pgvector-single-store.md),
  [ADR-0003](../adr/0003-postgres-job-queue.md)).
- OIDC with PKCE and opaque server sessions
  ([ADR-0004](../adr/0004-oidc-server-sessions-and-dev-idp.md)).

### 4. Agent roles and communication (2:00)
The four agents and their boundaries ([`docs/architecture/agents.md`](../architecture/agents.md)),
then the graph: RM and IE in parallel → planning consumes both → quality alongside.

The protocol slide is the one the marker is looking for
([`docs/architecture/agent-protocol.md`](../architecture/agent-protocol.md)):

- Custom versioned HTTP/JSON, `schema_version "1.0"` — **say explicitly that it is not A2A and not
  MCP** ([ADR-0005](../adr/0005-custom-http-agent-protocol.md)).
- `POST /internal/v1/agent-tasks`, idempotent on `idempotency_key`.
- Envelope scope/agent/task/dependencies are **server-verified against the loaded run**, never
  trusted from the request body.
- Invalid results rejected with `INVALID_AGENT_OUTPUT`.
- Durability: leases with fencing, bounded retries, at-least-once delivery (stated as
  at-least-once, never exactly-once).

### 5. The deterministic core (1:00)
The rule that makes the rest defensible: **LLMs never establish business truth and never authorize
a write** ([ADR-0006](../adr/0006-llm-boundary-and-fixture-provider.md)). Stock, capacity, cycle
time, quality eligibility and lifecycle transitions are computed in `app/domain/` and checked
against independently worked reference fixtures
([`docs/architecture/formulas.md`](../architecture/formulas.md)).

### 6. Progress against the plan (1:00)
The phase checklist from [`docs/IMPLEMENTATION_STATUS.md`](../IMPLEMENTATION_STATUS.md), with the
scope corrections named out loud: browser E2E dropped, no live-provider run, full evaluation and
load test pending.

### 7. Responsible AI check (1:30)
Four claims, each with its enforcement mechanism
([`responsible-ai.md`](responsible-ai.md)):

1. No worker is ever ranked — no code path exists that scores an individual.
2. Operators are pseudonymous aliases; no personal data exists in the schema.
3. Abstention is a first-class result — unknown policy or zero inspections never yields a pass.
4. Every consequential change needs approval by someone else, and self-approval is denied.

Then the limitation, said plainly: **no live model has ever run here**, so nothing in this build
evidences how a live model behaves.

### 8. Business pitch (1:15)
Positioning (decision-support layer with traceable recommendations, not an ERP replacement, not an
autonomous scheduler), the three tiers, and the cost model
([`commercialization.md`](commercialization.md)). Label the prices as **hypotheses** on the slide
itself, not only in the speech.

---

## Live demo: the two-agent vertical slice (5 minutes)

**Preparation.** Follow [`docs/user-guide.md`](../user-guide.md) §3–§4: database, dev identity
provider, backend, frontend, worker. Seed with `make seed`. Rehearse once end to end — a cold start
in front of a marker is where demos die.

| Step | What you show | What to say |
|---|---|---|
| 1 | Sign in as the planner; open `PO-DEMO-001` | Real OIDC flow, real session, real records |
| 2 | Start an analysis; the run timeline fills | The worker picked this up durably — kill it and it resumes |
| 3 | RM finding: insufficient accepted fabric | Click the evidence link; show the record *and its version* |
| 4 | Planning proposal, produced after RM's result | "This is the interaction the brief asks for: one agent's result changed another's proposal" |
| 5 | Approval inbox: diff, versions, impact, expiry | Nothing has been applied yet |
| 6 | Self-approval attempt → denied | `SELF_APPROVAL_DENIED`, 403 |
| 7 | Approve as a second user → applied | Locks rows in deterministic order; change + audit + follow-up job in one transaction |
| 8 | Change the stock, then retry an old proposal | Stale → 409; re-analysis required |
| 9 | Cross-factory request as a BYG user | 404, not 403 — the API does not confirm the resource exists |

**Point at the fixture label at least once.** Every summary reads *"Test fixture — not a live AI
model"*, and saying so unprompted is worth more than hoping nobody asks.

---

## Questions to have answers ready for

| Likely question | Where the answer is |
|---|---|
| "Is this A2A or MCP?" | Neither — custom versioned HTTP/JSON, [ADR-0005](../adr/0005-custom-http-agent-protocol.md) |
| "What stops an agent doing something destructive?" | Read-only tools, no SQL/shell/URL; bounded budgets; human approval |
| "Have you called a real model?" | **No.** No API key in this environment; the fixture provider is labelled everywhere |
| "What happens if the worker crashes mid-run?" | Lease expiry, fencing, reclaim; `tests/resilience/test_worker_kill.py` SIGKILLs a real worker and asserts exactly one result |
| "Two people approve at the same time — what happens?" | Deterministic lock order, re-validation, one succeeds; `test_two_applications_contend_for_the_last_slot_minutes` creates a real overlap with `pg_sleep`, five times |
| "How do you know the tests test anything?" | Mutation check: removing `FOR UPDATE` from `lock_balances` fails all 11 concurrency tests |
| "What are your evaluation numbers?" | Entities 0.991, classification 0.835, calculations all pass — **from a smoke run with a stand-in embedder**; full run PENDING |
| "What is not done?" | [`completion-matrix.md`](completion-matrix.md), read from it directly rather than from memory |

## Marking-criteria mapping

| Criterion | Where it is covered |
|---|---|
| Architecture | Slides 3, 5 |
| Agent roles and communication | Slide 4; demo steps 3–4 |
| Progress demo | Demo, all 9 steps |
| Responsible AI check | Slide 7; demo fixture label |
| Business pitch | Slide 8 |
