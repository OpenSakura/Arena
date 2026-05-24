"""ORM schema bootstrap entry point."""

from __future__ import annotations

from app.db.base import Base
from app.db.session import get_engine
import app.models  # noqa: F401
from sqlalchemy import text


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
            Base.metadata.create_all(bind=connection, checkfirst=True)
        return

    Base.metadata.create_all(bind=engine, checkfirst=True)


if __name__ == "__main__":
    bootstrap_schema()
