from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest

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
        table_exists: bool = True,
        provider_column_exists: bool = True,
        unique_constraint_exists: bool = False,
        duplicates: list[tuple[str, int]] | None = None,
    ) -> None:
        self.table_exists = table_exists
        self.provider_column_exists = provider_column_exists
        self.unique_constraint_exists = unique_constraint_exists
        self.duplicates = duplicates or []
        self.sql: list[str] = []

    def execute(self, statement: object, params: dict[str, object] | None = None) -> Any:
        sql = str(statement)
        self.sql.append(" ".join(sql.split()))
        params = params or {}

        if "to_regclass" in sql:
            return _ScalarResult(self.table_exists)
        if "information_schema.columns" in sql:
            assert params["table_name"] == "models"
            assert params["column_name"] == "provider_type"
            return _ScalarResult(self.provider_column_exists)
        if "FROM pg_constraint" in sql:
            return _ScalarResult(self.unique_constraint_exists)
        if "HAVING count(*) > 1" in sql:
            return _RowsResult(self.duplicates)
        return _RowsResult([])


def test_legacy_model_registry_upgrade_skips_missing_models_table() -> None:
    connection = _BootstrapConnection(table_exists=False)

    bootstrap._upgrade_legacy_model_registry_schema(connection)

    assert not any("ALTER TABLE models" in sql for sql in connection.sql)


def test_legacy_model_registry_upgrade_drops_provider_type_and_adds_unique_name() -> None:
    connection = _BootstrapConnection()

    bootstrap._upgrade_legacy_model_registry_schema(connection)

    assert "DROP INDEX IF EXISTS ix_models_model_name" in connection.sql
    assert any("DROP CONSTRAINT IF EXISTS uq_models_provider_model" in sql for sql in connection.sql)
    assert any("ADD CONSTRAINT uq_models_model_name UNIQUE (model_name)" in sql for sql in connection.sql)
    assert any("DROP COLUMN provider_type" in sql for sql in connection.sql)


def test_legacy_model_registry_upgrade_requires_unique_model_names() -> None:
    connection = _BootstrapConnection(duplicates=[("same-model", 2)])

    with pytest.raises(RuntimeError, match="same-model"):
        bootstrap._upgrade_legacy_model_registry_schema(connection)
