# Durable jobs

LineSense runs background work (orchestration, agent tasks, document processing, maintenance)
through a job queue stored in PostgreSQL, in the `jobs` table. There is no separate broker. The
binding interface is in [backend-contracts.md §7](backend-contracts.md#7-durable-jobs-appjobsqueuepy).
The code is in `services/backend/app/jobs/`:

| Module | Responsibility |
| --- | --- |
| `queue.py` | `enqueue`, `claim`, `heartbeat`, `complete`, `fail`, `ClaimedJob`, the error types, and the backoff |
| `worker.py` | `Worker`, `HandlerRegistry`, `JobContext`, `finish_in_transaction` |
| `handlers.py` | `build_registry(settings)`: registers every job type the worker runs |
| `reconcile.py` | `reconcile_once`: expires idempotency keys and overdue recommendations |
| `__main__.py` | `python -m app.jobs` (`make worker`): signal handling and CLI |

Queues: `orchestrator`, `agent`, `document`, `maintenance`.

## States

```mermaid
stateDiagram-v2
    [*] --> READY: enqueue (dedupe_key → at most one row)
    READY --> LEASED: claim (available_at <= now())
    LEASED --> DONE: complete (fenced by lease_token)
    LEASED --> READY: fail, retryable and attempt < max_attempts\n(available_at = now() + backoff)
    LEASED --> FAILED: fail, permanent / unknown type / attempts used up
    LEASED --> LEASED: heartbeat (extends leased_until)\nor reclaim after lease expiry (new token, attempt+1)
    LEASED --> FAILED: reclaim after lease expiry when attempt would exceed max_attempts
    DONE --> [*]
    FAILED --> [*]
```

`CANCELLED` exists in the schema for later tasks. No code path in Task 10 sets it.

## Claiming

`claim` is a single statement:

```sql
UPDATE jobs SET status = 'LEASED', lease_token = :fresh, worker_id = :worker,
       attempt = attempt + 1, leased_until = now() + :lease, heartbeat_at = now()
WHERE id = (
  SELECT id FROM jobs
  WHERE queue IN (:queues)
    AND ((status = 'READY'  AND available_at <= now())
      OR (status = 'LEASED' AND leased_until  <  now()))
  ORDER BY available_at, created_at
  LIMIT 1
  FOR UPDATE SKIP LOCKED)
RETURNING id, queue, job_type, payload, attempt, max_attempts, lease_token
```

Because of `SKIP LOCKED`, concurrent claimers never block each other and never claim the same row.
All lease times use the database clock. If a reclaimed job's `attempt` is now greater than
`max_attempts`, the same transaction marks it `FAILED` with `last_error = 'attempts exhausted after
lease expiry'` and sets `attempt` back to `max_attempts`. After the commit, the job type's
`on_exhausted` hook runs and the claim is tried again.

## Leases, heartbeats and fencing

- While a handler runs, the worker calls `heartbeat` every `heartbeat_seconds` (default 10 s;
  the lease lasts 30 s). A heartbeat only succeeds if the row is still `LEASED` with the
  worker's `lease_token`.
- If a heartbeat returns `False`, the lease was lost. The worker cancels the handler task
  (`asyncio.Task.cancel`) and logs `job.lease_lost`. It does **not** mark the job failed, because
  the job now belongs to whichever worker holds the new lease.
- Handlers make their business writes and call `finish_in_transaction(ctx, session)` in the
  **same transaction**. That call runs `complete`, which is fenced by `lease_token` and
  `status = 'LEASED'`. If the lease is gone, it raises `LeaseLostError`, so the handler's writes
  roll back with it.
- If a handler returns without completing the job, the worker completes it in a fresh fenced
  transaction. If that returns `False`, the worker checks whether the row is already `DONE` under
  its own token (the handler completed it). Otherwise it logs `job.lease_lost`.
- `fail` is fenced the same way. A stale token changes nothing, and `fail` returns `False`; the
  worker then logs `job.lease_lost` instead of `job.failed`.
- When a heartbeat returns `False`, the worker first checks whether the row is `DONE` under its
  own token. If it is, the handler has already committed its completion, so heartbeating stops
  and the handler finishes its post-commit work without being cancelled.
- When a handler finishes, the worker signals the heartbeat task to stop and waits up to 5 s for
  any in-flight heartbeat. The heartbeat is not cancelled in the middle of its transaction.

## Failures and retries

| Handler outcome | Result |
| --- | --- |
| returns | `DONE` |
| `PermanentJobError`, or no handler registered for the type | `FAILED`, then `on_exhausted` runs |
| any other exception (including `RetryableJobError`) | `READY` after backoff if `attempt < max_attempts`, otherwise `FAILED` and `on_exhausted` runs |
| lease lost | unchanged (the new lease holder owns the job) |
| handler raises `CancelledError` itself (no lease loss, worker not shutting down) | treated like any other exception: retry |

Backoff is `min(2 ** attempt * 2, 60)` seconds plus up to 1 s of jitter. The error text stored in
`last_error` is `repr(type(exc).__name__) + ': ' + str(exc)[:500]`, capped at 2,000 characters.
It never includes tracebacks or local variables. For SQLAlchemy statement errors only the driver
exception's class and the first line of its message are stored, never the SQL text. Engines are
created with `hide_parameters=True`, so bound values stay out of error messages.

### Exhaustion hooks run at most once

An `on_exhausted` hook runs after the `FAILED` state has been committed. If the hook raises, the
worker logs `job.exhausted_hook_failed` and the job stays `FAILED`. If the process dies between
the commit and the hook, the hook never runs. Anything a hook does (for example, marking an
analysis run `FAILED`) therefore needs a reconciliation fallback that detects `FAILED` jobs whose
business rows were never updated.

## Worker runtime

`python -m app.jobs --queues orchestrator,agent,document,maintenance --concurrency 4` (or
`make worker`) does the following:

- Runs `concurrency` asyncio slots. Each slot claims and processes one job at a time.
- Runs `reconcile_once` every 15 s. It is idempotent, so several workers can run it safely.
- On SIGINT/SIGTERM, stops claiming and gives running handlers up to 20 s to finish. Handlers
  still running after that are cancelled. Their jobs stay `LEASED` and are recovered when the
  lease expires.
- Logs `job.claimed`, `job.completed`, `job.failed` and `job.lease_lost` with the job id, type and
  attempt.
- Runs a separate liveness task every 30 s, so a worker whose slots are all busy still reports.
  The task logs `worker.alive`. Outside production it also touches `.local/worker-<id>.alive`
  (the directory is set by `LS_WORKER_ALIVE_DIR`, relative to `services/backend`, or by the
  `alive_dir` argument). The file is removed on shutdown, and file errors are logged without
  stopping anything. No table stores worker liveness.
- Keeps each slot running when a job misbehaves: errors (including a stray `CancelledError`) are
  logged, and polling backs off exponentially up to 5 s. A slot exits only on shutdown.

## Delivery guarantee: at least once

A job can run more than once. For example, a worker can finish its side effects outside the
database, then lose its lease before committing. Handlers must be idempotent:

- Put database writes and `finish_in_transaction` in one transaction. Fencing then ensures that
  at most one attempt's database writes commit.
- Key any external side effect (LLM calls, files) on the job id or on a business idempotency key,
  and make it safe to repeat.
- Use `dedupe_key` when enqueuing, so that a repeated request does not create a second job.

## Reconciliation (initial scope)

`reconcile_once(session_factory, settings, *, now=None)` makes these changes in one transaction:

- deletes `idempotency_keys` that expired more than one hour ago, in batches of 500
  (`DELETE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 500)`). The hour absorbs clock
  skew, because `idempotency.begin` stamps `expires_at` with the API host's clock. If a purge
  removes the conflicting row between `begin`'s `INSERT ... ON CONFLICT DO NOTHING` and its
  `SELECT ... FOR UPDATE`, `begin` retries the insert (up to 3 attempts).
- marks `recommendations` in `PROPOSED`/`APPROVED` whose `expires_at <= now` as `EXPIRED`,
  increments `version`, and writes a `SYSTEM` audit event (`recommendation.expire`) for each row.
  Rows locked by a concurrent decision are skipped (`SKIP LOCKED`) and picked up in a later round.

Task 13 adds more reconciliation steps.
