from __future__ import annotations

from collections.abc import Callable
import uuid
from typing import Any, TypeVar

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import Principal, get_principal_optional
from app.db.session import get_db
from app.models.auth_session import AuthSession
from app.services.auth_session import verify_auth_session_csrf_token


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_EXEMPT_REASON_ATTR = "csrf_exempt_reason"

_Endpoint = TypeVar("_Endpoint", bound=Callable[..., Any])


def csrf_exempt(reason: str) -> Callable[[_Endpoint], _Endpoint]:
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise ValueError("CSRF exemption reason cannot be empty")

    def decorator(endpoint: _Endpoint) -> _Endpoint:
        setattr(endpoint, CSRF_EXEMPT_REASON_ATTR, normalized_reason)
        return endpoint

    return decorator


def get_csrf_exempt_reason(endpoint: Callable[..., Any]) -> str | None:
    reason = getattr(endpoint, CSRF_EXEMPT_REASON_ATTR, None)
    if isinstance(reason, str) and reason.strip():
        return reason.strip()
    return None


def require_csrf_for_session(
    request: Request,
    principal: Principal = Depends(get_principal_optional),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    if request.method.upper() not in UNSAFE_METHODS:
        return
    if not principal.is_authenticated:
        return
    if not _is_session_authenticated(request=request, principal=principal):
        return

    auth_session = _resolve_auth_session(request=request, principal=principal, db=db)
    raw_csrf_token = request.headers.get(settings.auth_csrf_header_name)
    if raw_csrf_token is None or not raw_csrf_token.strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token required",
        )
    if not verify_auth_session_csrf_token(
        auth_session,
        csrf_token=raw_csrf_token,
        session_token=request.cookies.get(settings.auth_session_cookie_name),
        settings=settings,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token",
        )


def _resolve_auth_session(
    *,
    request: Request,
    principal: Principal,
    db: Session,
) -> AuthSession:
    request_session = getattr(request.state, "auth_session", None)
    if isinstance(request_session, AuthSession):
        return request_session

    session_id = principal.auth_session_id or getattr(
        request.state,
        "auth_session_id",
        None,
    )
    if session_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF session context required",
        )

    try:
        auth_session_id = uuid.UUID(str(session_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF session context invalid",
        ) from exc

    auth_session = db.get(AuthSession, auth_session_id)
    if auth_session is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF session context invalid",
        )
    return auth_session


def _is_session_authenticated(*, request: Request, principal: Principal) -> bool:
    return (
        principal.auth_method == "session"
        or principal.auth_session_id is not None
        or getattr(request.state, "auth_session", None) is not None
        or getattr(request.state, "auth_session_id", None) is not None
    )
