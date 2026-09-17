# Synthetic seed data

**This is entirely synthetic data generated for development, testing, and
demonstration. It is not real factory data and must never be presented,
exported, or referenced as if it came from an actual apparel manufacturer.**

Every seeded name is deliberately, obviously fictional (`Customer 07
Apparel Co.`, `Demo Apparel Group`), every worker identifier is a
pseudonymous alias code (`KTN-OP-014`, never a person's name), and every
order/material/style/defect code comes from the fixed vocabulary in
`services/backend/app/seed/vocabulary.py` — the same vocabulary the
synthetic SOP corpus and the labelled NLP/IR evaluation datasets (Task 16)
are authored against, so order refs, material and line codes, operation
names, and defect codes match exactly across the whole demo.

## Generator

- `services/backend/app/seed/generator.py` — `async def seed_demo(session,
  *, anchor_date, issuer, rng_seed=20260917) -> SeedSummary`. Builds the
  full dataset in one pass and is **idempotent**: if the `demo-apparel`
  organization already exists it performs no writes and returns
  `SeedSummary(created=False, ...)` with the current row counts.
- `services/backend/app/seed/scenario.py` — the fixed `PO-DEMO-001`
  walkthrough scenario (constants only; `generator.py` writes them).
- `services/backend/app/seed/vocabulary.py` — the fixed vocabulary (order
  refs, materials, operation catalog, defect catalog, lines, styles).
  Task 6 does not redefine or reorder anything here.
- `services/backend/app/seed/identities.py` — the 10 demo identities
  (backend-contracts.md §9), re-exported from `generator.py` and imported
  by `tests/helpers/auth.py` and the dev OIDC provider's user list so the
  three never drift apart.
- `python -m app.seed [--anchor-date YYYY-MM-DD]` (`make seed`) — CLI
  entry point against `LS_DATABASE_URL`; refuses to run when
  `LS_ENVIRONMENT=production` (exit code 2); prints the `SeedSummary` as
  JSON; never deletes or truncates data.

### Determinism

Every random choice is drawn from a single `random.Random(rng_seed)`
instance seeded once at the top of `seed_demo` (default seed `20260917`,
matching the anchor date the demo was designed around). Nothing in the
generator reads the wall clock, `uuid.uuid4()` outside the ORM's own
primary-key defaults, or any other source of nondeterminism; every date is
computed relative to the caller-supplied `anchor_date`. Given the same
`anchor_date` and `rng_seed`, two independent runs against two empty
databases produce byte-identical order external refs, quantities, and due
dates (`tests/integration/test_seed.py
::test_seed_is_deterministic_across_fresh_databases` proves this with a
digest that deliberately excludes primary keys, foreign keys, and
`created_at`/`updated_at`, so it only captures values the generator is
required to reproduce).

## Sizes

| Table (or table group)                        | Count |
|------------------------------------------------|------:|
| Organizations / factories                       | 1 / 2 |
| Users / memberships / role assignments          | 10 / 10 / 10 |
| Customers                                       | 26 (`C01`..`C26`) |
| Styles / style operations                       | 12 / ~88 (6-10 per style) |
| Materials                                       | 20 (`M01`..`M20`) |
| BOM versions / lines                            | 16 / ~61 (12 active, 4 superseded, 3-5 lines each) |
| Lines (KTN `L1`..`L6`, BYG `B1`..`B3`)           | 9 |
| Capacity slots (9 lines × 30 days × 2 shifts)    | 540 |
| Orders (80 `PO-KTN-*`, 20 `PO-BYG-*`, 1 demo)    | 101 |
| Allocations                                     | ~170 (only PLANNED/IN_PRODUCTION/PRODUCTION_COMPLETE orders) |
| Material lots / stock movements / balances       | 20 / ~300 / 20 |
| Reservations / expected receipts                | ~55 / 6 |
| Operator aliases (25 per KTN line)               | 150 |
| Skill records / operation staffing / cycle observations / line measurements | ~300 / ~144 / ~720 / ~108 |
| Quality policy versions / inspections / defects / holds / releases | 1 / ~40 / ~32 / 1 / ~18 |

Exact counts vary slightly run-to-run **only** based on `rng_seed` (they
are always identical for a fixed seed); a live `make seed` run prints the
authoritative `SeedSummary.counts` for the dataset it just built or found.

## The `PO-DEMO-001` demo scenario

`app/seed/scenario.py` fixes one order every walkthrough, screenshot, and
demo anchors on, at factory KTN:

- Order `PO-DEMO-001`, customer `C07`, style `ST-03` (all 7 operations use
  skills present on every KTN line, `L1`-`L6`, including `L6`, which
  otherwise lacks the `BH` skill), quantity 1000, due `anchor + 5`,
  priority 2, `production_state=VALIDATED`, `material_state=UNKNOWN`,
  `quality_state=NOT_INSPECTED`.
- BOM line: material `M01` (Cotton Pique Fabric, `m`), 1.2 m/unit,
  5% wastage.
- `M01` balance at factory KTN: `on_hand_accepted=1500`, `reserved=400`
  (an `ACTIVE` reservation against a different KTN order — never the demo
  order itself), backed by an exact ledger (one 2760 m receipt lot minus
  14 daily 90 m `ISSUE` movements, so `average_daily_consumption` over the
  last 14 days is exactly 90 m/day and `on_hand_accepted` is exactly
  1500 m by construction — not by chance).
- One `OPEN` expected receipt: 500 m of `M01` on `anchor + 8`, strictly
  **after** the order's due date, so it cannot rescue the shortage before
  then.
- Capacity: `L1`-`L6`'s combined remaining standard minutes before the due
  date are, by construction, at least 5× the order's required standard
  minutes (`1000 × sum(ST-03 SAM) = 1000 × 6.70 = 6700` minutes) —
  comfortably sufficient.
- IE: cycle observations and operation staffing for `ST-03` on line `L2`
  are fixed (not random) so operation `OP-04` (sleeve set) is
  deterministically the bottleneck: representative (median) cycle 120s
  with 2 parallel operators gives an effective cycle of exactly 60s, the
  highest of the seven operations.

These numbers are chosen so the deterministic domain functions in
`app.domain.inventory.calc` and `app.domain.ie.calc` reproduce the exact
reference figures below (see `docs/architecture/formulas.md` for the
formulas themselves, and `tests/integration/test_seed.py
::test_demo_order_facts` for the assertions):

| Function | Inputs | Result |
|---|---|---|
| `available_now(on_hand, reserved)` | `1500, 400` | **1100 m** |
| `gross_demand(units, qty_per_unit, wastage)` | `1000, 1.2, 0.05` | **1260 m** |
| `shortage(available, demand)` | `1100, 1260` | **160 m** |
| `coverable_units(available, qty_per_unit, wastage)` | `1100, 1.2, 0.05` | **873 units** |
| `effective_cycle_seconds` (bottleneck, `OP-04` on `L2`) | median 120s, 2 operators | **60s** (58-62s tolerance) |

## Not real factory data

Nothing in this dataset should ever be mistaken for a real customer,
factory, worker, or order. There is no PII: worker identity is limited to
pseudonymous alias codes (`operator_aliases.alias_code`, e.g.
`KTN-OP-014`), never a name or other personal attribute
(`tests/integration/test_seed.py::test_no_personal_names_in_seeded_data`
checks every seeded text column against a personal-name heuristic list).
Every `orders.source` value is `synthetic_seed`, never `manual` or
`csv_import`, so downstream code and audit logs can always tell seeded
data apart from anything a real user entered.
