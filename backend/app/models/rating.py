"""app.models.rating

Rating tables.

Notes:
- Ratings are derived from votes. Persist current snapshot + event history.
- MVP uses Elo with tie support.
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
    UniqueConstraint,
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


class RatingEvent(Base):
    __tablename__ = "rating_events"
    __table_args__ = (
        UniqueConstraint("vote_id", name="uq_rating_events_vote_id"),
        Index("ix_rating_events_model_a_id", "model_a_id"),
        Index("ix_rating_events_model_b_id", "model_b_id"),
        Index("ix_rating_events_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    vote_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("votes.id", ondelete="CASCADE"), nullable=False
    )

    model_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("models.id", ondelete="RESTRICT"), nullable=False
    )
    model_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("models.id", ondelete="RESTRICT"), nullable=False
    )

    delta_a: Mapped[float] = mapped_column(Float, nullable=False)
    delta_b: Mapped[float] = mapped_column(Float, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
