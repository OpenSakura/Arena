# Tests

Test suite for the backend.

Structure:
- `tests/*.py`: unit tests for pure logic and service helpers.
- `tests/e2e/*`: docker-backed end-to-end tests for auth + infra integrations.

Commands (from `backend/`):
- Unit/default suite: `uv run pytest`
- Only e2e suite: `uv run pytest tests/e2e --run-e2e`
- Full suite including e2e: `uv run pytest --run-e2e`

Notes:
- E2E tests spin up Postgres, Redis, and Authentik via Docker Compose.
- E2E tests are skipped by default; pass `--run-e2e` when you want them.
