# LineSense evaluation methodology

`python -m app.evaluation` (wired up as `make eval`) runs six sections --
retrieval, entities, classification, calculations, agents, fairness -- plus
a security section, against a dedicated database, and writes a versioned
JSON report plus a Markdown rendering under `docs/evaluation/results/`
(`eval-<UTC timestamp>.json`, `latest.json`, `latest.md`). This document
says what each section measures, what dataset it runs against, and --
most importantly for the agent and fairness sections -- what it does
**not** show.

## The one thing to read before trusting any number below

**Every "agent" result in this report comes from the deterministic
`fixture` LLM provider (`app.llm.fixture_client.FixtureLLMClient`), never
a live model.** The fixture provider is a pure function of the request: it
always investigates with the tools offered (up to a fixed count) and then
picks the deterministically-best candidate action and cites the available
evidence. It cannot hallucinate, refuse, phrase something ambiguously, or
be swayed by wording -- which is exactly why it is useless for measuring
how a real model would behave, and exactly why it is safe for measuring
whether the bounded agent loop, the orchestrator's task graph, and the
deterministic domain calculations wire together correctly. The `agents`
and `fairness` sections of the report both carry `"not_a_live_llm_evaluation":
true` for this reason, and neither `--scenarios` count nor a clean pass
here is evidence that a live model would produce safe or accurate output.

## Sections

### Retrieval

Runs every **test**-split question in `data/eval/retrieval_questions.jsonl`
(45 of the 56 questions; the other 11 are `split: "dev"` and never scored)
through `app.retrieval.search.search` in `lexical`, `vector` and `hybrid`
mode, under a single fixed scope: the seeded demo organization, its `KTN`
factory, and the `supervisor` role. For each question:

- **Recall@5**: 1 if any of the top-5 returned chunks matches one of the
  question's `(doc_slug, section)` relevant targets, else 0; averaged
  across questions.
- **MRR@10**: the reciprocal rank (`1/rank`) of the first matching chunk
  within the top 10, or 0 if none; averaged across questions.

Target: hybrid Recall@5 >= 0.85 (task-19-brief.md requirement 2).

**Known, expected miss under this scope**: 2 of the 45 test questions are
`scope_factory: "BYG"` -- they ask about a document scoped to the *other*
demo factory. Under the fixed KTN-supervisor scope this harness always
uses, those 2 questions can never be retrieved, by design (access control
must never leak a document across factory scope). Their misses are
expected and are not a retrieval defect; they are included in the recall
denominator anyway because the brief specifies "every test question," and
adjusting the dataset or the scope to make the number look better would
violate requirement 9 (never chase the target by changing the data).

**Known, expected weakness of `--embedder hashing`**: `HashingEmbedder` is
a deterministic, dependency-free feature-hashing stand-in for tests (see
`app/retrieval/embedder.py`) -- lower-cased word uni/bigrams hashed into
384 signed buckets, L2-normalized. It captures crude lexical overlap, not
semantics, and is dramatically weaker than the real embedder
(`fastembed`/`BAAI/bge-small-en-v1.5`). The 0.85 recall target was set
with the real embedder in mind. Running `--embedder hashing` (this
project's smoke test; see "Running this locally" below) is expected to
undershoot that target -- that is fine for a smoke test whose only job is
to confirm the retrieval pipeline runs end to end, not to hit the
production quality bar. Only a `--embedder fastembed` run's numbers should
be read as a real answer to "does hybrid retrieval hit its target."

We also observed, independent of the embedder, that lexical-mode-alone
recall is very low on this dataset: `websearch_to_tsquery` effectively
ANDs a query's significant terms, so a full natural-language question
(e.g. "What percentage of rolls in a fabric lot must be sampled during
four-point inspection?") often matches no single chunk containing every
term. This is exactly the reason hybrid search exists (vector search
recovers the semantic matches lexical misses) and is reported as
observed, not treated as a bug to fix in this task.

### Entities

Micro precision/recall/F1 over exact `(label, start, end)` matches between
`EntityExtractor` (built from the seeded demo organization's master data,
merged across its `KTN` and `BYG` factories so cross-factory order/line
references in the test notes resolve) and the labels in
`data/eval/notes_test.jsonl` (110 notes). Per-label precision/recall/F1
plus up to 25 misses and 25 false positives are recorded. Target: micro-F1
>= 0.90 (task-19-brief.md requirement 3).

As `data/eval/README.md` states plainly, this is a demonstration score
against a small, closed, templated vocabulary -- a system can score very
well here and still fail badly on messy real factory text. Treat it as a
pipeline sanity check, not evidence of production NER quality.

### Classification

`KeywordBaselineClassifier` (fixed keyword lists, no training) and
`TfidfNoteClassifier` (TF-IDF 1-2 grams + logistic regression, trained on
`data/eval/notes_train.jsonl` only -- never the test split) are both
scored on `data/eval/notes_test.jsonl`: accuracy, macro-F1, per-class
support, and a confusion matrix. Target: TF-IDF macro-F1 >= 0.80
(task-19-brief.md requirement 4).

This section deliberately uses the classifier's plain `.predict()`, not
`.predict_with_margin()`. The margin/threshold behaviour
(`app.nlp.classifier.MARGIN_THRESHOLD`) is a *production* abstention
policy -- fall back to `"unknown"` below a confidence cutoff tuned for
live note classification -- not a measure of the trained model's raw
classification ability, which is what this section reports.

### Calculations

Re-runs the plan's four independently-worked, pure-arithmetic reference
fixtures (`tests/unit/test_reference_fixtures.py`, backed by
`docs/architecture/formulas.md`): one-shift capacity, material shortage,
line balance, and quality rates. Each check calls the exact Task 4 domain
function with the exact fixture input and records pass/fail (never
raises); a broken formula shows up as a `false` here, not a harness crash.
Target: all four pass.

### Agents (NOT a live-LLM evaluation)

For each of `--scenarios` synthetic scenarios (seeded, `random.Random(20260917
+ index)`), a fresh, self-contained order/style/material/line/slot set is
built via `tests.factories` (never the shared demo scenario, so scenarios
never share state or interfere with each other), with one material's
on-hand quantity and one line's capacity minutes chosen so that, when fed
through the exact Task 4 formulas, they produce a *known* ground truth for
two independent yes/no questions: "is there a material conflict" and "is
capacity sufficient." Ground truth is always re-derived from the exact
values written to the database (never trusted from the a-priori random
draw), so a `Decimal.quantize()` rounding step can never silently mislabel
a scenario.

Three approaches are compared against that ground truth:

- **(a) deterministic baseline**: the Task 4 calc functions only, given
  the same inputs. Its accuracy is close to 100% by construction -- ground
  truth is computed with the same functions -- so it is reported as the
  *ceiling* this comparison can reach, not an independent check of the
  formulas (that is what the calculations section is for).
- **(b) single-agent baseline**: the planning agent alone, fully
  in-memory (`dependency_results={}`, no RM/IE results, no database). By
  design (task-19-brief.md requirement 6), it never sees the RM agent's
  material assessment, so `material_conflict_pred` is always `False` --
  it cannot detect a material conflict, ever, regardless of the scenario.
  This is expected, not a bug, and the report's `agents.single_agent_baseline_note`
  field says so explicitly.
- **(c) the real four-agent flow**: RM, IE and quality dispatched
  together, planning proposes, and (when the top plan overcommits
  material) one targeted replan -- driven end to end through the real job
  queue and worker (`tests.helpers.worker.drain`) against a fresh
  `AnalysisRun`, exactly the production code path, just with the fixture
  LLM client standing in for a real model.

For the four-agent flow, the report also records: whether the final
stored `Recommendation`'s allocated units respect the RM agent's
`coverable_units` figure ("respects_material_coverage"), the number of
evidence refs per blocker-severity finding, a citation-validity check
(every evidence ref with a `chunk_id` must resolve via
`app.retrieval.search.get_citation` under the run's own scope -- these
synthetic scenarios carry no documents, so this is usually `0/0`, reported
as such rather than a false pass), total model calls, and wall-clock time.

**Known approximation**: `capacity_sufficient_pred` (for both the
single-agent baseline and the four-agent flow) is read from the planning
agent's `unscheduled_units` metric being zero. `unscheduled_units` can be
driven by *either* a capacity shortfall or a material shortfall (the two
are not independently exposed in the agent's output), so this is an
approximation, not an exact separation of the two causes. Treat the
`capacity_sufficient` accuracy numbers as directional, not exact --
especially at the default `--scenarios 12` (or the smoke test's
`--scenarios 2`), where the sample is small enough that a couple of
scenarios can swing the percentage a lot.

### Fairness (NOT a live-LLM evaluation)

For each of `min(10, --scenarios)` pairs, the *same* order is run through
the four-agent flow twice, with only its customer changed in between
(everything else -- style, BOM, material balance, line, capacity slot --
is untouched), and the stored `recommendations.proposal_hash` values are
compared. **Two different orders, even with identical business content,
are never a valid comparison here**: `proposal_hash` is computed over a
proposal payload that embeds the order's own id
(`app.orchestration.recommendations.create_recommendation`), so re-running
the *same* order with only its customer changed is the only way to make
this an "identical except the customer" test.

**Limitation** (this harness's own statement -- the original product
spec's numbered fairness clause was not available to read while writing
this, so this is written from scratch, deliberately conservative rather
than copied): this only shows that the four-agent flow, under the
deterministic fixture LLM provider, proposes byte-identical allocations
for the same order regardless of which customer it belongs to. It is
**not** an audit of fairness across any protected characteristic, it
cannot show whether a live model would let a customer's identity leak
into its phrasing or tool choices even when the deterministic assessment
underneath is identical, and a 100% pass rate here is necessary, not
sufficient, for calling the system fair. The same text is embedded in the
report's `fairness.limitation` field.

### Security

Two independent checks, both real (no fixture-provider caveat applies to
either):

- **Prompt-injection resistance**: rather than re-implementing the
  adversarial-document and scripted-compromised-model scenarios (risking
  a second, drifting copy of the real test), this section runs the two
  existing pytest cases that already cover them as a subprocess and
  reports blocked/total from their pass/fail outcome:
  `tests/agents/test_agent_loop.py::test_prompt_injection_in_tool_output_changes_nothing`
  and
  `tests/agents/test_document_tool.py::test_adversarial_document_cannot_hijack_the_agent_loop`
  (the latter loads `data/synthetic/adversarial/injection-sop.md` through
  the real document pipeline). Because
  `tests/conftest.py`'s autouse integration-test fixture truncates every
  table before each test, this subprocess call runs against the harness's
  own database and the security section therefore always runs **last**,
  after every other section has already read what it needed.
- **Abstention**: `app.domain.quality.calc.shipment_eligibility` (a pure
  function, no database) is exercised directly for "no inspection",
  "policy unknown", and "missing required inspection type / incomplete
  production" -- none of these may ever yield `eligible=True`.

## Datasets and splits

| File | Rows | Used by |
|---|---|---|
| `data/eval/notes_train.jsonl` | 150 | Classification (TF-IDF training only) |
| `data/eval/notes_test.jsonl` | 110 | Entities, classification |
| `data/eval/retrieval_questions.jsonl` | 56 (45 `test` scored, 11 `dev` unused) | Retrieval |
| `data/synthetic/sops/*.md` | 30 documents | Retrieval corpus |

All three JSONL files and the SOP corpus are synthetic, authored for this
project (`data/eval/README.md`, `data/synthetic/README.md`) -- no real
factory data. `notes_test.jsonl` is checked (by
`scripts/validate_datasets.py`, `make datasets-check`) to never duplicate
or lightly paraphrase a training note. The `versions` block of every
report records `corpus_sha256`, `notes_test_sha256` and
`questions_sha256` (sha256 digests) so a report can always be tied back to
the exact dataset snapshot it ran against.

## Metric definitions

Implemented as pure, dependency-free functions in `app/evaluation/metrics.py`
(unit-tested with hand-computed examples in `tests/unit/test_metrics.py`):

- `recall_at_k(retrieved, relevant, k)`: fraction of queries where any of
  the top-`k` retrieved items is in that query's relevant set.
- `mrr(retrieved, relevant, k=None)`: mean, over queries, of `1/rank` of
  the first relevant item within the top-`k` (0 if none).
- `micro_prf(true_sets, pred_sets)`: precision/recall/F1 pooling true/false
  positives/negatives across every example before dividing.
- `per_label_prf(true_sets, pred_sets, labels=None)`: the same, broken out
  per label, with the example index folded into the comparison key so two
  different examples that happen to produce an identical-looking item are
  never conflated into one true positive.
- `macro_f1(y_true, y_pred, labels=None)`: unweighted mean of each class's
  one-vs-rest F1 (single-label classification).
- `confusion_matrix(y_true, y_pred, labels)`: `{true: {predicted: count}}`,
  in exactly `labels` order (never re-sorted).
- `accuracy(y_true, y_pred)`: exact-match fraction.

## Targets and honesty

`targets`/`target_results` in the report are the four named, numeric
targets from task-19-brief.md requirements 2-5 (hybrid Recall@5,
entities micro-F1, TF-IDF macro-F1, calculations all-pass). **Targets are
fixed in `app/evaluation/report.py` and are never adjusted after seeing a
result**; a missed target is reported as `"met": false` with the real
actual number next to it, and `python -m app.evaluation` still exits 0 (a
non-zero exit is reserved for a harness error -- an unreachable database,
a missing dataset file -- never for a missed target). The agents,
fairness and security sections do not have brief-specified numeric
targets and are reported as measurements, not pass/fail gates.

## Running this locally

```
# Light smoke test (hashing embedder, no network, 2 synthetic scenarios --
# what this task's own dispatch ran, via scripts/heavy-job.sh, against an
# isolated per-agent database rather than provisioning linesense_eval):
cd services/backend
LS_EVAL_DATABASE_URL=postgresql+psycopg://linesense_app:dev-app-only@127.0.0.1:55432/linesense_test_d \
LS_EVAL_MIGRATION_DATABASE_URL=postgresql+psycopg://linesense_owner:dev-owner-only@127.0.0.1:55432/linesense_test_d \
uv run python -m app.evaluation --embedder hashing --scenarios 2 \
  --output-dir /tmp/eval-smoke

# Full evaluation (real fastembed embedder, real linesense_eval database,
# default 12 scenarios): `make eval` from the repo root, or directly:
cd services/backend
uv run python -m app.evaluation --embedder fastembed
```

`--embedder fastembed`'s first run downloads the `BAAI/bge-small-en-v1.5`
model (~100MB) to `.local/models` -- a real, one-off network fetch. This
task's dispatch runs *only* the hashing smoke test locally (machine-heat
policy: no model download, no 30-document embedding pass, no 55-question
live run on a laptop); the full `--embedder fastembed` evaluation is
**PENDING (needs user approval, or CI)** with the exact command above.
The CI workflow's `eval` job already calls `make eval`, which runs it.

### Running a live-provider evaluation

No Anthropic API key is available in this environment, and this harness
must never claim a live-LLM result it did not produce. Once a real key is
configured (`LS_ANTHROPIC_API_KEY`), a live-provider run of the agent and
fairness sections is:

```
LS_LLM_PROVIDER=anthropic LS_ANTHROPIC_API_KEY=<key> make eval
```

This replaces the fixture provider with `AnthropicLLMClient` for every
agent call in the `agents`/`fairness` sections; the `versions.llm_provider`/
`versions.llm_model` fields in the resulting report record exactly which
provider/model produced it, and `not_a_live_llm_evaluation` becomes
`false`. Because a live model is non-deterministic, a single such run is
not enough to draw a conclusion from -- see the next section.

### Reporting variance

A live-provider run (or any run whose numbers matter for a decision)
should be repeated 3 times with the same `--scenarios` and dataset
snapshot (same `corpus_sha256`/`notes_test_sha256`/`questions_sha256` in
the `versions` block confirms the datasets did not drift between runs),
and the three `latest.json`s' matching numbers (accuracy figures, recall,
pass rates) reported together as min/median/max or mean +/- range rather
than a single number, exactly because a fixture-provider run's
reproducibility (identical input -> identical output, always) does not
carry over to a live model. This harness does not automate the 3x repeat
or the aggregation itself; each run's timestamped JSON under
`docs/evaluation/results/` is retained specifically so a human (or a
follow-up script) can diff and aggregate them after the fact.
