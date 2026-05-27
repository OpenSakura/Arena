from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.db import bootstrap


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one(self) -> object:
        return self._value


class _RowsResult:
    def __init__(self, rows: Iterable[tuple[object, ...]]) -> None:
        self._rows = list(rows)

    def all(self) -> list[tuple[object, ...]]:
        return self._rows


class _BootstrapConnection:
    def __init__(
        self,
        *,
        table_exists: bool | dict[str, bool] = True,
        provider_column_exists: bool = True,
        runs_response_full_column_exists: bool = False,
        unique_constraint_exists: bool = False,
        duplicates: list[tuple[str, int]] | None = None,
        votes_unique_index_exists: bool = False,
        vote_duplicates: list[tuple[str, int]] | None = None,
    ) -> None:
        self.dialect = SimpleNamespace(name="postgresql")
        self.table_exists = table_exists
        self.provider_column_exists = provider_column_exists
        self.runs_response_full_column_exists = runs_response_full_column_exists
        self.unique_constraint_exists = unique_constraint_exists
        self.duplicates = duplicates or []
        self.votes_unique_index_exists = votes_unique_index_exists
        self.vote_duplicates = vote_duplicates or []
        self.sql: list[str] = []

    def execute(self, statement: object, params: dict[str, object] | None = None) -> Any:
        sql = str(statement)
        self.sql.append(" ".join(sql.split()))
        params = params or {}

        if "to_regclass" in sql:
            table_name = str(params["table_name"])
            if isinstance(self.table_exists, dict):
                return _ScalarResult(self.table_exists.get(table_name, False))
            return _ScalarResult(self.table_exists)
        if "information_schema.columns" in sql:
            if params["table_name"] == "models":
                assert params["column_name"] == "provider_type"
                return _ScalarResult(self.provider_column_exists)
            if params["table_name"] == "runs":
                assert params["column_name"] == "response_full"
                return _ScalarResult(self.runs_response_full_column_exists)
            raise AssertionError(f"unexpected column lookup: {params}")
        if "FROM pg_constraint" in sql:
            return _ScalarResult(self.unique_constraint_exists)
        if "FROM pg_class index_class" in sql:
            return _ScalarResult(self.votes_unique_index_exists)
        if "HAVING count(*) > 1" in sql:
            if "FROM votes" in sql:
                return _RowsResult(self.vote_duplicates)
            return _RowsResult(self.duplicates)
        if "CREATE UNIQUE INDEX IF NOT EXISTS uq_votes_battle_id" in sql:
            self.votes_unique_index_exists = True
        return _RowsResult([])


def test_legacy_model_registry_upgrade_skips_missing_models_table() -> None:
    connection = _BootstrapConnection(table_exists=False)

    bootstrap._upgrade_legacy_model_registry_schema(connection)

    assert not any("ALTER TABLE models" in sql for sql in connection.sql)


def test_legacy_model_registry_upgrade_drops_provider_type_and_adds_unique_name() -> None:
    connection = _BootstrapConnection()

    bootstrap._upgrade_legacy_model_registry_schema(connection)

    assert "DROP INDEX IF EXISTS ix_models_model_name" in connection.sql
    assert any(
        "DROP CONSTRAINT IF EXISTS uq_models_provider_model" in sql
        for sql in connection.sql
    )
    assert any(
        "ADD CONSTRAINT uq_models_model_name UNIQUE (model_name)" in sql
        for sql in connection.sql
    )
    assert any("DROP COLUMN provider_type" in sql for sql in connection.sql)


def test_legacy_model_registry_upgrade_uses_models_table_existence() -> None:
    connection = _BootstrapConnection(table_exists={"models": False, "runs": True})

    bootstrap._upgrade_legacy_model_registry_schema(connection)

    assert not any("ALTER TABLE models" in sql for sql in connection.sql)


def test_legacy_model_registry_upgrade_requires_unique_model_names() -> None:
    connection = _BootstrapConnection(duplicates=[("same-model", 2)])

    with pytest.raises(RuntimeError, match="same-model"):
        bootstrap._upgrade_legacy_model_registry_schema(connection)


def test_runs_response_full_bootstrap_adds_missing_jsonb_column() -> None:
    connection = _BootstrapConnection(
        table_exists={"runs": True},
        runs_response_full_column_exists=False,
    )

    bootstrap._ensure_runs_response_full_column(connection)

    assert any(
        "ALTER TABLE runs ADD COLUMN response_full JSONB" in sql
        for sql in connection.sql
    )


def test_runs_response_full_bootstrap_is_idempotent() -> None:
    connection = _BootstrapConnection(
        table_exists={"runs": True},
        runs_response_full_column_exists=True,
    )

    bootstrap._ensure_runs_response_full_column(connection)

    assert not any(
        "ALTER TABLE runs ADD COLUMN response_full JSONB" in sql
        for sql in connection.sql
    )


def test_runs_response_full_bootstrap_skips_missing_runs_table() -> None:
    connection = _BootstrapConnection(table_exists={"runs": False})

    bootstrap._ensure_runs_response_full_column(connection)

    assert not any("ALTER TABLE runs" in sql for sql in connection.sql)


def test_votes_battle_id_bootstrap_adds_missing_unique_index_postgres() -> None:
    connection = _BootstrapConnection(table_exists={"votes": True})

    bootstrap._ensure_votes_battle_id_unique_index(connection)

    assert any(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_votes_battle_id ON votes (battle_id)"
        in sql
        for sql in connection.sql
    )


def test_votes_battle_id_bootstrap_is_idempotent_postgres() -> None:
    connection = _BootstrapConnection(
        table_exists={"votes": True},
        votes_unique_index_exists=True,
    )

    bootstrap._ensure_votes_battle_id_unique_index(connection)

    assert not any("CREATE UNIQUE INDEX" in sql for sql in connection.sql)


def test_votes_battle_id_bootstrap_rejects_existing_duplicates_postgres() -> None:
    connection = _BootstrapConnection(
        table_exists={"votes": True},
        vote_duplicates=[("battle-1", 2)],
    )

    with pytest.raises(RuntimeError, match="duplicate battle_id rows: battle-1"):
        bootstrap._ensure_votes_battle_id_unique_index(connection)

    assert not any("CREATE UNIQUE INDEX" in sql for sql in connection.sql)


def test_votes_battle_id_bootstrap_upgrades_existing_sqlite_table(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'legacy-votes.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE votes (
                    id TEXT PRIMARY KEY,
                    battle_id TEXT NOT NULL,
                    voter_user_id TEXT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX uq_votes_battle_voter_user "
                "ON votes (battle_id, voter_user_id)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO votes (id, battle_id, voter_user_id) "
                "VALUES ('vote-1', 'battle-1', 'voter-1')"
            )
        )

        bootstrap._ensure_votes_battle_id_unique_index(connection)
        bootstrap._ensure_votes_battle_id_unique_index(connection)

        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO votes (id, battle_id, voter_user_id) "
                    "VALUES ('vote-2', 'battle-1', 'voter-2')"
                )
            )
    engine.dispose()


def test_votes_battle_id_bootstrap_rejects_existing_duplicates_sqlite(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'duplicate-votes.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE votes (
                    id TEXT PRIMARY KEY,
                    battle_id TEXT NOT NULL,
                    voter_user_id TEXT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX uq_votes_battle_voter_user "
                "ON votes (battle_id, voter_user_id)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO votes (id, battle_id, voter_user_id) "
                "VALUES ('vote-1', 'battle-1', 'voter-1')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO votes (id, battle_id, voter_user_id) "
                "VALUES ('vote-2', 'battle-1', 'voter-2')"
            )
        )

        with pytest.raises(RuntimeError, match="Consolidate or delete duplicate votes"):
            bootstrap._ensure_votes_battle_id_unique_index(connection)
    engine.dispose()
