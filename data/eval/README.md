# LineSense synthetic NLP/IR evaluation datasets

This directory holds the labelled evaluation datasets built for Task 16
against the synthetic SOP corpus in `data/synthetic/sops/` and the fixed
demo vocabulary in `services/backend/app/seed/vocabulary.py`. Everything
here is synthetic, authored specifically for this project — no real
factory data, no real personal data, and no data scraped from any
external source.

**Every score computed against this data is a demonstration score. It
does not predict how any model or pipeline will perform against real
factory shift notes, real defect logs, or a real document set.** The
vocabulary is small and closed, the notes are templated, and the
documents are short by design; a system can do very well here and still
fail badly on messy real-world text. Treat results here only as a sanity
check that the pipeline (parsing, chunking, retrieval, NER) works end to
end, never as evidence of production readiness.

## Files

### `notes_train.jsonl` / `notes_test.jsonl`

One JSON object per line, one synthetic supervisor shift note per object:

```json
{"id": "n-train-001", "text": "...", "label": "planning", "entities": [
  {"label": "ORDER", "text": "PO-KTN-0072", "start": 17, "end": 28}
]}
```

- `label` is exactly one of `planning`, `materials`, `ie`, `quality`,
  `unknown` — the note's overall topic classification.
- `entities` is a list of named-entity spans with `label` one of `ORDER`,
  `LINE`, `STYLE`, `MATERIAL`, `OPERATION`, `DEFECT`, where
  `text[start:end] == entity.text` always holds (offsets are UTF-16/byte
  agnostic Python string indices).
- Only mentions that resolve to real master data (an order reference
  that actually exists, a real line/style/material/operation/defect) are
  labelled; a plausible-looking but non-existent reference (for example
  `PO-KTN-9999`) appears in some notes deliberately and is **not**
  labelled `ORDER` — this tests that a downstream NER system does not
  over-generalize from surface pattern alone.
- Entity mentions use varied surface forms on purpose (`L3` / `Line 3` /
  `line 3`; `M01` / `Cotton Pique Fabric` / `cotton pique fabric`; a
  defect's code or its name), and some notes have no entities at all.
- No personal names appear anywhere in this dataset, consistent with the
  corpus's `worker-data-privacy` procedure (operator data is
  pseudonymous only).

### `retrieval_questions.jsonl`

One JSON object per line, one evaluation question per object:

```json
{"id": "q-001", "split": "test", "question": "...", "relevant": [
  {"doc_slug": "fabric-receiving-inspection", "section": "Inspection Sampling"}
], "scope_factory": "org"}
```

- `split` is `test` or `dev`.
- `relevant` lists the document(s)/section(s) that answer the question;
  `section` is a `##` heading's exact text (without the `## ` prefix) in
  the *active* version of that document — the superseded
  `fabric-receiving-inspection-v1.md` under `versions/` is never a valid
  `relevant` target.
- `scope_factory` records which factory scope the question assumes
  (`org`, `KTN`, or `BYG`), matching the referenced document's `scope`
  front-matter field; this supports access-control tests (for example, a
  KTN-only user must not be able to retrieve a `BYG`-scoped answer).
- Questions are phrased as a person would ask them, never as a copy of
  the section heading text.

## How these were written

All three files were generated for this project by an authoring/
generation script committed alongside them
(`scripts/build_notes_dataset.py` for the notes, with a fixed random seed
for reproducibility), then hand-reviewed, plus `retrieval_questions.jsonl`
authored by hand against the corpus's actual section headings.
`scripts/validate_datasets.py` checks structural and content invariants
for all three files (and the SOP corpus) and is run via
`make datasets-check`.

## Train/test separation rule

`notes_test.jsonl` must not duplicate or lightly paraphrase any note in
`notes_train.jsonl`: the validator computes the token-set Jaccard
similarity between every test note and every train note and fails if any
pair is at or above `0.8`. Train and test notes were generated from
disjoint template sets with different phrasing conventions specifically
so this holds by construction, not by accident.

## Sentence-frame diversity

A note built from a sentence template with only its entity *values*
swapped (for example the same "`{ORDER} is behind schedule on {LINE}`"
skeleton with a different order/line each time) is not genuinely a new
example — it inflates classifier/NER scores without adding real
diversity. `scripts/validate_datasets.py::normalize_frame` collapses each
note to its **entity-normalized sentence frame** (every labelled span
replaced by its `<label>` placeholder, lowercased, whitespace-collapsed).
Two rules run against these frames:

- `check_frame_diversity` fails the build if any frame is used more than
  `MAX_FRAME_USES` (3) times within a split, or if fewer than
  `MIN_UNIQUE_FRAME_FRACTION` (60%) of a split's notes have a unique
  frame overall.
- `check_frame_diversity_by_label` fails the build if any single label
  within a split has fewer than `min(MIN_UNIQUE_FRAMES_PER_LABEL, its own
  note count)` unique frames (20, since every label has at least 22
  notes) — a split-wide average can otherwise hide one label built from a
  handful of frames while other labels carry the diversity.

`scripts/build_notes_dataset.py` draws templates by **shuffle-and-cycle**
per label (a seeded shuffle of that label's template bank, walked in
order and wrapped when exhausted) rather than independent random
sampling with a cap — sampling independently, even with a per-template
cap, could by chance leave several templates completely unused. Each
label/split bank has at least 20 genuinely distinct sentence templates
(240 in total: 5 labels × 2 splits × 24 templates each), mixing short
fragments, one-clause and multi-clause sentences, 0–3 entity mentions,
and a few notes that mention a second domain's entity while staying
dominantly about their own label. No template is used more than
`MAX_USES_PER_TEMPLATE` (3) times within a split.

Actual counts for the committed files (regenerate and re-check with
`make datasets-check` if the generator or its seed ever changes):

| Split | Notes | Unique frames | Unique fraction | Max reuse of one frame |
|---|---|---|---|---|
| `notes_train.jsonl` | 150 | 120 | 80.0% | 3 |
| `notes_test.jsonl`  | 110 | 110 | 100.0% | 1 |

Per-label breakdown (each label has a 24-template bank per split):

| Split | Label | Notes | Unique frames | Max reuse |
|---|---|---|---|---|
| train | planning | 30 | 24 | 2 |
| train | materials | 30 | 24 | 2 |
| train | ie | 30 | 24 | 2 |
| train | quality | 30 | 24 | 2 |
| train | unknown | 30 | 24 | 3 |
| test | planning | 22 | 22 | 1 |
| test | materials | 22 | 22 | 1 |
| test | ie | 22 | 22 | 1 |
| test | quality | 22 | 22 | 1 |
| test | unknown | 22 | 22 | 1 |

## Licence

These files are covered by this repository's project licence (see the
root `README.md`); as of this writing that licence is not yet finalized
(`_TBD_`), so treat this dataset as available only for coursework use
within this project until that is settled.
