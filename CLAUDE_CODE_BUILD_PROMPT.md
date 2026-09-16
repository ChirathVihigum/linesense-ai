# Copy-paste prompt for Claude Code

Open this project in Claude Code using the model available in your installation, then paste the following prompt. Keep `LINESENSE_IMPLEMENTATION_PLAN.md` in the project root. This prompt authorizes implementation when you give it to Claude; the present planning deliverable does not itself implement or deploy the application.

---

Act as a senior full-stack engineer and architect. Build LineSense AI from the supplied assignment materials and `LINESENSE_IMPLEMENTATION_PLAN.md` in this repository.

First read:

1. Existing repository instructions, including any `AGENTS.md` and `CLAUDE.md`.
2. `LINESENSE_IMPLEMENTATION_PLAN.md` in full.
3. `Group Assignment Brief (1).pdf` and `WhatsApp Image 2026-09-17 at 00.09.06.jpeg` if your environment can inspect them. If not, say so and use the plan's traceability while preserving this verification gap.

The goal is a working, secure, tested factory decision-support application and the technical evidence needed for IT 3041. Use four domain agents: planning/allocation, raw materials, IE/cycle time, and quality. Preserve explicit LLM, NLP, IR, agent communication, security, Responsible AI, and commercialization deliverables.

Use the proposed stack unless existing code or verified compatibility requires a documented change: React + TypeScript + Vite frontend; FastAPI/Pydantic backend; SQLAlchemy/Alembic; PostgreSQL + pgvector; separate durable worker; OIDC identity; Docker Compose; pytest/Vitest/Playwright. Verify official dependency documentation, choose compatible stable versions, and pin lockfiles. Do not invent package APIs or model identifiers.

Architecture rules:

- One modular backend codebase and a separate worker process. No unnecessary microservices, Kafka, Kubernetes, extra vector service, or competing agent frameworks.
- A versioned HTTP/JSON agent task protocol must actually run and have integration tests. Do not label a custom protocol as A2A/MCP.
- Agents have goals, typed inputs/outputs, scoped read/compute tools, bounded model tool-selection loops, and evidence. At least planning and RM must demonstrate real model-mediated interaction and revision.
- Deterministic domain code computes stock, capacity, cycle-time metrics, policy eligibility, and lifecycle transitions. LLMs explain and propose; they do not establish business truth or authorize writes.
- Runs, tasks, snapshots, results, approvals, and audit events are durable. Implement lease fencing, bounded retries, idempotency, stale-input checks, and crash recovery.
- Apply approved changes through transactional command services that lock/revalidate shared capacity and material resources. Prevent self-approval and reject expired/stale proposals.
- Distinguish production, material, quality, and analysis states. Missing inspection cannot produce a quality pass. Shipment readiness is derived from current validated conditions and authorized release.
- Protect organization/factory boundaries on every path, including worker tools, document chunks, citations, exports, and caches. Use backend sessions, CSRF protection, secure cookies in deployment, and server-side OIDC validation.
- Treat uploads, notes, documents, model outputs, and tool arguments as untrusted. No arbitrary SQL/shell/URL tools. Keep provider secrets out of frontend bundles and logs.
- Real integrations and deterministic test fixtures must be visibly separate. Missing model credentials permit independent development but do not justify claiming live AI success.

Begin by inspecting the repository and writing:

- `docs/requirements.md`: requirements, assumptions, acceptance criteria, and assignment mapping.
- `docs/adr/`: concise architecture decisions, including workflow/queue/auth/protocol choices.
- `docs/IMPLEMENTATION_STATUS.md`: phase checklist, implemented features, commands actually run, outcomes, known issues, external blockers, and next step.
- A concise `CLAUDE.md` with repository commands and critical invariants, referencing detailed docs.

Then implement the phases from the plan in order. For each phase:

1. State its concrete behavior and acceptance checks.
2. Build a small end-to-end slice using real persistence and actual API calls.
3. Add meaningful tests for its domain/security/failure boundaries.
4. Run applicable format/lint/type checks, migrations, tests, and builds; inspect failures and fix the cause.
5. Review for authorization gaps, stale/duplicate writes, evidence mistakes, and regression risk.
6. Record exact commands/results and limitations in the status file. Continue to the next phase after the gate passes; routine implementation choices do not require repeated approval.

Required developer interface: implement and document `make bootstrap`, `make dev`, `make migrate`, `make seed`, `make lint`, `make typecheck`, `make test`, `make test-integration`, `make test-e2e`, `make eval`, and `make build`, or an equally clear cross-platform command set. Commands must fail on real errors. Provide safe `.env.example`, deterministic synthetic data, isolated test databases, migration checks, and CI. Never erase existing developer data as part of normal startup/tests.

The first milestone is: sign in through OIDC -> view/create/import an order -> run RM and planning -> inspect evidence and inter-agent messages -> authorized different user approves -> transactional apply -> audit history. Include viewer denial, a stale-proposal rejection, and worker restart recovery. Complete this before expanding to every dashboard chart.

The final milestone adds all four domain agents, hybrid document retrieval and citations, measured NLP/IR, secure file processing, complete screen states, quality holds/releases, concurrency tests, deployment configuration, smoke checks, restore procedure, and accurate assessment documentation.

Quality rules:

- Do not claim zero bugs or production readiness without evidence. Meet the plan's gates and disclose remaining limitations.
- Never remove, skip, or weaken a failing test to conceal a defect. Document a changed specification if a test genuinely needs revision.
- Do not fabricate test output, benchmark scores, screenshots, citations, factory data, or provider integration success.
- No placeholder success handlers, required-function TODOs, hardcoded production secrets, mock dashboard numbers presented as live data, or silent exception swallowing.
- Generate frontend API types from the backend contract and detect drift in CI.
- Use real PostgreSQL for lease, locking, migration, tenant, and concurrency tests.
- Keep model/corpus/prompt/data versions with evaluation results. Report actual scores, including failed targets.
- Preserve unrelated work and supplied source files. Do not purchase services, publish externally, or access real factory data without the applicable authorization and credentials.
- Do not stop after scaffolding or writing a plan. Continue implementing within the current authorized environment. If blocked, complete unaffected work and state the exact missing dependency/credential/decision.
- Use the official report template when supplied; otherwise prepare content with a clearly marked template gap. Maintain member contribution and source/license records.

At the end of each working session report what now works, what was tested and its outcome, what remains, and the precise next action. Before declaring completion, run the full applicable release checklist, inspect the application in a browser, and reconcile README/report claims with actual behavior.

Start with repository inspection and Phase 0, then proceed to the first working vertical slice.

---

## Resume prompt

Read `CLAUDE.md`, `LINESENSE_IMPLEMENTATION_PLAN.md`, and `docs/IMPLEMENTATION_STATUS.md`. Inspect the actual code and working tree to verify the recorded state. Continue the next incomplete phase using the existing architecture and release gates. Preserve completed work. Run the checks relevant to changes you make, update the status file with actual evidence, and do not declare unverified functionality complete.

## Final review prompt

Review the implemented LineSense system against every required item and release gate in `LINESENSE_IMPLEMENTATION_PLAN.md`. Trace an order from authenticated import through four-agent analysis, evidence, human approval, transactionally applied changes, quality hold/release, and audit. Check tenant/factory isolation, role enforcement, CSRF, uploads, prompt injection, citation authorization, duplicate requests, conflicting reservations/allocations, stale approvals, worker crashes, expired leases, and provider outages. Run the applicable tests and evaluations. Fix defects without weakening checks, report actual remaining limitations, and produce an evidence-linked completion matrix. Do not claim deployment, recovery, or live-provider success for anything you did not verify.
