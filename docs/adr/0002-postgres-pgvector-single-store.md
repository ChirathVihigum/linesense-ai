# ADR-0002: PostgreSQL + pgvector as the single data/vector store

**Status:** Accepted — 2026-09-17

## Context

The system needs transactional business data (orders, capacity, inventory,
approvals, audit — spec §5) and a vector index for SOP/document retrieval
(spec §8). Running a separate vector database (e.g. a dedicated vector
service) alongside PostgreSQL adds a second consistency domain, a second
backup/restore procedure, and a second set of access-control checks to get
right under the assignment's security requirements (ADR is explicit in
spec §3 that a separate vector database was an "original ambiguity" to
remove).

## Decision

Use one PostgreSQL 16 instance with the `pgvector` extension for both
domains. `chunks.embedding` is a `vector(384)` column (dimension fixed by
the `BAAI/bge-small-en-v1.5` embedding model, see ADR context in
`docs/architecture/backend-contracts.md` §2) alongside the same row's
lexical `tsv` (generated `tsvector`) column, so lexical and vector search
share one table, one transaction boundary, and one authorization path
(`chunks.org`, `chunks.factory_id`, `document_acl`). Start with **exact**
nearest-neighbor search (no `ivfflat`/`hnsw` index) because the assignment
corpus is small (spec §13: ~30 SOP/quality documents); introduce an
approximate index only if measured latency on the actual corpus requires
it, and only after re-measuring Recall@5 with it enabled.
`pgvector` 0.8.6 is built from source into the Homebrew `postgresql@16`
installation (see ADR-0008) so `CREATE EXTENSION vector` works identically
on the project-local dev/test clusters and in Compose-based environments.

## Consequences

- One database engine to operate, back up, and restore; one place to apply
  row-level security and tenant scoping (spec §11).
- Retrieval queries join directly against `document_versions`/`document_acl`
  in the same SQL statement that does the vector search, so authorization
  filtering happens before content ever reaches the LLM (spec §8), instead
  of filtering after a separate vector-store round trip.
- Exact search is O(n) per query; acceptable at the assignment's ~30-document
  corpus but must be revisited before any larger real deployment.
- Embedding dimension (384) is fixed at the schema level; changing embedding
  model requires a new column/index and a controlled re-embedding migration,
  not a silent swap.

## Alternatives considered

- **Dedicated vector database** (e.g. a standalone vector service): rejected
  — duplicate consistency/auth surface for no capability the assignment
  needs at this scale; explicitly called out as unnecessary in the plan.
- **Approximate index (ivfflat/HNSW) from the start**: rejected — adds
  tuning parameters and recall/latency trade-offs that are premature before
  a baseline Recall@5 measurement exists (spec §13 evaluation gate).
- **Elasticsearch/OpenSearch for lexical search**: rejected — PostgreSQL's
  built-in `tsvector`/GIN already meets the lexical half of hybrid
  retrieval without a second search engine to secure and operate.
