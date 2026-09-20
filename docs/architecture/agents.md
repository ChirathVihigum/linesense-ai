# The four agents

Every agent is the same shape (see
[`app/agents/base.py`](../../services/backend/app/agents/base.py)): a
deterministic `assess()` over the run snapshot produces the findings, metrics,
candidate actions and evidence; the model may then only write a summary, add
notes, pick one of the offered actions and cite the offered evidence. Nothing
a model returns becomes a number, a state or a verdict.

Shared limits: ≤4 investigative tool calls per invocation, ≤1 repair turn, ≤12
model calls per **run** (reserved in the database before every call, so an
exhausted budget degrades the agent instead of overspending), 120-second run
deadline, ≤2 retries after the first attempt. Tools read the run snapshot
only — never live tables, never SQL, never the network.

---

## `rm` — raw material readiness

| | |
| --- | --- |
| **Goal** | Whether the order's materials are available, which material binds, and what to replenish. |
| **Task types** | `assess_material_readiness` (round 0), `validate_plan_materials` (round 1) |
| **Inputs** | The snapshot's BOM, balances, open receipts and 14-day issues; round 1 also the selected plan. |
| **Domain** | `app.domain.inventory.calc` (`gross_demand`, `available_now`, `shortage`, `projected_balance`, `coverable_units`, `reorder_point`, `material_state`) |
| **Tools** | `get_material_position`, `get_expected_receipts`, `get_consumption_history`, `get_bom_demand` |
| **Findings** | `MATERIAL_SHORTAGE`, `MATERIAL_AT_RISK`, `BELOW_REORDER_POINT`, `LEAD_TIME_EXCEEDED`, `CONSUMPTION_UNKNOWN`, `MATERIAL_BALANCE_MISSING`, `UNIT_CONVERSION_MISSING`, `PLAN_MATERIAL_SHORT`, `PLAN_MATERIAL_COVERED`, `PLAN_INPUT_MISSING` |
| **Metrics** | `gross_demand:<code>`, `available_now:<code>`, `shortage:<code>`, `coverage_days:<code>`, `coverable_units` |
| **Actions** | `REPLENISHMENT_SUGGESTION` (never a purchase order), `RESERVATION` (round 1; a proposal a human approves) |
| **Human boundary** | Suggests; never buys, reserves or approves. |
| **Prompt** | `app/agents/prompts/rm.md`, version `rm-v1` |

## `ie` — industrial engineering

| | |
| --- | --- |
| **Goal** | What limits each compatible line's output on this style, and where a method/staffing review would help. |
| **Task type** | `assess_line_capability` (round 0) |
| **Inputs** | `snapshot.ie` — one `line_style_analysis` per compatible line with observations (per-operation sample count, median and effective cycle seconds, staffing level, balance, SAM-based and measured throughput). |
| **Domain** | `app.domain.ie.calc` (`line_balance`; the stored balance is reused verbatim when every operation was measured). |
| **Tools** | `get_line_analysis`, `get_operation_statistics` (count and median only), `compare_observed_vs_standard` |
| **Findings** | `BOTTLENECK_OPERATION` (warning), `LINE_CAPACITY_BELOW_PLAN` (warning, when modelled units/hour < 0.9 × SAM units/hour), `INSUFFICIENT_SAMPLES` (info), `NO_OBSERVATIONS` (info) |
| **Metrics** | `units_per_hour:<line>`, `balance_index:<line>`, `bottleneck_seconds:<line>`, `sam_units_per_hour:<line>` |
| **Actions** | `IE_REVIEW` — "Review method and staffing at `<operation>` on `<line>`", a suggestion for a supervisor. |
| **Human boundary** | **Never judges, names, ranks or compares individual workers.** Observations reach the agent aggregated per operation; operator aliases are not in the snapshot, no tool returns them, and no summary, finding or action may contain one. A review targets an operation's method, layout and staffing *level*. |
| **Limits** | Fewer than 3 recent observations means an operation has no representative cycle — it is excluded and reported, never estimated. The balance index is this model's index, not a universal KPI. The snapshot keeps the median and the sample count, not the observed range. |
| **Prompt** | `app/agents/prompts/ie.md`, version `ie-v1` |

## `quality` — quality status

| | |
| --- | --- |
| **Goal** | What has been inspected, against which policy version, what is holding the order, and what the calculated shipment gates say. |
| **Task type** | `assess_quality_status` (round 0) |
| **Inputs** | `snapshot.quality` — the active policy version, inspections with their defect counts, active holds, releases, the calculated shipment eligibility and the order's quality state. |
| **Domain** | `app.domain.quality.calc` (`defective_rate`, `defects_per_hundred_units`); eligibility comes from `shipment_eligibility` as computed when the snapshot was built. |
| **Tools** | `get_inspections`, `get_policy_rules`, `get_defect_breakdown` (by defect code or operation) |
| **Findings** | `POLICY_MISSING` (critical), `ACTIVE_HOLD` (critical), `INSPECTION_FAILED` (critical), `DEMO_POLICY` (warning), `SHIPMENT_INELIGIBLE` (warning), `SHIPMENT_ELIGIBLE` (info), `NOT_INSPECTED` (info — "Quality pending — no inspection recorded; this is not a pass.") |
| **Metrics** | `defective_rate:<inspection>`, `dhu:<inspection>` (null when nothing was inspected), `defect_count:<code>`, `shipment_eligible` |
| **Actions** | `QUALITY_HOLD_REVIEW` — a suggestion; releasing a hold needs the release command, a role and an explicit reason. |
| **Human boundary** | The agent never decides shipment readiness and can never release a hold. A defect belongs to an operation and an inspection, never to a person. |
| **Limits** | No approved policy → nothing can be dispositioned and the order cannot ship. A demo policy's thresholds are illustrative, not a customer AQL. |
| **Prompt** | `app/agents/prompts/quality.md`, version `quality-v1` |

## `planning` — production planning

| | |
| --- | --- |
| **Goal** | How to allocate the order's remaining units to capacity slots before the due date, honouring the material limit. |
| **Task types** | `propose_allocation` (round 0), `revise_allocation` (round 1) |
| **Inputs** | The snapshot's compatible lines and capacity slots, the RM result's `coverable_units`, the IE result's `LINE_CAPACITY_BELOW_PLAN` lines. |
| **Domain** | `app.domain.planning.calc.plan_earliest_slots` |
| **Tools** | `get_dependency_findings`, `list_compatible_lines`, `get_remaining_capacity`, `simulate_allocation` |
| **Findings** | `NO_COMPATIBLE_LINE`, `UNSCHEDULED_QUANTITY`, `LINE_OVERCOMMITTED`, `IE_BOTTLENECK_RISK`, `MATERIAL_CONSTRAINT_KNOWN`, `REVISED_FOR_MATERIAL`, `DEPENDENCY_MISSING` |
| **Metrics** | `required_standard_minutes`, `allocated_units`, `unscheduled_units` |
| **Actions** | `ALLOCATION` (one per option; the model may reorder them, never rewrite one) |
| **Human boundary** | Proposes; the allocation only takes effect when a supervisor approves the recommendation. |
| **Prompt** | `app/agents/prompts/planning.md`, version `planning-v1` |

---

## Degradation

| Situation | Result |
| --- | --- |
| `LS_LLM_PROVIDER=disabled` | `DEGRADED`, deterministic summary, warning "AI explanation unavailable", `degraded_reason = LLM_DISABLED` |
| Provider outage | Three attempts, then the deterministic assessment, `degraded_reason = PROVIDER_UNAVAILABLE` |
| Run budget spent | The deterministic assessment, `degraded_reason = BUDGET_EXCEEDED` |
| Model refusal / invalid output | The deterministic assessment, `degraded_reason = LLM_REFUSAL` / `INVALID_AGENT_OUTPUT` |

In every case the findings, metrics, actions and evidence are unchanged — only
the explanation is missing — and the run still finalizes into a `run.report`
whose `degraded_reasons` name what was lost.
