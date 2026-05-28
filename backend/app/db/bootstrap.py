"""ORM schema bootstrap entry point."""

from __future__ import annotations

from app.db.base import Base
from app.db.session import get_engine
import app.models  # noqa: F401
from sqlalchemy import text
from sqlalchemy.engine import Connection


_SCHEMA_BOOTSTRAP_LOCK_KEY = 0x4172656E6100_0002
_VOTES_BATTLE_ID_UNIQUE_INDEX_NAME = "uq_votes_battle_id"
_VOTES_HUMAN_BATTLE_ID_UNIQUE_INDEX_NAME = "uq_votes_human_battle_id"
_VOTES_BOT_BATTLE_ID_UNIQUE_INDEX_NAME = "uq_votes_bot_battle_id"


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
            _ensure_votes_battle_id_unique_index(connection)
        return

    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            Base.metadata.create_all(bind=connection, checkfirst=True)
            _ensure_votes_battle_id_unique_index(connection)
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


def _ensure_votes_battle_id_unique_index(connection: Connection) -> None:
    dialect_name = connection.dialect.name
    if dialect_name == "postgresql":
        if not _postgres_table_exists(connection, "votes"):
            return
        if _postgres_scoped_vote_unique_indexes_exist(connection):
            connection.execute(text(f"DROP INDEX IF EXISTS {_VOTES_BATTLE_ID_UNIQUE_INDEX_NAME}"))
            return
        _raise_for_duplicate_vote_battle_ids(connection, consumer_type="human")
        _raise_for_duplicate_vote_battle_ids(connection, consumer_type="bot")
        connection.execute(
            text(
                "ALTER TABLE votes DROP CONSTRAINT IF EXISTS "
                f"{_VOTES_BATTLE_ID_UNIQUE_INDEX_NAME}"
            )
        )
        connection.execute(text(f"DROP INDEX IF EXISTS {_VOTES_BATTLE_ID_UNIQUE_INDEX_NAME}"))
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                f"{_VOTES_HUMAN_BATTLE_ID_UNIQUE_INDEX_NAME} ON votes (battle_id) "
                "WHERE service_account_id IS NULL"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                f"{_VOTES_BOT_BATTLE_ID_UNIQUE_INDEX_NAME} ON votes (battle_id) "
                "WHERE service_account_id IS NOT NULL"
            )
        )
        _require_votes_battle_id_unique_index(connection)
        return

    if dialect_name == "sqlite":
        if not _sqlite_table_exists(connection, "votes"):
            return
        _raise_for_duplicate_vote_battle_ids(connection, consumer_type="human")
        _raise_for_duplicate_vote_battle_ids(connection, consumer_type="bot")
        connection.execute(text(f"DROP INDEX IF EXISTS {_VOTES_BATTLE_ID_UNIQUE_INDEX_NAME}"))
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                f"{_VOTES_HUMAN_BATTLE_ID_UNIQUE_INDEX_NAME} ON votes (battle_id) "
                "WHERE service_account_id IS NULL"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                f"{_VOTES_BOT_BATTLE_ID_UNIQUE_INDEX_NAME} ON votes (battle_id) "
                "WHERE service_account_id IS NOT NULL"
            )
        )
        _require_votes_battle_id_unique_index(connection)


def _raise_for_duplicate_vote_battle_ids(connection: Connection, *, consumer_type: str) -> None:
    predicate = (
        "service_account_id IS NOT NULL"
        if consumer_type == "bot"
        else "service_account_id IS NULL"
    )
    duplicate_rows = connection.execute(
        text(
            f"""
            SELECT battle_id, count(*) AS row_count
            FROM votes
            WHERE {predicate}
            GROUP BY battle_id
            HAVING count(*) > 1
            ORDER BY battle_id
            LIMIT 5
            """
        )
    ).all()
    if not duplicate_rows:
        return

    duplicates = ", ".join(f"{row[0]} ({row[1]} rows)" for row in duplicate_rows)
    raise RuntimeError(
        f"Cannot enforce {consumer_type} vote uniqueness because votes contains "
        f"duplicate battle_id rows: {duplicates}. Consolidate or delete duplicate "
        f"{consumer_type} votes for each battle, then restart the backend."
    )


def _require_votes_battle_id_unique_index(connection: Connection) -> None:
    dialect_name = connection.dialect.name
    if dialect_name == "postgresql":
        exists = _postgres_votes_battle_id_unique_index_exists(
            connection,
            _VOTES_HUMAN_BATTLE_ID_UNIQUE_INDEX_NAME,
            "service_account_id IS NULL",
        ) and _postgres_votes_battle_id_unique_index_exists(
            connection,
            _VOTES_BOT_BATTLE_ID_UNIQUE_INDEX_NAME,
            "service_account_id IS NOT NULL",
        )
    elif dialect_name == "sqlite":
        exists = _sqlite_votes_battle_id_unique_index_exists(
            connection,
            _VOTES_HUMAN_BATTLE_ID_UNIQUE_INDEX_NAME,
        ) and _sqlite_votes_battle_id_unique_index_exists(
            connection,
            _VOTES_BOT_BATTLE_ID_UNIQUE_INDEX_NAME,
        )
    else:
        exists = True

    if exists:
        return

    raise RuntimeError(
        "Cannot enforce vote uniqueness because scoped human and bot vote "
        "unique indexes are missing or invalid. Recreate them, then restart the backend."
    )


def _postgres_scoped_vote_unique_indexes_exist(connection: Connection) -> bool:
    return _postgres_votes_battle_id_unique_index_exists(
        connection,
        _VOTES_HUMAN_BATTLE_ID_UNIQUE_INDEX_NAME,
        "service_account_id IS NULL",
    ) and _postgres_votes_battle_id_unique_index_exists(
        connection,
        _VOTES_BOT_BATTLE_ID_UNIQUE_INDEX_NAME,
        "service_account_id IS NOT NULL",
    )


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


def _postgres_votes_battle_id_unique_index_exists(
    connection: Connection,
    index_name: str,
    predicate: str,
) -> bool:
    return bool(
        connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_class index_class
                    JOIN pg_index index_def
                      ON index_def.indexrelid = index_class.oid
                    JOIN pg_class table_class
                      ON table_class.oid = index_def.indrelid
                    JOIN pg_namespace namespace
                      ON namespace.oid = table_class.relnamespace
                    JOIN pg_attribute attribute
                      ON attribute.attrelid = table_class.oid
                     AND attribute.attnum = ANY(index_def.indkey)
                    WHERE namespace.nspname = current_schema()
                      AND table_class.relname = 'votes'
                      AND index_class.relname = :index_name
                      AND index_def.indisunique
                      AND index_def.indisvalid
                      AND index_def.indnatts = 1
                      AND replace(replace(pg_get_expr(index_def.indpred, index_def.indrelid), '(', ''), ')', '') = :predicate
                      AND index_def.indexprs IS NULL
                      AND attribute.attname = 'battle_id'
                )
                """
            ),
            {"index_name": index_name, "predicate": predicate},
        ).scalar_one()
    )


def _sqlite_table_exists(connection: Connection, table_name: str) -> bool:
    return bool(
        connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM sqlite_master
                    WHERE type = 'table'
                      AND name = :table_name
                )
                """
            ),
            {"table_name": table_name},
        ).scalar_one()
    )


def _sqlite_votes_battle_id_unique_index_exists(
    connection: Connection,
    expected_index_name: str,
) -> bool:
    indexes = connection.execute(text("PRAGMA index_list('votes')")).all()
    for index_row in indexes:
        index_name = index_row[1]
        is_unique = bool(index_row[2])
        if index_name != expected_index_name or not is_unique:
            continue

        columns = connection.execute(
            text(f"PRAGMA index_info('{index_name}')")
        ).all()
        return [column_row[2] for column_row in columns] == ["battle_id"]
    return False


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
