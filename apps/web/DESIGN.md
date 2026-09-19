# LineSense web design system

Rules every screen follows (Tasks 20 to 23). The tokens live in `src/index.css`; the shared
components live in `src/components/`. When a screen needs something new, add it here and to the
shared components first. Don't style it one-off.

## Design read

A data-dense operations console for garment-factory planners, supervisors, storekeepers, IE and
quality staff. They use it on desktop monitors all day, scan tables for exceptions, and act on
money- and delivery-critical decisions. So the direction is **calm, precise, utilitarian**: neutral
surfaces, one accent colour for interaction, colour reserved for status, dense but legible tables,
no decoration that competes with the data.

Dials (from the design-taste skill): variance 2 (predictable layouts), motion 2 (feedback only),
density 7 (operations tables).

## Why Tailwind + own primitives (and not a packaged design system)

The design-taste skill is scoped to landing pages. For dashboards it points to packaged systems
(Fluent, Carbon, Polaris). We did not adopt one, for these reasons:

- The Task 20 brief fixes the stack (Tailwind CSS + accessible primitives). Tasks 21 to 23 build
  on the same primitives.
- The CSP is same-origin only, and the bundle must stay small. The whole app shell and orders
  screens are about 142 kB gzip of JS. Carbon or Fluent would add a second styling system and
  several hundred kB.
- The components we need (badges, tables, forms, dialog, tabs, banners) are few, and accessible
  versions are small to own.

From the skill we did adopt: semantic tokens with dark mode, a single accent, one icon family
(Phosphor, no hand-drawn paths), a self-hosted non-default typeface, skeleton loaders, tactile
button feedback, no emoji, and the em-dash ban (one exception, below).

## Colour tokens

Semantic CSS variables on `:root`, swapped under `prefers-color-scheme: dark` (a
`data-theme="light"` attribute on `<html>` forces light). Tailwind utilities read them:
`bg-surface`, `text-fg`, `border-line` and so on. **Use only these tokens.** Never use raw
palette classes (`bg-blue-600`, `text-slate-700`) in components.

| Token | Light | Dark | Use |
|---|---|---|---|
| `canvas` | `#f4f4f5` | `#0c0c0e` | Page background |
| `surface` | `#fcfcfc` | `#151518` | Panels, tables, header, sidebar, inputs |
| `surface-sunken` | `#f0f0f2` | `#1c1c20` | Table header, hover rows, skeletons |
| `line` | `#e4e4e7` | `#2a2a30` | Dividers and panel borders |
| `line-strong` | `#71717a` | `#8a8a93` | Input borders (≥ 3:1 against surface) |
| `fg` | `#18181b` | `#f4f4f5` | Primary text |
| `fg-muted` | `#52525b` | `#a1a1aa` | Secondary text, labels, captions (AA on surface) |
| `accent` | `#1d4ed8` | `#6ea0ff` | The single interactive colour: primary buttons, links, active nav |
| `accent-hover` | `#1e40af` | `#93b8ff` | Hover for accent elements |
| `accent-fg` | `#fafafa` | `#0c0c0e` | Text on accent |
| `accent-soft` | `#eaf0ff` | `#172243` | Active navigation background |
| `focus` | `#2563eb` | `#6ea0ff` | Focus ring |

Status tokens (never used for decoration, never used as an accent):

| Tone | Tokens (`*-bg`, `*-fg`, `*-line`) | Meaning |
|---|---|---|
| success | `ok-*` | Ready, released, approved, eligible |
| warning | `warn-*` | At risk, awaiting review, degraded, test fixture, stale |
| danger | `bad-*` | Shortage, hold, failed, rejected, errors |
| info | `info-*` | Validated, planned, in progress, running |
| neutral | `neutral-*` | Unknown, draft, not inspected |
| muted | dashed `neutral-line`, `fg-muted` text | Terminal or inactive: cancelled, expired, superseded |

No pure `#000`/`#fff`. Shadows are only for floating layers (menus, dialogs, toasts):
`shadow-lg shadow-zinc-950/10`.

## Status is never colour alone

Every state is shown as **icon + text + colour** (`StateBadge`, `stateStyles.ts`). Each state in
a vocabulary has a distinct icon, and a screen-reader prefix names the vocabulary
("Materials: At risk"). New vocabularies go into `STATE_STYLES`. Don't build ad-hoc badges.
Provenance uses `SourceLabel` with its fixed wording:

- "Calculated from records"
- "AI recommendation"
- "Test fixture — not a live AI model"
- "AI explanation unavailable"
- "Pending human approval"
- "Approved by <name> at <time>"

## Typography

- UI font: **Geist Variable**. Data font: **Geist Mono Variable**, for order refs, codes, trace
  IDs and CSV columns. Both are self-hosted from `@fontsource-variable/*` and bundled (no remote
  fonts).
- Numbers in tables use `tabular-nums` and are right-aligned.
- Scale (Tailwind sizes). Body text is 14 px for density:

| Role | Class |
|---|---|
| Page title (h1) | `text-xl font-semibold tracking-tight` (20 px) |
| Section title (h2) | `text-base font-semibold` (16 px) |
| Body, table cells, inputs | `text-sm` (14 px) |
| Labels, table headers, captions, badges, hints | `text-xs` (12 px), `font-medium` for labels and headers |

- Sentence case everywhere. Use short labels, with at most 3 words on buttons.
- Paragraphs are capped at `max-w-[65ch]`.

## Spacing, shape, layout

- The spacing grid is 4 px. Page padding is `p-6`. Stacks between sections use `gap-6`, form
  fields use `gap-5`, and label-to-control uses `gap-1.5`.
- Table cells use `px-3 py-2`. Controls are 36 px tall (`h-9`). Header controls are 32 px (`h-8`).
- **Radius rule:** 6 px (`rounded-md`) for panels, inputs, buttons, dialogs and toasts. Status
  badges and role chips are full pills (`rounded-full`). Nothing else.
- The shell has a 56 px header, a 224 px sidebar from `lg` (1024 px) up, and content capped at
  1400 px. Below 1024 px the sidebar collapses behind a "Menu" toggle. The layout is desktop-first.
  Wide tables scroll horizontally inside their panel, never the page.
- Use grid for multi-column forms and filters, not flex percentage math.

## Components and patterns

- **Buttons:** `.btn-primary` for the one main action per view, `.btn-secondary` for everything
  else, and `.link` for inline navigation. Buttons press in with `active:scale-[0.98]`, which is
  disabled under reduced motion.
- **Tables:** `DataTable` has a caption (screen readers), column alignment, and `aria-sort` plus a
  visible arrow on the column the server sorts by. Tables never re-sort locally. Pagination is on
  the server (`Pagination`, contract `limit`/`offset`).
- **Filters:** a row above the table inside a `role="search"` form. Labels sit above controls.
  Search is debounced by 300 ms. State lives in the URL query.
- **Forms:** the label sits above the control, with an optional hint and the error below
  (`FormField` + `fieldAria`). Never use a placeholder as the label. Client validation mirrors the
  backend schema with Zod. Server `field_errors` map onto fields, and the input is kept on error.
  Submit is disabled while pending. The `Idempotency-Key` is reused on a retry of the same attempt.
- **Destructive or bulk writes** need `ConfirmDialog`. The UI updates only after the server
  confirms (no optimistic updates).
- **States** (required on every data view):
  - Loading: `LoadingState` skeletons shaped like the content, not spinners.
  - Empty: `EmptyState` with a next step.
  - Error: `ErrorState` with the server message, the trace ID and Retry.
  - Forbidden: `PermissionDenied`.
  - Out-of-date or reduced results: `StaleBanner` and `DegradedBanner`.
- **Toasts** are for transient confirmations only (`useToast`). Errors stay inline.
- **Icons:** Phosphor, regular weight, 16 px (14 px in badges). Register each icon in
  `components/Icon.tsx` by semantic name. Icons are decorative (`aria-hidden`) and always sit next
  to text. Don't use emoji.
- **Values:** all numbers and dates go through `lib/format.ts`. Instants use the factory time zone.
  A missing value renders as "Unknown", never a made-up number.

## Accessibility

- A skip link to `#main-content`, landmarks (`header`, `nav[aria-label=Main]`, `main`), and one h1
  per page.
- Visible `:focus-visible` ring (2 px `focus` token, 2 px offset) on every interactive element.
- Text meets WCAG AA in both themes. Input borders are ≥ 3:1.
- Menus and dialogs close with Escape, and the dialog moves focus to Cancel.
- Motion is limited to feedback, and `motion-reduce` disables pulses and press effects.

## Copy rules

- No em or en dashes in UI copy. Use a comma, colon, period or parentheses. Ranges use a hyphen
  ("1-50") or words ("3 to 40").
- The **only exception** is the contractual provenance label "Test fixture — not a live AI
  model". The brief and backend fix its exact wording, and it must match everywhere.
- Say what happened and what to do next ("The file is larger than 1 MB."). Always show server
  messages and trace IDs verbatim.
