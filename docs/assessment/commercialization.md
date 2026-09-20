# Commercialization proposal

**Status of this document.** This is a *proposal*, written from the plan in
[`LINESENSE_IMPLEMENTATION_PLAN.md`](../../LINESENSE_IMPLEMENTATION_PLAN.md) §16 and from what
this repository actually implements. Every price, cost and benefit figure below is a
**hypothesis**. No customer has been interviewed, no pilot has been run, no invoice has been
issued, and no market research was performed for this assignment. Nothing here may be quoted as
a validated market rate or as evidence of demand.

Summary form of this document appears in the project report
([`docs/report/linesense-report.tex`](../report/linesense-report.tex) §14).

---

## 1. Problem and buyer

Apparel factories in the target segment run production on a patchwork: an ERP or spreadsheet for
orders, a stores ledger for material, a separate IE time-study workbook, and a quality register.
Answering "can PO-KTN-0072 ship on time, and if not, why" means a person walking between four
systems and three people, and the answer is stale by the time it is assembled.

**Primary buyer:** production manager or factory operations head — the person accountable for
on-time delivery who currently pays for that reconciliation in supervisor hours.

**Users:** planner, supervisor, storekeeper, IE engineer, quality manager, factory manager
(see [`docs/security/role-matrix.md`](../security/role-matrix.md) for the implemented permission
set).

**Economic buyer vs. user is not the same person**, which matters for pricing: the user feels the
time saved, the buyer sees the delivery penalty avoided. Both must be measured in a pilot before
any claim is made.

## 2. Positioning

LineSense AI is a **decision-support layer with traceable recommendations** that sits on top of
existing records.

It is explicitly **not**:

- an ERP or MES replacement — it has no live connector to either
  ([`docs/requirements.md`](../requirements.md) §7);
- an autonomous scheduler — every consequential change requires human approval by someone other
  than the proposer ([`docs/security/approval-integrity.md`](../security/approval-integrity.md));
- a workforce-monitoring product — no individual is ranked, scored or compared anywhere in the
  system ([`responsible-ai.md`](responsible-ai.md)).

The differentiator a buyer can verify in a demo is **evidence**: every finding resolves to a
record with its version or a document with its version, page and section, and the citation is
re-authorized when opened. A recommendation nobody can trace back to a record does not get
approved in a factory, and that is the reason most "AI for manufacturing" pilots stall.

## 3. Pricing hypotheses

These tiers are reproduced from the project specification. **They are hypotheses, not current
vendor prices or validated market rates**, and they were not benchmarked against any competitor's
published pricing.

| Tier | Proposed price | Proposed scope |
|---|---|---|
| Pilot | USD 99 / factory / month | Up to 5 lines, 10 users, 500 analysis runs/month, CSV imports |
| Growth | USD 249 / factory / month | Up to 20 lines, 30 users, 2,000 runs/month, richer reporting |
| Enterprise | Quoted | Multiple factories, connector work, isolated deployment, negotiated support |

Rules this project commits to before any tier is offered:

1. **Every tier is described against implemented features.** Anything not in the build is labelled
   roadmap in the offer itself. As of this commit that means: no ERP/MES connector, no email/SMS
   notification, no OCR of scanned documents, no row-level database isolation
   ([`completion-matrix.md`](completion-matrix.md)).
2. **A run's budget is defined in the contract**, because it is already enforced in code: ≤4 tool
   calls per agent invocation, ≤12 model calls per run, ≤1 replan, ≤2 retries, 120-second run
   deadline, and a per-run token budget
   ([`docs/architecture/llm-boundary.md`](../architecture/llm-boundary.md)).
3. **Document storage allowance, retention and overage behaviour are stated before sale**, not
   discovered afterwards.
4. **Unlimited AI use is never promised.** The product's cost is driven by model calls; a flat
   "unlimited" tier would be a loss-making promise.
5. Onboarding, data mapping and connector work are **quoted separately** after an effort estimate.
   They are labour, not software, and bundling them into a monthly price hides the real cost.

## 4. Cost model

Monthly service delivery cost per factory:

```
allocated compute (API + worker)
+ managed PostgreSQL (with pgvector)
+ object/document storage and encrypted backups
+ monitoring and log retention
+ LLM model usage + embedding computation
+ support labour (the largest line item at small scale)
+ identity provider costs, if a hosted IdP is used
```

Gross margin = `(revenue − service delivery cost) / revenue`, computed per factory, not blended.

### Why this build cannot quote a per-run model cost

The token counts recorded anywhere in this repository come from the **fixture** provider, which
computes them as `len(json.dumps(...)) // 4`
([`services/backend/app/llm/fixture_client.py`](../../services/backend/app/llm/fixture_client.py)).
That is a cheap deterministic stand-in, **not a tokenizer and not provider usage accounting**.
No live-provider run has ever been executed in this environment (no API key), so no real token
figure exists.

The *method* is nonetheless implemented and usable the moment a key exists: `analysis_runs` stores
`model_calls_used` and `tokens_used` per run
([`docs/architecture/llm-boundary.md`](../architecture/llm-boundary.md) "Run budgets"), so
per-run cost is `tokens_used × the provider's published per-token prices + embedding cost`.
**Any per-run cost figure quoted to a customer must come from live-provider runs with prices
verified on the day of quoting.**

Embedding cost is bounded differently and is the cheaper half: embeddings are computed locally by
`fastembed` (`BAAI/bge-small-en-v1.5`, 384 dimensions) and cached by content hash plus model
version, so a document is embedded once per version, not once per query
([`docs/architecture/retrieval.md`](../architecture/retrieval.md)).

## 5. Deployment options

| Option | Who runs it | When it fits |
|---|---|---|
| Single-tenant SaaS | Vendor | Factory has no IT operations capacity; data leaving site is acceptable |
| Customer-hosted Compose | Customer, on their own VM | Factory will not let operational data leave the site |

Both use the same artefacts (`infra/`, one backend image with API and worker entrypoints,
[`docs/operations/deployment.md`](../operations/deployment.md)). **Neither has been built or run:**
the deployment artefacts were validated statically only (`make infra-check`), because no Docker
host was available in this environment
([`docs/IMPLEMENTATION_STATUS.md`](../IMPLEMENTATION_STATUS.md), Task 26 entry). A customer-hosted
offer cannot be sold until a real build-and-run has happened.

Single-tenant is the honest default at this maturity: row-level security is **not** implemented, so
multi-tenant isolation currently rests on application-layer scope checks plus negative tests
([`docs/security/threat-model.md`](../security/threat-model.md)). Putting two customers in one
database before RLS exists would be selling an isolation property the build does not have.

## 6. Pilot plan and what would be measured

Sell a **paid pilot**, not a free trial: a free trial produces no decision-maker attention.

Progression, one gate at a time:

1. **Synthetic demo** — the seeded dataset, on the vendor's environment. No customer data.
2. **Read-only pilot** — customer's real orders imported by CSV; agents analyse; nobody applies
   anything. Measures whether the findings are *right* with zero risk.
3. **Supervised actions** — approvals enabled; every application is human-approved and audited.
4. **Integrations** — only after steps 2–3 succeed, and quoted separately.

Before/after measures, taken for the same lines and the same style mix:

| Measure | How it is taken |
|---|---|
| Time to identify the blocker on a late order | Stopwatch, supervisor-reported, n ≥ 20 orders each side |
| Time spent preparing an order-status report | Same |
| Stale or missing data rate | Proportion of analyses that abstain for missing inputs |
| Proposal acceptance rate | `approvals` table: approved ÷ (approved + rejected) |
| Supervisor-rated usefulness | 1–5 rating per analysis, recorded in-app |
| On-time delivery rate | Customer's own delivery record, as the lagging outcome |

**No productivity percentage will be quoted until before/after evidence exists.** A pilot that
cannot show a difference in the first four measures is a failed pilot and should be reported as
one.

## 7. Competition and honest disadvantages

The realistic alternatives a buyer compares against are: the existing spreadsheet process (free,
already understood); their ERP vendor's planning module (already paid for, poor at evidence);
and a general-purpose AI assistant pointed at exported files (cheap, ungrounded, no audit trail,
no approval workflow).

Where this build is genuinely behind:

- **No connector.** CSV import only. Every competitor bundled with an ERP starts with the data.
- **Single-host deployment is not highly available** — documented downtime and recovery limits,
  not an enterprise SLA ([`docs/operations/backup-restore.md`](../operations/backup-restore.md)).
- **Unmeasured performance.** The load test (`make perf`) has never been run
  ([`docs/evaluation/performance.md`](../evaluation/performance.md)), so no latency figure can be
  put in front of a buyer.
- **Unmeasured retrieval quality at the real embedder.** Only a hashing-embedder smoke run exists
  ([`docs/evaluation/results/README.md`](../evaluation/results/README.md)).
- **English only**, and the synthetic corpus is short and clean; real factory notes are neither.

## 8. Risks to the commercial case

| Risk | Why it matters | Mitigation already in the build |
|---|---|---|
| Model cost per run turns out higher than the tier price supports | Kills margin at the Pilot tier | Hard per-run budgets already enforced; tiers priced per run, not unlimited |
| Buyer expects autonomy, gets recommendations | Perceived as "it doesn't do anything" | Positioning and demo script lead with the approval workflow, not with automation |
| Data quality at the customer is worse than the synthetic set | Abstentions dominate; product looks useless | Abstention is explicit and explains what is missing — that itself is a saleable finding |
| A wrong recommendation is applied | Trust loss, possible material loss | Human approval, self-approval denial, staleness rejection, full audit trail |
| Provider dependency (price, availability, terms) | Single supplier for the LLM path | One `LLMClient` interface; provider is swappable; deterministic findings survive a provider outage in DEGRADED form |
| Real factory data brings legal obligations | Jurisdiction-specific | Treated as a separate review, never as a compliance claim ([`responsible-ai.md`](responsible-ai.md)) |

## 9. What would have to be true for this to be a business

Stated plainly, so it can be falsified rather than assumed:

1. A production manager will pay to avoid the reconciliation cost — **untested**.
2. The read-only pilot's findings are right often enough to be trusted — **untested against real
   data**; only synthetic-set results exist.
3. Per-run model cost is well under the per-run revenue implied by the tiers — **unmeasurable in
   this build** (fixture token counts only).
4. Factories will accept a CSV-import workflow long enough to prove value before a connector
   exists — **untested**.

None of the four has been validated. This document is a plan for finding out, not a claim that the
answers are yes.
