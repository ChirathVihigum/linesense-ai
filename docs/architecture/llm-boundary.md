# LLM boundary

LineSense talks to a model through exactly one interface, `LLMClient` (backend-contracts.md
[§8](backend-contracts.md#8-llm-boundary-appllm)). Agents (Task 12) and the orchestrator never
import an SDK type directly; every provider adapter converts to/from the plain
`LLMToolSpec`/`LLMToolCall`/`LLMResponse` models. The code is in `services/backend/app/llm/`:

| Module | Responsibility |
| --- | --- |
| `client.py` | `LLMToolSpec`, `LLMToolCall`, `LLMResponse`, the `LLMClient` protocol, the `LLMError` hierarchy |
| `anthropic_client.py` | `AnthropicLLMClient`: the live provider, built on `anthropic` SDK 1.x |
| `fixture_client.py` | `FixtureLLMClient`, `FixtureRequest`, `FixtureScript`, `default_fixture_script`: deterministic, network-free |
| `factory.py` | `build_llm_client(settings)`: picks the provider from `LS_LLM_PROVIDER` |
| `budget.py` | `reserve_model_call`, `record_usage`, `token_budget_remaining`: atomic per-run limits |
| `redaction.py` | `redact_text`, `redact_payload`: strip secrets/PII before anything reaches a provider |

## What data reaches the provider

`AnthropicLLMClient.complete` sends exactly `system`, `messages`, and `tools` (converted to the
plain `{"name", "description", "input_schema"}` shape) — nothing else about the run, the
organization, or the request is attached. The agent framework (Task 12) is responsible for calling
`redact_payload` on every tool result and prompt-context fragment *before* it is placed into a
message, since `app/llm` itself has no visibility into which fields of a tool's output are
sensitive. Per `structlog` conventions repo-wide (backend-contracts.md §1), full prompts and
provider keys are never logged; the adapter only ever logs (via raised error messages) the SDK
exception's class name, never its `.message` or the request body.

## Redaction

`redact_text` replaces, in order, Anthropic API keys (`sk-ant-...`), `Bearer <token>` headers,
email addresses, and phone-number-like sequences (a run of digits and phone punctuation —
space/`+`/`-`/`.`/parens — containing at least 9 digits) with `[REDACTED]`. `redact_payload`
recurses through dicts/lists/tuples and applies `redact_text` to every string leaf.

Operator aliases (e.g. `KTN-OP-017`, backend-contracts.md §2 "Industrial engineering") are
pseudonymous by design and are **not** redacted: the phone-number pattern requires the whole run to
be digits/punctuation, and an alphanumeric alias never matches it.

## Run budgets

Every `analysis_runs` row carries `model_calls_used`/`model_calls_limit` and
`tokens_used`/`token_budget` (backend-contracts.md §2 "Workflow"). `app/llm/budget.py` enforces
both atomically, independent of any application-level lock, using the same fenced-`UPDATE` pattern
as `app/jobs/queue.py`:

```sql
UPDATE analysis_runs
SET model_calls_used = model_calls_used + 1
WHERE id = :run_id
  AND model_calls_used < model_calls_limit
  AND tokens_used < token_budget
  AND status IN ('QUEUED', 'RUNNING')
RETURNING model_calls_used
```

`reserve_model_call` returns whether a row was updated; the agent loop must call it immediately
before every model call and skip the call (falling back to a deterministic/degraded path) on
`False`. Because the predicate is re-checked by Postgres against the row's current committed
values after the row lock is acquired, concurrent reservations against the same run serialize
correctly under `READ COMMITTED`: with `model_calls_limit = 12`, exactly 12 of any number of
concurrent callers succeed, never more. `record_usage` adds `input_tokens + output_tokens` to
`tokens_used` with the equivalent atomic `UPDATE`. `token_budget_remaining` is a plain read
(`token_budget - tokens_used`, floored at 0) for display/planning; it does not reserve anything.

## Fixture labelling

`LLM_PROVIDER=fixture` (`FixtureLLMClient`) never makes a network call. `default_fixture_script` is
a pure function of the request:

1. It counts `tool_result` blocks already in the conversation. While that count is below
   `min(2, number of investigative tools offered)`, it calls the next investigative tool (any tool
   other than `submit_assessment`) that has not yet appeared as a `tool_use` block, with arguments
   built from that tool's JSON Schema `default`/`examples` values.
2. Once enough investigative tools have run, it calls `submit_assessment`, selecting the
   lowest-`rank` action from the `candidate_actions` JSON array and citing every id in
   `available_evidence`. Both arrays are read from a JSON object embedded between `<context>` and
   `</context>` tags in the **first** user message (the agent framework's contract with the
   fixture); if that JSON is absent or has no candidate actions, it still calls
   `submit_assessment` with `selected_action_id=None` and `cited_evidence_ids=[]`. Every summary is
   prefixed `"[Fixture] "`.

Token counts are `len(json.dumps(...)) // 4` (input on the full message list, output on the chosen
tool call's arguments) — a cheap, deterministic stand-in, not a real tokenizer. Every
`LLMResponse` from this client carries `provider="fixture"` and `model="fixture-scripted-v1"`, and
the UI must render "Test fixture — not a live AI model" whenever it sees that provider. The same
`(system, messages, tools)` always produces the same `LLMResponse` (verified by
`tests/unit/test_fixture_client.py::test_deterministic_same_input_same_output`).

## Anthropic adapter: model selection, thinking, and the refusal fallback

`AnthropicLLMClient.complete` branches on the configured model id:

- **`claude-opus-5`**: calls `client.beta.messages.create(...)` with `betas=
  ["server-side-fallback-2026-07-01"]`, `fallbacks="default"`, `thinking={"type": "adaptive"}`, and
  `output_config={"effort": "medium"}`. The refusal-fallback feature is opt-in per current API
  guidance rather than on by default, so it is requested explicitly: on a policy decline, the API
  re-runs the same request on a fallback model inside the same call instead of the run simply
  stopping. `betas`/`fallbacks` are accepted directly as keyword arguments by the installed SDK
  (`anthropic` 1.6.0, verified by reading `anthropic/resources/beta/messages/messages.py`'s
  `create` signature) — no `extra_body` workaround is needed.
- **Any other model id**: calls the plain `client.messages.create(...)` with only
  `model`/`max_tokens`/`system`/`messages`/`tools` — no `betas`, `fallbacks`, `thinking`, or
  `output_config`, since those models may not accept adaptive thinking or the refusal-fallback
  beta.

Before reading `response.content`, the adapter checks `stop_reason`: `"refusal"` raises
`LLMRefusalError` (including the `stop_details.category`/`.explanation` when present); `"max_tokens"`
raises `LLMInvalidResponseError("truncated")`. Otherwise it converts `tool_use` blocks to
`LLMToolCall` using the block's already-parsed `input` dict (never string-matching the serialized
JSON — Anthropic models can vary escaping), concatenates `text` blocks into `LLMResponse.text`, and
copies every block (including `thinking`) into `raw_content` via the SDK's own `block.to_dict()` so
the agent loop can append the assistant turn back unmodified on the next request (thinking blocks
must be replayed verbatim). `usage.input_tokens`/`.output_tokens` and `response._request_id` are
copied onto the `LLMResponse`.

SDK exceptions are mapped most-specific-first (subclasses of `APIConnectionError` and
`APIStatusError` must be caught before their base class):

| SDK exception | LineSense error | retryable |
| --- | --- | --- |
| `RateLimitError` | `LLMRateLimitedError` (carries `retry-after`, parsed from the response header) | yes |
| `APITimeoutError`, `APIConnectionError`, `InternalServerError`, or `APIStatusError` with `status_code >= 500` | `LLMUnavailableError` | yes |
| `AuthenticationError`, `PermissionDeniedError`, `BadRequestError`, `NotFoundError`, or any other `APIStatusError` | `LLMInvalidResponseError` | no |

Every raised message is the exception's class name only (e.g. `"anthropic.AuthenticationError"`),
never `.message`/the request body, so an API key can never leak into a raised error string even by
accident.

`AsyncAnthropic` is constructed once per `AnthropicLLMClient` with `max_retries` (governing the
SDK's own retry policy for connection errors and 408/409/429/5xx); the per-call `timeout_seconds`
argument is applied per request via `with_options(timeout=...)` rather than baked into the shared
client, so a tighter deadline near a run's overall time budget does not require a new client.

## No-key / disabled status

`build_llm_client(settings)` returns `None` for `LS_LLM_PROVIDER=disabled` — callers (the
orchestrator/agents) must fall back to deterministic-only behaviour and label results "AI
explanation unavailable" (backend-contracts.md §8). It raises `ValueError` for
`LS_LLM_PROVIDER=anthropic` with no `LS_ANTHROPIC_API_KEY` configured, and for
`LS_LLM_PROVIDER=fixture` when `LS_ENVIRONMENT=production` (`app/settings.py` independently refuses
to start in that configuration; `factory.py` re-checks it because `build_llm_client` can also be
called from contexts that construct a `Settings` object directly, e.g. tests). This environment has
no Anthropic API key configured, so `AnthropicLLMClient` is exercised only against in-test stub
clients (`tests/unit/test_anthropic_client.py`) — never against the live API — and no live-LLM
success is claimed anywhere in this task's tests or report.
