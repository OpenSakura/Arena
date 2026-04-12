"""app.models.vote

Vote table.

Notes:
- Anonymous voting is allowed. Store anonymous identifiers + hashed IP/UA for
  abuse detection.
- Logged-in users additionally store `voter_user_id`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (
        CheckConstraint("winner IN ('A', 'B', 'tie')", name="ck_votes_winner"),
        CheckConstraint(
            "(voter_user_id IS NOT NULL) OR (voter_anon_id IS NOT NULL)",
            name="ck_votes_identity_present",
        ),
        Index(
            "uq_votes_battle_voter_user",
            "battle_id",
            "voter_user_id",
            unique=True,
            postgresql_where=sa_text("voter_user_id IS NOT NULL"),
        ),
        Index(
            "uq_votes_battle_voter_anon",
            "battle_id",
            "voter_anon_id",
            unique=True,
            postgresql_where=sa_text("voter_anon_id IS NOT NULL"),
        ),
        Index("ix_votes_battle_id", "battle_id"),
        Index("ix_votes_voter_user_id", "voter_user_id"),
        Index("ix_votes_voter_anon_id", "voter_anon_id"),
        Index("ix_votes_ip_hash", "ip_hash"),
        Index("ix_votes_created_at", "created_at"),
        # Composite index for leaderboard refresh: WHERE revealed IS TRUE ORDER BY created_at
        # (queries in leaderboard_refresh.py filter on revealed then sort by created_at).
        Index("ix_votes_revealed_created_at", "revealed", "created_at"),
        # Composite indexes for daily vote-cap queries (battles.py):
        # WHERE voter_user_id = ? AND created_at >= day_start AND created_at < day_end
        Index("ix_votes_voter_user_id_created_at", "voter_user_id", "created_at"),
        # WHERE voter_anon_id = ? AND created_at >= day_start AND created_at < day_end
        Index("ix_votes_voter_anon_id_created_at", "voter_anon_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    battle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("battles.id", ondelete="CASCADE"), nullable=False
    )

    winner: Mapped[str] = mapped_column(String(8), nullable=False)  # "A" | "B" | "tie"
    rubric: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    voter_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    voter_anon_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    ip_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_agent_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # When True, the voter has seen the model reveal and the vote is locked.
    revealed: Mapped[bool] = mapped_column(
        Boolean, server_default="false", default=False, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
