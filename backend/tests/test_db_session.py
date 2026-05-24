from __future__ import annotations

from types import SimpleNamespace

from app.db import session as db_session


def test_postgresql_engine_kwargs_apply_idle_transaction_timeout() -> None:
    settings = SimpleNamespace(
        database_idle_in_transaction_session_timeout_ms=45000,
    )

    kwargs = db_session._engine_connect_kwargs(
        "postgresql+psycopg://user:pass@db:5432/arena",
        settings,
    )

    assert kwargs == {
        "connect_args": {
            "options": "-c idle_in_transaction_session_timeout=45000ms"
        }
    }


def test_postgresql_engine_kwargs_support_bare_postgresql_scheme() -> None:
    settings = SimpleNamespace(
        database_idle_in_transaction_session_timeout_ms=30000,
    )

    kwargs = db_session._engine_connect_kwargs(
        "postgresql://user:pass@db:5432/arena",
        settings,
    )

    assert kwargs["connect_args"]["options"] == (
        "-c idle_in_transaction_session_timeout=30000ms"
    )


def test_non_postgresql_engine_kwargs_skip_idle_transaction_timeout() -> None:
    settings = SimpleNamespace(
        database_idle_in_transaction_session_timeout_ms=45000,
    )

    kwargs = db_session._engine_connect_kwargs("sqlite+pysqlite:///arena.db", settings)

    assert kwargs == {}


def test_disabled_idle_transaction_timeout_skips_engine_kwargs() -> None:
    settings = SimpleNamespace(
        database_idle_in_transaction_session_timeout_ms=0,
    )

    kwargs = db_session._engine_connect_kwargs(
        "postgresql+psycopg://user:pass@db:5432/arena",
        settings,
    )

    assert kwargs == {}
