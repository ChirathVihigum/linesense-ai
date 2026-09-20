# AI assistance log

**Disclosure.** An AI coding assistant was used extensively to build this project, under human
direction and review. This document records how, so that the work can be assessed for what it
actually is. It deliberately does not name the vendor or product — what matters for assessment is
the *process*, and the process is what is described below.

---

## 1. What the assistant did

Essentially all of the application code, tests and documentation in this repository were drafted by
an AI coding assistant working from a written specification
([`LINESENSE_IMPLEMENTATION_PLAN.md`](../../LINESENSE_IMPLEMENTATION_PLAN.md)) and a binding
contracts document ([`docs/architecture/backend-contracts.md`](../architecture/backend-contracts.md)).

Concretely, per unit of work ("task"), the assistant:

- read the task brief, the contracts document, and the current state of the repository;
- wrote failing tests first, then the implementation;
- ran the focused tests for that change, plus lint and type checks;
- appended a dated entry to [`docs/IMPLEMENTATION_STATUS.md`](../IMPLEMENTATION_STATUS.md) stating
  what was built, what was run, what passed, and what was left undone;
- committed with a conventional commit message and a pathspec.

## 2. How the work was structured

The build ran as **27 planned tasks**, dispatched one or a few at a time against a written plan,
with a separate reviewing pass for each.

| Stage | What happened |
|---|---|
| Plan | A single written plan enumerated the tasks, their interfaces, their required tests and the conflicts between them. A pre-flight conflict scan resolved overlaps between tasks that touched the same files, *before* any code was written |
| Implement | One assistant instance per task, with an explicit instruction set: tests first, no placeholder handlers, no silent exception swallowing, no fabricated output |
| Review | A **separate** assistant instance, which had not written the code, reviewed each task's diff against the brief and the contracts, and classified findings Critical / Important / Minor |
| Fix rounds | Findings were fixed and re-reviewed, up to five rounds. Several tasks needed two or more; one (the LLM redaction work) needed five |
| Rulings | Where the review and the brief disagreed, or where two tasks' requirements conflicted, a human decision was recorded as a "ruling" with its cost-if-wrong, and the implementation followed the ruling |

The review pass was not decorative. It caught, among others: a foreign-key error in the initial
migration; a lock-ordering bug that allowed a double decrement of material balances; a staleness
check that failed *open* on malformed input; a secret-scanning script whose PEM pattern silently
never ran; and a vacuous resilience assertion that asserted nothing. All of these are recorded, with
their fixes, in the status log.

## 3. What the human decided

The assistant did not choose the shape of the project. Human decisions, recorded as ADRs or as
rulings in the build log, include:

- modular monolith plus one durable worker, rather than per-agent microservices
  ([ADR-0001](../adr/0001-modular-monolith-and-worker.md));
- PostgreSQL with pgvector as the single store, rather than a separate vector database
  ([ADR-0002](../adr/0002-postgres-pgvector-single-store.md));
- a PostgreSQL job queue rather than a broker ([ADR-0003](../adr/0003-postgres-job-queue.md));
- a development OIDC provider locally instead of running Keycloak
  ([ADR-0004](../adr/0004-oidc-server-sessions-and-dev-idp.md));
- a custom versioned HTTP/JSON agent protocol, explicitly not A2A and not MCP
  ([ADR-0005](../adr/0005-custom-http-agent-protocol.md));
- the LLM boundary rule — models never establish business truth and never authorize writes
  ([ADR-0006](../adr/0006-llm-boundary-and-fixture-provider.md));
- **dropping browser end-to-end testing** (Task 24) entirely, accepting that the project would have
  no screenshots and no automated UI flow coverage;
- a machine heat and workload policy that forbids running full test suites, load tests and the full
  evaluation locally — which is why so much of [`completion-matrix.md`](completion-matrix.md) reads
  *Not verified*;
- extending the final task to produce a LaTeX report with hand-laid-out diagrams.

## 4. Guardrails the assistant worked under

These were stated as hard rules, and the review pass checked them:

- Write failing tests before implementation; **never** delete, skip or weaken a failing test to get
  a green result.
- Integration tests use real PostgreSQL — never SQLite, never mocks — for locking, leases, tenancy,
  migrations and concurrency.
- No placeholder success handlers, no TODO stubs for required behaviour, no silent `except: pass`,
  no hardcoded secrets.
- **No fabricated test output and no mock numbers presented as live data.** Where a check was not
  run, the status entry says so, in those words.
- Table names, columns, role and permission names, error codes and protocol fields must match the
  contracts document exactly.

The last two are the reason this repository's documentation is full of the word *PENDING* rather
than full of plausible numbers.

## 5. Known process deviations (recorded, not hidden)

The status log records where the process itself was not followed, including:

- the durable-worker task and the LLM-boundary task where implementation code was written **before**
  its tests (disclosed in their status entries; correctness was checked afterwards by mutation —
  breaking the code and confirming the tests failed);
- one integration test written but **not run**, because the task had no database assigned to it;
- one commit whose message was mis-amended by a concurrent process and then restored, with the
  contents verified unchanged and no history rewritten;
- shared files (`Makefile`, `.env.example`) twice losing an edit to a concurrent write, caught by
  re-reading before committing.

## 6. What this means for assessment

1. **The code is not a black box to the team.** The viva requires each member to explain the
   modules they own ([`contribution-log.md`](contribution-log.md) §4). Assistance in writing code
   does not transfer that obligation.
2. **The honesty properties are the assessable ones.** A system that says "PENDING" where it did not
   measure, and "Test fixture — not a live AI model" where a fixture produced the text, is
   demonstrating the discipline this project is about.
3. **Nothing in this repository is presented as more verified than it is.**
   [`completion-matrix.md`](completion-matrix.md) is the single place where every requirement and
   release gate is marked Met, Partially met, Not verified or Not met, with its evidence.

## 7. Where the record lives

| What | Where |
|---|---|
| What was built, run and passed, per task, dated | [`docs/IMPLEMENTATION_STATUS.md`](../IMPLEMENTATION_STATUS.md) |
| Design decisions and their alternatives | [`docs/adr/`](../adr/README.md) |
| Per-task review findings and fix rounds | Internal review notes, not published in this repository |
| Requirement-by-requirement verdicts | [`completion-matrix.md`](completion-matrix.md) |
| Git history | `git log` on branch `feat/linesense-build` |
