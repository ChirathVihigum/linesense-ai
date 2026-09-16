# Architecture decision records

Each ADR is a single-page, dated decision record: Status, Context, Decision,
Consequences, Alternatives considered. ADRs are immutable once accepted; a
later decision that changes or reverses one is recorded as a new ADR that
supersedes it, not an edit to the old one. Refer to
[`docs/architecture/backend-contracts.md`](../architecture/backend-contracts.md)
for the binding schema/contract details each decision implies.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-modular-monolith-and-worker.md) | Modular monolith with a separate worker process | Accepted |
| [0002](0002-postgres-pgvector-single-store.md) | PostgreSQL + pgvector as the single data/vector store | Accepted |
| [0003](0003-postgres-job-queue.md) | PostgreSQL job table as the durable work queue | Accepted |
| [0004](0004-oidc-server-sessions-and-dev-idp.md) | OIDC + PKCE with opaque server sessions, dev-only IdP | Accepted |
| [0005](0005-custom-http-agent-protocol.md) | Custom versioned HTTP/JSON agent task protocol | Accepted |
| [0006](0006-llm-boundary-and-fixture-provider.md) | LLM boundary, provider choice, and fixture provider | Accepted |
| [0007](0007-single-style-orders-and-ledger-balances.md) | Single style per order; ledger-derived material balances | Accepted |
| [0008](0008-local-environment-without-docker.md) | Local development environment without Docker/Java | Accepted |
