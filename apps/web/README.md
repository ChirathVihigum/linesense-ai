# LineSense web app

React + TypeScript single-page app for LineSense AI, built with Vite. It talks only to the
FastAPI backend in `services/backend` through a client generated from `contracts/openapi.json`.
Design rules for all screens live in [DESIGN.md](./DESIGN.md).

## Prerequisites

- Node.js 26 (see `.nvmrc`) and npm 11.
- The backend running on `http://127.0.0.1:8000` for `npm run dev` (see the repository README).

## Commands

Run from `apps/web/` (or use the root `make` targets shown in brackets):

| Command | What it does |
|---|---|
| `npm ci` (`make web-install`) | Install the exact dependency tree from `package-lock.json`. |
| `npm run dev` (`make web-dev`) | Dev server on `http://localhost:5173`. `/api` and `/auth` are proxied to the backend on `127.0.0.1:8000`, so the session cookie stays same-origin and the OIDC callback (`/auth/callback`) reaches the API. |
| `npm run lint` (`make web-lint`) | ESLint (typescript-eslint strict type-checked, React Hooks, React Refresh), zero warnings allowed. |
| `npm run typecheck` (`make web-typecheck`) | `tsc -b` over the app, test and config projects (strict). |
| `npm test` (`make web-test`) | Vitest + Testing Library + MSW component tests (jsdom), single run. |
| `npm run build` (`make web-build`) | Type-check and produce the production bundle in `dist/`. |
| `npm run generate:api` | Regenerate `src/generated/api.ts` from `../../contracts/openapi.json`. Normally run via `make contracts`, which exports the OpenAPI document first. |

`make contracts-check` fails when `contracts/openapi.json` no longer matches the backend routes,
when `src/generated/api.ts` is stale, or when either has uncommitted changes. Never edit
`src/generated/api.ts` by hand.

## Structure

- `src/app/`: providers, route table, authenticated layout (header, factory selector, user menu, sidebar).
- `src/lib/`: API client (`api.ts`: CSRF header, 401 redirect, `ApiError`), session (`auth.tsx`),
  factory scope (`factory.tsx`), permissions, idempotency keys, formatting, toasts.
- `src/components/`: shared, accessible building blocks (state badges, source labels, tables, states).
- `src/features/<area>/`: screens. Each screen's tests sit beside it as `*.test.tsx`.
- `src/test/`: Vitest setup, MSW handlers and fixtures (test-only data).

## Security notes

- The session is the backend's HttpOnly `ls_session` cookie. No token or session data is stored
  in `localStorage`; only the last selected factory code is.
- Unsafe requests carry `X-CSRF-Token` (value from `GET /api/v1/me`, held in memory).
- Creates, imports and commits send an `Idempotency-Key` that is reused when the same attempt is retried.
- The UI hides actions the user lacks permission for, but the backend enforces every permission.
