# Model card

This build uses **four** model-shaped components. Three of them are small, local and deterministic;
one is a hosted LLM that **has never been called in this environment**. Each gets its own section,
because lumping them together is how a system ends up claiming a capability it does not have.

Scope: this card describes the components *as configured in this repository*. It is not a card for
any vendor's model — for the hosted LLM, consult the provider's own model documentation.

| # | Component | Role | Ever run here? |
|---|---|---|---|
| 1 | Hosted LLM (`claude-opus-5` by default) | Tool selection, text interpretation, explanation phrasing | **No — no API key exists** |
| 2 | `FixtureLLMClient` (`fixture-scripted-v1`) | Deterministic stand-in for #1 in dev/test/demo | Yes, exclusively |
| 3 | `BAAI/bge-small-en-v1.5` via `fastembed` | Document/query embeddings for vector retrieval | Downloaded once; not used in any committed evaluation |
| 4 | TF-IDF + logistic regression; rule-based `spacy.blank("en")` matcher | Note classification; entity extraction | Yes |

---

## 1. Hosted LLM (the live provider path)

**Identifier.** `LS_ANTHROPIC_MODEL`, default `claude-opus-5`. Selected by `LS_LLM_PROVIDER=anthropic`.
Adapter: [`services/backend/app/llm/anthropic_client.py`](../../services/backend/app/llm/anthropic_client.py),
on the `anthropic` Python SDK (`>=1.6.0`, `services/backend/pyproject.toml`).

**Configuration actually sent.** For `claude-opus-5` the adapter calls the beta messages endpoint
with `betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`,
`thinking={"type": "adaptive"}` and `output_config={"effort": "medium"}`. For any other model id it
calls the plain messages endpoint with only `model`/`max_tokens`/`system`/`messages`/`tools`,
because other models may not accept adaptive thinking or that beta
([`docs/architecture/llm-boundary.md`](../architecture/llm-boundary.md)).

**Intended use.** Three bounded jobs only: choose the next read-only tool, interpret ambiguous free
text, and phrase an explanation of a result deterministic code already computed.

**Out-of-scope use.** Establishing any business fact (stock, capacity, cycle time, quality
eligibility, lifecycle state); authorizing any write; ranking or assessing any individual worker;
selecting `shipment_ready`; producing a number that is displayed as a calibrated probability.
These are not discouraged — they are structurally unavailable, because the model is never given a
tool that could do them.

**Input sent to the provider.** Exactly `system`, `messages` and `tools`. Nothing about the
organization, the request, or the run is attached. Every tool result and context fragment passes
through `redact_payload` (API keys, bearer tokens, emails, phone-shaped digit runs) before being
placed in a message. Full prompts are never logged.

**Limits enforced outside the model.** ≤4 tool calls per agent invocation, ≤12 model calls per run,
≤1 replan, ≤2 retries, 120-second run deadline (`LS_RUN_DEADLINE_SECONDS`), plus a per-run token
budget. Reservation is atomic against the `analysis_runs` row, so exactly `model_calls_limit` calls
succeed under concurrency, never more.

**Failure handling.** `stop_reason="refusal"` raises `LLMRefusalError`; `"max_tokens"` raises
`LLMInvalidResponseError("truncated")`. SDK exceptions map to a retryable/non-retryable LineSense
error hierarchy. On provider failure or budget exhaustion the run returns **deterministic findings
marked degraded** with "AI explanation unavailable" — never fallback text presented as a model run.

**Evaluation.** *None.* No live call has been made in this environment. The adapter is unit-tested
against stub SDK objects only (tests assert, among other things, that an API key never appears in a
raised exception's message). **No claim about this model's accuracy, latency, cost or behaviour in
this system is supported by evidence from this repository.**

**Known risks not mitigated here.** Provider availability and price changes; variation between runs
(the plan calls for repeating a subset of live runs to measure variance — never done); the
possibility that a live model phrases an explanation more confidently than the evidence supports,
which only human review of live output could detect.

## 2. `FixtureLLMClient` — the labelled test fixture

**Identifier.** `provider="fixture"`, `model="fixture-scripted-v1"`.
[`services/backend/app/llm/fixture_client.py`](../../services/backend/app/llm/fixture_client.py).

**What it is.** A pure function of the request, with no network access. It calls investigative tools
until `min(2, number of investigative tools offered)` tool results exist, then calls
`submit_assessment`, choosing the lowest-`rank` candidate action and citing every available
evidence id. Every summary is prefixed `"[Fixture] "`. The same `(system, messages, tools)` always
produces the same response — asserted by
`tests/unit/test_fixture_client.py::test_deterministic_same_input_same_output`.

**Labelling.** Every response carries `provider="fixture"`, and the API surfaces
`{provider, model, is_fixture, label}` so the UI renders **"Test fixture — not a live AI model"**
wherever a provider label appears. Production startup **refuses to boot** with
`LS_LLM_PROVIDER=fixture` (`app/settings.py` production validator).

**Intended use.** Repeatable CI, local development without a key, and the demo scenario.

**Out-of-scope use.** Any statement about AI capability, latency, token cost or quality. Its token
counts are `len(json.dumps(...)) // 4` — a deterministic stand-in, **not a tokenizer and not
provider usage accounting**.

**Evaluation.** Used as the provider for every agent, security and fairness figure recorded in this
repository. Those figures measure *the system's enforcement and orchestration*, not model quality.

## 3. Embedding model — `BAAI/bge-small-en-v1.5`

**Identifier.** `LS_EMBEDDING_MODEL`, default `BAAI/bge-small-en-v1.5`, 384 dimensions, run locally
through `fastembed` (ONNX) and cached under `.local/models`. Selected by `LS_EMBEDDER=fastembed`.

**Role.** Embeds document chunks and queries for the vector half of hybrid retrieval. Results are
fused with a lexical `tsvector`/GIN search by reciprocal-rank fusion (`rrf_k = 60`, ties broken by
chunk id), *after* organization/factory/ACL/active-version filtering
([`docs/architecture/retrieval.md`](../architecture/retrieval.md)).

**Stand-in.** `LS_EMBEDDER=hashing` selects a dependency-free, semantics-free feature-hashing
embedder (SHA-256 of word unigrams and bigrams into 384 signed buckets, L2-normalized). The test
session pins `hashing`, so **no committed test result reflects the real embedder**.

**Evaluation.** The only recorded retrieval number, `Recall@5 = 0.711` against a 0.85 target, comes
from a `--embedder hashing --scenarios 2` smoke run. That number **is not a measurement of this
model** — it is a measurement of the hashing stand-in, and it is expected to be low. Two of the 45
test questions are additionally out-of-scope BYG documents by design. The real run (`make eval`) is
**PENDING** ([`docs/evaluation/results/README.md`](../evaluation/results/README.md)).

**Limitations.** English only. No approximate-nearest-neighbour index — exact scan over matching
rows, which is fine at 30 documents and is not a scale claim. Embeddings are cached by content hash
plus model version, so changing the model requires re-embedding the corpus.

## 4. NLP components (local, trained in-process)

### 4a. Entity extraction — rule-based

**What it is.** `EntityExtractor(master)` compiles authorized master data into a
`spacy.blank("en")` pipeline with matcher rules. **No statistical model and no model download**:
extraction is dictionary/entity-rule matching against records the requester may actually see
([`docs/architecture/nlp.md`](../architecture/nlp.md)).

**Labels.** `ORDER`, `LINE`, `STYLE`, `MATERIAL`, `OPERATION`, `DEFECT`.

**Deliberate behaviour.** A plausible-looking but non-existent reference (e.g. `PO-KTN-9999`) is
**not** extracted. Extraction grants no authority to act on a mentioned record, and an
unknown/ambiguous mention requires resolution rather than silently creating anything.

**Measured.** Micro F1 **0.991** against a 0.90 target, on the held-out `notes_test.jsonl`
(110 notes), in the hashing smoke run. Because the vocabulary is closed and the notes are
templated, this score says the matcher works — **it does not predict performance on real shift
notes** ([`data-card.md`](data-card.md)).

### 4b. Note classification — TF-IDF + logistic regression

**What it is.** `TfidfVectorizer(ngram_range=(1,2), min_df=1, sublinear_tf=True)` +
`LogisticRegression(max_iter=2000)`, trained in-process from `data/eval/notes_train.jsonl`
(150 notes). No pretrained weights, no download.

**Classes.** `planning`, `materials`, `ie`, `quality`, `unknown`.

**Measured.** Macro F1 **0.835** against a 0.80 target, accuracy 0.836, on the 110 held-out test
notes. Per-class support and a confusion matrix are produced by the harness
([`docs/evaluation/methodology.md`](../evaluation/methodology.md)), not just an aggregate.

**An honest note about the abstention policy.** Production code calls `predict_with_margin()`,
which falls back to `"unknown"` below a confidence threshold tuned for live use. Scored on this
dataset, that policy collapses macro F1 to roughly **0.12**, because it abstains on most templated
notes. The evaluation therefore scores plain `.predict()` and says so. **The number above measures
the classifier, not the shipped abstention behaviour**; the shipped behaviour is deliberately
conservative and has not been separately scored.

### 4c. Grounded summarization

Canonical structured status is computed first; an optional model-generated explanation is layered
second and validated before display. A rejected or unavailable model sentence falls back to the
deterministic summary **with a label explaining why**, and `summary_source` distinguishes the two
on every response. `?mode=model` is permission-gated and capped at 10 `summary.model_generated`
audit events per run.

---

## Version and provenance

| Item | Value |
|---|---|
| Backend Python | 3.12 |
| `anthropic` SDK | `>=1.6.0` (1.6.0 verified against the installed source) |
| `fastembed` | `>=0.8.0` |
| `scikit-learn` | `>=1.9.1` |
| `spacy` | `>=3.8.16` |
| Full third-party inventory | [`licenses.md`](licenses.md) (generated from both lockfiles) |

Every evaluation artefact records provider, model, prompt, corpus, dataset and code versions
alongside its numbers ([`docs/evaluation/methodology.md`](../evaluation/methodology.md)).

## Summary of what this card does and does not license you to say

**Supported by evidence in this repository:** the deterministic components work on the synthetic
datasets at the scores quoted above; the enforcement layer around the LLM holds under a scripted
hostile model; the fixture provider is labelled everywhere.

**Not supported by any evidence here:** any statement about the hosted LLM's behaviour, accuracy,
latency or cost in this system; any statement about retrieval quality with the real embedder; any
statement about performance on real factory text.
