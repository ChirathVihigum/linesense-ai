# Deterministic engineering calculation formulas

These are the **proposed demonstration formulas** from
`LINESENSE_IMPLEMENTATION_PLAN.md` §6, restated here with explicit units,
assumptions, and rounding rules, plus worked arithmetic for the plan's six
independent reference fixtures. They are computed by deterministic code in
`app/domain/`, never by a model (see
[ADR-0006](../adr/0006-llm-boundary-and-fixture-provider.md)). An
IE/domain owner must validate these against real production assumptions
before any non-demonstration use; every result they produce must be shown
next to its units and assumptions, never as a bare number.

Test implementation for these fixtures is planned for Task 4 (domain unit
tests); this document is the agreed reference before that code exists.

## Rounding rules (apply to all formulas below)

- Retain full decimal precision (`Decimal`, never `float`) through every
  intermediate calculation; round only for **display** or for a quantity
  that must be **purchased/allocated** in discrete units.
- Purchasable/allocatable quantities round **up** (ceiling) to the
  material's `pack_size` when one is defined, and up to the nearest whole
  unit otherwise — under-ordering/under-allocating is the unsafe direction.
- Percentages and rates for display round to 2 decimal places; minutes and
  seconds round to 2 decimal places; counts are always integers.
- A metric with an undefined denominator (division by zero) is never
  silently coerced to `0` — it is reported as `unknown`/`unbounded` with an
  explanation, per the plan's explicit prohibition on misleading zero
  values.

## Planning: standard minutes, capacity, utilization

```text
required_standard_minutes = remaining_units * SAM_minutes_per_unit
available_standard_minutes =
    sum(available_operator_minutes_per_shift * planned_efficiency_fraction)
utilization = allocated_standard_minutes / available_standard_minutes
```

- Units: `SAM_minutes_per_unit` is minutes/unit (`style_operations.sam_minutes`
  summed across a style's operations, or per-operation as needed);
  `available_operator_minutes_per_shift` is minutes (`line_capacity_slots
  .available_operator_minutes`); `planned_efficiency_fraction` is a
  dimensionless fraction in `(0, 1]` (`line_capacity_slots.planned_efficiency`).
- Assumptions: efficiency is applied exactly once (never compounded with a
  second efficiency factor elsewhere); breaks, absence, and
  setup/changeover are already reflected in `available_operator_minutes`
  before this formula runs; due-date cutoffs and line capability
  compatibility are checked separately, not folded into this arithmetic.
- Allocation policy: start with deterministic earliest-due-date allocation
  with an explicit, documented tie-break (e.g. order id) before considering
  a resource-limited optimizer.

**Fixture 1** — 1,000 units at SAM 12 minutes; 20 operators, 420 available
minutes each, 75% efficiency:

```text
required_standard_minutes = 1000 * 12 = 12,000 standard minutes
one-shift available_operator_minutes (raw) = 20 * 420 = 8,400 minutes
one-shift capacity (standard minutes) = 8,400 * 0.75 = 6,300 standard minutes
6,300 < 12,000  =>  cannot fit in one shift
```

Expected result: demand 12,000 standard minutes; one-shift capacity 6,300
standard minutes; the order cannot fit in a single shift (matches the
plan's stated expectation exactly).

## Materials: availability, demand, projected balance, reorder point

```text
available_now = accepted_on_hand - active_reservations
gross_demand = planned_units * BOM_quantity_per_unit * (1 + wastage_fraction)
projected_balance(t) = available_now + eligible_receipts_by(t) - new_demand_by(t)
reorder_point = expected_daily_consumption * lead_time_days + safety_stock
```

- Units: quantities are in the material's own unit (`materials.unit`:
  `m`/`kg`/`pcs`/`cone`); `wastage_fraction` is dimensionless in `[0, 1)`;
  `lead_time_days`/time terms are days.
- Assumptions: `active_reservations` are **already excluded** from
  `available_now`, so `new_demand_by(t)` must never subtract them a second
  time; `eligible_receipts_by(t)` counts only toward *projected* coverage,
  never toward current availability; BOM unit conversions use explicit
  approved conversion factors (no implicit unit coercion); zero or missing
  consumption history yields an unknown/unbounded coverage indicator with
  an explanation, never a computed zero-day value.

**Fixture 2** — 1,500 accepted meters, 400 already reserved; new demand
1,000 units at 1.2 meters/unit with 5% wastage; no incoming receipt:

```text
available_now = 1,500 - 400 = 1,100 meters
gross_demand  = 1,000 * 1.2 * (1 + 0.05) = 1,200 * 1.05 = 1,260 meters
shortage      = gross_demand - available_now = 1,260 - 1,100 = 160 meters
```

Expected result: available 1,100 meters; new demand 1,260 meters; shortage
160 meters (matches the plan's stated expectation exactly).

## IE: effective cycle time, bottleneck, throughput, line balance index

```text
effective_cycle_seconds(op) = representative_single_operator_seconds(op) /
                               parallel_operators(op)
bottleneck_effective_cycle_seconds = max(effective_cycle_seconds(op) for op in operations)
estimated_units_per_hour = 3600 / bottleneck_effective_cycle_seconds
line_balance_index_pct =
    sum(effective_cycle_seconds(op) for op in operations) /
    (operation_count * bottleneck_effective_cycle_seconds) * 100
```

- Units: seconds per unit for cycle times; `3600` is seconds/hour; the
  index is a dimensionless percentage.
- Assumptions (state alongside every result): steady flow, comparable
  operators, no unmodeled machine/material limit; this is the demo's
  sequential-operation model, not a universal industrial line-balance KPI —
  label it explicitly as "this model's balance index". Never mix seconds
  and minutes in the same calculation, and never apply an efficiency factor
  here as well as in the planning formula above (efficiency is a planning
  concept applied exactly once, upstream).

**Fixture 3** — three sequential effective operation cycles of 40, 60, and
50 seconds:

```text
bottleneck_effective_cycle_seconds = max(40, 60, 50) = 60 seconds
estimated_units_per_hour = 3600 / 60 = 60 units/hour
line_balance_index_pct = (40 + 60 + 50) / (3 * 60) * 100
                        = 150 / 180 * 100
                        = 83.333...%  ≈ 83.33%
```

Expected result: bottleneck 60 seconds; theoretical output 60 units/hour;
model-specific balance index ≈ 83.33% (matches the plan's stated
expectation exactly).

## Quality: defect rate, DHU, disposition

```text
defective_rate = defective_units / inspected_units
dhu_pct = (total_defects / inspected_units) * 100
```

- Units: `defective_units`, `inspected_units`, and `total_defects` are
  counts (integers); `defective_rate` is a dimensionless fraction; `dhu_pct`
  is defects per hundred units, expressed as a percentage-like figure
  (labelled "DHU", not "%", to avoid confusion with `defective_rate`).
- Assumptions: `inspected_units == 0` means the result is **unknown**, never
  "pass" and never a computed `0%`. Disposition (PASS/FAIL/
  INSUFFICIENT_SAMPLE) is decided only by comparing against a versioned,
  approved `quality_policy_versions` row (`rules.sample_size`,
  `max_defective_units`, `max_critical_defects`,
  `required_inspection_types`) — never by an LLM-invented threshold. Any
  simplified synthetic policy is labelled "demo policy, not certified" (see
  [`glossary.md`](glossary.md)).

**Fixture 4** — 100 inspected units, 7 defective units, 12 total defects:

```text
defective_rate = 7 / 100 = 0.07  =>  7%
dhu_pct         = (12 / 100) * 100 = 12 DHU
```

Expected result: defective rate 7%; DHU 12; disposition (PASS/FAIL/
INSUFFICIENT_SAMPLE) depends on which approved policy version applies —
the fixture intentionally does not presuppose a policy, matching the
plan's stated expectation.

## Concurrency and staleness fixtures (no arithmetic — behavioral)

**Fixture 5** — two simultaneous requests each reserving 80 units from 100
available: at most one full reservation may succeed; deterministic-order
row locking on `material_balances` (and, where relevant,
`line_capacity_slots`) must make the second request observe the reduced
balance and receive a conflict/shortage result — never allow both to
succeed and oversubscribe the material.

**Fixture 6** — a proposal computed against stock version 4 is applied
after stock has since moved to version 5: application must be rejected as
stale (`STALE_INPUT`, HTTP 409); an approval decision cannot bypass a fresh
reanalysis against current data. This is enforced by revalidating all
relevant input versions inside the same transaction that would otherwise
apply the change (`docs/architecture/backend-contracts.md` §5, `EXPIRED`/
`STALE_INPUT` error codes).
