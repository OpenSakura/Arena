# E2E Test Stack

This directory contains docker-backed end-to-end tests.

Local stack:
- Postgres on `localhost:15432`
- Redis on `localhost:16379`
- Authentik on `localhost:19000`

Run from `backend/`:

```bash
uv run pytest tests/e2e --run-e2e
```

Real case scenario env file:
- `tests/e2e/test.env`
- Fill:
  - `E2E_CEREBRAS_API_BASE_URL`
  - `E2E_CEREBRAS_API_KEY`
  - `E2E_CEREBRAS_MODEL`

Load env vars before running tests:

```bash
set -a
source tests/e2e/test.env
set +a
uv run pytest tests/e2e --run-e2e
```

The fixtures bootstrap an Authentik OAuth2 provider and request real RS256
tokens so `/api/v1/me` is tested with production-like OIDC verification.
