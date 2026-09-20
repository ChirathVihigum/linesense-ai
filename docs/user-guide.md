# LineSense AI — user guide

This guide covers every screen and workflow in LineSense AI as the
application is actually built in this repository. It is written for the
local development deployment (the only one that has ever been run — see
[Limitations](#14-limitations-you-will-meet-while-following-this-guide)).

**No screenshots.** `docs/assessment/screenshots/` does not exist and this
guide contains no images. Browser end-to-end automation (Task 24 of the
build plan) was **dropped by the user**, and with it the scripted
screenshot capture that would have produced them. Every screen below is
therefore described in words, naming the exact on-screen labels, buttons
and states from the source under `apps/web/src/features/`. Anyone
preparing the assignment video or report should capture screenshots
manually by following section 4 onwards.

Contents:

1. [What LineSense does](#1-what-linesense-does)
2. [Prerequisites](#2-prerequisites)
3. [First-time setup](#3-first-time-setup)
4. [Starting the application](#4-starting-the-application)
5. [Signing in and choosing a factory](#5-signing-in-and-choosing-a-factory)
6. [Roles: what each one can do](#6-roles-what-each-one-can-do)
7. [The shell: navigation, notifications, source labels](#7-the-shell-navigation-notifications-source-labels)
8. [Screen reference](#8-screen-reference)
9. [Workflow A — planner: create an order and analyse it](#9-workflow-a--planner-create-an-order-and-analyse-it)
10. [Workflow B — supervisor: review and apply a recommendation](#10-workflow-b--supervisor-review-and-apply-a-recommendation)
11. [Workflow C — storekeeper: receive, accept, issue, reserve](#11-workflow-c--storekeeper-receive-accept-issue-reserve)
12. [Workflow D — quality manager: inspect, hold, release](#12-workflow-d--quality-manager-inspect-hold-release)
13. [Workflow E — everyone else: IE, notes, knowledge base, administration](#13-workflow-e--everyone-else-ie-notes-knowledge-base-administration)
14. [Limitations you will meet while following this guide](#14-limitations-you-will-meet-while-following-this-guide)

---

## 1. What LineSense does

LineSense AI is a decision-support application for apparel factory
operations. It answers, for one production order: **can this order finish
on time, what is blocking it, what evidence supports that, and what should
we do next?**

Four bounded AI agents — **planning**, **RM** (raw materials /
supermarket), **IE** (industrial engineering / cycle time) and
**quality** — investigate an order. Each agent computes its findings with
deterministic Python (`app/domain/`), and only then may a language model
write the explanation, pick one of the offered actions, and cite evidence
that already exists. A model can never change a number, a state or a
verdict.

Anything an agent proposes is a **recommendation**, never a change. A
supervisor who is not the person who asked for the analysis must approve
it, and then apply it in a transaction that re-checks every input version
before writing anything.

The system recommends. It does not run machines, place purchase orders, or
authorise shipments.

---

## 2. Prerequisites

| Requirement | Version used here | Install |
|---|---|---|
| macOS or Linux with a POSIX shell | — | — |
| PostgreSQL | 16 (Homebrew `postgresql@16`, 16.15) | `brew install postgresql@16` |
| pgvector | 0.8.6, compiled into that PostgreSQL install | see below |
| uv (Python package manager) | 0.11.17+ | `brew install uv` |
| Python | 3.12 (pinned by `services/backend/pyproject.toml`) | installed by `uv` |
| Node.js | 26 (`apps/web/.nvmrc`); Node ≥ 20 works for the dev server | `brew install node` |

Building pgvector into the Homebrew PostgreSQL 16 install:

```bash
git clone --branch v0.8.6 https://github.com/pgvector/pgvector.git
cd pgvector
make PG_CONFIG=$(brew --prefix postgresql@16)/bin/pg_config
make install PG_CONFIG=$(brew --prefix postgresql@16)/bin/pg_config
```

Docker is **not** required for local development. Compose, Caddy and
Keycloak files exist under `infra/` but have never been run in this
environment (`docs/operations/deployment.md`).

---

## 3. First-time setup

From the repository root:

```bash
make bootstrap    # init the project-local PostgreSQL cluster, copy .env.example -> .env, uv sync
make migrate      # apply the database schema (one Alembic revision, 0001_initial_schema)
make web-install  # npm ci in apps/web
make seed         # load the deterministic synthetic demo dataset + the 30-document SOP corpus
```

What each one does:

- **`make bootstrap`** runs `scripts/dev-db.sh init`, which creates a
  *project-local* PostgreSQL cluster under `.local/pgdata` listening on
  **127.0.0.1:55432** (never your Homebrew default cluster, never
  `brew services`). It creates the roles `linesense_owner` (DDL/migrations)
  and `linesense_app` (DML only), the databases `linesense_dev`,
  `linesense_test` and `linesense_eval`, and the `vector` and `pg_trgm`
  extensions in each. It then copies `.env.example` to `.env` if you do
  not have one, and runs `uv sync` in `services/backend`.
- **`make migrate`** runs `alembic upgrade head` against
  `LS_MIGRATION_DATABASE_URL` (the owner role).
- **`make seed`** runs `python -m app.seed --with-documents`. It is
  idempotent (a second run prints `{"created": false, ...}` and writes
  nothing) and never deletes or truncates. It refuses to run when
  `LS_ENVIRONMENT=production` (exit code 2).

The seeded dataset is entirely synthetic
(`docs/evaluation/synthetic-data.md`): 1 organization (`Demo Apparel
Group`), 2 factories (`KTN` Katunayake Plant, `BYG` Biyagama Plant), 10
users, 26 customers, 12 styles, 20 materials, 9 lines, 540 capacity slots,
101 orders, ~600 stock movements, 720 cycle observations, 1 quality policy
version (`QP-DEMO`), and the `PO-DEMO-001` walkthrough order.

> **First `make seed --with-documents` downloads an embedding model.**
> `fastembed` fetches `BAAI/bge-small-en-v1.5` (~100 MB) into
> `.local/models` on first use. This is a deliberate one-off network
> fetch. It has **not** been performed in this environment (the machine
> heat policy blocked it), so the document/search screens have not been
> exercised against a real embedder here. Set `LS_EMBEDDER=hashing` to
> skip the download — but the hashing embedder is a bag-of-words stand-in
> with no semantics and gives poor vector search results
> (`docs/architecture/retrieval.md`).

Check `.env` before starting. The development defaults are correct for a
local run; the ones you may want to change are:

| Variable | Default | Meaning |
|---|---|---|
| `LS_LLM_PROVIDER` | `fixture` | `fixture` = deterministic test double, never a network call. `anthropic` = the real provider (needs `LS_ANTHROPIC_API_KEY`). `disabled` = deterministic only, every agent result is labelled "AI explanation unavailable". |
| `LS_ANTHROPIC_MODEL` | `claude-opus-5` | Only used when the provider is `anthropic`. |
| `LS_EMBEDDER` | `fastembed` | `fastembed` (real, downloads a model) or `hashing` (test stand-in). |
| `LS_DEV_IDP_PASSWORD` | `demo-password` | The shared password for all ten demo identities in the development identity provider. |

---

## 4. Starting the application

Four processes. Use four terminals, all from the repository root.

```bash
# 1. Database (idempotent; skip if it is already running)
make db-start

# 2. Development OIDC identity provider  ->  http://127.0.0.1:8090
make idp

# 3. Backend API  ->  http://127.0.0.1:8000
cd services/backend && uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000

# 4. Frontend dev server  ->  http://localhost:5173
make web-dev
```

Optionally, a fifth terminal for the durable worker. **You need it**: the
analysis workflow, document processing and maintenance all run as
background jobs, and without a worker an analysis run stays `QUEUED`
forever.

```bash
# 5. Worker (orchestrator + agents + document processing + maintenance)
make worker
# equivalently: cd services/backend && uv run python -m app.jobs \
#   --queues orchestrator,agent,document,maintenance --concurrency 4
```

Open **<http://localhost:5173>**. The Vite dev server proxies `/api` and
`/auth` to `127.0.0.1:8000`, so the browser only ever talks to one origin
and the session cookie stays same-origin.

There is no `make dev` target that starts everything at once.

Health checks, if something looks wrong:

```bash
curl http://127.0.0.1:8000/api/health/live     # {"status":"ok"}
curl http://127.0.0.1:8000/api/health/ready    # also checks the DB and reports the pgvector version
curl http://127.0.0.1:8090/.well-known/openid-configuration   # the dev IdP's discovery document
```

Stop everything with `Ctrl-C` in each terminal (the worker shuts down
gracefully, giving running handlers up to 20 s), then `make db-stop` if
you want the cluster down too.

---

## 5. Signing in and choosing a factory

1. Visiting `http://localhost:5173` while signed out redirects to
   **`/login`**.
2. The login page has a single **Sign in** action, which sends you to
   `GET /auth/login` on the backend. The backend starts an OIDC
   authorization-code flow with PKCE, `state` and `nonce`, and redirects
   you to the identity provider.
3. In development that provider is `devtools/dev_oidc` — a local,
   development-only OIDC server that shows a banner saying so, a **user
   dropdown** listing the ten demo identities, and a password field. Pick
   an identity and enter the password from `LS_DEV_IDP_PASSWORD`
   (`demo-password` by default). The provider refuses to start at all
   unless `LS_ENVIRONMENT` is `development` or `test` (exit code 2).
4. The backend exchanges the code, validates the issuer, audience,
   signature, nonce and lifetime, upserts the user by
   `(issuer, subject)`, **rotates the session**, writes an `auth.login`
   audit event, and sets an opaque `ls_session` cookie. The cookie holds
   only an opaque identifier — no profile data, no provider tokens, which
   stay server-side.
5. You land on `/` and are redirected to
   `/f/<FACTORY_CODE>/overview` for the first factory you hold a role in.
   If you hold no role anywhere you get the **`/no-access`** page instead.

**The demo identities** (all share `LS_DEV_IDP_PASSWORD`):

| Sign-in name | Display name | Role | Factory |
|---|---|---|---|
| `admin@demo.test` | Org Admin (All plants) | `org_admin` | organization-wide |
| `supervisor@demo.test` | Supervisor (KTN) | `supervisor` | KTN |
| `supervisor.b@demo.test` | Supervisor B (KTN) | `supervisor` | KTN |
| `planner@demo.test` | Planner (KTN) | `planner` | KTN |
| `storekeeper@demo.test` | Storekeeper (KTN) | `storekeeper` | KTN |
| `ie@demo.test` | IE Engineer (KTN) | `ie_engineer` | KTN |
| `quality@demo.test` | Quality Manager (KTN) | `quality_manager` | KTN |
| `quality.b@demo.test` | Quality Manager B (KTN) | `quality_manager` | KTN |
| `viewer@demo.test` | Viewer (KTN) | `viewer` | KTN |
| `byg.planner@demo.test` | Planner (BYG) | `planner` | BYG |

There are **two** supervisors and **two** quality managers on purpose:
separation of duties needs a second person. The proposer of an analysis
can never approve or apply the resulting recommendation, and a quality
manager can never release an order against their own inspection.

> These identities exist only in development and test. They are seeded by
> `app/seed/identities.py` and mirrored into the dev IdP's `users.json`
> (a unit test keeps the two in sync). A real deployment points
> `LS_OIDC_ISSUER` at a real identity provider.

**Switching factory.** The header has a factory dropdown listing every
factory you hold a role in. Choosing another factory keeps you on the same
section (`/f/BYG/orders` instead of `/f/KTN/orders`) but not on the same
record — record ids belong to one factory. Users with a role in only one
factory still see the selector, with one option.

**Signing out.** The user menu (your display name and roles, top right)
has a **Sign out** action. It sends `POST /auth/logout` with the CSRF
token, which revokes the server session and audits `auth.logout`. An
expired session shows up as a `401` on the next request, and the app
redirects you to `/login?next=<where you were>` so you return to the same
screen after signing in again.

---

## 6. Roles: what each one can do

Roles are **additive, explicit permissions**. Nothing is implied: an
`org_admin` manages memberships and configuration and has *no* business
authority — it cannot approve a recommendation, transition an order, or
write inventory, IE or quality records.

The authoritative, generated matrix is
[`docs/security/role-matrix.md`](security/role-matrix.md) (produced from
`app/auth/policy.py` by `scripts/gen_role_matrix.py`). In summary:

| Role | Can do |
|---|---|
| `viewer` | Read everything in their factory: orders, materials, capacity, IE, quality, documents, analyses. No writes at all. |
| `planner` | Everything `viewer` can, plus: create orders, import orders from CSV, transition orders, **start an analysis run**, add notes. |
| `storekeeper` | Reads, plus: record material receipts, accept lots, record issues and corrections, create and release reservations, add notes. |
| `ie_engineer` | Reads, plus: record cycle observations, mark an observation an outlier, upload documents, add notes. |
| `quality_manager` | Reads, plus: record inspections, place holds, record releases, upload documents, add notes. |
| `supervisor` | Reads, plus: everything `planner` can, **decide** (approve/reject) and **apply** recommendations, cancel and dispatch orders, read the audit log, upload documents. |
| `org_admin` | Membership and role administration, audit log, quality policy list, effective run limits. Plus the all-roles read set. |

Two permission outcomes you will see in the UI:

- **404 Not found** — the resource is in a factory you hold no role in.
  Existence is never revealed.
- **403 Forbidden** — the resource is in a factory you can reach, but your
  roles do not carry the required permission. The screen shows a
  permission-denied state rather than an empty table.

Navigation is filtered the same way: a `viewer` never sees the
**Administration** section, a non-storekeeper never sees the stock
movement forms, and so on.

---

## 7. The shell: navigation, notifications, source labels

Every signed-in screen sits inside one layout:

- A **skip link** ("Skip to main content") as the first focusable element.
- A left **sidebar** with sections, each shown only if you hold the
  permission to read it: Overview, Orders (All orders / New order /
  Import orders), Planning (Planning board), Approvals (Approval inbox),
  Materials, Industrial engineering (Line balance), Quality (Inspections
  and holds), Notes, Knowledge base (Documents and search), Administration.
  The sidebar collapses below 1024 px into a toggle.
- A **header** with the factory selector, the notifications bell, and the
  user menu (display name, roles, Sign out).
- The **notifications menu** shows an unread badge, lists your
  notifications (material shortages, quality holds, completed analyses,
  proposals you should look at), and lets you mark one read. Notifications
  that point at an order or a recommendation link to the real screen;
  anything else is shown as plain text rather than a dead link.

**Source labels are the most important UI convention in the product.**
Wherever a value could plausibly have come from a model, the screen says
where it actually came from:

| Label | Meaning |
|---|---|
| `Calculated from records` | Deterministic Python over database rows. Never a model. |
| `AI recommendation` | Written by the configured model, and only ever an explanation or a ranking — never a number or a verdict. |
| `Test fixture — not a live AI model` | `LS_LLM_PROVIDER=fixture`. A deterministic scripted stand-in, no network call. **This is what you will see on every run in this repository**, because no API key has ever been configured here. |
| `AI disabled — deterministic results only` | `LS_LLM_PROVIDER=disabled`. |
| `Pending human approval` / `Approved by …` | Recommendation status. |

Other shared states you will meet: loading skeletons, empty states with an
explanation, validation errors next to the field, a permission-denied
panel, a **stale-data banner** ("inputs changed — run a new analysis"), a
**degraded-AI banner**, and an error state that shows the server's
`trace_id` so a failure can be found in the logs.

Every write in the application is a real server round-trip. Nothing about
stock, approvals, releases or allocations is optimistically applied in the
browser; the UI only updates from the server's response, and duplicate
submission is disabled in the UI *and* rejected on the server by an
`Idempotency-Key`.

---

## 8. Screen reference

### 8.1 Overview (`/f/:factory/overview`)

The landing screen. Six cards, all computed in deterministic SQL (six
statements per request — there is a test that keeps it at six), with an
"as of" freshness timestamp:

| Card | Contents |
|---|---|
| **Orders at risk** | Orders whose material or quality state puts the due date at risk, with a link to the orders list. |
| **Material shortages** | Demand from open `VALIDATED`/`PLANNED` orders' BOM lines against `available_now`, plus materials below their reorder point. Links to Materials. |
| **Quality holds** | Active holds. Links to Quality. |
| **Active analysis runs** | Runs currently `QUEUED`/`RUNNING`. (No dedicated list screen; the count is informational.) |
| **Pending approvals** | Recommendations awaiting a decision. |
| **Capacity, next 7 days** | Per-line utilization with a bar *and* the numeric percentage. Links to the Planning board. |

No LLM is called when a dashboard card is opened.

### 8.2 Orders (`/f/:factory/orders`)

A server-paginated table with a debounced search box, three state filters
(production / material / quality), a due-before date filter, a sort
indicator, and a shipment-eligibility column. Each order shows separate
state badges — production, materials and quality are **different
concerns** and are never merged into one status.

State vocabularies:

- Production: `DRAFT → VALIDATED → PLANNED → IN_PRODUCTION →
  PRODUCTION_COMPLETE → DISPATCHED` (with explicit cancellation
  transitions).
- Materials: `UNKNOWN | READY | AT_RISK | SHORTAGE`.
- Quality: `NOT_INSPECTED | PENDING | HOLD | RELEASED`.
- Analysis (per run): `QUEUED | RUNNING | AWAITING_REVIEW | COMPLETED |
  DEGRADED | FAILED | CANCELLED`.

### 8.3 New order (`/f/:factory/orders/new`) — `order:create`

A validated form (customer, style, active BOM version, quantity, due date,
priority, optional external reference). Client validation mirrors the
server contract; server field errors are mapped back onto the right
fields; the submission carries an `Idempotency-Key` that is reused for an
unchanged retry, so a double-click or a network retry cannot create two
orders. On success you go straight to the new order's detail page.

### 8.4 Import orders (`/f/:factory/orders/import`) — `order:import`

Three steps: choose a `.csv` file (≤ 1 MB) → the server validates it and
returns a **preview** (the first 20 rows plus a per-row error list) → you
explicitly **commit**. The commit re-runs the exact same validation the
upload did. Either the whole batch commits or nothing does: a commit-time
failure returns `409` and writes no orders and no batch status change.
Row errors name the physical file line number. Cells that begin with a
spreadsheet formula character are neutralised, never stored raw.

### 8.5 Order detail (`/f/:factory/orders/:orderId`)

The header shows the order reference, all three state badges, shipment
eligibility, a due-date countdown in the factory's timezone
(`Asia/Colombo` by default), the allowed lifecycle transition buttons
(each behind a confirmation dialog that sends `expected_version`), and —
for a `planner`/`supervisor` — the **Start analysis** button. If someone
else changed the order since you loaded it, the transition returns
`409 STALE_INPUT` and a stale banner appears with a reload action.

Eight tabs:

| Tab | Contents |
|---|---|
| **Overview** | The canonical order report from the latest finalized analysis run: states, shipment eligibility, blockers (critical findings first), one summary per agent with its own source label, and a link to the recommendation. A `stale` banner appears when the order has changed since the report was computed. |
| **Plan** | The order's active allocations (line, date, shift, standard minutes, units). Empty until an approved allocation has been applied. |
| **Materials** | BOM demand against the order quantity, and the order's reservations. On-hand/available figures live on the Materials screen, which this tab links to. |
| **IE** | Bottleneck and line-balance figures for the order's style. |
| **Quality** | Inspections, defects, active holds, and the shipment-eligibility reasons. |
| **Evidence** | Every evidence reference the run's agents cited — records, calculations and document citations — in one list. Opening a document citation fetches it through `GET /api/v1/citations/{chunk_id}`, which re-checks your authorization. Chunk text is rendered as plain text, never as HTML. |
| **Runs** | Every analysis run for this order, newest first, linking to the run timeline. |
| **History** | The order's audit events with the acting user's display name. |

### 8.6 Run timeline (`/f/:factory/runs/:runId`)

A live view of one analysis run. While the run is `QUEUED` or `RUNNING`
the page polls `GET /runs/{id}` and `/runs/{id}/events` every 2 seconds
and stops as soon as the run is terminal.

It shows the LLM identity label for the run, and a chronological timeline:
`run.created`, `run.started`, one `task.dispatched` per agent task (with a
collapsible pretty-printed view of the actual protocol envelope — the
versioned JSON message the orchestrator sent), `task.completed` with a
summary of the reply, a highlighted `orchestrator.replan` event carrying
its reason string, and `run.finalized`. Per-agent cards show status,
summary with its source, findings (severity-coded), metrics with units,
and the cited evidence.

`Cancel` and `Retry` are available to holders of `analysis:run`. Cancelling
a run also cancels its pending tasks and supersedes its open
recommendations.

### 8.7 Planning board (`/f/:factory/planning`) — `capacity:read`

A date-range picker (capped at 31 days) drives one table per production
line: dates across, shifts A/B down, each cell showing capacity, allocated
and remaining standard minutes plus a utilization bar **and** its numeric
value. Slots at or above 95 % utilization carry a "High utilization" flag
with an icon *and* text. Allocation chips link to the order they belong to.

Below the grid, the **recommendation comparison** panel lists this
factory's `PROPOSED` allocation recommendations side by side so a
supervisor can compare options before opening one.

### 8.8 Approval inbox (`/f/:factory/approvals`) — `analysis:read`

A segmented control switches between `PROPOSED` and `APPROVED`
recommendations. Columns include the order, the kind of proposal, who
proposed it, its status, and an expiry countdown (with the absolute time on
hover). Opening a row goes to the review screen.

### 8.9 Recommendation review (`/f/:factory/approvals/:recId`)

The screen for the single most consequential action in the product.

- **Before/after diff tables** for every line of the proposal: which
  capacity slots would be allocated, and which material balances would be
  reserved, with current and proposed values.
- The **evidence** the selected actions cited, openable like anywhere else.
- **Input versions** — the exact order/slot/balance versions the proposal
  was derived from.
- **Banners** for `stale` (an input changed) and `expired` (past
  `expires_at`), each with a "Run new analysis" link.
- **Approve / Reject / Apply** buttons whose enabled state comes entirely
  from the server's own `can_decide`/`can_apply` flags and their
  `decide_blocked_reason`/`apply_blocked_reason` strings. The UI never
  re-derives the rule. If you are the person who requested the analysis,
  the buttons are hidden with the explanation that a proposer cannot
  approve their own proposal.
- **Reject requires a reason**; the form refuses to submit without one and
  never calls the server.
- Every decision and apply sends the `proposal_hash` you were shown, so a
  proposal that changed under you is refused with `409 CONFLICT`.
- Apply sends one `Idempotency-Key` per attempt. A `409 STALE_INPUT`
  response renders the server's per-input `field_errors` as an explicit
  "these inputs changed" list, and the page refetches so the status badge
  updates to `SUPERSEDED` immediately.

**Approving does not apply anything.** They are two separate acts.

### 8.10 Materials (`/f/:factory/materials`) — `inventory:read`

The overview table lists, per material: on hand (accepted), reserved,
available now, open expected receipts, average daily consumption, coverage
days (rendered **"Unknown"**, never `0`, when consumption is zero or
unknown), the reorder point, and a below/above-reorder-point badge with an
icon and text. The whole table is labelled `Calculated from records`.

Selecting a material opens the **ledger drawer**: a paginated, non-modal
panel listing every stock movement — type, lot, signed quantity, reason,
actor, and (for a correction) the movement it corrects.

**Stock movement forms** (storekeeper only, gated on `inventory:write`)
have four tabs: **Receipt**, **Accept lot**, **Issue**, **Correction**.
Every write carries an `Idempotency-Key`; the UI updates only from the
server's response; a `409` is shown inline. There is no "list lots"
endpoint, so the lot pickers are built from the distinct lots actually
observed in that material's ledger.

The **reservations** table lists the factory's reservations with their
status (`ACTIVE`/`RELEASED`/`CONSUMED`) and a storekeeper **Release**
action behind a confirmation dialog.

### 8.11 Industrial engineering (`/f/:factory/ie`) — `ie:read`

Choose a line and a style. The page then shows the analysis for that
pairing:

- A **bottleneck bar chart** (per operation, effective cycle seconds) that
  fills the bottleneck bar differently and suffixes its axis label with
  "(bottleneck)". The chart carries a text caption — e.g. *"Bottleneck:
  OP-04 sleeve set, 60.0 s effective, ≈ 60 units/hour"* — and an
  accessible label, so the figure is never chart-only.
- A **sample table** giving the same data as text, with an "Insufficient
  samples" marker (icon and text) for any operation with fewer than 3
  recent observations. Such an operation is **excluded and reported**,
  never estimated.
- The explicit **assumptions** list, and observed throughput compared
  against SAM-based capacity *separately* — the two are never mixed.

The **line balance index** shown here is this model's index
(`sum(effective cycles) / (operation count × bottleneck cycle) × 100`),
labelled as such and not as a universal industrial KPI.

**Recording an observation** (IE engineers only): pick line, style,
operation and an **operator alias code** — the form has no name field
anywhere, because the API never exposes one. Newly recorded observations
appear in a session-local list with a "mark as outlier" action that
requires a non-blank reason.

### 8.12 Quality (`/f/:factory/quality`) — `quality:read`

Left: the factory's **active holds**, each row naming the order. Right,
once an order is selected (from a hold or via order search):

- **Inspection form** (`quality:inspect`): inspection type, inspected
  units, defective units (validated client-side to be ≤ inspected units,
  mirroring the server), and a dynamic defect-row list (catalogue code,
  severity, count). After submit it shows the server's **deterministic**
  result: `PASS`, `FAIL` or `INSUFFICIENT_SAMPLE`. A `FAIL` automatically
  places an `ACTIVE` hold, created by the system with the failing reasons.
- **Defect trend chart**: a 30-day by-code bar chart with a text summary
  and a data-table alternative.
- **Release review** (`quality:release`): the latest `FINAL` inspection,
  the policy's own label string — **"Demo policy — not a certified AQL
  standard"** — the shipment-eligibility reasons, and the Release action.
  Release is disabled with an explanation when you are the person who
  recorded that inspection (and the server independently returns `403
  SELF_APPROVAL_DENIED` if you try anyway).

`shipment_ready` is **derived**, not stored and not chosen by a model:
production complete **and** the required current inspections satisfied
**and** no active hold **and** packing/quantity records complete **and**
an authorized quality release recorded. Zero inspections means *unknown*,
never *pass*. Dispatch stays a separate, human-recorded event.

### 8.13 Notes (`/f/:factory/notes`) — `note:create`

A free-text box (3–2000 characters, counter mirrored client-side). On
submit the server:

1. **Classifies** the note into `planning | materials | ie | quality |
   unknown` (TF-IDF + logistic regression trained from the project's own
   labelled training split) and shows the classification with the
   classifier version.
2. **Extracts entities** — order references, line codes, style codes,
   material codes/names, operation and defect categories — grounded in
   *this factory's* authorized master data. Resolved entities become
   chips: `ORDER` links to the order detail, `LINE` to the planning board,
   `MATERIAL` to the Materials screen. Everything else is a plain chip.
3. Lists **unresolved** order references (well-formed but unknown, e.g.
   `PO-KTN-9999`) as text — never as a link, never created as a record.
4. Shows the fixed notice: *"Entity links are for navigation only; they do
   not authorize any action."*

Below, a classification-filtered, paginated list of recent notes.

### 8.14 Knowledge base (`/f/:factory/knowledge`) — `document:read`

- **Document list** with a status badge per version (`QUARANTINE`,
  `PROCESSING`, `ACTIVE`, `SUPERSEDED`, `REJECTED`) and the rejection
  reason when there is one. The version list polls while anything is still
  processing.
- **Upload** (`document:upload` — IE engineer, quality manager,
  supervisor, org admin): `.pdf`, `.md` or `.txt`, ≤ 10 MB, with a
  progress bar. An organization-wide (rather than factory-scoped) upload
  is only offered if you are an `org_admin` or `supervisor`. The file goes
  to quarantine first and only becomes searchable after processing
  succeeds. Encrypted or scanned/image-only PDFs, and PDFs containing
  active content (`/JavaScript`, `/Launch`, `/EmbeddedFile`, …), are
  **rejected with a reason** — this project has no OCR and no antivirus
  engine.
- **Search** with a mode selector (`lexical` / `vector` / `hybrid`).
  Hybrid fuses a lexical `tsvector` ranking and a pgvector cosine ranking
  by reciprocal-rank fusion. Every result shows a literal-text excerpt and
  an "open citation" action; the citation endpoint re-checks organization,
  factory, document ACL and version status before returning anything.

### 8.15 Administration (`/f/:factory/admin`) — `admin:manage` (org admin only)

Four tabs:

| Tab | Contents |
|---|---|
| **Audit log** | The factory's audit events with filters and pagination: actor, action, target, outcome (`SUCCESS`/`FAILED`/`DENIED`), reason, trace id. |
| **Memberships** | Users, their role assignments, and grant/revoke/deactivate actions, each behind a confirmation dialog. Granting a role that already exists is a no-op, not a duplicate. Revoking the organization's **last** `org_admin` assignment is refused (`409`). Deactivating yourself is refused (`409`). |
| **Quality policies** | The quality policy versions with a "demo policy" pill on any policy flagged as such. |
| **Settings** | A read-only definition list of the effective run limits (tool calls, model calls, replans, deadline) and the configured LLM provider/model label. |

**Every membership or role change immediately revokes every active
session of the affected user** — not just the caller's. The next request
that user makes gets a `401` and they must sign in again with their new
permissions.

---

## 9. Workflow A — planner: create an order and analyse it

Sign in as **`planner@demo.test`** (KTN).

1. **Create the order.** Sidebar → Orders → **New order**. Choose a
   customer and style, enter a quantity and a due date, submit. You land
   on the new order's detail page in state `DRAFT`.
   *Or* use the seeded walkthrough order **`PO-DEMO-001`** (customer
   `C07`, style `ST-03`, quantity 1000, due anchor+5 days), which was
   designed to reproduce the plan's reference numbers exactly: material
   `M01` has 1500 accepted metres with 400 already reserved, the BOM needs
   1.2 m/unit with 5 % wastage, so `available_now = 1100`,
   `gross_demand = 1260` and the **shortage is 160 metres** — and line
   `L2`'s operation `OP-04` (sleeve set) is deterministically the
   bottleneck at exactly 60 s effective.
2. **Validate it.** On the detail header, use the lifecycle transition
   button to move `DRAFT → VALIDATED`. The confirmation dialog sends the
   order version you were shown; if someone changed the order meanwhile
   you get a stale banner instead of a silent overwrite.
   Note: `VALIDATED → PLANNED` is deliberately **not** available here —
   planning is applied only through an approved recommendation.
3. **Start the analysis.** Press **Start analysis**. The server checks
   your `analysis:run` permission, locks the order, refuses if the order
   version you hold is stale (`409 STALE_INPUT`), if the order is
   cancelled or dispatched (`409 INVALID_TRANSITION`), if this order
   already has an active run (`409 CONFLICT`, naming it), or if the
   factory already has more than five active runs (`429 RATE_LIMITED`).
   Otherwise it creates the run **and an immutable snapshot of every input
   it will reason about** in one transaction and returns `202` with a run
   id.
4. **Watch the run.** You are taken to the run timeline. With the worker
   running you will see, in order:
   - `run.started`, then **three `task.dispatched` events in one step** —
     RM, IE and quality round 0. They do not depend on each other and are
     dispatched together, over the internal HTTP protocol
     (`POST /internal/v1/agent-tasks`, service-token authenticated).
   - `task.completed` for each, with the agent's deterministic findings
     and a summary labelled with its source.
   - `planning` round 0, which waits for **both** RM and IE to finish and
     receives their results as input references.
   - If the top-ranked plan commits more units than RM says the materials
     can cover, an **`orchestrator.replan` event** carrying the reason
     string
     `MATERIAL_SHORTAGE_CONFLICT: selected plan allocates <x> units but
     materials cover <y>`, followed by planning round 1
     (`revise_allocation`), which ranks the material-limited option first
     and warns `REVISED_FOR_MATERIAL`. **At most one replan happens per
     run.**
   - `rm` round 1 (`validate_plan_materials`) against the final plan.
   - `run.finalized`, with the run ending in `AWAITING_REVIEW` (a
     recommendation was created), `COMPLETED`, `DEGRADED` or `FAILED`.
5. **Read the report.** Go back to the order and open the **Overview**
   tab. The canonical report is built from deterministic state only:
   states, shipment eligibility, blockers, one summary per agent (each
   labelled with the provider that wrote it), the recommendation, and all
   evidence. Model text appears **only** inside the agent summaries. A
   model that claims an order is ready to ship cannot change
   `shipment.eligible`, a state, or a blocker.
6. **Follow the evidence.** The **Evidence** tab lists every record,
   calculation and document reference the agents cited. Open one: the
   citation is re-authorized server-side before you see it.

On the fixture provider (this repository's only configured provider), the
run is fully reproducible: the same inputs always give the same output,
and every summary is prefixed `[Fixture]` and labelled *"Test fixture —
not a live AI model"*.

---

## 10. Workflow B — supervisor: review and apply a recommendation

Sign in as **`supervisor@demo.test`**. (If *you* were the person who
started the analysis, use `supervisor.b@demo.test` instead — the proposer
may not decide or apply.)

1. **Open the inbox.** Sidebar → Approvals → **Approval inbox**, `PROPOSED`
   tab. Open the recommendation for the order.
2. **Review.** Read the before/after diff (which slots, which balances,
   current vs proposed values), the cited evidence, the input versions,
   and the expiry countdown. Recommendations expire 24 hours after they
   are created.
3. **Reject a bad option.** Press **Reject**, enter a reason (required),
   submit. The recommendation becomes `REJECTED`, is audited, and can
   never be applied afterwards.
4. **Approve a good one.** Press **Approve**. The status becomes
   `APPROVED` — **and nothing has been written to capacity or stock yet.**
5. **Apply it.** Press **Apply**. In one transaction the server:
   - locks the order row, then the recommendation row, then the **union**
     of every capacity slot the proposal touches *plus* every slot the
     order was ever allocated to, then the union of every material balance
     involved — each set in ascending id order, so two concurrent applies
     can never take the same rows in opposite orders;
   - **re-checks every input version under those locks** and fails closed:
     a row that moved, a row that vanished, or a row the proposal writes
     to that the recorded versions do not pin, all count as stale;
   - releases the order's existing allocations and reservations, then
     re-validates and applies each proposed allocation and reservation —
     if any one no longer fits, the **entire** transaction rolls back and
     you get `409 CONFLICT` with zero partial rows;
   - moves the order to `PLANNED`, recomputes its material state, marks
     the recommendation `APPLIED`, writes the audit events, notifies, and
     enqueues a follow-up job to refresh the other orders that share those
     materials.
6. **See the stale path.** To demonstrate it: after approving, change the
   material's stock on the Materials screen (as the storekeeper), then
   come back and press Apply. The server sets the recommendation
   `SUPERSEDED` with `superseded_reason = "STALE_INPUT: …"`, **commits
   that**, notifies you ("Inputs changed — run a new analysis"), and
   *then* fails the request with `409 STALE_INPUT`, listing each changed
   input. The screen renders that list and refetches, so the Apply button
   disappears immediately. **An approval can never bypass a fresh
   analysis.**
7. **Check the audit trail.** Order detail → **History**, or
   Administration → **Audit log** as the org admin. Every decision and
   application is there with before/after summaries, the actor, and the
   trace id.

---

## 11. Workflow C — storekeeper: receive, accept, issue, reserve

Sign in as **`storekeeper@demo.test`**.

1. **Materials** → pick a material → **Receipt**: record an incoming
   quantity against a new lot. A lot can be received as `ACCEPTED` or
   `QUARANTINE`. Quarantined quantity does **not** count toward
   `on_hand_accepted`.
2. **Accept lot**: move a quarantined lot to `ACCEPTED`. Only now does its
   quantity join the available balance.
3. **Issue**: consume from an `ACCEPTED` lot. The server refuses to drive
   `on_hand − reserved` negative, except by consuming the order's own
   `ACTIVE` reservations (oldest first; a partially consumed reservation
   is split into an `ACTIVE` remainder and a new `CONSUMED` row).
4. **Correction**: reference the original receipt or issue and correct it.
   A correction can never push a balance below what is reserved, and never
   makes a lot negative.
5. **Reservations**: create a reservation for an order (the material must
   be on that order's BOM — otherwise `422`), or **Release** one. Both are
   confirmed before the server is called and both are audited with
   before/after balance snapshots.
6. **Watch the consequences.** Every ledger command recomputes the
   material state of the orders that use that material (bounded to 500
   orders, worst-severity-wins per BOM line), which is what turns an
   order's badge from `READY` to `AT_RISK` or `SHORTAGE` on the orders
   list and the overview cards. An overdue order's demand is dated at its
   due date, so it reports `SHORTAGE` rather than `AT_RISK`.

Two concurrent reservations that would both consume the same remaining
stock cannot both succeed: balances are locked and re-read, and the loser
gets a `409` conflict/shortage.

---

## 12. Workflow D — quality manager: inspect, hold, release

Sign in as **`quality@demo.test`**.

1. **Quality** → find the order (from the active-holds list or by
   searching). Open **Record inspection**: choose the inspection type,
   enter inspected units and defective units, and add defect rows
   (catalogue code, severity, count).
2. Submit. The server evaluates the inspection against the **active,
   versioned quality policy** (`QP-DEMO` in the demo data, whose thresholds
   are sample size 80, at most 5 defective units, zero critical defects,
   `FINAL` inspection required) and returns a deterministic result:
   - `PASS`,
   - `FAIL` — which **automatically places an `ACTIVE` quality hold** with
     the failing reasons, or
   - `INSUFFICIENT_SAMPLE`.
   With **no** approved policy the inspection is refused (`409 CONFLICT`,
   "No approved quality policy") — the system abstains rather than
   guessing.
3. **Place a hold manually** if you need to (`quality:hold`), with a
   reason.
4. **Try to ship a held order.** Look at the order's shipment
   eligibility: it stays ineligible, listing the reason
   (`ACTIVE_QUALITY_HOLD`). Even if an agent summary is optimistic, the
   badge does not move — eligibility is calculated, and the agent's own
   eligibility metric is the calculated verdict quoted verbatim.
5. **Re-inspect and release.** Record a passing `FINAL` inspection, then
   open **Release review**. The server validates that the inspection is
   `FINAL`, `PASS`, the latest `FINAL`, and against the *current* policy
   version (a version mismatch is `409 STALE_INPUT`). **If you recorded
   that inspection yourself, release is refused** (`403
   SELF_APPROVAL_DENIED`) — sign in as `quality.b@demo.test` to complete
   it. Releasing also releases every `ACTIVE` hold on the order.
6. Shipment eligibility now updates deterministically. Dispatch itself is
   a separate lifecycle transition recorded by a supervisor.

Defect rates are always reported as **two separate numbers**:
`defective_units / inspected_units` and DHU
(`total_defects / inspected_units × 100`). Zero inspections yields
*unknown*, never *pass*.

---

## 13. Workflow E — everyone else: IE, notes, knowledge base, administration

**IE engineer (`ie@demo.test`).** Industrial engineering → pick line `L2`
and style `ST-03` on the seeded data and you will see `OP-04` (sleeve set)
as the bottleneck at 60 s effective cycle, ≈ 60 units/hour, and a balance
index around 83 %. Record a new observation by operator **alias code**
(there is no name anywhere in the system), and mark an outlier with a
required reason. Operations with fewer than 3 observations are listed as
"Insufficient samples" and excluded from the balance calculation, not
estimated.

**Anyone with `note:create`.** Notes → type a shift note such as *"Line 3
is waiting on M01 for PO-KTN-0020, broken stitch on the sleeve set"* →
submit → see the classification, the classifier version, the resolved
entity chips (order, line, material, operation, defect), any unresolved
order references as plain text, and the "navigation only" notice.

**Uploaders (`document:upload`).** Knowledge base → Upload a `.md` or a
text-based `.pdf` → watch it move `QUARANTINE → PROCESSING → ACTIVE` →
search for a phrase from it in `hybrid` mode → open a citation. Try an
encrypted PDF to see the rejection reason.

**Org admin (`admin@demo.test`).** Administration → Audit log (filter by
outcome `DENIED` to see the negative-permission trail), Memberships (grant
a role to a user and watch their session get revoked), Quality policies,
Settings (the effective run limits and the LLM provider label).

**Viewer (`viewer@demo.test`).** Everything reads; nothing writes. There
is no Administration section in the sidebar, no Start analysis button, no
stock forms, no inspection form. This is the fastest way to demonstrate
permission filtering.

**Cross-factory denial.** Sign in as `byg.planner@demo.test` and try to
open a KTN order's URL directly. You get a **404**, not a 403 — the
existence of a resource in a factory you hold no role in is never
revealed.

---

## 14. Limitations you will meet while following this guide

Honest list, all of them documented elsewhere in the repository:

- **No screenshots exist.** Browser end-to-end testing (Task 24) was
  dropped by the user, so nothing ever drove the UI automatically and
  `docs/assessment/screenshots/` was never produced. Capture them by hand.
- **No live LLM has ever run.** No Anthropic API key is configured in this
  environment. `LS_LLM_PROVIDER=fixture` is the only provider ever
  exercised, and it is labelled *"Test fixture — not a live AI model"*
  everywhere it appears. The Anthropic adapter is unit-tested against stub
  SDK objects only (`docs/architecture/llm-boundary.md`).
- **The embedding model download has not been performed here.** `make
  seed --with-documents` needs one ~100 MB fetch of
  `BAAI/bge-small-en-v1.5` on first use. The knowledge-base search screens
  have therefore never been exercised against the real embedder in this
  environment; tests run against the semantics-free `hashing` stand-in.
- **No Docker deployment has been run.** The Compose, Caddy and Keycloak
  artefacts under `infra/` were validated statically (`make infra-check`)
  and never built or started. The `docs/operations/deployment.md` sequence
  is intent, not an observed run.
- **The dev identity provider is not a real IdP.** Shared password,
  in-memory state, a signing key regenerated on every restart, no TLS. It
  refuses to run outside development/test.
- **Row-level security is not implemented.** Tenant isolation is enforced
  in the application layer and covered by negative tests; database RLS is
  a documented pre-pilot requirement (`docs/security/threat-model.md`).
- **No antivirus scan on uploads** — a structural byte/marker scan only.
  **No OCR** — scanned PDFs are rejected with a message rather than
  partially parsed.
- **Vector search has no ANN index.** Exact scan over matching rows; fine
  at 30 documents, not at scale.
- **Rate limits are per process.** Behind more than one API instance each
  enforces its own budget.
- **Notifications are in-app only.** No email, no SMS.
- **No live ERP/MES connector.** CSV import and synthetic data only; the
  adapter *interface* is documented, nothing is connected.
- **`make perf` and the full `make eval` have not been run here** (machine
  heat policy), so there are no measured latency figures and no committed
  evaluation results file. See `docs/evaluation/performance.md` and
  `docs/evaluation/results/README.md`.

---

## See also

- [`README.md`](../README.md) — setup, command reference, project structure
- [`docs/requirements.md`](requirements.md) — numbered requirements and acceptance criteria
- [`docs/architecture/agents.md`](architecture/agents.md) — what each agent may and may not do
- [`docs/architecture/agent-protocol.md`](architecture/agent-protocol.md) — the task protocol and the orchestration graph
- [`docs/security/role-matrix.md`](security/role-matrix.md) — the generated permission matrix
- [`docs/security/approval-integrity.md`](security/approval-integrity.md) — the approval and apply rules
- [`docs/assessment/completion-matrix.md`](assessment/completion-matrix.md) — what is met, partially met, and not verified
