from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

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


class OAuthLoginState(Base):
    __tablename__ = "oauth_login_states"
    __table_args__ = (
        UniqueConstraint("state_hash", name="uq_oauth_login_states_state_hash"),
        CheckConstraint(
            "length(state_hash) = 64",
            name="ck_oauth_login_states_state_hash_length",
        ),
        CheckConstraint(
            "length(login_binding_hash) = 64",
            name="ck_oauth_login_states_login_binding_hash_length",
        ),
        Index("ix_oauth_login_states_expires_at", "expires_at"),
        Index("ix_oauth_login_states_consumed_at", "consumed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    login_binding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce: Mapped[str] = mapped_column(Text, nullable=False)
    code_verifier: Mapped[str] = mapped_column(Text, nullable=False)
    return_to: Mapped[str] = mapped_column(Text, nullable=False, default="/")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        UniqueConstraint(
            "session_token_hash", name="uq_auth_sessions_session_token_hash"
        ),
        CheckConstraint(
            "length(session_token_hash) = 64",
            name="ck_auth_sessions_session_token_hash_length",
        ),
        CheckConstraint(
            "length(csrf_token_hash) = 64",
            name="ck_auth_sessions_csrf_token_hash_length",
        ),
        Index("ix_auth_sessions_user_id", "user_id"),
        Index("ix_auth_sessions_expires_at", "expires_at"),
        Index("ix_auth_sessions_revoked_at", "revoked_at"),
        Index("ix_auth_sessions_oidc_identity", "oidc_issuer", "oidc_sub"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    oidc_issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    oidc_sub: Mapped[str] = mapped_column(String(512), nullable=False)
    claims: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
