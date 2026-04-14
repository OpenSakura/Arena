# Backend

FastAPI control-plane API for the translation arena.

Notes:
- This backend does not host model inference. It calls your existing LLM gateway
  / provider endpoints via HTTP.
- Authentication is OIDC (Authentik). Anonymous access is allowed; when a valid
  OIDC access token is provided, the backend records `user_id` for higher-trust
  analysis later.
- **Single-worker requirement**: battle execution relies on in-process singletons
  (orchestrator, leaderboard refresher) that are not safe across multiple OS
  processes. Always run with `WEB_CONCURRENCY=1` (the default). Starting with
  more than one worker is a startup error.
- Battle model pairing supports FastChat-inspired knobs via env JSON
  (`BATTLE_SAMPLING_WEIGHTS`, `BATTLE_TARGETS`, `BATTLE_STRICT_TARGETS`, etc.).
- Battle execution is capped by `BATTLE_RUNNING_WAIT_TIMEOUT_SECONDS` — if the
  owned battle task exceeds this wall-clock limit the battle is force-failed.
- Leaderboard ratings are periodically refreshed in a background job
  (`LEADERBOARD_REFRESH_*`) and can be inspected/refreshed via admin endpoints.
- `/leaderboard` supports `method=elo` (default) and `method=bt`.
  Bradley-Terry confidence intervals can be enabled via
  `include_confidence=true`.
- Request correlation IDs are accepted via `X-Request-ID` (or generated when
  absent) and returned on responses.
- Access logs are optional (`ACCESS_LOG_ENABLED=true`) and can be emitted in
  JSON via `LOG_JSON=true` for easier ingestion.
- Redis-backed throttling and shared caching use `RATE_LIMIT_REDIS_URL`; leaving
  it unset disables both rate limits (anonymous and authenticated) and the shared
  confidence-interval result cache.
- Anonymous identity cookies use `anon_id_cookie_secure=True` by default — set
  `ANON_ID_COOKIE_SECURE=false` only for local HTTP development.
- Cloudflare Turnstile verification for anonymous battle creation is enabled
  when `TURNSTILE_SECRET_KEY` is set (leave empty to disable). The frontend
  must also have `NEXT_PUBLIC_TURNSTILE_SITE_KEY` set so the widget renders;
  having only one side configured breaks anonymous battle creation.

Local quickstart (dev):
1. Start local infra (Postgres + Redis): `docker compose -f ../infra/compose.yaml up -d`
2. Copy env: `cp .env.example .env` and edit as needed.
3. Install deps: `uv sync`
4. Bootstrap schema: `uv run python -m app.db.bootstrap` (creates tables from ORM models, idempotent)
5. Run: `uv run uvicorn app.main:app --reload --port 8000`
