"""app.core.security

Security primitives (OIDC bearer tokens, principals, role checks).

Notes:
- This project integrates with Authentik via standard OAuth2/OIDC.
- Public read endpoints may accept missing/invalid tokens and treat the
  requester as unauthenticated.
- Authenticated-only endpoints should depend on ``get_principal_required``.
- Admin endpoints must enforce authorization based on OIDC claims (e.g. groups).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
import logging
from typing import Any

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.service_tokens import (
    SERVICE_TOKEN_PREFIX,
    constant_time_verify,
    hash_service_token,
    has_required_scopes,
    normalize_scopes as normalize_service_token_scopes,
)
from app.db.session import get_db
from app.models.service_account import ServiceAccount, ServiceAccountToken
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
    actor_type: str = "human"
    # Set when authenticated; maps to our internal `users.id`.
    user_id: str | None = None
    oidc_issuer: str | None = None
    oidc_sub: str | None = None
    service_account_id: str | None = None
    service_account_name: str | None = None
    token_id: str | None = None
    scopes: tuple[str, ...] = Field(default_factory=tuple)
    claims: dict[str, Any] = Field(default_factory=dict)


def _upsert_user(db: Session, *, issuer: str, sub: str) -> User:
    stmt = select(User).where(User.oidc_issuer == issuer, User.oidc_sub == sub)
    user = db.execute(stmt).scalar_one_or_none()
    if user is not None:
        return user

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
            user = User(oidc_issuer=issuer, oidc_sub=sub)
            db.add(user)
            db.flush()
    except IntegrityError:
        user = db.execute(stmt).scalar_one_or_none()
        if user is None:
            raise

    return user


def claim_by_path(claims: dict[str, Any], path: str) -> Any:
    current: Any = claims
    for segment in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def normalize_groups(value: Any) -> set[str]:
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
    if creds.scheme.lower() != "bearer":
        return Principal(is_authenticated=False)

    if creds.credentials.startswith(SERVICE_TOKEN_PREFIX):
        return _principal_from_service_token(db=db, token=creds.credentials)

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
        actor_type=_principal_actor_type(user),
        user_id=str(user.id),
        oidc_issuer=user.oidc_issuer,
        oidc_sub=user.oidc_sub,
        claims=claims,
    )


def _principal_from_service_token(*, db: Session, token: str) -> Principal:
    settings = get_settings()
    try:
        candidate = hash_service_token(token, settings=settings)
    except ValueError as exc:
        logger.info("Malformed service token rejected: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid service token") from exc
    except RuntimeError as exc:
        logger.error("Service token rejected because hash secret is not configured")
        raise HTTPException(status_code=401, detail="Invalid service token") from exc

    stmt = (
        select(ServiceAccountToken, ServiceAccount, User)
        .join(
            ServiceAccount,
            ServiceAccount.id == ServiceAccountToken.service_account_id,
        )
        .join(User, User.id == ServiceAccount.bot_user_id)
        .where(
            ServiceAccountToken.token_prefix == candidate.token_prefix,
            ServiceAccountToken.token_hash == candidate.token_hash,
        )
    )

    rows = db.execute(stmt).all()
    for service_token, service_account, bot_user in rows:
        try:
            verified = constant_time_verify(
                token,
                service_token.token_hash,
                settings=settings,
            )
        except RuntimeError as exc:
            logger.error("Service token rejected because hash secret is not configured")
            raise HTTPException(status_code=401, detail="Invalid service token") from exc
        if verified:
            return _validated_service_token_principal(
                db=db,
                service_token=service_token,
                service_account=service_account,
                bot_user=bot_user,
            )

    raise HTTPException(status_code=401, detail="Invalid service token")


def _validated_service_token_principal(
    *,
    db: Session,
    service_token: ServiceAccountToken,
    service_account: ServiceAccount,
    bot_user: User,
) -> Principal:
    now = datetime.now(timezone.utc)

    if service_token.revoked_at is not None:
        raise HTTPException(status_code=401, detail="Service token revoked")
    if service_token.expires_at is not None and _is_past(service_token.expires_at, now):
        raise HTTPException(status_code=401, detail="Service token expired")
    if not service_account.is_active:
        raise HTTPException(status_code=403, detail="Service account is inactive")
    if _principal_actor_type(bot_user) != "bot":
        raise HTTPException(status_code=403, detail="Service account backing user is invalid")

    try:
        scopes = normalize_service_token_scopes(service_token.scopes)
    except ValueError as exc:
        logger.info("Stored service token scopes rejected: %s", exc)
        raise HTTPException(status_code=403, detail="Service token scopes are invalid") from exc

    service_token.last_used_at = now
    db.add(service_token)
    db.flush()

    return Principal(
        is_authenticated=True,
        actor_type="bot",
        user_id=str(bot_user.id),
        oidc_issuer=bot_user.oidc_issuer,
        oidc_sub=bot_user.oidc_sub,
        service_account_id=str(service_account.id),
        service_account_name=service_account.name,
        token_id=str(service_token.id),
        scopes=scopes,
        claims={},
    )


def _principal_actor_type(user: User) -> str:
    actor_type = getattr(user, "actor_type", None)
    return actor_type if actor_type in {"human", "bot"} else "human"


def _is_past(value: datetime, now: datetime) -> bool:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value <= now.replace(tzinfo=None)
    return value <= now


def get_principal_required(
    principal: Principal = Depends(get_principal_optional),
) -> Principal:
    """Dependency that requires an authenticated requester."""

    if not principal.is_authenticated or principal.user_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    return principal


def is_bot_principal(principal: Principal) -> bool:
    return getattr(principal, "actor_type", "human") == "bot"


def require_human_principal(principal: Principal) -> Principal:
    if is_bot_principal(principal):
        raise HTTPException(status_code=403, detail="Human principal required")
    return principal


def require_admin(principal: Principal = Depends(get_principal_optional)) -> Principal:
    """Dependency that enforces admin authorization."""

    if not principal.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required")
    if is_bot_principal(principal):
        raise HTTPException(status_code=403, detail="Human admin principal required")

    settings = get_settings()
    claim_value = claim_by_path(principal.claims, settings.oidc_admin_group_claim)
    groups = normalize_groups(claim_value)
    if settings.oidc_admin_group_name not in groups:
        raise HTTPException(status_code=403, detail="Admin group membership required")

    return principal


def require_scopes(
    required_scopes: Iterable[str] | str,
) -> Callable[[Principal], Principal]:
    normalized_required_scopes = normalize_service_token_scopes(required_scopes)

    def dependency(
        principal: Principal = Depends(get_principal_required),
    ) -> Principal:
        if not principal.is_authenticated or principal.user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not is_bot_principal(principal):
            raise HTTPException(status_code=403, detail="Service token principal required")
        try:
            authorized = has_required_scopes(principal.scopes, normalized_required_scopes)
        except ValueError as exc:
            logger.info("Service token principal has invalid scopes: %s", exc)
            authorized = False
        if not authorized:
            raise HTTPException(status_code=403, detail="Required service token scope missing")
        return principal

    return dependency
