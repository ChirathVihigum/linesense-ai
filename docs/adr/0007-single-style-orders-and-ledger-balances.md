# ADR-0007: Single style per order; ledger-derived material balances

**Status:** Accepted — 2026-09-17

## Context

The data model must stay simple enough for a student team to implement and
test correctly under the assignment timeline while still supporting
concurrency-safe inventory/capacity operations, retrieval with citations,
and single-decision approvals (spec §5, §7, §13). Three structural
simplifications need an explicit, recorded rationale so later readers do
not mistake them for oversights.

## Decision

1. **One style per order.** `orders` has a direct `style_id`/`bom_version_id`
   (`docs/architecture/backend-contracts.md` §2); there is no `order_items`
   table for multiple styles per order. The assignment's synthetic dataset
   and demonstration scenario (spec §15) never require multi-style orders,
   and every planning/materials/IE/quality calculation in spec §6 is
   defined per style.
2. **Ledger + lockable balance row for materials.** `stock_movements` is an
   append-only ledger (`RECEIPT`/`ISSUE`/`CORRECTION`); `material_balances`
   holds one row per `(factory_id, material_id)` with `version` for
   optimistic/pessimistic concurrency, maintained in the same transaction
   as every ledger write. A test asserts `on_hand_accepted == sum(movements
   on ACCEPTED lots)` so the cached balance can never silently drift from
   the ledger it summarizes.
3. **Embeddings stored on `chunks`**, not a separate embeddings table (see
   ADR-0002) — one row per chunk carries both its lexical `tsv` and its
   vector `embedding`.
4. **One `approvals` row per `recommendation`** (`approvals.recommendation_id
   unique`), recording a single terminal decision (`APPROVED`/`REJECTED`)
   per proposal rather than a multi-step approval chain — matching the
   plan's single-approver-per-proposal model (spec §11: "An approver cannot
   approve their own proposal").

## Consequences

- Planning/materials/IE/quality tools and calculations (spec §6) operate on
  one style context per order without a join through an items table,
  simplifying every formula and its tests.
- Adding multi-style orders later requires a migration (introducing
  `order_items` and moving `style_id`/`bom_version_id` onto it) — this is
  an explicit, out-of-scope-for-now decision, not a bug.
- The ledger/balance split gives an auditable trail (every balance change
  traces to a `stock_movements` row) while keeping hot-path reads
  (`material_balances`) O(1) instead of re-summing the ledger per request;
  the equality test is the safeguard against the two drifting apart.
- Locking `material_balances` rows in deterministic (sorted id) order at
  proposal application (spec §5) is sufficient because there is exactly one
  balance row per material per factory to lock — no items-table fan-out to
  reason about.
- A single `approvals` row per recommendation means re-review after
  rejection requires a new `recommendation` (via reanalysis), not a second
  approval row on the same one — consistent with proposals being
  superseded, not mutated, once decided.

## Alternatives considered

- **`order_items` from the start**: rejected as premature — no requirement
  in the assignment brief or demonstration scenario needs it, and it would
  add a join to every domain calculation and test without a corresponding
  capability gain.
- **Store only the ledger, compute balance on every read**: rejected —
  correct but re-sums potentially many rows per read; the maintained
  balance row with a verifying test gets both correctness and O(1) reads.
- **Multiple approval rounds per recommendation** (a history table instead
  of a unique row): rejected for the initial scope — the plan's approval
  model is propose → single decision → apply/reject; a full approval-chain
  history table is deferred until a requirement for it appears.
