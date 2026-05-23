"""Alembic environment configuration.

Reads the database URL directly from ``DATABASE_URL`` in the environment
so migrations stay independent of the application's runtime security
validators (e.g. ``ARENA_MASTER_KEY``, ``SERVICE_TOKEN_HASH_SECRET``),
which are enforced when constructing ``Settings`` but are irrelevant
to schema upgrades.
"""

# pyright: reportMissingImports=false, reportAttributeAccessIssue=false

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db.base import Base

# Import all models so Base.metadata is fully populated.
import app.models.battle  # noqa: F401
import app.models.model_registry  # noqa: F401
import app.models.rating  # noqa: F401
import app.models.service_account  # noqa: F401
import app.models.task  # noqa: F401
import app.models.user  # noqa: F401
import app.models.vote  # noqa: F401


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    """Return the database URL for migrations.

    Read directly from the environment rather than constructing the full
    ``Settings`` object so migrations do not trip the production-only
    security validators (master key, service token hash, etc.) that are
    irrelevant to schema upgrades.
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    # Fall back to the same default the application uses for local dev.
    return "postgresql+psycopg://postgres:postgres@localhost:5432/arena"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
