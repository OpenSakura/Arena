from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.csrf import require_csrf_for_session
from app.core.security import Principal, require_admin
from app.core.service_tokens import (
    generate_service_token,
    hash_service_token,
    normalize_scopes,
)
from app.db.session import get_db
from app.models.service_account import ServiceAccount, ServiceAccountToken
from app.models.user import User
from app.schemas._types import UuidStr
from app.schemas.service_accounts import (
    ServiceAccountCreate,
    ServiceAccountListResponse,
    ServiceAccountPublic,
    ServiceAccountTokenCreate,
    ServiceAccountTokenCreateResponse,
    ServiceAccountTokenRedacted,
    ServiceAccountUpdate,
)
from app.utils.id import parse_uuid_or_422


_SERVICE_ACCOUNT_ISSUER = "system:service-account"


def require_oidc_admin(principal: Principal = Depends(require_admin)) -> Principal:
    return _require_oidc_admin(principal)


router = APIRouter(
    prefix="/admin",
    tags=["admin", "service-accounts"],
    dependencies=[Depends(require_oidc_admin), Depends(require_csrf_for_session)],
)


class ServiceAccountTokenRevokeResponse(BaseModel):
    token_id: str
    revoked: bool = True


@router.get("/service-accounts", response_model=ServiceAccountListResponse)
def list_service_accounts(db: Session = Depends(get_db)) -> ServiceAccountListResponse:
    service_accounts = (
        db.execute(select(ServiceAccount).order_by(ServiceAccount.created_at.desc()))
        .scalars()
        .all()
    )
    tokens_by_account = _load_tokens_by_service_account(db, service_accounts)
    return ServiceAccountListResponse(
        service_accounts=[
            _to_service_account(item, tokens_by_account.get(item.id, ()))
            for item in service_accounts
        ]
    )


@router.post(
    "/service-accounts",
    response_model=ServiceAccountPublic,
    status_code=status.HTTP_201_CREATED,
)
def create_service_account(
    payload: ServiceAccountCreate,
    admin: Principal = Depends(require_oidc_admin),
    db: Session = Depends(get_db),
) -> ServiceAccountPublic:
    service_account_id = uuid.uuid4()
    bot_user = User(
        oidc_issuer=_SERVICE_ACCOUNT_ISSUER,
        oidc_sub=f"service-account:{service_account_id}",
        actor_type="bot",
    )
    db.add(bot_user)
    db.flush()

    service_account = ServiceAccount(
        id=service_account_id,
        name=payload.name,
        description=payload.description,
        bot_user_id=bot_user.id,
        created_by_user_id=_principal_user_id(admin),
        is_active=payload.enabled,
    )
    db.add(service_account)
    _commit_or_conflict(db, detail="Service account creation conflicted")
    db.refresh(service_account)
    return _to_service_account(service_account)


@router.patch(
    "/service-accounts/{service_account_id}",
    response_model=ServiceAccountPublic,
)
def update_service_account(
    service_account_id: UuidStr,
    payload: ServiceAccountUpdate,
    db: Session = Depends(get_db),
) -> ServiceAccountPublic:
    service_account = db.get(
        ServiceAccount, parse_uuid_or_422(service_account_id, "service_account_id")
    )
    if service_account is None:
        raise HTTPException(status_code=404, detail="Service account not found")

    patch = payload.model_dump(exclude_unset=True)
    patch.pop("scopes", None)
    if "enabled" in patch:
        service_account.is_active = patch.pop("enabled")
    for field in ("name", "description"):
        if field in patch:
            setattr(service_account, field, patch[field])

    db.add(service_account)
    _commit_or_conflict(db, detail="Service account update conflicted")
    db.refresh(service_account)
    tokens = _load_tokens_for_service_account(db, service_account.id)
    return _to_service_account(service_account, tokens)


@router.post(
    "/service-accounts/{service_account_id}/tokens",
    response_model=ServiceAccountTokenCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_service_account_token(
    service_account_id: UuidStr,
    payload: ServiceAccountTokenCreate,
    db: Session = Depends(get_db),
) -> ServiceAccountTokenCreateResponse:
    service_account = db.get(
        ServiceAccount, parse_uuid_or_422(service_account_id, "service_account_id")
    )
    if service_account is None:
        raise HTTPException(status_code=404, detail="Service account not found")
    if not service_account.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inactive service accounts cannot receive new tokens",
        )

    try:
        scopes = list(normalize_scopes(payload.scopes))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    plaintext_token = generate_service_token()
    try:
        hashed = hash_service_token(plaintext_token)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail="Service token hashing is not configured",
        ) from exc

    token = ServiceAccountToken(
        service_account_id=service_account.id,
        token_prefix=hashed.token_prefix,
        token_hash=hashed.token_hash,
        scopes=scopes,
        expires_at=_parse_expires_at(payload.expires_at),
    )
    db.add(token)
    _commit_or_conflict(db, detail="Service account token creation conflicted")
    db.refresh(token)
    db.refresh(service_account)

    tokens = _load_tokens_for_service_account(db, service_account.id)
    return ServiceAccountTokenCreateResponse(
        service_account=_to_service_account(service_account, tokens),
        token=_to_token(token),
        plaintext_token=plaintext_token,
    )


@router.post(
    "/service-account-tokens/{token_id}/revoke",
    response_model=ServiceAccountTokenRevokeResponse,
)
def revoke_service_account_token(
    token_id: UuidStr,
    db: Session = Depends(get_db),
) -> ServiceAccountTokenRevokeResponse:
    token = db.get(ServiceAccountToken, parse_uuid_or_422(token_id, "token_id"))
    if token is None:
        raise HTTPException(status_code=404, detail="Service account token not found")

    if token.revoked_at is None:
        token.revoked_at = _utcnow()
        db.add(token)
        db.commit()

    return ServiceAccountTokenRevokeResponse(token_id=str(token.id))


def _require_oidc_admin(principal: Principal) -> Principal:
    actor_type = getattr(principal, "actor_type", None)
    oidc_sub = principal.oidc_sub or ""
    if (
        actor_type == "bot"
        or principal.oidc_issuer == _SERVICE_ACCOUNT_ISSUER
        or oidc_sub.startswith("service-account:")
    ):
        raise HTTPException(status_code=403, detail="OIDC admin principal required")
    return principal


def _principal_user_id(principal: Principal) -> uuid.UUID | None:
    if principal.user_id is None:
        return None
    try:
        return uuid.UUID(principal.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Invalid admin user id") from exc


def _load_tokens_by_service_account(
    db: Session,
    service_accounts: Iterable[ServiceAccount],
) -> dict[uuid.UUID, list[ServiceAccountToken]]:
    service_account_ids = [item.id for item in service_accounts]
    if not service_account_ids:
        return {}
    tokens = (
        db.execute(
            select(ServiceAccountToken)
            .where(ServiceAccountToken.service_account_id.in_(service_account_ids))
            .order_by(ServiceAccountToken.created_at.desc())
        )
        .scalars()
        .all()
    )
    grouped: dict[uuid.UUID, list[ServiceAccountToken]] = {}
    for token in tokens:
        grouped.setdefault(token.service_account_id, []).append(token)
    return grouped


def _load_tokens_for_service_account(
    db: Session,
    service_account_id: uuid.UUID,
) -> list[ServiceAccountToken]:
    return (
        db.execute(
            select(ServiceAccountToken)
            .where(ServiceAccountToken.service_account_id == service_account_id)
            .order_by(ServiceAccountToken.created_at.desc())
        )
        .scalars()
        .all()
    )


def _to_service_account(
    service_account: ServiceAccount,
    tokens: Iterable[ServiceAccountToken] = (),
) -> ServiceAccountPublic:
    token_list = list(tokens)
    return ServiceAccountPublic(
        id=str(service_account.id),
        name=service_account.name,
        description=service_account.description,
        enabled=service_account.is_active,
        scopes=list(_service_account_scope_union(token_list)),
        tokens=[_to_token(token) for token in token_list],
        created_at=_serialize_datetime(service_account.created_at),
        updated_at=_serialize_datetime(service_account.updated_at),
    )


def _to_token(token: ServiceAccountToken) -> ServiceAccountTokenRedacted:
    return ServiceAccountTokenRedacted(
        id=str(token.id),
        service_account_id=str(token.service_account_id),
        token_prefix=token.token_prefix,
        status=_token_status(token),
        scopes=list(normalize_scopes(token.scopes)),
        created_at=_serialize_datetime(token.created_at),
        expires_at=_serialize_optional_datetime(token.expires_at),
        last_used_at=_serialize_optional_datetime(token.last_used_at),
        revoked_at=_serialize_optional_datetime(token.revoked_at),
    )


def _service_account_scope_union(
    tokens: Iterable[ServiceAccountToken],
) -> tuple[str, ...]:
    scopes: set[str] = set()
    for token in tokens:
        if _token_status(token) == "active":
            scopes.update(normalize_scopes(token.scopes))
    return normalize_scopes(scopes)


def _token_status(token: ServiceAccountToken) -> str:
    if token.revoked_at is not None:
        return "revoked"
    expires_at = token.expires_at
    if expires_at is not None and _as_utc(expires_at) <= _utcnow():
        return "expired"
    return "active"


def _parse_expires_at(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="expires_at must be ISO 8601"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HTTPException(status_code=422, detail="expires_at must include timezone")
    expires_at = parsed.astimezone(timezone.utc)
    if expires_at <= _utcnow():
        raise HTTPException(status_code=422, detail="expires_at must be in the future")
    return expires_at


def _serialize_optional_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _serialize_datetime(value)


def _serialize_datetime(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _commit_or_conflict(db: Session, *, detail: str) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=detail) from exc
