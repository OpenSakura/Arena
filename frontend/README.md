# Frontend

Next.js (TypeScript) web app for the translation arena.

Notes:
- Integrates with Authentik via OIDC (Auth.js / NextAuth).
- Anonymous users can battle/vote; logged-in users add profile metadata used for
  downstream filtering.

Local quickstart (dev):
1. Copy env: `cp .env.example .env.local` and edit.
2. Install deps: `npm install`
3. Run: `npm run dev`

## Auth E2E smoke

This frontend includes a Playwright smoke test for NextAuth + Authentik login/logout.

Run from `frontend/`:

```bash
npx playwright install chromium
npm run test:e2e
```

Notes:
- The e2e runner starts a docker-backed Authentik stack from `backend/tests/e2e/docker-compose.yaml`.
- It then starts the frontend server in production mode on `http://localhost:13000` and runs the login/logout smoke flow.

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

To exercise the anonymous Turnstile misconfiguration branch (backend requires Turnstile, frontend site key missing):

```bash
NEXT_PUBLIC_TURNSTILE_SITE_KEY= npm run test:e2e -- e2e/turnstile-misconfigured.spec.ts
```
