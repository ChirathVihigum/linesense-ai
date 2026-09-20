# Scan results (task-25-brief.md req. 10)

Recorded from an actual run on 2026-09-20 against this branch (`feat/linesense-build`,
commit range ending at the Task 25 commits). Re-run with `make security` (secret scan +
dependency audit + the `security`-marked test suite).

## Secret scan (`scripts/secret-scan.sh`)

```
$ bash scripts/secret-scan.sh
[secret-scan] OK: no high-risk secret patterns found in 413 tracked files
```

**Triage note — test fixtures excluded by design.** The scan excludes every path under a
`tests/` directory (for all four patterns, not only the password one) because this repository's
own test suite deliberately contains literal fake `sk-ant-...`-shaped strings to test *this
project's own* redaction and error-handling code:

| File | Line | Value | Why it exists |
|---|---|---|---|
| `services/backend/tests/unit/test_redaction.py` | 26, 63, 249 | `sk-ant-abc123XYZ_-token`, `sk-ant-secrettoken123`, `sk-ant-abc` | Asserts `app.llm.redaction.redact_text` actually redacts a key-shaped string |
| `services/backend/tests/unit/test_anthropic_client.py` | 28 | `sk-ant-should-never-leak-into-errors` | Asserts the Anthropic client never lets a key leak into a raised exception's message |
| `services/backend/tests/unit/test_llm_factory.py` | 57 | `sk-ant-test-key-not-real` | A `SecretStr` fixture for building a `Settings` object in a unit test |

None of these are real credentials (no such Anthropic key has ever existed for this project); they
are reviewed, and this exclusion is the reason `scripts/secret-scan.sh` documents it inline rather
than silently. Every other tracked file (including all of `services/backend/app/`, `apps/web/src/`,
and every script/doc in this task) was scanned and found clean.

## Dependency audit (`scripts/dependency-audit.sh`)

### Backend (`uvx pip-audit` against the exported `uv export --no-hashes --format requirements-txt`)

```
$ uv export --no-hashes --format requirements-txt > /tmp/requirements.txt   # (services/backend)
$ uvx pip-audit --requirement /tmp/requirements.txt
Resolved 114 packages in 2ms
No known vulnerabilities found
```

No findings; nothing to triage.

### Web (`npm audit --omit=dev --audit-level=high` in `apps/web`)

```
$ npm audit --omit=dev --audit-level=high
found 0 vulnerabilities
```

No findings; nothing to triage.

## Interpreting a future failure

- A **secret-scan** hit outside `tests/`/`.env.example` means a real-looking key/password landed in
  a tracked file: rotate the credential immediately (assume it is compromised the moment it is
  committed, even if removed in a later commit) and remove it from history.
- A **pip-audit**/**npm audit** hit means a resolved dependency has a known CVE: check whether the
  vulnerable code path is reachable from this project's usage before deciding urgency, then bump the
  dependency (or, if no fixed version exists yet, document the accepted risk and a re-check date
  here).
