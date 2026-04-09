"""app.models.prompt_template

Prompt template tables.

Notes:
- Prompt templates are versioned for reproducibility.
- Deletion is only blocked while a template is bound to a model config.
- Store a content hash so runs can reference the exact prompt used.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_prompt_templates_name_version"),
        Index("ix_prompt_templates_content_hash", "content_hash"),
        CheckConstraint("version >= 1", name="ck_prompt_templates_version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    template_text: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
