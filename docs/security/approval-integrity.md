# Approval integrity

How LineSense AI keeps an agent proposal from becoming an unreviewed,
stale or self-approved change. Implementation: `app/domain/approvals/service.py`
(rules and transaction), `app/api/recommendations.py` (routes, retry,
idempotency). Error codes and the audit contract: `docs/architecture/backend-contracts.md` §5.

## The model

A recommendation is a **proposal**, never a change. It records what would
happen (`proposal`), a hash of exactly that (`proposal_hash`), and the
version of every input it was derived from (`input_versions`: the order,
each capacity slot, each material balance). Nothing in the proposal path
writes to `allocations`, `reservations`, `line_capacity_slots` or
`material_balances`.

Two separate acts follow, by two separate people:

1. `POST /api/v1/recommendations/{id}/decision` — a supervisor records
   `APPROVED` or `REJECTED`. **Approving does not apply anything.**
2. `POST /api/v1/recommendations/{id}/apply` — a supervisor who is *not*
   the proposer applies it, inside one transaction.

## Separation of duties

- `recommendation:decide` and `recommendation:apply` are supervisor-only
  (`app/auth/policy.py`). A caller with no role in the recommendation's
  factory gets 404 (existence is never revealed); a caller with a role but
  without the permission gets 403 `FORBIDDEN`.
- The **proposer** — `recommendations.proposer_user_id`, i.e. whoever
  requested the analysis run — may neither decide nor apply the proposal:
  403 `SELF_APPROVAL_DENIED`, recorded as an audit event with outcome
  `DENIED` in its own committed transaction. A second supervisor of the
  same factory may do both; only the proposer is excluded.
- Every successful decision and application writes `recommendation.decided`
  / `recommendation.applied` audit events with before/after summaries.

## Lock order

Shared with `app.domain.capacity.service` and `app.domain.inventory.service`:

```
order row  ->  recommendation row  ->  line_capacity_slots  ->  material_balances
                                        (ascending id)          (ascending id)
```

`apply` locks the **union** of everything the transaction may touch before
it touches anything:

- slots: every slot in the proposal **plus** every slot the order has ever
  been allocated to (the set `release_order_allocations` locks);
- balances: the balance of every proposed material **plus** the order's BOM
  materials and every material it has ever reserved (the set
  `release_order_reservations` locks). A proposed material without a balance
  row gets one (`ensure_balance`) *before* the lock sweep, so
  `reserve_material` can never create and lock a row out of order.

Because the union is locked up front and by sorted id, the release/allocate/
reserve helpers never need a lock the transaction does not already hold, and
two concurrent applies can never take the same two rows in opposite orders.

ACTIVE `allocations`/`reservations` rows are re-read `FOR UPDATE` after the
slot/balance locks (inside those helpers), so a release that another
transaction committed first is never applied twice.

## Freshness

`check_staleness` compares `input_versions` with the current `version` of
every referenced order, slot and balance. A missing row counts as stale.

- In the **detail view** it is advisory: `stale`, `stale_inputs` and
  `apply_blocked_reason: "STALE"` tell the reviewer before they act.
- In **apply** it is authoritative: it runs *after* the row locks, inside
  the transaction that would otherwise write. Any drift:
  1. sets the recommendation to `SUPERSEDED` with
     `superseded_reason = "STALE_INPUT: <kinds>"`,
  2. audits `recommendation.applied` with outcome `FAILED`,
  3. notifies the proposer ("Inputs changed — run a new analysis"),
  4. **commits that status change**, and only then
  5. fails the request with 409 `STALE_INPUT`, listing each changed input in
     `field_errors`.

  The idempotency claim is released on this path (`app.idempotency.service.release`)
  so the key is not burned on a rejection.

An approval can therefore never bypass a fresh analysis: reference fixture 6
(`docs/architecture/formulas.md`) is exactly this case — an approved proposal
whose material balance moved on is refused, not applied.

Expiry works the same way: past `expires_at`, the recommendation becomes
`EXPIRED` (committed, audited, proposer notified) and the request fails with
409 `EXPIRED`.

The `proposal_hash` is checked on both decide and apply: a proposal whose
content changed since the reviewer read it yields 409 `CONFLICT`
("Proposal changed").

## The apply transaction

Under the locks, and only when nothing is stale:

1. the order must be `VALIDATED` or `PLANNED` (else 409 `INVALID_TRANSITION`);
2. the order's existing ACTIVE allocations and reservations are released
   (slot minutes and balance `reserved` decremented, versions bumped);
3. each proposed allocation goes through `capacity.allocate`, which
   re-validates remaining capacity under the slot lock — a failure rolls the
   **whole** transaction back (409 `CONFLICT`, zero partial rows);
4. each proposed reservation goes through `inventory.reserve_material`, which
   re-validates `on_hand - reserved` under the balance lock — same rollback;
5. the order becomes `PLANNED`, its `material_state` is recomputed
   (`recompute_material_states`, restricted to this order — the only order
   row this transaction holds), and its version is bumped;
6. the recommendation becomes `APPLIED` with `applied_by` / `applied_at`;
7. a `maintenance.refresh_material_states` job (deduped per order +
   recommendation) recomputes *other* orders that share the materials, in
   its own later transaction — locking them here would mean taking an order
   lock after a balance lock.

**Idempotency**: `Idempotency-Key` is required. A retry with the same key
replays the stored 200 body and creates nothing; a different key against an
already `APPLIED` recommendation gets 409 `CONFLICT`.

**Bounded retry**: the route owns its session and retries the whole
transaction up to 3 times, with jittered backoff, on SQLSTATE `40001`
(serialization failure) and `40P01` (deadlock detected) only. Everything
else propagates.

## Test evidence

| Rule | Test |
| --- | --- |
| Decide then apply, 873 units allocated, 1099.98 reserved, versions bumped, both audit events, refresh job enqueued | `tests/integration/test_approvals.py::test_supervisor_approves_then_applies_the_demo_recommendation` |
| Proposer cannot decide or apply (audited `DENIED`); a second supervisor can | `tests/security/test_approval_rules.py::test_the_proposer_can_neither_decide_nor_apply_their_own_proposal` |
| Viewer/planner 403, other factory 404 | `tests/security/test_approval_rules.py::test_only_supervisors_of_the_own_factory_may_decide` |
| Rejection needs a reason; a rejected proposal cannot be applied | `tests/integration/test_approvals.py::test_reject_needs_a_reason_and_a_rejected_proposal_cannot_be_applied` |
| Hash mismatch 409; expiry → `EXPIRED` + 409 | `tests/integration/test_approvals.py::test_a_changed_hash_and_an_expired_proposal_are_refused` |
| Reference fixture 6 (stale stock) → `SUPERSEDED` + 409 `STALE_INPUT`, no rows, proposer notified | `tests/integration/test_approvals.py::test_apply_rejects_stale_inputs_and_supersedes_the_recommendation` |
| Same key replays the original body; new key after `APPLIED` → 409 | `tests/integration/test_approvals.py::test_apply_is_idempotent_per_key` |
| Mid-apply capacity failure rolls everything back | `tests/integration/test_approvals.py::test_apply_rolls_back_entirely_when_one_allocation_no_longer_fits` |
| Two applies contend for the last 100 slot minutes (real overlap via `pg_sleep` under the lock, repeated 5x): exactly one `APPLIED`, capacity never exceeded | `tests/integration/test_apply_concurrency.py::test_two_applications_contend_for_the_last_slot_minutes` |
| Ungated concurrent applies never exceed capacity | `tests/integration/test_apply_concurrency.py::test_ungated_concurrent_applications_never_exceed_capacity` |
