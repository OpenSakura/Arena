"""app.models.user

User and user profile tables.

Notes:
- The platform allows anonymous usage; users are created/upserted only when a
  valid OIDC identity is present.
- Profile fields capture language capability and translation experience for
  downstream filtering/analysis.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("oidc_issuer", "oidc_sub", name="uq_users_oidc_issuer_sub"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    oidc_issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    oidc_sub: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )

    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ui_language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    zh_variant: Mapped[str | None] = mapped_column(String(32), nullable=True)

    jp_proficiency: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    translation_experience: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    consents: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
