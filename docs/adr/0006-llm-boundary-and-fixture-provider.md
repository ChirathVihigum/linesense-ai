# ADR-0006: LLM boundary, provider choice, and fixture provider

**Status:** Accepted — 2026-09-17

## Context

The plan requires that LLMs never establish business truth or authorize
writes — deterministic code computes stock, capacity, cycle-time, and
policy eligibility; the model explains and proposes (spec §6, §11). It also
requires a real, explicit model provider behind an `LLMClient` interface,
with model/prompt versions recorded, while never claiming a live-provider
result that was not actually produced (spec §2, §17). **This development
environment has no Anthropic API key configured**, so no live-provider call
can be made or claimed here.

## Decision

Define `LLMClient` as a narrow protocol (`app/llm/`,
`docs/architecture/backend-contracts.md` §8: `complete(system, messages,
tools, max_tokens, timeout_seconds) -> LLMResponse`) with three
implementations selected by `LS_LLM_PROVIDER`:

- `anthropic` — the real provider, default model `claude-opus-5`
  (`LS_ANTHROPIC_MODEL`). Every Anthropic request includes the server-side
  refusal-fallback parameters (`fallbacks: "default"` with beta
  `server-side-fallback-2026-07-01`) so a safety-classifier refusal is
  automatically retried against a fallback model rather than surfacing as a
  bare failure; the response's `provider`/`model` fields always record what
  actually served the request.
- `fixture` — a deterministic, scripted client used in tests and CI. Every
  result it produces is labelled `provider="fixture"`; the UI renders "Test
  fixture — not a live AI model". Forbidden in production (enforced by
  `Settings`' production validator).
- `disabled` — produces deterministic-only findings labelled "AI
  explanation unavailable"; used to prove the system degrades safely
  without a provider rather than fabricating a plausible-looking result.

Agents call tools that read/compute over deterministic domain code
(`app/domain/`); the model chooses among permitted tool calls and drafts
explanations/rationale, but numeric findings, eligibility, and lifecycle
transitions are always computed in code and validated
(`app/orchestration/validation.py`) before being trusted, regardless of
provider. No API key is available in this environment, so
`LS_LLM_PROVIDER=fixture` is the only provider actually exercised here; no
live-provider success is or will be claimed until a key is available and a
run is recorded.

## Consequences

- All agent contract tests and CI runs are reproducible without network
  access or a paid API key, using the fixture provider.
- The moment a key is provisioned, switching to `anthropic` requires no
  code change — only `.env` configuration — because agents/orchestrator
  code depends only on the `LLMClient` protocol.
- Refusal fallback means a classifier refusal degrades to a fallback model
  automatically rather than failing the whole agent task; the executed
  model is always recorded in `execution_metadata` so this is auditable,
  never silently substituted without a trace.
- Every screen/report that shows AI-derived content must be able to show
  which of the three providers actually produced it; conflating fixture
  output with a live run is treated as a defect, not a formatting detail.

## Alternatives considered

- **Mock the LLM inline in agent code (no `LLMClient` abstraction)**:
  rejected — makes it impossible to swap providers without touching agent
  logic, and blurs the fixture/live distinction the plan requires to stay
  explicit.
- **Skip a fixture provider and require a real key for all tests**:
  rejected — no key exists in this environment; this would block all agent
  and orchestration testing entirely.
- **Silently fall back to a canned response on provider error instead of a
  labelled `disabled`/degraded state**: rejected — the plan explicitly
  forbids "success-looking fallback" text presented as a real model run.
