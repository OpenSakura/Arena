# Local Development Runbook

This repo is a monorepo:

- `backend/`: FastAPI + Postgres control plane
- `frontend/`: React + Vite SPA for the arena UI and admin UI
- `infra/`: local dev dependencies, Postgres and Redis compose

## Prereqs

- Docker
- Node.js + npm
- Python 3.11+
- `uv` (recommended; see backend README)

## Start Local Infra

From repo root:

```bash
docker compose -f infra/compose.yaml up -d
```

Default local DB:

- host: `localhost`
- port: `5432`
- user: `postgres`
- password: `postgres`
- db: `arena`

Default local Redis:

- host: `localhost`
- port: `6379`
- url: `redis://localhost:6379/0`

## Backend

```bash
cd backend
cp .env.example .env
uv sync
uv run python -m app.db.bootstrap
uv run uvicorn app.main:app --reload --port 8000
```

For local browser login, configure a confidential Authentik provider with this
redirect URI:

```text
http://localhost:5173/api/v1/auth/callback
```

Set these backend values in `backend/.env`:

```bash
PUBLIC_BASE_URL=http://localhost:5173
OIDC_ISSUER=http://localhost:19000/application/o/arena/
OIDC_CLIENT_ID=arena-backend
OIDC_CLIENT_SECRET=<local backend secret>
OIDC_CLIENT_AUTH_METHOD=client_secret_basic
OIDC_SCOPE=openid email profile
OIDC_REDIRECT_PATH=/api/v1/auth/callback
AUTH_SESSION_HASH_SECRET=<local session hash secret>
AUTH_SESSION_COOKIE_NAME=arena_session
AUTH_SESSION_MAX_AGE_SECONDS=28800
AUTH_SESSION_LAST_SEEN_MIN_INTERVAL_SECONDS=60
AUTH_SESSION_LAST_SEEN_LOCK_TIMEOUT_MS=100
AUTH_SESSION_LAST_SEEN_STATEMENT_TIMEOUT_MS=500
AUTH_CSRF_HEADER_NAME=X-CSRF-Token
DATABASE_IDLE_IN_TRANSACTION_SESSION_TIMEOUT_MS=30000
```

PKCE `S256` stays enabled. The backend stores the verifier server-side and sends
it during token exchange.

Notes:

- `OIDC_CLIENT_SECRET` and `AUTH_SESSION_HASH_SECRET` belong only in backend or
  server secret storage. Do not place provider secrets in frontend or Vite env.
- `AUTH_SESSION_HASH_SECRET` HMAC-hashes opaque login-state, session, and CSRF
  tokens stored by the backend.
- Auth session `last_seen_at` touches are throttled and bounded by the
  `AUTH_SESSION_LAST_SEEN_*` timeouts so stale session-row locks cannot block
  normal request handling indefinitely.
- `DATABASE_IDLE_IN_TRANSACTION_SESSION_TIMEOUT_MS` protects PostgreSQL from
  app connections that are abandoned while holding an open transaction.
- If you plan to store per-model API keys, set `ARENA_MASTER_KEY` in `backend/.env`.
  If it's unset, model CRUD still works, but saving an `api_key` will fail.
- **Single-worker only**: the backend must run with `WEB_CONCURRENCY=1` (the default).
  Starting with more than one worker raises an error at startup because battle execution
  depends on in-process singletons that are not safe across OS processes.
- Set `RATE_LIMIT_REDIS_URL=redis://localhost:6379/0` to enable Redis-backed
  throttling and shared caching. If unset, rate limits and the shared
  confidence-interval cache are disabled.
- Cloudflare Turnstile settings are currently a deprecated placeholder from the
  original anonymous battle-creation flow. Battle creation now requires
  authentication, and Turnstile verification is not enforced locally even if
  `TURNSTILE_SECRET_KEY` is set. Leave it empty unless re-enabling that flow.
- Tune `BATTLE_RUNNING_WAIT_TIMEOUT_SECONDS` to control how long an active
  battle execution task may run before being force-failed (default: 600s).
- Set `ACCESS_LOG_ENABLED=true` to emit per-request latency/access logs.
- Set `LOG_JSON=true` when shipping logs to a structured log collector.

Auth notes:

- Admin endpoints (`/api/v1/admin/*`) require a backend session and admin group
  membership.
- Public read endpoints are limited to the leaderboard.
- All mutations and viewing, creating battles, viewing battles, submitting votes,
  and retrying battles, require authentication. Successful vote submission
  reveals model identities immediately.
- Unsafe browser session requests must include the per-session `X-CSRF-Token`
  returned from `GET /api/v1/auth/session`.

## Frontend

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

Open:

- `http://localhost:5173`

The Vite dev server proxies `/api/v1` requests to `http://localhost:8000`.
The SPA fetches auth mode and paths from `GET /api/v1/public-config`, starts
login at `/api/v1/auth/login`, and bootstraps the app session from
`GET /api/v1/auth/session`.

Do not add provider secrets or token endpoint credentials to `frontend/.env.local`.
The browser should only receive the app session cookie and a CSRF token from the
backend.

## Seed Data (Tasks + Models)

The arena needs at least:

- 2 public, enabled models
- 1 task

Recommended workflow:

1. Configure OIDC and the admin group claim.
2. Login in the UI.
3. Use the admin pages:
   - `http://localhost:5173/admin/models`
   - `http://localhost:5173/admin/tasks`

If you do not have an OIDC provider available in local dev, you can still run
the public leaderboard UI route, but you will not be able to create battles,
view battles, vote, or use admin endpoints to seed the database.

## Secret Rotation Practice

For `OIDC_CLIENT_SECRET`, update the provider secret, update the backend secret
source, deploy or restart the backend, verify login through
`/api/v1/auth/callback`, then revoke the old provider secret if overlap is
available. If the provider cannot overlap secrets, schedule a tight cutover.

For `AUTH_SESSION_HASH_SECRET`, plan for existing app sessions to become invalid
because session, CSRF, and login-state hashes depend on the previous key.
