# Backend

FastAPI control-plane API for the translation arena.

Notes:
- This backend does not host model inference. It calls your existing LLM gateway
  / provider endpoints via HTTP.
- Authentication is OIDC (Authentik). Public reads are limited to the
  leaderboard. Battle detail reads, battle creation, retrying battles, battle
  streaming, and vote submission require a valid OIDC access token. Successful
  vote submissions reveal model identities inline; there is no separate reveal
  compatibility call.
- **Single-worker requirement**: battle execution relies on in-process singletons
  (orchestrator, leaderboard refresher) that are not safe across multiple OS
  processes. Always run with `WEB_CONCURRENCY=1` (the default). Starting with
  more than one worker is a startup error.
- Battle model pairing supports FastChat-inspired knobs via env JSON
  (`BATTLE_SAMPLING_WEIGHTS`, `BATTLE_TARGETS`, `BATTLE_STRICT_TARGETS`, etc.).
- Battle execution is capped by `BATTLE_RUNNING_WAIT_TIMEOUT_SECONDS`.
  Owner-task timeouts consume the same one-shot automatic retry budget as run
  failures before a final timeout failure is recorded.
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
  it unset disables rate limits and the shared confidence-interval result cache.
- Cloudflare Turnstile (`TURNSTILE_SECRET_KEY`) can still be enabled as an
  extra anti-abuse layer, but it is no longer the primary gate for battle
  creation. All battle creation now requires authentication.

Local quickstart (dev):
1. Start local infra (Postgres + Redis): `docker compose -f ../infra/compose.yaml up -d`
2. Copy env: `cp .env.example .env` and edit as needed.
3. Install deps: `uv sync`
4. Bootstrap schema: `uv run python -m app.db.bootstrap` (creates tables from ORM models, idempotent)
5. Run: `uv run uvicorn app.main:app --reload --port 8000`
