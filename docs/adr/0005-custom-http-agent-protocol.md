# ADR-0005: Custom versioned HTTP/JSON agent task protocol

**Status:** Accepted — 2026-09-17

## Context

The four domain agents must exchange typed tasks/results through an
orchestrated, durable protocol with retries, evidence, and authorization
(spec §7, §17). The supplied architecture diagram lists A2A, REST, and MCP
together as candidate framings; the plan is explicit that this is an
ambiguity to resolve, not a menu to implement all of (spec §3: "A2A / REST
/ MCP presented together" → "Implement and document one HTTP/JSON
protocol; do not claim A2A or MCP compliance"). Neither A2A nor MCP is
designed around this system's specific needs: durable server-side task
records with lease/attempt bookkeeping, a fixed small set of recipients
(`planning`, `rm`, `ie`, `quality`), and validation rules tied to this
schema (evidence references, record ownership, snapshot versions).

## Decision

Implement a custom, versioned HTTP/JSON protocol
(`app/orchestration/protocol.py`, `schema_version "1.0"`), documented in
`docs/architecture/backend-contracts.md` §6: `POST /internal/v1/agent-tasks`
(dispatch, idempotent on `idempotency_key`) and
`GET /internal/v1/agent-tasks/{task_id}` (poll). The service credential
(`Authorization: Bearer <LS_SERVICE_TOKEN>`, constant-time compare)
identifies a permitted dispatcher; the server independently loads the run
and verifies envelope scope, agent, task type, and declared dependencies —
fields in the JSON body (`organization_id`, `read_only`, etc.) are never
trusted just because they are present. This protocol is never described,
labelled, or documented as A2A or MCP anywhere in code, comments, or
assessment materials.

## Consequences

- Full control over validation semantics specific to this domain: every
  `evidence_ids` entry must resolve in `evidence_refs`; every referenced
  record/document must belong to the run's organization/factory; action
  payloads must reference ids present in the immutable run snapshot.
- No dependency on an external protocol library's versioning or feature
  set; the protocol evolves by incrementing `schema_version` and adding
  explicit compatibility handling, not by tracking an upstream spec.
- Requires writing and maintaining the protocol's own OpenAPI/JSON Schema
  and integration tests (spec §2 traceability: "OpenAPI, schemas, sequence
  diagram, retry demo") since no ecosystem tooling validates it for us.
- Readers unfamiliar with the codebase cannot assume A2A/MCP tooling
  applies; this is intentional and stated up front in this ADR and in
  `CLAUDE.md`.

## Alternatives considered

- **Adopt A2A**: rejected — A2A targets cross-organization agent discovery
  and negotiation; this system's agents are internal, fixed, and already
  known to the orchestrator, so A2A's discovery/negotiation machinery adds
  complexity with no corresponding requirement.
- **Adopt MCP**: rejected — MCP is designed for exposing tools/resources to
  a model client, not for durable, server-orchestrated task dispatch with
  lease/attempt/evidence bookkeeping; bending MCP to fit would obscure
  rather than clarify the actual mechanism, and the plan explicitly forbids
  claiming MCP compliance for a protocol that isn't.
- **Plain function calls (no protocol at all, in-process)**: rejected — the
  orchestrator and agent executor must communicate durably across a worker
  crash/restart (ADR-0003); a real request/response boundary is required so
  every hand-off is persisted, not just held in memory.
