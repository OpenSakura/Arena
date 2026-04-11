# Migrations

This folder holds Alembic migrations for the backend Postgres schema.

Notes:
- `env.py` reads backend settings and targets `app.db.base.Base.metadata`.
- `versions/0001_initial_schema.py` bootstraps the initial schema.
