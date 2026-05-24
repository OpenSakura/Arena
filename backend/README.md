# Backend

FastAPI control-plane API for the translation arena.

Notes:
- This backend does not host model inference. It calls your existing LLM gateway
  or provider endpoints over HTTP.
- Browser authentication uses backend/BFF confidential OIDC with Authentik or
  another OpenID Provider. The backend builds the authorization request, retains
  PKCE `S256` server-side, exchanges the code at `/api/v1/auth/callback` with
  `client_secret_basic`, and stores only an app session. The browser receives an
  HttpOnly session cookie and uses `X-CSRF-Token` for unsafe session requests.
- Public reads are limited to the leaderboard. Battle detail reads, battle
  creation, retrying battles, battle streaming, and vote submission require
  authentication. Successful vote submissions reveal model identities inline;
  there is no separate reveal compatibility call.
- `OIDC_CLIENT_SECRET` and `AUTH_SESSION_HASH_SECRET` are backend/server-only
  secrets. `AUTH_SESSION_HASH_SECRET` is the HMAC key for opaque login-state,
  session, and CSRF token hashes. Never expose either value through frontend or
  Vite environment variables.
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
- Cloudflare Turnstile settings are retained as a deprecated placeholder from
  the original anonymous battle-creation design. Battle creation now requires
  authentication, and Turnstile verification is not currently enforced.

Local quickstart (dev):
1. Start local infra (Postgres + Redis): `docker compose -f ../infra/compose.yaml up -d`
2. Copy env: `cp .env.example .env` and edit as needed.
3. Configure OIDC when testing login: set `PUBLIC_BASE_URL`, `OIDC_ISSUER`,
   `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`,
   `OIDC_CLIENT_AUTH_METHOD=client_secret_basic`, `OIDC_SCOPE`,
   `OIDC_REDIRECT_PATH=/api/v1/auth/callback`, `AUTH_SESSION_HASH_SECRET`,
   `AUTH_SESSION_COOKIE_NAME`, `AUTH_SESSION_MAX_AGE_SECONDS`, and
   `AUTH_CSRF_HEADER_NAME` in backend/server env.
4. Install deps: `uv sync`
5. Bootstrap schema: `uv run python -m app.db.bootstrap` (creates tables from ORM models, idempotent)
6. Run: `uv run uvicorn app.main:app --reload --port 8000`

Rotate `OIDC_CLIENT_SECRET` by updating the provider secret, updating the backend
secret source, deploying, verifying login, then revoking the old provider secret
if overlap is available. If overlap is not available, schedule a tight cutover.
