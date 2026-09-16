# ADR-0003: PostgreSQL job table as the durable work queue

**Status:** Accepted — 2026-09-17

## Context

Analysis runs must survive a worker crash, must not process the same task
twice with conflicting effects, and must recover stalled work without a
human restarting anything by hand (spec §7, §13). The assignment has no
budget for operating a separate broker (Redis/RabbitMQ/Kafka), and none of
its durability guarantees exceed what PostgreSQL itself can provide at this
throughput.

## Decision

Use a single `jobs` table (`docs/architecture/backend-contracts.md` §2, §7)
as the queue for four logical queues (`orchestrator`, `agent`, `document`,
`maintenance`). Workers claim eligible rows with
`SELECT ... FOR UPDATE SKIP LOCKED`, recording `lease_token`,
`leased_until`, `attempt`, and `heartbeat_at`. A claim is a short
transaction; the actual task execution (including any LLM call) runs
**outside** a database transaction so row locks are never held across a
network call. Completion is fenced by `lease_token`: a worker whose lease
already expired and was reclaimed cannot overwrite a newer attempt's
result. Delivery is **at least once** — task idempotency keys and
transactional result commits make repeat delivery safe; this is never
described as exactly-once. Retries use bounded exponential backoff with
jitter (`min(2**attempt*2, 60)` seconds) up to `max_attempts`; exhausted
jobs are marked `FAILED`, not silently dropped.

## Consequences

- No new infrastructure dependency; the existing project-local PostgreSQL
  cluster is the only durable store to operate, back up, and restore.
- `SKIP LOCKED` is documented by PostgreSQL specifically for queue-like
  consumers and explicitly unsuitable for ordinary reporting queries — the
  `jobs` table is never queried for business reporting.
- Throughput is bounded by row-lock contention on `jobs`; acceptable at
  assignment scale (spec §13 targets ~100 orders, four agents per run) but
  a documented limitation before higher-volume production use.
- Crash recovery is lease-expiry based: a periodic reconciliation job (job
  type in the `maintenance` queue) finds runs whose current task's lease
  has expired and reschedules or fails them, rather than relying on
  process supervision alone.

## Alternatives considered

- **Redis/RabbitMQ queue**: rejected — adds an operational dependency with
  no correctness benefit at this scale, and duplicates transactional
  guarantees PostgreSQL already provides for free within the same commit
  as the business writes it depends on.
- **Celery/Kafka-based orchestration**: rejected per ADR-0001 — a second
  orchestration framework is explicitly out of scope for the assignment.
- **In-process background tasks (no queue table)**: rejected — does not
  survive a process restart and cannot express lease fencing or bounded
  retries, both required by the recovery test scenarios (spec §13.6).
