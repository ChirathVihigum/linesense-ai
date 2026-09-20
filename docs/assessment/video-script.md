# Gen AI video script (3–5 minutes)

Target length **4:00**, which leaves headroom inside the 3–5 minute requirement. Section timings
follow [`LINESENSE_IMPLEMENTATION_PLAN.md`](../../LINESENSE_IMPLEMENTATION_PLAN.md) §15:
30 s problem/users · 45 s architecture · 90 s workflow · 45 s security and responsible AI ·
30 s measured results · 30 s commercialization and limitations.

---

## Rules this script follows (read before recording)

1. **Every figure spoken aloud is a figure recorded in this repository.** If a number is not in
   [`docs/IMPLEMENTATION_STATUS.md`](../IMPLEMENTATION_STATUS.md) or a committed document, it does
   not go in the video.
2. **No screenshots exist.** Browser end-to-end testing was dropped, so nothing ever captured the
   UI automatically ([`completion-matrix.md`](completion-matrix.md)). Any UI footage must be
   **screen-recorded live** by a team member against a seeded local environment, following
   [`docs/user-guide.md`](../user-guide.md) §4 to start it. Do not let a generative tool invent UI.
3. **Every agent summary on screen will read "Test fixture — not a live AI model."** Do not crop it
   out, do not blur it, and do not narrate over it as though a live model produced it. Say it
   instead — it is the strongest honesty signal in the demo.
4. **Generative video is for illustration only** — the factory scene, the diagram animations, the
   title cards. Real UI, real numbers, real terminal output are recorded, never generated.
5. Synthetic/generated shots are identifiable as such, per the assignment brief.

---

## Section 1 — Problem and users · 0:00–0:30 (30 s)

**Visual.** Generative shot: an apparel sewing floor, then a split screen of four disconnected
surfaces — a spreadsheet, an ERP screen, a stores ledger, a quality register. Label the shot
*"Illustrative — generated"* in the corner for its full duration.

**Narration (~75 words).**

> A supervisor gets one question all day: will this order ship on time, and if not, why? The answer
> lives in four places — the order book, the stores ledger, the IE time study, the quality
> register — and by the time someone has walked between them, it is out of date. LineSense AI
> answers that question in one place, with the records that prove it. Its users are planners,
> supervisors, storekeepers, IE engineers and quality managers.

**On-screen text.** `Can PO-KTN-0072 ship on time — and why not?`

## Section 2 — Architecture · 0:30–1:15 (45 s)

**Visual.** The C4 container diagram from the report (figure 2,
[`docs/report/linesense-report.tex`](../report/linesense-report.tex)), animated one box at a time:
React dashboard → FastAPI → PostgreSQL with pgvector → durable worker → four agents. Then the
orchestration graph: RM and IE in parallel, planning consuming both, quality alongside.

**Narration (~115 words).**

> One modular-monolith backend and one durable worker — no microservices, no Kafka, no Kubernetes.
> Everything persists in PostgreSQL: domain records, the job queue, run traces, the audit trail, and
> the vector index, in one store.
>
> Four agents investigate an order. Materials and industrial engineering run in parallel; planning
> consumes both of their results and proposes an allocation; quality runs alongside. They exchange
> typed tasks over a custom versioned HTTP protocol — version one-point-zero — with durable task
> ids, idempotency keys and server-verified scope. It is our own protocol; it is not A2A and it is
> not MCP.
>
> Crucially, the agents never establish facts. Stock, capacity, cycle time and quality eligibility
> are computed by deterministic code.

**On-screen text.** `4 agents · 1 database · versioned HTTP task protocol (schema_version 1.0)`

## Section 3 — The workflow, end to end · 1:15–2:45 (90 s)

**Visual.** Live screen recording, seeded demo order `PO-DEMO-001`. Shot list, in order:

| Time | Shot |
|---|---|
| 1:15 | Order detail page, order due in five days |
| 1:25 | Click **Analyse**; the run timeline appears and fills with agent events |
| 1:40 | RM finding: insufficient accepted fabric — **click an evidence link**, show the record with its version |
| 1:55 | IE finding: bottleneck operation, with its sample-size limitation visible on screen |
| 2:05 | Planning proposal: revised allocation, produced *after* the RM result |
| 2:15 | Approval inbox: the diff, source versions, impact, expiry |
| 2:25 | Approve as a **different user** — then show self-approval being denied for the proposer |
| 2:35 | Quality: a failed inspection places a hold; shipment eligibility stays **blocked** |

**Narration (~230 words).**

> The planner opens an order due in five days and starts an analysis. The worker picks it up, and
> the run timeline fills in real time.
>
> Materials reports insufficient accepted fabric. Every claim is a link — click it and you get the
> actual record, at the version the agent read. Industrial engineering identifies the bottleneck
> operation, and states its own sample-size limitation rather than hiding it.
>
> Planning then proposes a revised allocation — and it is revised *because* of the materials
> finding. That is the interaction: one agent's result changes another's proposal.
>
> Nothing is applied yet. The proposal goes to an approval inbox with a diff, the source versions,
> the impact, and an expiry. An approver reviews it and approves — and note this: the person who
> proposed it cannot approve it. Self-approval is denied. If the stock changed since the proposal
> was made, the proposal is stale and is rejected with a conflict; you have to re-analyse.
>
> Applying it locks the affected capacity and material rows in a fixed order, re-validates, and
> commits the change, the audit event and the follow-up job in one transaction.
>
> Then quality fails an inspection. A hold goes on. And watch the shipment badge — it stays
> blocked. A positive AI explanation cannot override an active hold, because shipment readiness is
> computed from the records, not chosen by a model.

**On-screen text at 2:25.** `Self-approval denied · stale proposals rejected (409)`

## Section 4 — Security and responsible AI · 2:45–3:30 (45 s)

**Visual.** Split screen. Left: the role matrix from
[`docs/security/role-matrix.md`](../security/role-matrix.md), with a cross-factory request
returning 404. Right: a terminal running the prompt-injection test file, passing.

**Narration (~115 words).**

> Login is OIDC with PKCE and opaque server sessions. Every path — the API, the agent tools,
> retrieval, citations, exports — re-checks organization and factory scope. A resource you cannot
> see returns a 404, not a 403, so the API does not confirm that it exists.
>
> Agents get read-only tools. No SQL, no shell, no URL fetching. We keep a deliberately hostile
> document in the corpus that tells the model to ignore its instructions, and we drive the real
> agent loop against it. It changes nothing.
>
> Workers are never ranked. Operators exist only as alias codes; no names, no protected attributes.
> There is no code path in this system that scores an individual.

**On-screen text.** `Read-only tools · ≤4 tool calls · ≤12 model calls · 120 s deadline`

## Section 5 — Measured results · 3:30–4:00 (30 s, first half)

**Visual.** The evaluation table on screen, with the PENDING items visibly marked.

**Narration (~70 words).**

> Honestly: entity extraction micro-F1 of 0.991 against a 0.90 target. Note classification macro-F1
> of 0.835 against 0.80. All deterministic calculation reference cases pass.
>
> Retrieval scored 0.711 against a 0.85 target — but that run used a semantics-free stand-in
> embedder, so it is not a real retrieval measurement. The full evaluation has not been run. We are
> not going to show you a number we did not measure.

**On-screen text.** `entities 0.991 ✓ · classification 0.835 ✓ · retrieval 0.711 (stand-in embedder) · full eval PENDING`

## Section 6 — Commercialization and limitations · 4:00 end (30 s, second half)

**Visual.** Pricing tiers, then a plain limitations list on a neutral background.

**Narration (~70 words).**

> Commercially: a decision-support layer for factories reconciling this by hand. Pilot at 99
> dollars per factory per month, Growth at 249 — hypotheses, not validated prices, and we have
> interviewed no customers.
>
> What we did not do: no live model has ever run — there is no API key, so every summary you saw is
> a labelled fixture. No load test. No browser end-to-end tests. No Docker deployment. It is all
> written down.

**On-screen text.** `No live LLM run · no load test · no browser E2E · no Docker deploy — see docs/assessment/completion-matrix.md`

---

## Production checklist

- [ ] Local environment seeded and running ([`docs/user-guide.md`](../user-guide.md) §3–§4)
- [ ] Screen recording captured for section 3, at a readable resolution
- [ ] The *"Test fixture — not a live AI model"* label is legible in at least one shot
- [ ] Every generated shot carries an on-screen "generated" label for its full duration
- [ ] Every spoken figure cross-checked against
      [`docs/IMPLEMENTATION_STATUS.md`](../IMPLEMENTATION_STATUS.md)
- [ ] Total runtime between 3:00 and 5:00
- [ ] Captions or subtitles included
- [ ] Contributors credited at the end, matching [`contribution-log.md`](contribution-log.md)
