"""app.models.task

Task and task set tables.

Notes:
- A Task is a source JP passage to translate.
- TaskSets are curated groups used for filtering/benchmarks.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TaskSet(Base):
    __tablename__ = "task_sets"
    __table_args__ = (UniqueConstraint("name", name="uq_task_sets_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_task_set_id", "task_set_id"),
        Index("ix_tasks_source_target_lang", "source_lang", "target_lang"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_set_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("task_sets.id", ondelete="SET NULL"),
        nullable=True,
    )

    source_lang: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ja", server_default=text("'ja'")
    )
    target_lang: Mapped[str] = mapped_column(
        String(16), nullable=False, default="zh", server_default=text("'zh'")
    )
    source_text: Mapped[str] = mapped_column(Text, nullable=False)

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
