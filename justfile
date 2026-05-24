set shell := ["bash", "-uc"]
set dotenv-load := false

infra_compose      := "infra/compose.yaml"
authentik_compose  := "backend/tests/e2e/docker-compose.yaml"
pg_container       := "infra-postgres-1"
pg_db              := "arena"
pg_user            := "postgres"

# Default: list recipes
default:
    @just --list

# --- Infra (Postgres + Redis) ----------------------------------------------

# Bring up local Postgres + Redis
infra-up:
    docker compose -f {{infra_compose}} up -d

# Stop infra (keeps volumes)
infra-down:
    docker compose -f {{infra_compose}} down

# Stop infra and DELETE volumes (nukes postgres + redis data)
infra-nuke:
    docker compose -f {{infra_compose}} down -v

infra-logs:
    docker compose -f {{infra_compose}} logs -f

infra-ps:
    docker compose -f {{infra_compose}} ps

# --- Authentik stack (e2e) -------------------------------------------------

authentik-up:
    docker compose -f {{authentik_compose}} up -d

authentik-down:
    docker compose -f {{authentik_compose}} down

authentik-nuke:
    docker compose -f {{authentik_compose}} down -v

# --- Database --------------------------------------------------------------

# Drop & recreate the `arena` database. The backend re-creates the schema on
# next startup via app.db.bootstrap.bootstrap_schema().
reset-db:
    @echo ">> dropping & recreating database '{{pg_db}}'"
    docker exec -i {{pg_container}} psql -U {{pg_user}} -d postgres -c "DROP DATABASE IF EXISTS {{pg_db}} WITH (FORCE);"
    docker exec -i {{pg_container}} psql -U {{pg_user}} -d postgres -c "CREATE DATABASE {{pg_db}};"
    @echo ">> done — schema will be bootstrapped on next backend start"

# Open a psql shell against the dev DB
psql:
    docker exec -it {{pg_container}} psql -U {{pg_user}} -d {{pg_db}}

# --- Backend ---------------------------------------------------------------

backend-dev:
    cd backend && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

backend-test *args:
    cd backend && .venv/bin/pytest {{args}}

# --- Frontend --------------------------------------------------------------

frontend-dev:
    cd frontend && npm run dev

frontend-build:
    cd frontend && npm run build

frontend-lint:
    cd frontend && npm run lint

frontend-typecheck:
    cd frontend && npm run typecheck
