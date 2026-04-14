from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import app.utils.process_guard as process_guard_module
from app.utils.process_guard import (
    _BATTLE_ORCHESTRATOR_LOCK_KEY,
    acquire_battle_process_lock,
    release_battle_process_lock,
)


def _make_settings(
    database_url: str = "postgresql+psycopg://user:pass@localhost/arena",
):
    return SimpleNamespace(database_url=database_url)


def _reset_guard():
    process_guard_module._guard_conn = None


def test_acquire_succeeds_and_stores_connection(monkeypatch) -> None:
    _reset_guard()

    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = (True,)

    mock_psycopg = MagicMock()
    mock_psycopg.connect.return_value = mock_conn

    monkeypatch.setattr(process_guard_module, "get_settings", lambda: _make_settings())

    with patch.dict("sys.modules", {"psycopg": mock_psycopg}):
        acquire_battle_process_lock()

    assert process_guard_module._guard_conn is mock_conn
    mock_conn.execute.assert_called_once_with(
        "SELECT pg_try_advisory_lock(%s)", (_BATTLE_ORCHESTRATOR_LOCK_KEY,)
    )
    _reset_guard()


def test_acquire_raises_when_lock_already_held(monkeypatch) -> None:
    _reset_guard()

    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = (False,)

    mock_psycopg = MagicMock()
    mock_psycopg.connect.return_value = mock_conn

    monkeypatch.setattr(process_guard_module, "get_settings", lambda: _make_settings())

    with patch.dict("sys.modules", {"psycopg": mock_psycopg}):
        with pytest.raises(RuntimeError, match="battle orchestrator lock"):
            acquire_battle_process_lock()

    mock_conn.close.assert_called_once()
    assert process_guard_module._guard_conn is None


def test_acquire_raises_when_db_query_fails(monkeypatch) -> None:
    _reset_guard()

    mock_conn = MagicMock()
    mock_conn.execute.side_effect = OSError("connection refused")

    mock_psycopg = MagicMock()
    mock_psycopg.connect.return_value = mock_conn

    monkeypatch.setattr(process_guard_module, "get_settings", lambda: _make_settings())

    with patch.dict("sys.modules", {"psycopg": mock_psycopg}):
        with pytest.raises(
            RuntimeError, match="Failed to check battle orchestrator process lock"
        ):
            acquire_battle_process_lock()

    mock_conn.close.assert_called_once()
    assert process_guard_module._guard_conn is None


def test_release_closes_connection_and_clears_guard() -> None:
    mock_conn = MagicMock()
    process_guard_module._guard_conn = mock_conn

    release_battle_process_lock()

    mock_conn.close.assert_called_once()
    assert process_guard_module._guard_conn is None


def test_release_is_idempotent_when_not_acquired() -> None:
    _reset_guard()
    release_battle_process_lock()
    assert process_guard_module._guard_conn is None


def test_release_tolerates_connection_close_error() -> None:
    mock_conn = MagicMock()
    mock_conn.close.side_effect = OSError("already closed")
    process_guard_module._guard_conn = mock_conn

    release_battle_process_lock()

    assert process_guard_module._guard_conn is None


def test_acquire_strips_psycopg_driver_prefix_from_database_url(monkeypatch) -> None:
    _reset_guard()

    captured_dsn: list[str] = []

    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = (True,)

    mock_psycopg = MagicMock()

    def _capture_connect(dsn, **kwargs):
        captured_dsn.append(dsn)
        return mock_conn

    mock_psycopg.connect.side_effect = _capture_connect

    monkeypatch.setattr(
        process_guard_module,
        "get_settings",
        lambda: _make_settings("postgresql+psycopg://user:pass@db:5432/arena"),
    )

    with patch.dict("sys.modules", {"psycopg": mock_psycopg}):
        acquire_battle_process_lock()

    assert captured_dsn == ["postgresql://user:pass@db:5432/arena"]
    _reset_guard()
