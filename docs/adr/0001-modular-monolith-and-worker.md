# ADR-0001: Modular monolith with a separate worker process

**Status:** Accepted — 2026-09-17

## Context

LineSense needs four cooperating domain agents (planning, RM, IE, quality), a
public API, a durable background workflow, and document/retrieval
processing, built by a 3–4-person student team in a fixed assignment
timeline (spec §14). The supplied architecture diagram groups these as
logical functions, not as a mandate for one deployed service per function.
Microservices, a message broker (Kafka), and Kubernetes each add
operational surface (service discovery, distributed tracing, independent
deployment pipelines, network-partition handling) that this team cannot
build, test, and defend at the required quality within the schedule, and
none of it is required to demonstrate the assessed capabilities (agent
protocol, evidence, approvals, security boundaries).

## Decision

Build one backend codebase (`services/backend`, FastAPI + SQLAlchemy) with
internal module boundaries (`app/domain`, `app/agents`, `app/orchestration`,
`app/retrieval`, `app/nlp`, `app/llm`, ...) and a separate **process**
entrypoint for the durable worker that shares the same codebase and
dependencies. The API process serves `/api` (public) and `/internal`
(private agent dispatch, blocked at the proxy); the worker process claims
jobs from the PostgreSQL queue and executes the orchestrator/agent
executor. No microservices, no Kafka/RabbitMQ, no Kubernetes, no separate
vector database service, and no second orchestration framework are
introduced. This matches the plan's explicit architecture (spec §3–§4).

## Consequences

- Deployment is one backend image with two entrypoints (`api`, `worker`);
  local dev and CI run both directly with `uv run`.
- Module boundaries are enforced by code review and import discipline, not
  by network isolation; a future split into real services remains possible
  because the module boundaries already match the eventual service
  boundaries.
- All agent-to-orchestrator communication still goes over the documented
  HTTP/JSON protocol (ADR-0005) even though it stays on one host, so the
  protocol and its tests are not weakened by staying in-process.
- Scaling is vertical/process-count only in this environment; documented as
  a known limitation for a real pilot (spec §12).

## Alternatives considered

- **Per-agent microservices**: rejected — adds deployment/networking
  complexity disproportionate to a 4-agent, single-team assignment, and
  the plan explicitly instructs against it.
- **Kafka/event-bus orchestration**: rejected — the PostgreSQL job table
  (ADR-0003) already gives durable, at-least-once delivery with leases and
  fencing at the assignment's scale; a broker adds an unfunded dependency.
- **Single process, no worker** (run agents inline on the request path):
  rejected — analysis runs must survive process restarts and not hold the
  HTTP request open for a multi-agent LLM round trip (spec §7).
