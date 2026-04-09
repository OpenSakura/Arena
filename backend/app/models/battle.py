"""app.models.battle

Battle and run tables.

Notes:
- A Battle ties a Task to two Runs (side A and side B).
- A Run stores the exact prompt/messages, params, output, and stats.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Battle(Base):
    __tablename__ = "battles"
    __table_args__ = (
        Index("ix_battles_task_id", "task_id"),
        Index("ix_battles_status_created_at", "status", "created_at"),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_battles_status",
        ),
        CheckConstraint("mode IN ('jp2zh_ab')", name="ck_battles_mode"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False
    )

    mode: Mapped[str] = mapped_column(
        String(64), nullable=False, default="jp2zh_ab", server_default="jp2zh_ab"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )

    metadata_json: Mapped[dict[str, object] | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("battle_id", "side", name="uq_runs_battle_side"),
        CheckConstraint("side IN ('A', 'B')", name="ck_runs_side"),
        Index("ix_runs_battle_id", "battle_id"),
        Index("ix_runs_model_id", "model_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    battle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("battles.id", ondelete="CASCADE"), nullable=False
    )
    side: Mapped[str] = mapped_column(String(1), nullable=False)
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("models.id", ondelete="RESTRICT"), nullable=False
    )

    request_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    prompt_rendered: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )

    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
