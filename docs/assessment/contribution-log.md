# Contribution log

**This file is a template. It contains no names, because inventing them would be fabrication.**
Each team member fills in their own rows before submission; the viva (Week 11, 20 marks) requires
each member to explain their own code, decisions, protocols and results, so a row here is a
commitment to being able to do that.

Referenced by [`README.md`](../../README.md) ("Contributors") and by
[`docs/report/linesense-report.tex`](../report/linesense-report.tex)'s title page.

---

## 1. Members and ownership

Fill one row per member. "Area owned" should match the plan's suggested split
([`LINESENSE_IMPLEMENTATION_PLAN.md`](../../LINESENSE_IMPLEMENTATION_PLAN.md) §14): frontend and
planning UX; API, data, auth and workflow; RM/planning agents plus IR and NLP; IE/quality plus
evaluation and delivery.

| Name | Student ID | Area owned | Reviews another member's | Contact |
|---|---|---|---|---|
| _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
| _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
| _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
| _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |

## 2. Per-member contribution record

Copy this block once per member. Keep it specific: "worked on the backend" is not a viva answer,
"implemented the lease-fencing path in `app/jobs/queue.py` and the worker-kill resilience test" is.

### Member: _TBD_

**Modules owned**

| Path | What it does | Tests you own |
|---|---|---|
| _TBD_ | _TBD_ | _TBD_ |

**Documents owned**

| Document | Your part |
|---|---|
| _TBD_ | _TBD_ |

**Commits.** Reconstruct with:

```bash
git log --author="<your email>" --oneline -- <paths you own>
git log --author="<your email>" --shortstat --since="<project start>"
```

Paste the resulting commit subjects (or a count plus the notable ones) here. Do **not** claim a
commit you did not author — `git log` is checkable and the mismatch is worse than a thin row.

**Reviews you performed.** Which member's work, what you found, what changed as a result.

**Decisions you made and can defend.** Name the ADR or the code path. If you argued for something
that was rejected, record that too — a viva answer that includes a considered alternative is
stronger than one that does not.

**What you could not finish, and why.** Honest gaps belong here, not hidden.

---

## 3. Shared work

Some work has no single owner. Record who did what:

| Item | Who | Notes |
|---|---|---|
| Requirements and ADRs | _TBD_ | |
| Database schema and migration | _TBD_ | |
| Agent protocol design | _TBD_ | |
| Evaluation harness | _TBD_ | |
| Report and diagrams | _TBD_ | |
| Video production | _TBD_ | |
| AI coding assistant supervision | _TBD_ | See [`ai-assistance-log.md`](ai-assistance-log.md) |

## 4. Declaration on AI assistance

An AI coding assistant was used extensively in building this project, under human direction and
review. That is disclosed in full in [`ai-assistance-log.md`](ai-assistance-log.md), and it does
not reduce anyone's obligation to understand the result.

Each member signs the same statement:

> I can explain the code in the modules listed against my name — what it does, why it is built that
> way, what it does not handle, and how it is tested — without reference to notes.

| Name | Date | Signed |
|---|---|---|
| _TBD_ | _TBD_ | _TBD_ |
| _TBD_ | _TBD_ | _TBD_ |
| _TBD_ | _TBD_ | _TBD_ |
| _TBD_ | _TBD_ | _TBD_ |

## 5. Viva preparation checklist

Per member, before Week 11:

- [ ] Can walk through one end-to-end request in owned code, from route to database and back
- [ ] Can explain one concurrency or failure path in owned code and the test that proves it
- [ ] Can state one limitation of owned code without prompting
- [ ] Has read [`completion-matrix.md`](completion-matrix.md) and can say what is *not* verified
- [ ] Can answer "did you call a real LLM?" correctly: **no — no API key; the fixture provider is
      labelled everywhere**
- [ ] Has rehearsed the demo at least once on a cold environment
