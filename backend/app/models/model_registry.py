"""app.models.model_registry

Model registry tables.

Notes:
- This stores *config* needed to call models via your existing gateway/provider.
- Provider secrets are stored encrypted at rest (see `app.core.crypto`).
- For security, only trusted admins should be able to create/modify models.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
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


class Model(Base):
    __tablename__ = "models"
    __table_args__ = (
        Index("ix_models_enabled_visibility", "enabled", "visibility"),
        Index("ix_models_model_name", "model_name"),
        UniqueConstraint(
            "provider_type", "model_name", name="uq_models_provider_model"
        ),
        CheckConstraint(
            "visibility IN ('public', 'private')", name="ck_models_visibility"
        ),
        CheckConstraint(
            "temperature IS NULL OR (temperature >= 0 AND temperature <= 2)",
            name="ck_models_temperature",
        ),
        CheckConstraint(
            "frequency_penalty IS NULL OR (frequency_penalty >= -2 AND frequency_penalty <= 2)",
            name="ck_models_frequency_penalty",
        ),
        CheckConstraint(
            "presence_penalty IS NULL OR (presence_penalty >= -2 AND presence_penalty <= 2)",
            name="ck_models_presence_penalty",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Display metadata
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)

    # Endpoint configuration (for openai-compatible gateways, etc.)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)

    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    visibility: Mapped[str] = mapped_column(
        String(32), nullable=False, default="public", server_default=text("'public'")
    )

    tags: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Common OpenAI-compatible sampling params (first-class for UI/editing).
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    frequency_penalty: Mapped[float | None] = mapped_column(Float, nullable=True)
    presence_penalty: Mapped[float | None] = mapped_column(Float, nullable=True)

    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Encrypted provider token (optional). Store ciphertext here.
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
