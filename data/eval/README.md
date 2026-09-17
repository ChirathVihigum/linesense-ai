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

## Licence

These files are covered by this repository's project licence (see the
root `README.md`); as of this writing that licence is not yet finalized
(`_TBD_`), so treat this dataset as available only for coursework use
within this project until that is settled.
