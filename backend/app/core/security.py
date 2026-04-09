"""app.core.security

Security primitives (OIDC bearer tokens, principals, role checks).

Notes:
- This project integrates with Authentik via standard OAuth2/OIDC.
- Anonymous use is allowed; most endpoints should accept missing/invalid tokens
  and treat the requester as unauthenticated.
- Admin endpoints must enforce authorization based on OIDC claims (e.g. groups).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models.user import User
from app.services.oidc import (
    OIDCConfigurationError,
    OIDCVerificationError,
    OIDCVerifier,
    get_oidc_verifier,
)


bearer_scheme = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)


class Principal(BaseModel):
    """Represents the requester identity (or lack thereof)."""

    is_authenticated: bool = False
    # Set when authenticated; maps to our internal `users.id`.
    user_id: str | None = None
    oidc_issuer: str | None = None
    oidc_sub: str | None = None
    claims: dict[str, Any] = Field(default_factory=dict)


def _upsert_user(db: Session, *, issuer: str, sub: str) -> User:
    stmt = select(User).where(User.oidc_issuer == issuer, User.oidc_sub == sub)
    user = db.execute(stmt).scalar_one_or_none()
    if user is not None:
        return user

    user = User(oidc_issuer=issuer, oidc_sub=sub)
    db.add(user)

    try:
        # Use flush (not commit) to keep the user upsert inside the
        # per-request transaction boundary. Committing here would break
        # transaction atomicity: if the endpoint handler's DB work fails,
        # the user upsert could not be rolled back.
        #
        # Use begin_nested() (savepoint) so that the IntegrityError only
        # rolls back this insert attempt, preserving any prior work in
        # the same request transaction.
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        user = db.execute(stmt).scalar_one_or_none()
        if user is None:
            raise

    return user


def _claim_by_path(claims: dict[str, Any], path: str) -> Any:
    current: Any = claims
    for segment in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def _normalize_groups(value: Any) -> set[str]:
    if value is None:
        return set()

    if isinstance(value, str):
        raw = value.replace(",", " ").split()
        return {item for item in raw if item}

    if isinstance(value, (list, tuple, set)):
        groups: set[str] = set()
        for item in value:
            if isinstance(item, str) and item:
                groups.add(item)
        return groups

    return set()


async def get_principal_optional(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
    oidc_verifier: OIDCVerifier = Depends(get_oidc_verifier),
) -> Principal:
    """Return an authenticated principal when possible.

    Notes:
    - Missing or invalid bearer tokens return unauthenticated principal.
    - Valid tokens are mapped to an internal user row keyed by (issuer, sub).
    """

    if creds is None:
        return Principal(is_authenticated=False)

    try:
        claims = await oidc_verifier.verify(creds.credentials)
    except (OIDCConfigurationError, OIDCVerificationError) as exc:
        logger.info("OIDC token rejected: %s", exc)
        return Principal(is_authenticated=False)

    issuer = claims.get("iss")
    sub = claims.get("sub")
    if not isinstance(issuer, str) or not issuer:
        logger.info("OIDC token missing issuer claim")
        return Principal(is_authenticated=False, claims=claims)
    if not isinstance(sub, str) or not sub:
        logger.info("OIDC token missing subject claim")
        return Principal(is_authenticated=False, claims=claims)

    try:
        user = _upsert_user(db, issuer=issuer, sub=sub)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to upsert OIDC user")
        raise HTTPException(status_code=500, detail="Failed to persist user")

    return Principal(
        is_authenticated=True,
        user_id=str(user.id),
        oidc_issuer=user.oidc_issuer,
        oidc_sub=user.oidc_sub,
        claims=claims,
    )


def require_admin(principal: Principal = Depends(get_principal_optional)) -> Principal:
    """Dependency that enforces admin authorization."""

    if not principal.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required")

    settings = get_settings()
    claim_value = _claim_by_path(principal.claims, settings.oidc_admin_group_claim)
    groups = _normalize_groups(claim_value)
    if settings.oidc_admin_group_name not in groups:
        raise HTTPException(status_code=403, detail="Admin group membership required")

    return principal
