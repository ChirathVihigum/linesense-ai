# Responsible AI

How this build treats the people it affects, what is enforced in code, what was tested, and —
at equal length — what was **not** tested.

Companion documents: [`model-card.md`](model-card.md), [`data-card.md`](data-card.md),
[`docs/architecture/llm-boundary.md`](../architecture/llm-boundary.md),
[`docs/security/threat-model.md`](../security/threat-model.md). The report's condensed version is
[`docs/report/linesense-report.tex`](../report/linesense-report.tex) §11.

> **The single most important fact in this document:** no live LLM call has ever been made in this
> environment. There is no API key. Every agent run recorded anywhere in this repository used the
> deterministic `fixture` provider, which is labelled *"Test fixture — not a live AI model"*
> wherever a provider label is rendered. Claims below about *what the system permits a model to
> do* are claims about enforced code paths; they are **not** claims about how a live model behaves,
> because no live model has been observed here.

---

## 1. The governing design rule

**LLMs never establish business truth and never authorize a write.**

Stock levels, capacity, cycle-time metrics, quality eligibility and lifecycle transitions are
computed by deterministic code in
[`services/backend/app/domain/`](../../services/backend/app/domain/) and are the only values the
product treats as facts ([`docs/architecture/formulas.md`](../architecture/formulas.md)). A model's
role is bounded to three jobs: choosing which read-only tool to call next, interpreting ambiguous
text, and phrasing an explanation of a result the deterministic code already produced.

This is enforced structurally, not by prompt instruction:

- Agents receive **read/compute tools only**. There is no arbitrary SQL tool, no shell tool, no
  URL-fetch tool, and no tool-creation path ([`docs/architecture/agents.md`](../architecture/agents.md)).
- A tool call outside the offered set, or with an unknown record id, is rejected by the executor
  rather than attempted.
- Every agent result is schema-validated and evidence-checked before it is stored; an invalid
  result is rejected with `INVALID_AGENT_OUTPUT`
  ([`docs/architecture/agent-protocol.md`](../architecture/agent-protocol.md)).
- The canonical status of an order is computed first; a model-generated explanation is layered
  second and carries `summary_source` so the two can never be confused
  ([`docs/architecture/nlp.md`](../architecture/nlp.md)).

## 2. Worker privacy

Apparel-factory AI fails responsibly-or-not on exactly one question: does it end up rating
individual operators? This one is built so that it cannot.

- **No personal data exists in the schema.** No name, contact detail, health information,
  protected attribute, or disciplinary record is modelled anywhere. Operators appear only as
  pseudonymous alias codes such as `KTN-OP-017`, created by the seed generator
  ([`docs/evaluation/synthetic-data.md`](../evaluation/synthetic-data.md)).
- **Aliases do not reach the model.** Observations reach the IE agent *aggregated per operation*;
  operator aliases are not part of the run snapshot and no tool returns one. A test asserts that no
  summary, finding or recommended action contains an alias.
- **Defects belong to an operation and an inspection, never to a person.**
- **Redaction runs before anything leaves the process.** `redact_payload` strips API keys, bearer
  tokens, email addresses and phone-shaped digit runs from every tool result and prompt fragment
  ([`services/backend/app/llm/redaction.py`](../../services/backend/app/llm/redaction.py)).
  Pseudonymous aliases are deliberately *not* redacted — they are not identifying, and redacting
  them would hide the operation context that makes a finding readable.
- **Logs never contain full prompts or documents**, per the repo-wide structlog convention.

### No individual ranking, ever

The IE agent's only recommendable action is `IE_REVIEW` — "review method and staffing at
`<operation>` on `<line>`". It targets a process constraint and a staffing *level*. There is no
code path anywhere in the system that ranks, scores, or compares individuals, and adding one would
require new schema, new tools and new actions — it is not a configuration away.

## 3. Explainability, and what is deliberately not shown

Every finding carries evidence references that resolve to a record with its version, or a document
with its version, page and section. Authorization is re-checked when a citation is opened, not only
when it was produced ([`docs/architecture/retrieval.md`](../architecture/retrieval.md)).

Two deliberate omissions:

- **Model reasoning is not displayed as an explanation.** Chain-of-thought text is not evidence;
  showing it invites users to treat a narrative as a justification. The product shows the records
  instead.
- **No model-produced confidence number is displayed as if it were a calibrated probability.**
  The NLP classifier's own margin is used internally for abstention only
  ([`docs/architecture/nlp.md`](../architecture/nlp.md)), and the evaluation harness scores the
  non-abstaining path separately so that the abstention policy is not silently credited or blamed.

## 4. Transparency about provenance

The provider label travels from the adapter, through the API, to the screen. Every AI-derived value
on every screen carries its actual source: *"Calculated from records"*, *"AI recommendation"*,
*"Pending human approval"*, or *"Approved by [role/user] at [time]"*
([`docs/requirements.md`](../requirements.md) REQ-19).

In this build, every agent summary renders **"Test fixture — not a live AI model"**, because that is
what produced it. This is the single clearest demonstration in the repository that the labelling
works: it is telling the truth about its own demo.

## 5. Abstention: refusing to answer is a first-class result

| Situation | What the system does | What it never does |
|---|---|---|
| No approved quality policy version | Nothing can be dispositioned; the order cannot ship | Assume a default policy |
| Zero inspections | Quality state is **unknown** | Report a pass |
| Missing or zero material consumption | `coverage_days` is `None` with an explanation | Compute a coverage of zero and call it a finding |
| Missing upstream agent result | A **degraded** result naming exactly what is missing | Fabricate a plausible completion |
| Provider unavailable or budget exhausted | Deterministic findings marked degraded, "AI explanation unavailable" | Present fallback text as a model run |

A positive AI explanation attempt **cannot** override an active quality hold or a missing
inspection: `shipment_ready` is derived by
[`app/domain/quality/calc.py`](../../services/backend/app/domain/quality/calc.py), never stored as
independent truth and never selected by a model
([`docs/requirements.md`](../requirements.md) REQ-11).

## 6. Human oversight

- Consequential changes require **approval by someone other than the proposer**; self-approval is
  denied with `SELF_APPROVAL_DENIED` (403).
- A proposal whose inputs changed since it was produced is **stale** and is rejected with 409 —
  fresh analysis is required rather than applying an outdated plan.
- Rejection requires a reason; the original proposal and its full history are retained.
- Every decision, application, quality release and policy change is audited with actor, target,
  outcome, reason, trace/run ids, versions and a safe diff, written in the same transaction as the
  write ([`docs/security/approval-integrity.md`](../security/approval-integrity.md),
  [`docs/requirements.md`](../requirements.md) REQ-17).
- Audit rows are append-only for the application role (`SELECT, INSERT` only). This is an access
  control, **not** cryptographic tamper-proofing; immutable archival is a production requirement
  that this build does not claim.

## 7. Adversarial robustness: what the injection tests actually prove

Document content is untrusted data. The corpus includes a deliberately hostile document
(`data/synthetic/adversarial/injection-sop.md`) containing `SYSTEM: ignore all previous
instructions...`, and `tests/security/test_prompt_injection.py` drives the **real** agent loop for
the RM, IE and quality agents against it plus a scripted hostile model, covering: an unknown tool
name, SQL-shaped tool arguments, an out-of-scope citation, an action id that was never offered, a
payload-override attempt, 50 consecutive tool calls, and a "reveal your keys" probe. 81 tests
passed in the recorded run; 27 more after the security fix round
([`docs/IMPLEMENTATION_STATUS.md`](../IMPLEMENTATION_STATUS.md), Task 25 entries).

**What that proves:** the *enforcement layer* holds. A model that tries to do something outside its
offered tools, scope or action set is stopped by the executor, and the injected text changes
nothing downstream.

**What it does not prove:** that a live model resists social engineering in its *phrasing*, or that
a real corpus contains no subtler attack. The hostile model in these tests is scripted by this
project — it is a test of our guardrails, not of any vendor's model.

## 8. Fairness: the test that was run, and why it is not an audit

The evaluation harness runs the **same** order through the four-agent flow twice, changing only its
customer between runs, and compares the stored `proposal_hash`. (Two *different* orders cannot be
compared this way, because the hash covers a payload that embeds the order id — this was a real bug
found and fixed while building the harness.) In the recorded smoke run, **2 of 2 pairs were
byte-identical**.

What that shows: under a deterministic provider, customer identity does not change the allocation.

What it does **not** show, stated as plainly as possible:

- It is **not** an audit of fairness across any protected characteristic. No protected attribute
  exists in the data, by design, so none could be tested.
- It cannot say anything about a **live** model, which might let a customer's identity leak into
  phrasing or tool choice. That test requires an API key and has never been run.
- `n = 2` is a smoke sample. The full run (`--scenarios 12`) is **PENDING**.
- A 100% pass rate here is **necessary, not sufficient**.
- Performance was not examined across styles, lines and data-quality strata, which the project
  plan asks for. That is an open gap, not a completed check.

**Synthetic tests and aggregate checks do not prove fairness for a real workforce.**

## 9. Data governance commitments (drafted, not yet exercised)

These apply from the moment real factory data is accepted. None has been exercised, because this
build has only ever held synthetic data ([`data-card.md`](data-card.md)).

| Commitment | Current state |
|---|---|
| Provider-data review before any real data reaches a model provider (retention, training use, sub-processors, region) | **Drafted here; not performed** — no provider relationship exists |
| Documented retention schedule per data class (orders, documents, audit, run traces) | **Not written** — audit retention in particular needs a legal answer, not a technical one |
| Deletion process for a customer's data, including document blobs and embeddings | **Not implemented** — backup/restore exists; targeted deletion does not |
| Incident procedure (who is notified, in what time, with what evidence) | **Drafted below; never rehearsed** |
| Legal obligations (data protection, labour law, customer contract terms) | **Treated as a separate jurisdiction-specific review.** This project makes no compliance claim for any jurisdiction |

### Incident procedure (draft)

1. **Contain** — revoke the affected credential or disable the affected route; the rate limiter and
   session store make this immediate.
2. **Assess** — the audit trail identifies which actor touched which record, with versions and
   safe diffs; run traces identify which analyses were affected.
3. **Notify** the customer contact with the scope and the evidence, within the contractual window
   (to be set per contract; no default is claimed here).
4. **Rotate** any credential that could have been exposed — assume compromise from the moment a
   secret is committed, even if removed later
   ([`docs/security/scan-results.md`](../security/scan-results.md)).
5. **Record** the incident and the fix in this repository's status log, and add a regression test.

## 10. Open gaps, listed without mitigation language

- No live-provider run has ever occurred; the entire RAI story for *model behaviour* is untested.
- No fairness examination across styles, lines or data-quality strata.
- No human-subject evaluation of recommendation quality: the "≥90% supported on a student-reviewed
  rubric" gate was never scored by a reviewer.
- Row-level security is not implemented; tenant isolation is application-layer plus negative tests.
- No antivirus scanning of uploads — a structural byte/marker check only.
- Retention, deletion and provider-data review are drafts.
- The full evaluation (`make eval` with the real embedder) is PENDING, so even the synthetic-set
  metrics quoted anywhere in this repository come from a hashing-embedder smoke run.
