"""ORM schema bootstrap entry point."""

from __future__ import annotations

from app.db.base import Base
from app.db.session import get_engine
import app.models  # noqa: F401


def bootstrap_schema() -> None:
    """Create all ORM tables for the configured database."""

    engine = get_engine()
    Base.metadata.create_all(bind=engine, checkfirst=True)


if __name__ == "__main__":
    bootstrap_schema()
