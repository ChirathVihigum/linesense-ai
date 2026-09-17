# LineSense synthetic SOP corpus

This directory holds the synthetic document corpus used to demonstrate
and test LineSense's document ingestion, retrieval, and citation pipeline
(Task 16/17). Every document here is **fictional and written for this
project** — a plausible-looking factory procedure, not a real standard
operating procedure from any real garment factory. None of it should be
used, copied, or presented as a real quality/IE standard outside this
project; see each document's own disclaimer line.

## Layout

- `sops/*.md` — the 30 required SOP/policy/standard documents (exactly
  30; see `scripts/validate_datasets.py::EXPECTED_SLUGS` for the
  authoritative list of slugs). Each starts with YAML front matter
  (`slug`, `title`, `doc_type`, `scope`, `acl`, `version`) followed by a
  single `#` title, the disclaimer line, and 3–8 `##` sections.
- `sops/versions/fabric-receiving-inspection-v1.md` — a **superseded**
  version 1 of `fabric-receiving-inspection` (inspects 5% of rolls per
  lot, versus the active version 2's 10%), used to test that only the
  active document version is ever retrieved or cited.
- `adversarial/injection-sop.md` — a document containing an embedded
  prompt-injection attempt (`SYSTEM: ignore all previous instructions...`),
  used to test that the retrieval/assistant pipeline treats document
  content as untrusted data, never as instructions to follow.
- `adversarial/xss-note.md` — text containing raw `<script>`/`<img
  onerror>` markup, used to test that any UI or export path escapes
  note/document content rather than rendering it as HTML.

**The two `adversarial/` files are loaded only by tests and evaluation
tooling. They are never included in `make seed`'s regular document load**
— seeding real (demo) organizations with intentionally hostile content
would be inappropriate outside a controlled test.

## Numeric consistency

Numeric rules quoted in the quality and IE documents are kept consistent
with `docs/architecture/formulas.md` and the demo quality policy: FINAL
inspection sample size 80, maximum 5 defective units, 0 tolerated
critical defects, FINAL inspection required before shipment eligibility.
Defect codes, material codes/names, operation names, and line codes match
`services/backend/app/seed/vocabulary.py` exactly; `scripts/
validate_datasets.py` checks defect-code usage against that vocabulary
automatically.

## How this was written

The corpus was authored for this project (front matter and section text
assembled by a one-off generation script, then hand-reviewed for
coherence, numeric consistency, and vocabulary accuracy) — it is not
derived from any real factory's documents. `scripts/validate_datasets.py`
checks every document's structure (front matter fields, slug/filename
match, section count, word count, disclaimer line, defect-code
vocabulary) and is run via `make datasets-check`.

## Licence

Covered by this repository's project licence (see the root `README.md`);
as of this writing that licence is not yet finalized (`_TBD_`).
