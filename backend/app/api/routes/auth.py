from __future__ import annotations

import logging
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.routes.me import _check_admin, _serialize_profile, _serialize_user
from app.core.config import Settings, get_settings
from app.core.security import Principal, _upsert_user
from app.db.session import get_db
from app.models.auth_session import AuthSession
from app.models.user import User, UserProfile
from app.schemas.me import ProfilePublic, UserPublic
from app.services.auth_session import (
    consume_oauth_login_state,
    create_auth_session,
    create_oauth_login_state,
    load_auth_session,
    load_user_for_auth_session,
    refresh_auth_session_last_seen,
    revoke_auth_session,
    sanitize_return_to,
    stable_auth_session_csrf_token,
    verify_auth_session_csrf_token,
)
from app.services.oidc_client import (
    OIDCConfidentialClient,
    OIDCConfigurationError,
    OIDCVerificationError,
    OIDCTokenExchangeError,
    get_oidc_confidential_client,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

_NON_PRODUCTION_ENVS = {"dev", "development", "test", "testing", "local"}
_CALLBACK_FAILURE_RETURN_TO = "/?auth_error=login"


class AuthSessionResponse(BaseModel):
    authenticated: bool
    is_admin: bool = False
    user: UserPublic | None = None
    profile: ProfilePublic | None = None
    csrf_token: str | None = None


class LogoutResponse(BaseModel):
    ok: bool
    authenticated: bool = False
    logout_url: str | None = None


@router.get("/login")
async def login(
    request: Request,
    returnTo: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    oidc_client: OIDCConfidentialClient = Depends(get_oidc_confidential_client),
) -> RedirectResponse:
    try:
        authorization_request = await oidc_client.create_authorization_request()
        login_state = create_oauth_login_state(
            db,
            nonce=authorization_request.nonce,
            code_verifier=authorization_request.code_verifier,
            return_to=_sanitize_return_to(
                returnTo,
                request=request,
                settings=settings,
            ),
            settings=settings,
        )
    except (OIDCConfigurationError, RuntimeError) as exc:
        logger.info("Unable to start OIDC login: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured",
        ) from exc

    response = RedirectResponse(
        url=_authorization_url_with_state(
            authorization_request.authorization_url,
            state=login_state.state,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    _set_login_binding_cookie(
        response,
        login_binding_token=login_state.login_binding_token,
        settings=settings,
    )
    return response


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    oidc_client: OIDCConfidentialClient = Depends(get_oidc_confidential_client),
) -> RedirectResponse:
    _ = error_description
    if error is not None:
        if state:
            consume_oauth_login_state(
                db,
                state=state,
                login_binding_token=request.cookies.get(settings.auth_login_state_cookie_name),
                settings=settings,
            )
        logger.info("OIDC provider returned an authorization error")
        return _callback_failure_response(settings=settings)
    if not state or not code:
        logger.info("OIDC callback rejected before token exchange")
        return _callback_failure_response(settings=settings)

    consumed_state = consume_oauth_login_state(
        db,
        state=state,
        login_binding_token=request.cookies.get(settings.auth_login_state_cookie_name),
        settings=settings,
    )
    if consumed_state is None:
        logger.info("OIDC callback rejected before token exchange")
        return _callback_failure_response(settings=settings)

    try:
        exchange_result = await oidc_client.exchange_code(
            code=code,
            code_verifier=consumed_state.code_verifier,
            nonce=consumed_state.nonce,
        )
        user = _user_from_claims(db=db, claims=exchange_result.claims)
    except (
        HTTPException,
        OIDCConfigurationError,
        OIDCVerificationError,
        OIDCTokenExchangeError,
    ) as exc:
        logger.info("OIDC callback authentication failed: %s", exc)
        return _callback_failure_response(settings=settings)

    session = create_auth_session(
        db,
        user=user,
        oidc_issuer=user.oidc_issuer,
        oidc_sub=user.oidc_sub,
        claims=_session_safe_claims(exchange_result.claims),
        settings=settings,
    )

    response = RedirectResponse(
        url=consumed_state.return_to,
        status_code=status.HTTP_303_SEE_OTHER,
    )
    _set_session_cookie(response, session_token=session.session_token, settings=settings)
    _clear_login_binding_cookie(response, settings=settings)
    return response


@router.get("/session")
def get_session(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthSessionResponse:
    _set_no_store_headers(response)
    session_token = request.cookies.get(settings.auth_session_cookie_name)
    auth_session = load_auth_session(
        db,
        session_token=session_token,
        settings=settings,
    )
    if auth_session is None:
        if session_token is not None:
            _clear_session_cookie(response, settings=settings)
        return _unauthenticated_session_response()

    user = load_user_for_auth_session(db, auth_session=auth_session)
    if user is None:
        logger.error("Authenticated session %s references a missing user", auth_session.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authenticated user record not found",
        )

    assert session_token is not None
    refresh_auth_session_last_seen(db, auth_session=auth_session, settings=settings)
    _set_session_cookie(response, session_token=session_token, settings=settings)
    csrf_token = stable_auth_session_csrf_token(
        session_token,
        settings=settings,
    )
    profile = db.get(UserProfile, user.id)
    principal = _principal_from_auth_session(auth_session=auth_session, user=user)
    return AuthSessionResponse(
        authenticated=True,
        is_admin=_check_admin(principal),
        user=_serialize_user(user),
        profile=_serialize_profile(profile),
        csrf_token=csrf_token,
    )


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> LogoutResponse:
    session_token = request.cookies.get(settings.auth_session_cookie_name)
    auth_session = load_auth_session(
        db,
        session_token=session_token,
        settings=settings,
    )
    if auth_session is None:
        if session_token is not None:
            _clear_session_cookie(response, settings=settings)
        return LogoutResponse(ok=True)

    raw_csrf_token = request.headers.get(settings.auth_csrf_header_name)
    if raw_csrf_token is None or not raw_csrf_token.strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token required",
        )
    if not verify_auth_session_csrf_token(
        auth_session,
        csrf_token=raw_csrf_token,
        session_token=session_token,
        settings=settings,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token",
        )

    revoke_auth_session(db, session_token=session_token, settings=settings)
    _clear_session_cookie(response, settings=settings)
    return LogoutResponse(ok=True)


def _user_from_claims(*, db: Session, claims: dict[str, Any]) -> User:
    issuer = claims.get("iss")
    sub = claims.get("sub")
    if not isinstance(issuer, str) or not issuer:
        logger.info("OIDC callback claims missing issuer")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed",
        )
    if not isinstance(sub, str) or not sub:
        logger.info("OIDC callback claims missing subject")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed",
        )

    try:
        return _upsert_user(db, issuer=issuer, sub=sub)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to upsert OIDC callback user")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist user",
        ) from exc


def _principal_from_auth_session(*, auth_session: AuthSession, user: User) -> Principal:
    actor_type = getattr(user, "actor_type", "human")
    if actor_type not in {"human", "bot"}:
        actor_type = "human"
    return Principal(
        is_authenticated=True,
        actor_type=actor_type,
        auth_method="session",
        user_id=str(user.id),
        auth_session_id=str(auth_session.id),
        oidc_issuer=auth_session.oidc_issuer,
        oidc_sub=auth_session.oidc_sub,
        claims=dict(auth_session.claims or {}),
    )


def _unauthenticated_session_response() -> AuthSessionResponse:
    return AuthSessionResponse(
        authenticated=False,
        is_admin=False,
        user=None,
        profile=None,
        csrf_token=None,
    )


def _session_safe_claims(claims: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in claims.items()
        if not _is_sensitive_session_claim_key(key)
    }


def _is_sensitive_session_claim_key(key: str) -> bool:
    normalized_key = key.replace("-", "_").lower()
    compact_key = normalized_key.replace("_", "")
    sensitive_exact_keys = {
        "accesstoken",
        "authcode",
        "authorizationcode",
        "clientsecret",
        "code",
        "codeverifier",
        "idtoken",
        "nonce",
        "refreshtoken",
    }
    if compact_key in sensitive_exact_keys:
        return True
    sensitive_fragments = (
        "accesstoken",
        "authcode",
        "authorizationcode",
        "clientsecret",
        "codeverifier",
        "idtoken",
        "refreshtoken",
    )
    return any(fragment in compact_key for fragment in sensitive_fragments)


def _sanitize_return_to(
    value: str | None,
    *,
    request: Request,
    settings: Settings,
) -> str:
    if value is None:
        return "/"
    trimmed = value.strip()
    if not trimmed:
        return "/"

    try:
        parsed = urlsplit(trimmed)
    except ValueError:
        return "/"

    if parsed.scheme or parsed.netloc:
        configured_base_url = getattr(settings, "public_base_url", "") or ""
        origin = configured_base_url.strip() or str(request.base_url)
        origin_parts = urlsplit(origin)
        if not origin_parts.scheme or not origin_parts.netloc:
            return "/"
        if (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
        ) != (
            origin_parts.scheme.lower(),
            origin_parts.netloc.lower(),
        ):
            return "/"
        return urlunsplit(("", "", parsed.path or "/", parsed.query, parsed.fragment))

    return sanitize_return_to(trimmed)


def _callback_failure_response(*, settings: Settings) -> RedirectResponse:
    response = RedirectResponse(
        url=_CALLBACK_FAILURE_RETURN_TO,
        status_code=status.HTTP_303_SEE_OTHER,
    )
    _clear_login_binding_cookie(response, settings=settings)
    return response


def _set_login_binding_cookie(
    response: Response,
    *,
    login_binding_token: str,
    settings: Settings,
) -> None:
    response.set_cookie(
        key=settings.auth_login_state_cookie_name,
        value=login_binding_token,
        max_age=settings.oidc_login_state_max_age_seconds,
        path=_callback_cookie_path(settings),
        secure=_cookie_secure(settings),
        httponly=True,
        samesite="lax",
    )


def _clear_login_binding_cookie(response: Response, *, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.auth_login_state_cookie_name,
        path=_callback_cookie_path(settings),
        secure=_cookie_secure(settings),
        httponly=True,
        samesite="lax",
    )


def _set_session_cookie(
    response: Response,
    *,
    session_token: str,
    settings: Settings,
) -> None:
    response.set_cookie(
        key=settings.auth_session_cookie_name,
        value=session_token,
        max_age=settings.auth_session_max_age_seconds,
        path="/",
        secure=_cookie_secure(settings),
        httponly=True,
        samesite="lax",
    )


def _clear_session_cookie(response: Response, *, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.auth_session_cookie_name,
        path="/",
        secure=_cookie_secure(settings),
        httponly=True,
        samesite="lax",
    )


def _set_no_store_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"


def _cookie_secure(settings: Settings) -> bool:
    if settings.app_env.lower() not in _NON_PRODUCTION_ENVS:
        return True
    if settings.auth_cookie_secure is not None:
        return bool(settings.auth_cookie_secure)
    return False


def _callback_cookie_path(settings: Settings) -> str:
    callback_path = settings.oidc_redirect_path or "/api/v1/auth/callback"
    if not callback_path.startswith("/"):
        callback_path = f"/{callback_path}"
    return callback_path


def _authorization_url_with_state(authorization_url: str, *, state: str) -> str:
    parts = urlsplit(authorization_url)
    query: list[tuple[str, str]] = []
    replaced = False
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key == "state":
            query.append((key, state))
            replaced = True
        else:
            query.append((key, value))
    if not replaced:
        query.append(("state", state))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )
