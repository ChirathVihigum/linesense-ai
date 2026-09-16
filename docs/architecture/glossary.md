# Glossary

Domain and technical terms used across `LINESENSE_IMPLEMENTATION_PLAN.md`,
`docs/architecture/backend-contracts.md`, and the rest of this repository.
See also [`formulas.md`](formulas.md) for the arithmetic behind SAM, DHU,
and line balance index.

**SAM (Standard Allowed Minutes)** — the standard time, in minutes,
allowed for one operator to complete one unit of a given operation under
defined working conditions (`style_operations.sam_minutes`). Used to
convert order quantity into `required_standard_minutes` for planning (see
[`formulas.md`](formulas.md) § Planning). SAM values in this system are
demonstration inputs supplied with the synthetic dataset, not measured
against a real factory's operators.

**DHU (Defects per Hundred Units)** — `(total_defects / inspected_units) *
100`, i.e. how many individual defects were found per 100 units inspected.
Distinct from **defective rate** (`defective_units / inspected_units`),
which counts *units with at least one defect*, not the number of defects.
A single unit can carry several defects, so DHU is always reported
separately from defective rate, never merged into one number (see
[`formulas.md`](formulas.md) § Quality).

**AQL (Acceptable Quality Limit)** — an industry sampling-inspection
standard (e.g. ISO 2859) defining sample size and acceptance/rejection
numbers for a given lot size and quality level. **This system's
`quality_policy_versions` rows are demonstration policies only.** Any
policy shipped with the synthetic dataset is labelled `is_demo = true` and
described as a **"demo policy, not certified"** — it approximates the
shape of an AQL-style sampling policy (sample size, severity categories,
acceptance numbers) for teaching/demonstration purposes and must never be
presented, in the report, video, or UI, as a certified AQL/ISO-compliant
quality standard.

**Line balance index** — this system's demonstration metric,
`sum(effective_operation_cycles) / (operation_count * bottleneck_effective_cycle)
* 100`, measuring how evenly cycle time is distributed across a
sequential-operation line relative to its bottleneck. Always labelled as
*this model's* index (assumptions: steady flow, comparable operators, no
unmodeled machine/material limit) — not presented as a universal
industrial-engineering KPI, since real line balancing accounts for factors
(work-in-process buffers, machine changeovers, multi-skill routing) this
demo model does not.

**Supermarket** — in lean-manufacturing/apparel-IE usage, a controlled
buffer of materials or components staged near the point of use, replenished
on a pull signal rather than pushed on a fixed schedule. In this codebase,
"RM/supermarket agent" refers to the raw-materials domain agent
responsible for material readiness, coverage, and replenishment proposals
(`app/agents/rm/`) — it reasons about stock and reservations, not a
physical staging area the system itself operates.

**BOM (Bill of Materials)** — the versioned list of materials and
per-unit quantities required to produce one unit of a style
(`bom_versions`, `bom_lines`). Exactly one `bom_versions` row is active per
style at a time (partial unique index); `bom_lines.wastage_fraction`
accounts for expected material loss and is applied once, in the
`gross_demand` formula (see [`formulas.md`](formulas.md) § Materials).

**Lot** — a received, trackable quantity of a material from a single
receipt event, carrying its own quarantine/acceptance status
(`material_lots.status`: `QUARANTINE`/`ACCEPTED`/`REJECTED`). Only
`ACCEPTED` lots contribute to `on_hand_accepted`; a lot's status is never
inferred from its movements, it is the authoritative gate.

**Reservation** — a claim against a material's `on_hand_accepted` balance
on behalf of a specific order (`reservations`, status
`ACTIVE`/`RELEASED`/`CONSUMED`), reducing `available_now` for other orders
without yet reducing the underlying ledger balance. Reservations prevent
two orders from being planned against the same physical stock; they are
released back to availability if the order they were made for is cancelled
or the reservation is otherwise no longer needed, and consumed when the
material is actually issued.

**Allocation** — an assignment of an order's `standard_minutes`/`units` to
a specific line/date/shift capacity slot (`allocations`, referencing
`line_capacity_slots`), representing planned (not yet necessarily executed)
production capacity usage. Distinct from a *reservation* (which claims
material, not capacity).

**Standard minutes** — the unit of planning capacity throughout this
system: `SAM_minutes_per_unit * units`, comparable across operations and
lines because it is normalized by the SAM standard rather than raw wall-
clock time. `line_capacity_slots.available_operator_minutes *
planned_efficiency` expresses a slot's capacity in the same standard-
minutes terms so `utilization` is a like-for-like ratio.

**Shift slot** — one row of `line_capacity_slots`: a specific
`(line_id, slot_date, shift_code)` combination (`shift_code` is `A` or `B`
in this system) carrying its own available operator-minutes and planned
efficiency for that shift. Allocations are made *into* shift slots, never
directly against a line's aggregate capacity, so per-shift oversubscription
can be checked and prevented at the row level
(`allocated_standard_minutes <= available_operator_minutes *
planned_efficiency`, enforced by a database check constraint).
