# Backend

FastAPI control-plane API for the translation arena.

Notes:
- This backend does not host model inference. It calls your existing LLM gateway
  / provider endpoints via HTTP.
- Authentication is OIDC (Authentik). Anonymous access is allowed; when a valid
  OIDC access token is provided, the backend records `user_id` for higher-trust
  analysis later.
- Battle model pairing supports FastChat-inspired knobs via env JSON
  (`BATTLE_SAMPLING_WEIGHTS`, `BATTLE_TARGETS`, `BATTLE_STRICT_TARGETS`, etc.).
- Battle observer streams wait up to `BATTLE_RUNNING_WAIT_TIMEOUT_SECONDS`
  before timing out and marking a stuck running battle as failed.
- Leaderboard ratings are periodically refreshed in a background job
  (`LEADERBOARD_REFRESH_*`) and can be inspected/refreshed via admin endpoints.
- `/leaderboard` supports `method=elo` (default) and `method=bt`.
  Bradley-Terry confidence intervals can be enabled via
  `include_confidence=true`.
- Request correlation IDs are accepted via `X-Request-ID` (or generated when
  absent) and returned on responses.
- Access logs are optional (`ACCESS_LOG_ENABLED=true`) and can be emitted in
  JSON via `LOG_JSON=true` for easier ingestion.
- Anonymous rate limiting uses Redis-backed shared buckets via
  `RATE_LIMIT_REDIS_URL` (leave unset to disable throttling).

Local quickstart (dev):
1. Start local infra (Postgres + Redis): `docker compose -f ../infra/compose.yaml up -d`
2. Copy env: `cp .env.example .env` and edit as needed.
3. Install deps: `uv sync`
4. Run: `uv run uvicorn app.main:app --reload --port 8000`
