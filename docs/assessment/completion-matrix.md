# Completion matrix

Every requirement in [`docs/requirements.md`](../requirements.md) and every release gate in
[`LINESENSE_IMPLEMENTATION_PLAN.md`](../../LINESENSE_IMPLEMENTATION_PLAN.md) §13–§14, with a
verdict and the evidence behind it. This is the authoritative "what is actually done" document;
where any other document in this repository sounds more confident than this one, **this one wins**.

## How to read a verdict

| Verdict | Means |
|---|---|
| **Met** | Implemented, and exercised by a test run recorded in [`docs/IMPLEMENTATION_STATUS.md`](../IMPLEMENTATION_STATUS.md) with its pass count |
| **Partially met** | Implemented, but one named part of the acceptance criteria is unverified or deliberately out of scope |
| **Not verified** | Implemented, never executed here. The check exists and is runnable; nobody ran it |
| **Not met** | Not done, or dropped from scope |

**Three facts govern most of the gaps below**, and are repeated here so no row surprises anyone:

1. **No live LLM run has ever happened.** There is no API key in this environment. Every recorded
   run used the `fixture` provider, labelled *"Test fixture — not a live AI model"* everywhere it
   appears.
2. **Browser end-to-end testing (Task 24) was dropped by the user.** There is no Playwright suite
   and **there are no screenshots anywhere in this repository.**
3. **A machine heat and workload policy forbade heavy local jobs.** Full test suites, `make eval`
   with the real embedder, `make perf`, and anything requiring Docker were never run. Per-task
   focused test runs *were* recorded, with exact pass counts, and those are the evidence cited
   below.

---

## 1. Functional requirements

| ID | Requirement | Verdict | Evidence |
|---|---|---|---|
| REQ-01 | Order creation and CSV import | **Met** | Orders/import task and its fix round: per-row `import_errors`, dry-run preview, duplicate `external_ref` rejection, idempotency on `(organization_id, kind, file_sha256)`; recorded runs 398 unit / 146 integration passed, plus import-specific unit tests. Formula-injection neutralisation hardened in the polish batch. [`app/domain/orders/import_csv.py`](../../services/backend/app/domain/orders/import_csv.py), [status log](../IMPLEMENTATION_STATUS.md) |
| REQ-02 | Order lifecycle state transitions | **Met** | Central policy table; no `PATCH status` route exists; invalid transition → 422/409; every transition audited. [`app/domain/orders/lifecycle.py`](../../services/backend/app/domain/orders/lifecycle.py) |
| REQ-03 | Planning agent | **Partially met** | Deterministic earliest-due-date allocation with documented tie-breaking, no capacity oversubscription, infeasible orders left unscheduled with a stated reason — all tested (31 agent tests, 11 orchestrator, two-agent flow). **The acceptance criterion "at least one *real* model-mediated tool selection or revision" is not met**: the revision-driven-by-another-agent behaviour is demonstrated (`test_two_agent_flow`), but the model mediating it was the fixture, not a live provider |
| REQ-04 | RM (materials) agent | **Met** | Time-phased shortages, coverage, replenishment proposal; no double-counting of reservations already excluded from `available_now`; missing/zero consumption yields an explained unknown; every claim evidence-linked. RM/planning agent tests |
| REQ-05 | IE (cycle time) agent | **Met** | Bottleneck identification, throughput estimate, sample-size and assumption limitations surfaced (including the "no capacity slots ⇒ 100% planned efficiency" limitation added in the polish batch). 12 agent tests + 108-test four-agent run |
| REQ-06 | Quality agent | **Met** | Eligibility against an approved versioned policy; unknown policy or zero inspections never yields a pass; the model explains a rule result and never invents thresholds |
| REQ-07 | Versioned agent task protocol | **Met** | `schema_version "1.0"`, custom HTTP/JSON, never A2A or MCP; `POST /internal/v1/agent-tasks` idempotent on `idempotency_key`; envelope scope/agent/task/dependencies server-verified against the loaded run; invalid results → `INVALID_AGENT_OUTPUT`. 52-test protocol/dispatch/executor run. [`docs/architecture/agent-protocol.md`](../architecture/agent-protocol.md) |
| REQ-08 | Durable orchestration and recovery | **Met** | Lease fencing, bounded retries, stale-snapshot rejection, explicit exhaustion reasons, reconciliation of stalled runs; at-least-once delivery documented as such. `tests/resilience/test_worker_kill.py` SIGKILLs a real worker subprocess and asserts exactly one result per task and a finalized run |
| REQ-09 | Recommendation and approval inbox | **Met** | Diff, source versions, impact and expiry shown before a decision; `SELF_APPROVAL_DENIED` (403); stale/expired → 409; one `approvals` row per recommendation. 9 approval-rule + 35 post-fix tests |
| REQ-10 | Transactional apply | **Met** | Deterministic (sorted id) lock order, re-read, re-validate, then change + audit + follow-up job in one transaction. `test_two_applications_contend_for_the_last_slot_minutes` creates a real overlap with `pg_sleep`, repeated 5×, and asserts exactly one succeeds. Mutation-checked: removing `FOR UPDATE` from `lock_balances` fails all 11 concurrency tests |
| REQ-11 | Quality hold, release, shipment eligibility | **Met** | `shipment_ready` derived by [`app/domain/quality/calc.py`](../../services/backend/app/domain/quality/calc.py), never stored as independent truth; a positive AI explanation cannot override an active hold; dispatch remains a separate human-recorded event. Auto-hold now writes its own audit event (polish batch) |
| REQ-12 | Document pipeline | **Met** | Upload → quarantine → validate → scan → extract with page/section boundaries → normalize → version/hash → chunk → embed → activate; encrypted/scanned PDFs rejected with a message; originals never served from a public path; storage keys never derived from filenames. 41-test document-pipeline run |
| REQ-13 | Hybrid retrieval with citations | **Partially met** | Lexical (`tsvector`/GIN) + vector (`pgvector`) with RRF (`rrf_k=60`), filtered by organization/factory/ACL/active-version before anything reaches a model; citations re-authorized on open (6 document-access tests). **The Recall@5 ≥ 0.85 acceptance gate is not verified**: the only recorded number, 0.711, came from a semantics-free stand-in embedder |
| REQ-14 | Entity extraction | **Met** | Dictionary/entity-rule matching against authorized master data; nonexistent references deliberately not extracted; extraction grants no authority. **Micro F1 0.991** against a 0.90 target on 110 held-out notes |
| REQ-15 | Note classification | **Met** | TF-IDF + logistic regression; macro F1 and per-class support/confusion matrix reported, not a bare accuracy. **Macro F1 0.835** against a 0.80 target. Note: the *shipped* abstention policy (`predict_with_margin`) is deliberately more conservative and is not separately scored ([`model-card.md`](model-card.md) §4b) |
| REQ-16 | Grounded summarization | **Met** | Canonical structured status computed first, optional model explanation layered second, `summary_source` distinguishes them; rejected/unavailable model output falls back to the deterministic summary with a stated reason. 48-test NLP run |
| REQ-17 | Audit trail | **Met** | Recorded in the same transaction as the write; append-only for the application role (`SELECT, INSERT`); denied privileged writes audited with outcome `DENIED`; no tokens or unnecessary PII |
| REQ-18 | In-app notifications | **Met** | Links back to the source record/run; scoped like every other resource; no email/SMS by design |
| REQ-19 | Dashboards and screens | **Partially met** | All 11 screens exist and route (`overview`, `orders`, `orders/new`, `orders/import`, `orders/:id`, `runs/:id`, `approvals`, `planning`, `materials`, `ie`, `quality`, `notes`, `knowledge`, `admin`), with loading/empty/validation/permission-denied/stale/degraded/error states and per-value source labels; whole web suite 34 files / 123 tests passed. **Verified by component tests only** — no browser run, and **no screenshots exist** |
| REQ-20 | Authentication and authorization | **Met** | OIDC authorization-code + PKCE, opaque server sessions, scope enforced on API, worker tools, retrieval, exports and audit views; inaccessible → 404, accessible-without-permission → 403; cross-factory denial in negative tests; route-generated IDOR matrix (GET routes, documented scope) |
| REQ-21 | Application security controls | **Partially met** | CSRF double-check (token + `Origin`), idempotency keys with `IDEMPOTENCY_KEY_REUSED` (409), parameterized queries, no arbitrary SQL/shell/URL tools, upload size/type validation, malicious-fixture rejection, prompt-injection suite across RM/IE/quality (81 + 27 tests passed). **Not met within this row: upload scanning is a structural byte/marker check only — there is no antivirus engine** |

## 2. Non-functional requirements

| ID | Requirement | Verdict | Evidence |
|---|---|---|---|
| NFR-01 | Security | **Partially met** | Production startup fails on missing/weak security configuration (validated and tested); secrets never logged; secret scan clean over 531 tracked files with a documented, narrowed fixture allowlist; `pip-audit` and `npm audit` both zero findings. **TLS is a deployment property and no deployment was ever run** |
| NFR-02 | Tenancy | **Partially met** | Every tenant-owned record carries `organization_id` (+ `factory_id` where plant-specific); shared policy functions on every access path; negative access tests. **Row-level security is deliberately deferred** and documented as a pre-pilot requirement, not silently dropped ([`docs/security/threat-model.md`](../security/threat-model.md)) |
| NFR-03 | Reliability | **Met** | At-least-once delivery with lease fencing, bounded retries with backoff, crash recovery via lease expiry (real SIGKILL test), degraded — never fabricated — results on provider or dependency failure |
| NFR-04 | Performance | **Not verified** | Targets defined (non-AI API p95 < 500 ms at 20 concurrent users; analysis ack p95 < 1 s; four-agent run usually < 60 s with a 120 s deadline). `scripts/perf_smoke.py` and `make perf` are complete and smoke-checked (`--help`, ruff, mypy) but **no load was ever generated**. [`docs/evaluation/performance.md`](../evaluation/performance.md) |
| NFR-05 | Accessibility | **Partially met** | Keyboard-accessible controls, labels, `aria-required`, focus trapping, chart summaries, and icons/text alongside colour; component tests query by role and accessible name throughout. **No automated accessibility audit and no contrast measurement were run** |

## 3. Release gates — test layers (plan §13)

| # | Layer | Verdict | Evidence |
|---|---|---|---|
| 1 | Domain unit and property tests | **Met** | 78 passed on the domain/calculation module including Hypothesis property tests and the independently worked reference fixtures; zero/missing inputs, timezone/shift boundaries, invalid transitions and nonnegative invariants covered |
| 2 | Real PostgreSQL integration tests | **Met** | Real PostgreSQL throughout — never SQLite, never mocks — for migrations, tenancy, approval commits, leases, fencing, idempotency, import rollback and competing allocations. Largest recorded single integration runs: 207 and 108 passed |
| 3 | Agent contract tests | **Met** | Invalid JSON, wrong agent/task, fabricated citation, unauthorized tool, unknown record, prompt injection, budget exhaustion, stale snapshot, partial result, provider timeout and 429 |
| 4 | Frontend/component tests | **Met** | 34 files / 123 tests passed: forms, status distinctions, permission messages, pending approvals, evidence navigation, duplicate-submit handling, XSS escaping |
| 5 | Browser end-to-end tests | **Not met** | **Dropped by the user.** No Playwright suite, no e2e CI job, no screenshots. The `@playwright/test` dev dependency remains in `apps/web/package.json` but no suite uses it |
| 6 | Resilience tests | **Partially met** | Worker SIGKILL, lease expiry and reassignment, late completion, repeated import/request, budget exhaustion, provider failure, stock changed after proposal, and one real backup + restore + verification cycle (0.877 s / 1.507 s). **Gap:** that restore ran against a database with no documents, so the `document_versions.storage_key` verification branch had zero rows and has never been exercised |
| 7 | Security checks | **Partially met** | Secret scan (with planted-pattern self-tests), dependency audits, negative permission tests, malicious file fixtures, XSS and CSRF checks, documented injection suite — all run. **Container image scanning was never run: no Docker host** |

## 4. Release gates — evaluation acceptance gates (plan §13)

**Every number in this section comes from a `--embedder hashing --scenarios 2` smoke run.** The
real evaluation (`make eval`, real embedder, 12 scenarios, dedicated `linesense_eval` database) is
**PENDING** and `docs/evaluation/results/` is deliberately empty rather than holding a stand-in
result presented as canonical.

| Measure | Gate | Verdict | Actual |
|---|---|---|---|
| Deterministic calculation correctness | All reference cases pass | **Met** | `calculations_all_pass = 1`; the independently worked fixtures are also unit tests |
| Capacity/reservation safety | No oversubscription or double consumption | **Met** | Concurrency tests with real overlap, plus a mutation check proving the tests test the lock |
| Retrieval Recall@5 | ≥ 0.85; compare lexical/vector/hybrid | **Not verified** | 0.711 with the semantics-free stand-in embedder (2 of 45 test questions are out-of-scope BYG documents by design). Not a measurement of the real embedder |
| Entity extraction | Micro F1 ≥ 0.90, with per-entity failures | **Met** | **0.991** |
| Note classification | Macro F1 ≥ 0.80, with support/confusion matrix | **Met** | **0.835** (accuracy 0.836) |
| Citation validity | Every rendered citation resolves to authorized versioned evidence | **Met** | Document-access and citation tests; authorization re-checked on open |
| Grounded recommendations | ≥ 90% supported on a student-reviewed rubric | **Not met** | **No human rubric review was ever performed.** Citation existence was never treated as entailment, and entailment was never scored |
| Abstention | Unknown policy / no inspection / missing data never yields automatic release | **Met** | 3 of 3 abstention checks in the harness, plus the domain tests behind them |
| Critical access tests | No cross-scope disclosure or unauthorized mutation | **Met** | Negative access tests per module plus the route-generated IDOR matrix |
| Recovery | Worker crash resumes or fails clearly, no duplicate approved effects | **Met** | Real SIGKILL test with a full approve + apply afterwards, asserting exactly one allocation/reservation set and a 409 on re-application |
| Multi-agent comparison | Deterministic vs single-agent vs four-agent on the same scenarios | **Partially met** | All three ran; material-conflict accuracy 1.0 for all three at n=2; the single-agent baseline structurally cannot detect a material conflict, which is the comparison's point. `capacity_sufficient` is an acknowledged approximation. **n=2 is a smoke sample** |
| Live-run variance | Repeat a subset of real LLM runs to measure variation | **Not met** | No live run exists to repeat |

## 5. Definition of done (plan §13)

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | Clean checkout can follow README setup, migrations, seed and test commands | **Not verified** | Every command is documented and each was run at some point during the build, but **no clean-checkout reproduction was ever performed end to end** |
| 2 | All critical browser flows and permissions work on real persistence, not UI mock arrays | **Partially met** | No mock arrays: every screen is wired to the generated API client against real endpoints, and permissions are enforced and tested server-side. **The browser flows themselves were never driven** |
| 3 | At least one recorded real-provider multi-agent flow; fixtures labelled | **Not met** | **No live-provider run has ever occurred.** The fixture half of the criterion is met: fixtures are labelled everywhere, and production startup refuses the fixture provider |
| 4 | All required tests pass; no unresolved critical/high security finding | **Partially met** | No Critical or Important review finding is left open — each was fixed and re-reviewed. **But no full-suite run was ever executed**; evidence is per-task focused runs with recorded pass counts |
| 5 | No unimplemented required button, fake integration, fabricated citation, silent exception, or success-looking fallback | **Met** | Enforced by the per-task review pass; the polish batch closed the last deferred items, including narrowing a bare `except Exception` and removing a dead branch |
| 6 | Deployment smoke check, migration check and restore exercise have recorded evidence | **Partially met** | Migration check (`upgrade`/`downgrade`/`upgrade`/`check`) recorded; restore exercise recorded with timings. **No deployment smoke check: no Docker host** |
| 7 | Current limitations and lower-severity issues documented with owner and impact | **Partially met** | Documented with impact throughout the status log, the report §15 and this file. **Owners are not assigned** — [`contribution-log.md`](contribution-log.md) is still a template |
| 8 | Report, architecture, evaluation results, video script, contribution log and README match the build | **Partially met** | Report, architecture docs, video script and README are current and consistent. **Evaluation results are absent by design** (no real run) and the contribution log is a template |

## 6. Phase exit gates (plan §14)

| Phase | Exit gate | Verdict | Notes |
|---|---|---|---|
| 0 — requirements and contracts | Traceability and assumptions documented; formula examples agreed | **Met** | Requirements, ADRs, threat model, contracts, glossary, formulas with four independently worked fixtures |
| 1 — walking skeleton | Authenticated browser-to-DB flow; unauthorized access fails | **Partially met** | Full OIDC/session/scope stack with negative tests, orders list/create/detail, generated client, CI. **"Browser-to-DB" verified at the API and component level, never in a browser.** Compose exists but was never run |
| 2 — durable two-agent slice | Shortage causes revised proposal; restart recovery; self/stale approval denied | **Met** | `test_two_agent_flow` (revision driven by the RM result), worker-kill recovery, `SELF_APPROVAL_DENIED`, 409 on stale — all with recorded pass counts |
| 3 — retrieval and NLP | Source-access tests and baseline metrics saved | **Partially met** | Source-access tests pass. **Baseline metrics exist only as a stand-in-embedder smoke run**, deliberately not committed as results |
| 4 — complete domain scope | Four-agent investigation and safe shipment eligibility demo | **Met** | Four-agent flow tests (54 and 108 passed), all screens, quality holds and releases, derived shipment eligibility that a positive AI explanation cannot override |
| 5 — hardening and deployment | All release gates met, **or explicit scope correction** | **Partially met — by explicit scope correction** | Concurrency, uploads, injection testing and scans done. Scope corrections, all explicit: browser E2E dropped; load test not run; no hosted environment; no container build or scan |
| 6 — assessment package | Deliverables complete and claims trace to evidence | **Partially met** | Report (PDF + LaTeX + 9 diagrams), user guide, commercialization, responsible AI, model and data cards, video script, mid-evaluation outline, licences, this matrix — all complete and traceable. **Outstanding: the video itself is not produced, the contribution log is a template, and there is no measured comparison because the evaluation has not been run** |

## 7. Assignment deliverables (plan §2 traceability)

| Brief requirement | Verdict | Note |
|---|---|---|
| At least two interacting intelligent agents | **Met** | Four agents; planning demonstrably revises on the RM result |
| LLM use | **Partially met** | The full boundary, adapter, budgets, redaction and degradation paths are implemented and tested — **against a fixture, never a live provider** |
| NLP | **Met** | Extraction 0.991 micro F1, classification 0.835 macro F1, grounded summarization with `summary_source` |
| Information retrieval | **Partially met** | Hybrid lexical + vector with RRF and authorized citations, implemented and tested; **Recall@5 unmeasured at the real embedder** |
| Security | **Met** | OIDC + PKCE, server sessions, scope on every path, validated uploads, negative tests, threat model, recorded clean scans |
| Defined agent protocol | **Met** | Versioned JSON over private HTTP, durable task ids, idempotency, OpenAPI and protocol schemas exported, sequence diagram, retry/fencing tests |
| Responsible AI | **Met (as documented and enforced)** | Evidence, abstention, worker privacy, human review, adversarial tests, model and data cards — with the live-model gap stated in every one of them |
| Commercialization | **Met** | [`commercialization.md`](commercialization.md) plus report §14; prices labelled as hypotheses; per-run cost explicitly not quotable from this build |
| Week 6 mid evaluation | **Partially met** | [`mid-evaluation-outline.md`](mid-evaluation-outline.md) and a genuinely demonstrable two-agent slice; written retrospectively, which the document says on its first page |
| Week 10 Gen AI video | **Not met** | [`video-script.md`](video-script.md) is complete and timed; **no video has been produced** |
| Week 10 report | **Met** | [`docs/report/linesense-report.tex`](../report/linesense-report.tex) + compiled PDF. Caveat: **the official template was never supplied**, so the report is self-structured and says so |
| Week 10 repository | **Met** | README, user guide, development guide, ADRs, architecture, security, operations and evaluation documentation; `make docs-check` verifies every relative link |
| Week 11 viva | **Not met (pending)** | Contribution log is a template; individual rehearsals have not happened |

## 8. Everything that is Not met or Not verified, in one list

Nothing below is mitigated anywhere else. This is the list to read before claiming anything about
this project.

**Not met**

1. Browser end-to-end tests — dropped from scope. No screenshots exist.
2. A real-provider multi-agent run — no API key has ever been configured here.
3. Live-run variance measurement — nothing live to repeat.
4. Human rubric review of grounded-recommendation support (the ≥90% gate).
5. Deployment smoke check, container build and container image scan — no Docker host.
6. The Gen AI video itself (the script exists).
7. Named contributors and viva rehearsals.

**Not verified (runnable, never run)**

8. Full test suites: `make test`, `make test-integration`, `make test-all`, `make security`,
   `make web-test` as whole-target invocations.
9. `make eval` with the real embedder — the only evaluation numbers anywhere in this repository
   come from a stand-in-embedder smoke run.
10. `make perf` — no latency figure exists for this system.
11. Clean-checkout reproduction of the README setup path.
12. The `document_versions.storage_key` branch of the restore verification.
13. Automated accessibility audit and contrast measurement.

**Implemented but deliberately limited**

14. Row-level security — application-layer scope only, documented as a pre-pilot requirement.
15. Upload scanning — structural byte/marker checks, no antivirus engine.
16. No OCR — scanned or encrypted PDFs are rejected with a message rather than partially parsed.
17. Vector search has no ANN index — exact scan, fine at 30 documents, not a scale claim.
18. Rate limiting is per process — behind multiple API instances each enforces its own budget.
19. Notifications are in-app only; no ERP/MES connector exists.
20. The IDOR matrix covers `GET` routes; write routes are covered by their owning modules' tests.
