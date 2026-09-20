# NLP: entity extraction, note classification, grounded status summaries

Task 18. Code: [`app/nlp/`](../../services/backend/app/nlp/), routes
[`app/api/notes.py`](../../services/backend/app/api/notes.py) and
[`app/api/summaries.py`](../../services/backend/app/api/summaries.py). No model
download anywhere in this task: entity extraction is a `spacy.blank("en")`
pipeline with a rule-based `EntityRuler`, and the note classifier is a
TF-IDF + logistic-regression model trained in-process from
`data/eval/notes_train.jsonl` (Task 16).

## Entity extraction (`app/nlp/entities.py`)

`MasterData` is a factory's (plus its organization's) resolvable vocabulary:
`orders`/`lines` are loaded from the database scoped to
`(organization_id, factory_id)`; `styles`/`materials` are scoped to
`organization_id` only (those tables have no `factory_id` — backend-contracts.md
section 2); `operations`/`defects` come straight from the fixed seed catalogue
(`app.seed.vocabulary.OPERATION_CATALOG`/`DEFECT_CATALOG`), identical for every
organization. `load_master_data(session, organization_id, factory_id)` builds it.

`EntityExtractor(master)` compiles `MasterData` into `spacy.blank("en")` +
one `EntityRuler` (`phrase_matcher_attr="LOWER"` — every match is
case-insensitive) with phrase patterns for every known order ref, line
code/name, style code, material code/name, and operation/defect code/name
(operations also get a `+"s"` plural pattern). One extra **token regex**
pattern (`{"LOWER": {"REGEX": "^po-[a-z]{3}-\d{4}$"}}`) catches
well-formed-but-unrecognized order references (e.g. `PO-KTN-9999`) so they can
be surfaced without ever being resolved.

- **Overlap / longest match**: resolved by spaCy's own `EntityRuler` (it keeps
  the longest non-overlapping span among its own matches) — no custom logic
  needed; verified for a material name contained in another
  (`test_longest_material_match_wins_over_a_shorter_overlapping_pattern`).
- **Cross-factory isolation**: a BYG line code/name is never registered as a
  pattern when the extractor is built from KTN's `MasterData`, so it is not
  recognised as an entity at all for a KTN note (not merely "unresolved").
- **Ambiguity**: a material *name* is not unique in the schema (only
  `(organization_id, code)` is). `load_master_data` detects two different
  material ids normalizing to the same key and stores the sentinel
  `AMBIGUOUS_ID` (the nil UUID, which no `uuid4()` id can ever equal) instead
  of either real id; `EntityExtractor` turns that into
  `ambiguous=True, resolved_id=None`.
- **Tokenizer**: spaCy's default English infixes split a hyphen only between
  two *alphabetic* runs, so `ST-01`/`KTN-0020` already survive as one token but
  `DEF-OS` and the `PO`/`KTN` halves of an order ref do not.
  `_loosen_hyphen_tokenization` drops that one infix rule so every
  vocabulary code (which always uses a hyphen as an internal delimiter,
  never for anything a general English tokenizer would care about) stays a
  single token, matching how the same patterns are compiled.
- **Unknown order refs never act on anything**: the API's `entities` list only
  ever contains resolved, non-ambiguous mentions; `unresolved` only contains
  well-formed-but-unknown `ORDER` mentions (`label="ORDER"`, `resolved_id=None`,
  `ambiguous=False`). Ambiguous mentions of any label are dropped from both
  lists (never stored, never actioned).

## Note classification (`app/nlp/classifier.py`)

Labels: `planning`, `materials`, `ie`, `quality`, `unknown`.

- **`KeywordBaselineClassifier`**: fixed, documented keyword lists per class
  (see `_KEYWORDS` in the module); a note is scored by how many distinct
  keywords from each class appear (case-insensitive substring match), the
  highest-scoring class wins, ties break by list order
  (`planning, materials, ie, quality`), and zero matches anywhere → `unknown`.
  Used only as an evaluation baseline (Task 19), never in the notes API.
- **`TfidfNoteClassifier`**: `TfidfVectorizer(ngram_range=(1,2), min_df=1,
  sublinear_tf=True)` + `LogisticRegression(max_iter=2000,
  class_weight="balanced", random_state=seed)`. `.train(examples, seed=13)` is
  a classmethod; the same `examples`/`seed` always produce identical
  predictions (verified in `test_training_is_deterministic`).
  `predict_with_margin` returns `("unknown", probability)` whenever the
  winning class's predicted probability is below `MARGIN_THRESHOLD = 0.45`.
- **`get_default_classifier()`**: trains once from
  `data/eval/notes_train.jsonl` and caches the instance for the life of the
  process (thread-safe via a module lock); `get_default_classifier_version()`
  is `sha256(train file bytes)[:12]`. The loader reads the file with the
  builtin `open()` specifically so a test can patch `builtins.open` and prove
  the test split (`notes_test.jsonl`) is never opened by training.

## Grounded status summaries (`app/nlp/summarize.py`)

`OrderReport` here is a plain `dict[str, Any]` — the shape Task 15's synthesis
step stores as the `run.report` event payload
(`{"order", "states", "shipment", "blockers", "agent_summaries",
"recommendation", "evidence", "degraded", "degraded_reasons", "generated_at"}`,
task-15-brief.md requirement 4) and returns as `RunDetail.report`
(`app/api/runs.py`). This module depends only on that documented JSON shape,
never on a Task 15 Python type, because Task 15 was developed concurrently.

- **`grounded_summary(report)`**: a pure, deterministic template. One sentence
  for the order's states, one for shipment eligibility (with reasons), one per
  blocker, and one for the recommendation's status. Every sentence's
  `evidence_ids` are drawn only from `report["blockers"][*]["evidence_ids"]`
  (for the states/shipment/blocker sentences) or are empty (the recommendation
  sentence — a decision reference, not a synthesized fact), so they always
  exist in `report["evidence"]`; never invented.
- **`model_summary(report, llm, budget_reserver)`**: the `?mode=model` path.
  `budget_reserver()` is called first; `False` → returns `None` (the caller
  turns that into 429 `RATE_LIMITED`). Otherwise it asks the LLM once for
  `{"sentences": [{"text", "evidence_ids"}, ...]}` and validates the response:
  every sentence must have a non-empty `evidence_ids` list, every id must
  exist in `report["evidence"]`, and no sentence may match
  `ready to ship|shipment[- ]ready|can ship` (case-insensitive) when
  `report["shipment"]["eligible"]` is false. Any validation failure, an
  unparsable response, or a provider error all fall back to
  `grounded_summary(report)` with a label explaining why — `model_summary`
  itself never returns an unvalidated model sentence, and only returns `None`
  for the budget-exhausted case.

  | Situation | `summary_source` | `label` |
  |---|---|---|
  | Valid model response, live provider | `model` | `None` |
  | Valid model response, `provider == "fixture"` | `model` | `FIXTURE_LABEL` |
  | `LLMDisabledError` | `deterministic` | `DISABLED_LABEL` |
  | Any other `LLMError` (unavailable/refusal/invalid) | `deterministic` | `UNAVAILABLE_LABEL` |
  | Unparsable / invalid / shipment-contradicting response | `deterministic` | `REJECTED_LABEL` |
  | `budget_reserver()` returns `False` | — | `model_summary` returns `None` |

## Routes

### `POST /api/v1/factories/{factory_id}/notes` and `GET .../notes` (`app/api/notes.py`)

Permission `note:create` gates **both** routes — backend-contracts.md section 4
defines no separate `note:read`, so a principal who may not add notes to a
factory may not browse them either. `POST` requires `Idempotency-Key`; body
`{"text": str}` (3–2,000 chars, stored as plain text — no HTML
sanitization/escaping, which is the UI's job, verified with a literal
`<script>` string round-tripping unchanged through the JSON response).

Response: `{id, text, classification, classifier_version, entities,
unresolved, created_at, notice}`. `notice` is always
`"Entity links are for navigation only; they do not authorize any action."`
`entities` (resolved, non-ambiguous mentions only) is what `notes.entities`
(backend-contracts.md section 2) actually stores; `unresolved` is **not**
persisted (there is no column for it), so it is only ever populated on the
`POST` response and is always `[]` on `GET`/list — the entity extraction
that produced it is not re-run for a stored note. `classifier_version` is
always the *current* process-wide classifier's version, not the version that
classified a note when it was created (there is no column for that either);
both limitations are acceptable for a demo/single-process deployment and are
recorded here rather than silently.

### `GET /api/v1/orders/{order_id}/status-summary` (`app/api/summaries.py`)

A new module (not `app/api/orders.py`, developed concurrently by Task 15) —
registered directly in `app/main.py`. `order:read` (all roles) for the
default `?mode=deterministic`; `?mode=model` additionally requires
`analysis:run` (planner/supervisor).

The route finds the order's most recently `COMPLETED`/`DEGRADED`
`analysis_runs` row and its latest `run.report` `run_events` payload itself
(the same pattern `app/api/runs.py`'s `REPORT_EVENT_TYPE` lookup uses) rather
than depending on a Task 15 `OrderDetail.latest_report` field that does not
exist yet. **Degrade-gracefully choice** (documented per the brief, which
left this open): when there is no such run/report, the route returns 409
`CONFLICT` rather than a fabricated 200 "empty summary" — an empty
`sentences` list under a 200 risks being read as "this order has no issues"
rather than "no analysis has run yet".

`stale` mirrors task-15-brief.md requirement 5: `true` when the order's
current `version` differs from what the run's `run_snapshots.input_versions
["order"]` recorded it as when the report was computed.

`?mode=model` cap: at most 10 `summary.model_generated` audit events per
order per rolling 24h (counted directly from `audit_events`, scoped to the
organization); over the cap → 429 `RATE_LIMITED`. The audit event is written
whenever an actual LLM call was attempted — whether its output was accepted
(`model`) or fell back to deterministic because it was rejected/unavailable
— but **not** when `LS_LLM_PROVIDER=disabled` (`build_llm_client` returns
`None` and no call is ever attempted, so it neither costs budget nor writes
the audit event; `app/agents/base.py` treats a `None` LLM client the same
way). The cap check and the audit write are two separate statements (no row
lock), a known, documented, low-stakes race for a demo-scale feature.

## Testing

- `tests/unit/test_entities.py` — every requirement-1 surface form, longest
  match, cross-factory isolation, ambiguity.
- `tests/unit/test_classifier.py` — keyword baseline behaviour, deterministic
  training, low-margin → `unknown`, the `builtins.open` patch proving only the
  train file is read, cache/version behaviour.
- `tests/unit/test_summarize.py` — every deterministic sentence's evidence ids
  exist; states/shipment/blockers/recommendation are all covered; every
  `model_summary` rejection path (shipment contradiction, unknown evidence id,
  missing evidence ids, unparsable JSON, disabled/unavailable provider) and
  the accepted/fixture-labelled paths.
- `tests/integration/test_notes_api.py` — create/list, idempotent replay,
  viewer → 403, cross-factory → 403/404, XSS text round-trips verbatim,
  BYG entities absent from a KTN note, classification filter, length
  validation (422), unknown factory → 404.
- `tests/integration/test_status_summary.py` — no report → 409; a real
  `run.report`/`run_snapshots` fixture → 200 with valid evidence ids and
  `stale` computed both ways; unknown order → 404; `mode=model` without
  `analysis:run` → 403; ten real calls through the test app's default
  `LS_LLM_PROVIDER=fixture` client (which returns no scripted JSON sentences
  for this prompt, so every one falls back to `REJECTED_LABEL`) then an
  eleventh → 429 `RATE_LIMITED`, proving the cap end-to-end against the real
  audit log rather than a mock.
