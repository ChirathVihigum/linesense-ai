# Evaluation results

`eval-<UTC timestamp>.json`, `latest.json` and `latest.md` in this
directory are written by `python -m app.evaluation` (`make eval`).

**As of this commit, this directory is intentionally empty.** Per this
task's machine-heat policy, only the light `--embedder hashing --scenarios 2`
smoke test was run locally (see
`services/backend/tests/integration/test_eval_smoke.py`, which runs it
against a temp directory, not here); the real evaluation
(`--embedder fastembed`, the real `linesense_eval` database, default
`--scenarios 12`) is **PENDING (needs user approval, or CI)** -- see
`docs/evaluation/methodology.md`. A hashing-embedder run's numbers are not
representative of the real embedder (see that document's "Retrieval"
section) and are deliberately not committed here as if they were the
canonical result.

Run `make eval` (or the CI `eval` job, which already calls it) to populate
this directory for real.
