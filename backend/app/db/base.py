"""app.db.base

SQLAlchemy declarative base.

Notes:
- ORM models should inherit from `Base`.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
