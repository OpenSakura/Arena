"""app.models.rating

Rating tables.

Notes:
- Ratings are derived from votes.
- ``model_ratings`` persists the current Elo snapshot only.
- Bradley-Terry leaderboard values are computed on demand and are not stored
  in this table.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ModelRating(Base):
    __tablename__ = "model_ratings"
    __table_args__ = (Index("ix_model_ratings_rating", "rating"),)

    model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("models.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    rating: Mapped[float] = mapped_column(
        Float, nullable=False, default=1000.0, server_default="1000.0"
    )
    games_played: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
