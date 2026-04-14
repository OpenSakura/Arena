from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.core.config import get_settings

if TYPE_CHECKING:
    import psycopg

logger = logging.getLogger(__name__)

# Stable numeric key for the arena battle-orchestrator ownership lock.
# Chosen to be recognisable in pg_locks while avoiding collisions with
# any application-table OIDs (which are typically <16 384 for system
# tables and grow from there).  Constant must never change once deployed.
_BATTLE_ORCHESTRATOR_LOCK_KEY = 0x4172656E6100_0001  # "Arena\0" + 1

_guard_conn: psycopg.Connection[Any] | None = None


def acquire_battle_process_lock() -> None:
    """Acquire a Postgres session-level advisory lock for battle orchestration.

    Only one OS process may hold this lock at a time.  The lock is released
    automatically when the underlying connection closes (crash-safe).

    Raises RuntimeError if:
    - another process already holds the lock (duplicate worker), or
    - the database is unreachable at startup.

    Must be called once during application startup and matched with a call to
    release_battle_process_lock() during shutdown.
    """
    global _guard_conn

    import psycopg  # local import so the module is importable without psycopg installed in test envs

    settings = get_settings()
    db_url = settings.database_url

    # psycopg expects a libpq-style DSN or keyword args; SQLAlchemy URLs use
    # the "postgresql+psycopg://" scheme.  Strip the driver prefix.
    if db_url.startswith("postgresql+psycopg://"):
        dsn = "postgresql://" + db_url[len("postgresql+psycopg://") :]
    elif db_url.startswith("postgresql+psycopg2://"):
        dsn = "postgresql://" + db_url[len("postgresql+psycopg2://") :]
    else:
        dsn = db_url

    conn = psycopg.connect(dsn, autocommit=True)
    try:
        row = conn.execute(
            "SELECT pg_try_advisory_lock(%s)", (_BATTLE_ORCHESTRATOR_LOCK_KEY,)
        ).fetchone()
        acquired = row[0] if row else False
    except Exception as exc:
        conn.close()
        raise RuntimeError(
            "Failed to check battle orchestrator process lock in Postgres"
        ) from exc

    if not acquired:
        conn.close()
        raise RuntimeError(
            "Another process already holds the battle orchestrator lock "
            "(pg_try_advisory_lock key 0x{:X}). "
            "Only one API worker may run at a time. "
            "If no other worker is running, a previous process may not have "
            "shut down cleanly — wait a moment and retry, or check pg_locks.".format(
                _BATTLE_ORCHESTRATOR_LOCK_KEY
            )
        )

    _guard_conn = conn
    logger.info(
        "Battle orchestrator process lock acquired (pg_advisory key 0x%X)",
        _BATTLE_ORCHESTRATOR_LOCK_KEY,
    )


def release_battle_process_lock() -> None:
    """Release the advisory lock and close the guard connection.

    Safe to call even if acquire was never called or already released.
    """
    global _guard_conn
    conn = _guard_conn
    _guard_conn = None
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        logger.info(
            "Battle orchestrator process lock released (pg_advisory key 0x%X)",
            _BATTLE_ORCHESTRATOR_LOCK_KEY,
        )
