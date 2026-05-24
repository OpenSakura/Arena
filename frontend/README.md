# Frontend

React + Vite (TypeScript) SPA for the translation arena.

Notes:
- Auth uses backend-owned sessions. The SPA starts login at
  `/api/v1/auth/login`, bootstraps state from `/api/v1/auth/session`, and sends
  cookies with same-origin API calls. It does not run an OIDC token exchange in
  the browser.
- The backend owns the confidential OIDC client, provider secret, callback at
  `/api/v1/auth/callback`, HttpOnly app session cookie, and CSRF validation. The
  frontend receives only session JSON and a CSRF token for unsafe requests.
- Anonymous users can browse the leaderboard. Login is required to create
  battles, view battles, retry battles, and submit votes. Successful vote
  submission immediately reveals the models used inline. Logged-in users can
  also fill an optional profile for downstream filtering.
- Do not put provider secrets, token endpoint credentials, or browser-owned OIDC
  settings in Vite env files. `frontend/.env.example` only documents the dev
  proxy target.

Local quickstart (dev):
1. Copy env: `cp .env.example .env.local` and edit only the dev proxy target if needed.
2. Install deps: `npm install`
3. Run: `npm run dev`

The Vite dev server proxies `/api/v1` to the backend at `http://localhost:8000`.
Backend login and callback requests pass through that proxy during local dev.

## Auth E2E smoke

This frontend includes Playwright smoke tests for backend-session login/logout.

Run from `frontend/`:

```bash
npx playwright install chromium
npm run test:e2e
```

Notes:
- The e2e runner starts a docker-backed Authentik stack from `backend/tests/e2e/docker-compose.yaml`.
- It then starts the frontend dev server on `http://localhost:13000` and runs the login/logout smoke flow through `/api/v1/auth/login` and `/api/v1/auth/callback`.
- Deterministic e2e provider secrets stay in server-side Playwright/backend setup and are stripped from the Vite dev server environment.

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

Battle creation, retries, and vote submission/reveal flows are covered in
Playwright with authenticated session mocks. Anonymous coverage is limited to
the leaderboard and onboarding guard behavior.
