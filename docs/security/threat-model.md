# Threat model

Task 25 (security and resilience hardening). Refines
[LINESENSE_IMPLEMENTATION_PLAN.md §11](../../LINESENSE_IMPLEMENTATION_PLAN.md) ("Security and
Responsible AI") into concrete assets, trust boundaries, a STRIDE-mapped threat table (one row per
§11 "Threats and controls" entry) each with the implemented control and the test that proves it,
and the residual risks §11/§12 call out as accepted for this stage.

## Assets

- **Business records**: orders, BOM/materials, capacity, inventory ledger, quality inspections,
  recommendations/approvals, audit events — the facts the whole product exists to protect the
  integrity of.
- **Documents**: uploaded SOPs/policies and their extracted text/chunks — potentially sensitive
  operational content, and (per the prompt-injection threat) untrusted input to the LLM boundary.
- **Identity and session material**: OIDC tokens (never stored past the login exchange), the opaque
  `ls_session` token, CSRF tokens, the internal service token, provider API keys.
- **Model interactions**: prompts/tool results sent to an LLM provider, and any provider response —
  never itself a source of business truth (see `app/domain/*`), but a channel that must not leak
  secrets or accept forged authority.
- **Availability**: the ability to keep processing analysis runs and API requests through a worker
  crash, an unstable LLM provider, or a resource-exhaustion attempt.

## Trust boundaries

```
 Browser  --(HTTPS, cookie ls_session)-->  API (FastAPI)
   |                                          |  \
   | (OIDC redirect,                          |   \--(bearer service token)--> Internal dispatch
   |  authorization code)                     |                                 endpoint (worker-
   v                                          |                                 facing only)
 Identity Provider (OIDC)                     |
                                               v
                                          PostgreSQL (linesense_app: DML only;
                                          linesense_owner: DDL/migrations)
                                               ^
                                               |  (same DB, own connection)
                                          Worker process(es)
                                               |
                                               |--(HTTPS, provider API key)--> LLM provider
                                               |                                (Anthropic, or the
                                               |                                 `fixture` test
                                               |                                 double — never
                                               |                                 both in production)
                                               v
                                          File storage (LS_DOCUMENT_STORAGE_DIR)
```

Every arrow crossing a boundary is authenticated and, in production, encrypted in transit (HTTPS
externally; the database/provider/storage boundaries are deployment-infrastructure TLS, out of
this task's scope per §12). The browser never holds a provider token or the service token — only
the opaque session cookie. The worker never accepts inbound browser traffic; it only calls out
(to the internal dispatch endpoint, the LLM provider, and the database).

## STRIDE threat table

Each row is one of §11's "Threats and controls" table entries, with its STRIDE category(ies), the
control actually implemented, and the test that exercises it.

| Threat (§11) | STRIDE | Control | Proven by |
|---|---|---|---|
| Cross-factory/order access | Elevation of Privilege, Information Disclosure | `app.auth.scope.load_scoped`/`accessible_factory_ids`: every item/factory-scoped route resolves through organization+factory+permission checks; a row in an inaccessible factory is 404, an accessible-but-under-permissioned one is 403 | `tests/security/test_idor_matrix.py` (route-generated, every GET item/factory-scoped route in the live app), `tests/security/test_order_access.py`, `tests/security/test_document_access.py`, `tests/security/test_quality_release_rules.py` |
| Prompt injection in SOPs | Tampering, Elevation of Privilege | Read/compute-only agent tools (no arbitrary SQL/shell/URL fetch, no tool creation — `app/agents/base.py`); tool output and document text are passed as untrusted `tool_result` data, never as instructions; the model can only select among `candidate_actions` and cite `available_evidence` ids the deterministic assessment already produced; ≤4 tool calls, ≤1 repair, ≤12 model calls per run | `tests/security/test_prompt_injection.py` (the real RM, IE and quality loops -- all three wire `search_documents` -- against the real adversarial document: unknown tool name, SQL-shaped tool args, out-of-scope citation, unoffered action id, 50 consecutive tool calls, an instruction to reveal keys, each parametrized across all three agents; a payload-override attempt is RM-only, since it is the only one of the three whose deterministic assessment produces a real recommended-action payload to attempt to rewrite — each scenario blocked, no DB write, no secret in a tool result), `tests/agents/test_document_tool.py`, `tests/agents/test_agent_loop.py` |
| Forged/stale approval | Tampering, Repudiation | Self-approval denied (403 `SELF_APPROVAL_DENIED`); `proposal_hash`/input-version binding re-checked inside the locking transaction; expired/stale proposals rejected (409) and superseded, never silently applied | `tests/integration/test_approvals.py`, `tests/security/test_approval_rules.py` |
| Duplicate consumption | Tampering, Denial of Service | Capacity-slot/material-balance rows locked in deterministic (sorted id) order; `Idempotency-Key` required on every side-effecting request; a worker crash mid-task never leaves a duplicate result (unique `(task_id)` index on `agent_results`) | `tests/integration/test_apply_concurrency.py`, `tests/integration/test_reservation_concurrency.py`, `tests/integration/test_idempotency.py`, `tests/resilience/test_worker_kill.py` |
| SQL injection/XSS | Tampering | Every query is parameterized SQLAlchemy (no string-built SQL, no agent SQL tool); React renders every user/LLM/document-sourced string as a text node (no `dangerouslySetInnerHTML` anywhere in `apps/web/src`); API responses carry `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`, the web app's own `'self'`-scoped CSP | `apps/web/src/test/xss.test.tsx`, `tests/security/test_headers_and_limits.py`, `tests/security/test_prompt_injection.py`'s SQL-shaped-argument scenario |
| Malicious uploads | Tampering, Denial of Service | `LS_MAX_UPLOAD_BYTES` size cap, `doc_type`/media-type validation, documents start `QUARANTINE` and only become `ACTIVE` after processing succeeds; originals are never served publicly (download routes are scope-checked) | `tests/integration/test_document_pipeline.py` (task 17) |
| SSRF/exfiltration | Information Disclosure | No agent tool accepts an arbitrary URL; the only outbound calls are to the configured LLM provider and the internal dispatch endpoint, both from settings-derived, non-user-controlled addresses | `app/agents/base.py` (tool set is closed, defined per agent, never derived from request input) |
| Secret disclosure | Information Disclosure | Secrets only ever live in `Settings`/`SecretStr`/environment variables, never logged (`structlog` never receives tokens/prompts); `app.llm.redaction.redact_payload` strips API keys, bearer tokens, emails and phone-shaped sequences from everything sent to a provider; `scripts/secret-scan.sh` scans every tracked file for hardcoded keys/passwords/private keys | `tests/unit/test_redaction.py` (including this task's NFD-Unicode-email fix), `tests/security/test_prompt_injection.py`'s reveal-keys scenario, `scripts/secret-scan.sh` (see `docs/security/scan-results.md`) |
| Cost or request abuse | Denial of Service | Per-user/per-IP token-bucket rate limits (analysis creation, uploads, imports, search, notes, `/auth/login`); per-run model-call/token budgets reserved atomically before every call; `MAX_ACTIVE_RUNS_PER_FACTORY` | `tests/security/test_headers_and_limits.py`, `tests/integration/test_budget.py` |
| Worker/service impersonation | Spoofing | The internal dispatch endpoint (`/internal/v1/agent-tasks`) requires a constant-time-compared bearer service token, never a cookie; it is not part of the public API surface | `tests/integration/test_internal_dispatch.py` |

## Residual risks (accepted for this stage)

- **Single-instance rate limits.** `app.api.ratelimit.RateLimitMiddleware` keeps its token buckets in
  process memory; behind more than one API instance/worker, each enforces its own independent
  budget, so the effective aggregate limit scales with instance count. A shared store (e.g. Redis)
  would be needed to cap the true aggregate rate across instances — out of scope for this stage's
  single-instance deployment target (§12).
- **Structural file scan without antivirus.** Document upload validates size, declared media type
  and (for PDFs) structural parseability, and quarantines until processing succeeds, but there is no
  signature-based malware scan. Acceptable for a synthetic/pilot deployment; a real-data pilot should
  add one before accepting operator-uploaded files at scale.
- **Dev IdP.** `devtools/dev_oidc` is a local OIDC provider used only in development/test
  (`LS_ENVIRONMENT != production` rejects it — see `Settings._validate_production_hardening`); a
  real deployment must point `LS_OIDC_ISSUER` at a real, hardened identity provider.
- **No row-level security (RLS) yet.** Tenant isolation today is enforced entirely in the
  application layer (`app.auth.scope.load_scoped`, checked by `tests/security/test_idor_matrix.py`
  against every scoped route). §11 calls RLS a required defense-in-depth gate before any
  multi-organization pilot. **Plan**: before onboarding a second organization, add PostgreSQL RLS
  policies on every tenant table keyed on a transaction-local `organization_id` setting, using
  `linesense_app` (a non-owner, non-`BYPASSRLS` role) as the runtime role, with `USING`/`WITH CHECK`
  policies tested the same way `tests/integration/test_scope.py` already tests the application-layer
  checks, and with connection-pool reuse verified never to retain a prior tenant's context.
