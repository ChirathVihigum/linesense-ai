# The analysis run, step by step

What actually happens between a planner pressing **Start analysis** and a
supervisor seeing a proposal. The TikZ version of this sequence is
`docs/report/diagrams/d4-agent-sequence.tex` (figure "The end-to-end four-agent
analysis run" in the report); the orchestration graph itself is in
[`agent-protocol.md`](agent-protocol.md).

## The sequence

```mermaid
sequenceDiagram
    autonumber
    participant UI as Planner (browser)
    participant API as API (FastAPI)
    participant O as Orchestrator job
    participant RM as RM agent
    participant IE as IE agent
    participant QA as Quality agent
    participant PL as Planning agent
    participant DB as PostgreSQL

    UI->>API: POST /api/v1/orders/{id}/analyses<br/>Idempotency-Key, expected_order_version
    API->>DB: one transaction — analysis_runs + immutable run_snapshots<br/>+ run.created + orchestrator.advance job
    API-->>UI: 202 {run_id, status: QUEUED}
    DB->>O: worker claims the job (FOR UPDATE SKIP LOCKED, lease token)
    O->>RM: assess_material_readiness (round 0)
    O->>IE: assess_line_capability (round 0)
    O->>QA: assess_quality_status (round 0)
    Note over O,QA: one advance step — three TaskEnvelopes over<br/>POST /internal/v1/agent-tasks; the run lock is committed<br/>before any network call
    RM->>DB: deterministic assess() over the snapshot, then a bounded loop<br/>(≤4 scoped read tools; every model call reserved first)
    RM-->>O: AgentResult — MATERIAL_SHORTAGE 160 m, coverable_units 873
    IE-->>O: AgentResult — BOTTLENECK_OPERATION OP-04, 60 s, ≈60 units/h
    QA-->>O: AgentResult — ACTIVE_HOLD / NOT_INSPECTED, shipment_eligible=false
    O->>PL: propose_allocation (round 0)<br/>input_refs: snapshot + RM result + IE result
    PL-->>O: ranked ALLOCATION options
    O->>O: needs_replan → MATERIAL_SHORTAGE_CONFLICT
    O->>PL: revise_allocation (round 1 — at most one replan)
    PL-->>O: MATERIAL_LIMITED ranked first + REVISED_FOR_MATERIAL
    O->>RM: validate_plan_materials (round 1)
    RM-->>O: PLAN_MATERIAL_COVERED + RESERVATION proposal
    O->>DB: finalize, one transaction — recommendation + run.report<br/>+ run.finalized + notify supervisors + audit
    UI->>API: GET /runs/{id} and /events (polled every 2 s while active)
```

## Step notes

1. **Request.** `analysis:run` (planner or supervisor), an `Idempotency-Key`,
   and `expected_order_version`. Refusals: stale version `409 STALE_INPUT`;
   cancelled or dispatched order `409 INVALID_TRANSITION`; an active run already
   exists `409 CONFLICT` (naming it); more than five active runs in the factory
   `429 RATE_LIMITED` with `retry_after_seconds`.
2. **Snapshot.** The run and an **immutable snapshot** of every input the agents
   will reason about are created in one transaction. This is why two agents in
   the same run can never disagree about the facts, why a late agent result can
   never be based on data the requester never saw, and why the resulting
   recommendation carries the exact input versions it was derived from.
3. **Claim.** The worker claims `orchestrator.advance` with
   `UPDATE ... WHERE id = (SELECT ... ORDER BY available_at, created_at LIMIT 1
   FOR UPDATE SKIP LOCKED)` and a fresh lease token, using the database clock.
4. **Round 0, three tasks, one step.** RM, IE and quality are dispatched
   together — three `task.dispatched` events with consecutive ids before any
   `task.completed` — because none depends on another. Each is a
   `TaskEnvelope` (`schema_version "1.0"`) over
   `POST /internal/v1/agent-tasks` with the bearer service token; the server
   re-validates every id in the envelope against the run it loaded rather than
   trusting the JSON.
5. **Agent execution.** `execute_agent_task` re-checks that the requester still
   holds `analysis:run` on the factory, runs the agent **outside any
   transaction** (so no row lock is held across a model call), and then commits
   the validated result, the task status, the `task.completed` event, the next
   `orchestrator.advance` job and the fenced job completion **together**. A lost
   lease rolls the whole outcome back.
6. **Planning round 0** waits for RM **and** IE to be terminal and receives both
   results as `input_refs`. Its candidate options all come from
   `plan_earliest_slots`; the model may re-rank them but never rewrites a
   payload.
7. **The replan is deterministic.** `needs_replan` returns a reason only when
   the rank-0 `ALLOCATION` action commits more units than RM's
   `coverable_units` metric. The reason string is recorded verbatim in the
   `orchestrator.replan` event. At most one replan per run.
8. **RM round 1** validates the final plan's materials and proposes the
   reservation. A `DEGRADED` planning result is validated exactly like a
   `SUCCEEDED` one — otherwise the recommendation would propose an allocation
   whose materials were never checked.
9. **Finalization** writes, in one transaction: the recommendation
   (`proposal_hash`, `input_versions`, `expires_at = now + 24 h`), the
   supersession of earlier open recommendations for that order
   (`NEWER_ANALYSIS`), the run status, the canonical order report as a
   `run.report` event, `run.finalized`, a supervisor notification and the
   `analysis.completed` audit event.
10. **The UI reads persisted progress.** `GET /runs/{id}` and
    `GET /runs/{id}/events?after_id=` are polled every 2 s while the run is
    active and stop as soon as it is terminal. There is no SSE (explicitly out
    of scope).

## What the model actually did in this sequence

For each of the six agent tasks: at most one investigative tool call and then
`submit_assessment`. That is what the twelve-model-call run budget affords for
six tasks, and it is exactly what the fixture provider does. The model chose
which tool to call and wrote the summary; it did not produce a single number,
state or verdict.

## Failure paths in the same sequence

| Failure | What happens |
|---|---|
| Provider outage | three attempts per task; then the deterministic assessment with `degraded_reason = PROVIDER_UNAVAILABLE`. The graph is unchanged. |
| Run budget spent (an outage burns three attempts per task) | the remaining tasks return their deterministic assessment with `BUDGET_EXCEEDED` |
| Model cites evidence it was not given | validation rejects it, one repair turn is offered, a second failure degrades the result to `INVALID_AGENT_OUTPUT` with `evidence_refs` reduced back to the untouched deterministic set |
| Worker killed mid-task | the lease expires, another worker reclaims the job, and exactly one `agent_results` row exists (`ON CONFLICT DO NOTHING` on `task_id`). Proved by `tests/resilience/test_worker_kill.py`. |
| Run quiet for more than 30 s with no runnable job | `reconcile_once` re-enqueues `orchestrator.advance` with dedupe key `run:<id>:reconcile:<YYYYMMDDHHMM>` |
| Past `deadline_at` (120 s) | open tasks cancelled, `error_code = DEADLINE_EXCEEDED`, finalized `DEGRADED` if a planning result succeeded, else `FAILED` |

## Evidence

| Claim | Test |
|---|---|
| A shortage causes a revised proposal | `tests/integration/test_two_agent_flow.py::test_planner_requests_an_analysis_and_gets_a_recommendation` (the `orchestrator.replan` event carries `MATERIAL_SHORTAGE_CONFLICT`; the round-1 planning result carries `REVISED_FOR_MATERIAL`) |
| Four agents run as one graph | `tests/integration/test_four_agent_flow.py` |
| A crashed worker leaves exactly one result per task | `tests/integration/test_orchestrator.py::test_a_crashed_worker_leaves_exactly_one_result_per_task`, `tests/integration/test_executor.py::test_lease_lost_before_commit_writes_nothing` |
| A cancelled task reports the run's own reason | `test_a_cancelled_task_reports_the_runs_own_reason` |
| Prompt injection in a retrieved document cannot escalate | `tests/agents/test_document_tool.py::test_adversarial_document_cannot_hijack_the_agent_loop` |

**Provider note.** Every run described here has only ever been executed with the
deterministic `fixture` provider. No live-LLM run has been performed in this
environment.
