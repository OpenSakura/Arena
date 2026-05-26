"""ORM schema bootstrap entry point."""

from __future__ import annotations

from app.db.base import Base
from app.db.session import get_engine
import app.models  # noqa: F401
from sqlalchemy import text
from sqlalchemy.engine import Connection


_SCHEMA_BOOTSTRAP_LOCK_KEY = 0x4172656E6100_0002


def bootstrap_schema() -> None:
    """Create all ORM tables for the configured database."""

    engine = get_engine()
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": _SCHEMA_BOOTSTRAP_LOCK_KEY},
            )
            _upgrade_legacy_model_registry_schema(connection)
            _ensure_runs_response_full_column(connection)
            Base.metadata.create_all(bind=connection, checkfirst=True)
        return

    Base.metadata.create_all(bind=engine, checkfirst=True)


def _upgrade_legacy_model_registry_schema(connection: Connection) -> None:
    if not _postgres_table_exists(connection, "models"):
        return

    connection.execute(text("DROP INDEX IF EXISTS ix_models_model_name"))

    if _postgres_column_exists(connection, "models", "provider_type"):
        connection.execute(
            text("ALTER TABLE models DROP CONSTRAINT IF EXISTS uq_models_provider_model")
        )

    _ensure_model_name_unique_constraint(connection)

    if _postgres_column_exists(connection, "models", "provider_type"):
        connection.execute(text("ALTER TABLE models DROP COLUMN provider_type"))


def _ensure_runs_response_full_column(connection: Connection) -> None:
    if not _postgres_table_exists(connection, "runs"):
        return
    if _postgres_column_exists(connection, "runs", "response_full"):
        return
    connection.execute(text("ALTER TABLE runs ADD COLUMN response_full JSONB"))


def _postgres_table_exists(connection: Connection, table_name: str) -> bool:
    return bool(
        connection.execute(
            text("SELECT to_regclass(:table_name) IS NOT NULL"),
            {"table_name": table_name},
        ).scalar_one()
    )


def _postgres_column_exists(
    connection: Connection, table_name: str, column_name: str
) -> bool:
    return bool(
        connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = :table_name
                      AND column_name = :column_name
                )
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        ).scalar_one()
    )


def _ensure_model_name_unique_constraint(connection: Connection) -> None:
    has_constraint = bool(
        connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid = 'models'::regclass
                      AND conname = 'uq_models_model_name'
                )
                """
            )
        ).scalar_one()
    )
    if has_constraint:
        return

    duplicate_rows = connection.execute(
        text(
            """
            SELECT model_name, count(*) AS row_count
            FROM models
            GROUP BY model_name
            HAVING count(*) > 1
            ORDER BY model_name
            LIMIT 5
            """
        )
    ).all()
    if duplicate_rows:
        duplicates = ", ".join(
            f"{row[0]} ({row[1]} rows)" for row in duplicate_rows
        )
        raise RuntimeError(
            "Cannot migrate model registry provider_type removal because duplicate "
            f"model_name rows exist: {duplicates}"
        )

    connection.execute(
        text("ALTER TABLE models ADD CONSTRAINT uq_models_model_name UNIQUE (model_name)")
    )


if __name__ == "__main__":
    bootstrap_schema()
