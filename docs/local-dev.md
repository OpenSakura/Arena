# Local Development Runbook

This repo is a monorepo:

- `backend/`: FastAPI + Postgres (control plane)
- `frontend/`: Next.js (arena UI + admin UI)
- `infra/`: local dev dependencies (Postgres + Redis compose)

## Prereqs

- Docker
- Node.js + npm
- Python 3.11+
- `uv` (recommended; see backend README)

## Start local infra

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

Notes:

- If you plan to store per-model API keys, set `ARENA_MASTER_KEY` in `backend/.env`.
  If it's unset, model CRUD still works, but saving an `api_key` will fail.
- **Single-worker only**: the backend must run with `WEB_CONCURRENCY=1` (the default).
  Starting with more than one worker raises an error at startup because battle execution
  depends on in-process singletons that are not safe across OS processes.
- Set `RATE_LIMIT_REDIS_URL=redis://localhost:6379/0` to enable Redis-backed
  throttling and shared caching. If unset, rate limits and the shared
  confidence-interval cache are disabled.
- Cloudflare Turnstile can optionally be enabled as an extra anti-abuse layer
  by setting `TURNSTILE_SECRET_KEY` in the backend. Battle creation requires
  authentication regardless of Turnstile configuration. Leave it empty to
  skip Turnstile locally.
- Tune `BATTLE_RUNNING_WAIT_TIMEOUT_SECONDS` to control how long an active
  battle execution task may run before being force-failed (default: 600s).
- Set `ACCESS_LOG_ENABLED=true` to emit per-request latency/access logs.
- Set `LOG_JSON=true` when shipping logs to a structured log collector.

Notes:

- Admin endpoints (`/api/v1/admin/*`) require OIDC and admin group membership.
- Public read endpoints (battles, leaderboard, results) work without login.
- All mutations (creating battles, submitting votes, retrying battles, revealing
  votes) require authentication.

## Frontend

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

Open:

- `http://localhost:3000`

## Seed Data (Tasks + Models)

The arena needs at least:

- 2 public, enabled models
- 1 task

Recommended workflow:

1. Configure OIDC and the admin group claim.
2. Login in the UI.
3. Use the admin pages:
   - `http://localhost:3000/admin/models`
   - `http://localhost:3000/admin/tasks`

If you do not have an OIDC provider available in local dev, you can still run the
public UI routes, but you will not be able to use admin endpoints to seed the
database.
