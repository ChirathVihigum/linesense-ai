# Performance smoke test (task-25-brief.md req. 9)

## Status: PENDING (needs user approval or CI)

This machine's heat/workload policy (internal review notes (not published)
implementer-rules.md`) explicitly overrides this task's brief for this one item: **do not run
`make perf` or any load test locally.** Sustained concurrent load against a real server for 60
seconds is exactly the class of job that policy exists to prevent on this laptop. The script,
Makefile target and wiring are complete and were smoke-checked (imports, argument parsing, `ruff`,
`mypy` all pass — see below) but the actual load run has not been executed, and no numbers in this
document are fabricated placeholders standing in for a real run.

## What is ready to run

- `scripts/perf_smoke.py` — the load generator (concurrent GETs against orders/materials/capacity/
  dashboard list-shaped endpoints plus timed analysis-acknowledgement POSTs), fully implemented.
- `make perf` — runs it with the documented defaults.
- Verified without generating load: `uv run python ../../scripts/perf_smoke.py --help` succeeds
  (imports and argument parsing work); `uv run ruff check`, `uv run ruff format --check`, and
  `uv run mypy` all pass on the script.

## Exact command to run this (CI, or locally with explicit operator approval)

```bash
# 1. Seed the target database (once):
cd services/backend && uv run python -m app.seed --with-documents

# 2. Start the server under test — 2 workers, no reload, exactly as req. 9 specifies:
cd services/backend && uv run uvicorn app.main:create_app --factory \
    --workers 2 --host 127.0.0.1 --port 8000

# 3. In a second terminal, run the load generator against it:
cd services/backend && uv run python ../../scripts/perf_smoke.py \
    --base-url http://127.0.0.1:8000 --concurrency 20 --duration 60
```

(Equivalently, once a server is already running: `make perf`, which invokes the same script with
its defaults — `--base-url http://127.0.0.1:8000 --concurrency 20 --duration 60`.)

## What it measures and reports

Per `LINESENSE_IMPLEMENTATION_PLAN.md` §12's initial measurable targets (explicitly "targets, not
guarantees", to be revised based on actual observations):

| Metric | Target |
|---|---|
| Non-AI API p95 (orders list, order detail, materials, capacity, dashboard), 20 concurrent users | < 500 ms |
| Analysis acknowledgement p95 (`POST .../analyses` → 202) | < 1 s |

The script reports p50/p95/p99 latency and error rate for each of the five list/detail endpoints,
plus the analysis-acknowledgement p95, and records machine details
(`sysctl -n machdep.cpu.brand_string`, `hw.memsize`) alongside the results so a number is never
reported without the hardware it was measured on.

## Recording a real result

When this is run (in CI or with explicit operator approval), replace this section with:

- machine details (CPU, memory, OS) exactly as `perf_smoke.py` prints them;
- the seeded dataset used (row counts, or `make seed`'s default demo dataset);
- the full per-endpoint table (n, p50, p95, p99, error rate) and the analysis-ack row;
- for every target missed, the actual number and by how much, reported honestly (per this
  project's rules: "reporting misses honestly") — this section must never claim a target was met
  without the number that proves it, and must never present a plausible-looking number that was not
  actually measured.

This machine, for reference (not a load-test result — just the hardware this task was implemented
on, in case a future run happens here): Apple M5, 16 GB RAM, macOS 26.6.2.
