# Frontend

React + Vite (TypeScript) SPA for the translation arena.

Notes:
- Authenticates via SPA OIDC PKCE through `react-oidc-context` / `oidc-client-ts`.
  Public OIDC bootstrap values (issuer, client id, scopes, callback paths) are
  fetched at startup from the backend's `GET /api/v1/public-config` endpoint.
- Anonymous users can browse public pages (battles, leaderboards, results).
  Login is required to create battles, retry battles, submit votes, and reveal
  votes. Logged-in users can also fill an optional profile for downstream
  filtering.

Local quickstart (dev):
1. Copy env: `cp .env.example .env.local` and edit.
2. Install deps: `npm install`
3. Run: `npm run dev`

The Vite dev server proxies `/api/v1` to the backend at `http://localhost:8000`.

## Auth E2E smoke

This frontend includes Playwright smoke tests for SPA OIDC login/logout.

Run from `frontend/`:

```bash
npx playwright install chromium
npm run test:e2e
```

Notes:
- The e2e runner starts a docker-backed Authentik stack from `backend/tests/e2e/docker-compose.yaml`.
- It then starts the frontend dev server on `http://localhost:13000` and runs the login/logout smoke flow.

## Live backend contract smoke (optional)

You can also run a no-mock frontend/backend contract smoke:

```bash
PW_ENABLE_LIVE_STACK=1 npm run test:e2e -- e2e/live-contract.spec.ts
```

When enabled, Playwright additionally:
- Starts the backend e2e Docker dependencies (Postgres, Redis, Authentik).
- Bootstraps the database schema from ORM models and seeds deterministic models/tasks.
- Starts a local OpenAI-compatible mock gateway used by battle streaming.

If `PW_ENABLE_LIVE_STACK` is not set, `e2e/live-contract.spec.ts` is skipped by default.

Battle creation, retries, vote submission, and reveal flows are covered in Playwright with authenticated session mocks. Anonymous coverage remains browse-only (for example onboarding guard coverage).
