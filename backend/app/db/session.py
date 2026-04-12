"""app.db.session

SQLAlchemy engine + session factory.

Notes:
- Keep engine creation lazy to make testing and CLI scripts easier.
- Connection pooling defaults are fine for MVP; tune for prod later.
"""

from __future__ import annotations

from collections.abc import Generator
import threading

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


_lock = threading.Lock()
_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    engine = _engine
    if engine is not None:
        return engine

    with _lock:
        engine = _engine
        if engine is None:
            settings = get_settings()
            engine = create_engine(settings.database_url, pool_pre_ping=True)
            _engine = engine
        return engine


def get_sessionmaker() -> sessionmaker[Session]:
    global _SessionLocal, _engine
    session_local = _SessionLocal
    if session_local is not None:
        return session_local

    with _lock:
        session_local = _SessionLocal
        if session_local is None:
            engine = _engine
            if engine is None:
                settings = get_settings()
                engine = create_engine(settings.database_url, pool_pre_ping=True)
                _engine = engine
            session_local = sessionmaker(
                bind=engine,
                autocommit=False,
                autoflush=False,
                expire_on_commit=False,
            )
            _SessionLocal = session_local
        return session_local


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session.

    On the success path the session is committed so that route handlers
    do not need to remember to call ``db.commit()`` themselves.
    If the handler already committed, the extra commit is a no-op
    (empty transaction).  On any exception the session is rolled back.
    """

    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
