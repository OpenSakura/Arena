from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import logging
import secrets
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit
import uuid

from sqlalchemy import delete, select, text, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.models.auth_session import AuthSession, OAuthLoginState
from app.models.user import User


AUTH_TOKEN_RANDOM_BYTES = 32
AUTH_TOKEN_HASH_LENGTH = 64
AUTH_SESSION_LAST_SEEN_MIN_INTERVAL_SECONDS = 60
AUTH_SESSION_LAST_SEEN_LOCK_TIMEOUT_MS = 100
AUTH_SESSION_LAST_SEEN_STATEMENT_TIMEOUT_MS = 500
_AUTH_SESSION_TOUCH_TIMEOUT_SQLSTATES = frozenset({"55P03", "57014"})
_AUTH_SESSION_CSRF_DERIVATION_CONTEXT = b"opensakura.auth-session.csrf-token.v1\x00"

logger = logging.getLogger(__name__)


class _AuthSessionSettings(Protocol):
    auth_session_hash_secret: str
    auth_session_max_age_seconds: int
    auth_session_last_seen_min_interval_seconds: int
    auth_session_last_seen_lock_timeout_ms: int
    auth_session_last_seen_statement_timeout_ms: int
    oidc_login_state_max_age_seconds: int


@dataclass(frozen=True)
class CreatedOAuthLoginState:
    state: str
    login_binding_token: str
    nonce: str
    code_verifier: str
    row: OAuthLoginState


@dataclass(frozen=True)
class ConsumedOAuthLoginState:
    row: OAuthLoginState
    nonce: str
    code_verifier: str
    return_to: str


@dataclass(frozen=True)
class CreatedAuthSession:
    session_token: str
    row: AuthSession


def generate_urlsafe_token(nbytes: int = AUTH_TOKEN_RANDOM_BYTES) -> str:
    if nbytes < AUTH_TOKEN_RANDOM_BYTES:
        raise ValueError("Auth tokens must use at least 32 bytes of entropy")
    return secrets.token_urlsafe(nbytes)


def hash_auth_token(
    token: str | None,
    *,
    settings: _AuthSessionSettings | None = None,
) -> str:
    if not isinstance(token, str) or not token:
        raise ValueError("Auth token cannot be empty")
    return hmac.new(
        _auth_session_hash_key(settings=settings),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def stable_auth_session_csrf_token(
    session_token: str | None,
    *,
    settings: _AuthSessionSettings | None = None,
) -> str:
    if not isinstance(session_token, str) or not session_token:
        raise ValueError("Auth session token cannot be empty")
    return hmac.new(
        _auth_session_hash_key(settings=settings),
        _AUTH_SESSION_CSRF_DERIVATION_CONTEXT + session_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def constant_time_verify_auth_token(
    candidate_token: str | None,
    stored_token_hash: str,
    *,
    settings: _AuthSessionSettings | None = None,
) -> bool:
    if len(stored_token_hash) != AUTH_TOKEN_HASH_LENGTH:
        return False
    try:
        candidate_hash = hash_auth_token(candidate_token, settings=settings)
    except ValueError:
        return False
    return hmac.compare_digest(candidate_hash, stored_token_hash)


def create_oauth_login_state(
    db: Session,
    *,
    nonce: str | None = None,
    code_verifier: str | None = None,
    return_to: str | None = None,
    expires_at: datetime | None = None,
    settings: _AuthSessionSettings | None = None,
    now: datetime | None = None,
) -> CreatedOAuthLoginState:
    selected_settings = _selected_settings(settings)
    issued_at = _utc_now(now)
    state = generate_urlsafe_token()
    login_binding_token = generate_urlsafe_token()
    raw_nonce = nonce if nonce is not None else generate_urlsafe_token()
    raw_code_verifier = (
        code_verifier if code_verifier is not None else generate_urlsafe_token()
    )
    login_state = OAuthLoginState(
        state_hash=hash_auth_token(state, settings=selected_settings),
        login_binding_hash=hash_auth_token(
            login_binding_token, settings=selected_settings
        ),
        nonce=encrypt_secret(raw_nonce),
        code_verifier=encrypt_secret(raw_code_verifier),
        return_to=sanitize_return_to(return_to),
        expires_at=expires_at
        or issued_at
        + timedelta(seconds=selected_settings.oidc_login_state_max_age_seconds),
    )
    db.add(login_state)
    db.flush()
    return CreatedOAuthLoginState(
        state=state,
        login_binding_token=login_binding_token,
        nonce=raw_nonce,
        code_verifier=raw_code_verifier,
        row=login_state,
    )


def load_oauth_login_state(
    db: Session,
    *,
    state: str | None,
    settings: _AuthSessionSettings | None = None,
) -> OAuthLoginState | None:
    try:
        state_hash = hash_auth_token(state, settings=settings)
    except ValueError:
        return None
    row = db.execute(
        select(OAuthLoginState).where(OAuthLoginState.state_hash == state_hash)
    ).scalar_one_or_none()
    if row is None:
        return None
    if not hmac.compare_digest(state_hash, row.state_hash):
        return None
    return row


def validate_login_binding(
    login_state: OAuthLoginState,
    *,
    login_binding_token: str | None,
    settings: _AuthSessionSettings | None = None,
) -> bool:
    return constant_time_verify_auth_token(
        login_binding_token,
        login_state.login_binding_hash,
        settings=settings,
    )


def consume_oauth_login_state(
    db: Session,
    *,
    state: str | None,
    login_binding_token: str | None,
    settings: _AuthSessionSettings | None = None,
    now: datetime | None = None,
) -> ConsumedOAuthLoginState | None:
    selected_settings = _selected_settings(settings)
    consumed_at = _utc_now(now)
    login_state = load_oauth_login_state(db, state=state, settings=selected_settings)
    if login_state is None:
        return None
    if login_state.consumed_at is not None or _is_past(
        login_state.expires_at, consumed_at
    ):
        return None
    if not validate_login_binding(
        login_state,
        login_binding_token=login_binding_token,
        settings=selected_settings,
    ):
        return None

    result = db.execute(
        update(OAuthLoginState)
        .where(
            OAuthLoginState.id == login_state.id,
            OAuthLoginState.consumed_at.is_(None),
            OAuthLoginState.expires_at > consumed_at,
        )
        .values(consumed_at=consumed_at)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.expire(login_state)
        return None
    db.refresh(login_state)
    return ConsumedOAuthLoginState(
        row=login_state,
        nonce=decrypt_secret(login_state.nonce),
        code_verifier=decrypt_secret(login_state.code_verifier),
        return_to=login_state.return_to,
    )


def delete_oauth_login_state(db: Session, *, login_state_id: uuid.UUID) -> bool:
    result = db.execute(
        delete(OAuthLoginState).where(OAuthLoginState.id == login_state_id)
    )
    db.flush()
    return result.rowcount == 1


def delete_expired_oauth_login_states(
    db: Session,
    *,
    now: datetime | None = None,
) -> int:
    cutoff = _utc_now(now)
    result = db.execute(
        delete(OAuthLoginState).where(OAuthLoginState.expires_at <= cutoff)
    )
    db.flush()
    return int(result.rowcount or 0)


def expire_oauth_login_state(
    db: Session,
    *,
    state: str | None,
    settings: _AuthSessionSettings | None = None,
    now: datetime | None = None,
) -> bool:
    try:
        state_hash = hash_auth_token(state, settings=settings)
    except ValueError:
        return False
    expires_at = _utc_now(now)
    result = db.execute(
        update(OAuthLoginState)
        .where(OAuthLoginState.state_hash == state_hash)
        .values(expires_at=expires_at)
        .execution_options(synchronize_session=False)
    )
    db.flush()
    return result.rowcount == 1


def create_auth_session(
    db: Session,
    *,
    user: User,
    oidc_issuer: str | None = None,
    oidc_sub: str | None = None,
    claims: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
    settings: _AuthSessionSettings | None = None,
    now: datetime | None = None,
) -> CreatedAuthSession:
    selected_settings = _selected_settings(settings)
    issued_at = _utc_now(now)
    session_token = generate_urlsafe_token()
    csrf_token = stable_auth_session_csrf_token(
        session_token,
        settings=selected_settings,
    )
    auth_session = AuthSession(
        session_token_hash=hash_auth_token(session_token, settings=selected_settings),
        csrf_token_hash=hash_auth_token(
            csrf_token,
            settings=selected_settings,
        ),
        user_id=user.id,
        oidc_issuer=oidc_issuer or user.oidc_issuer,
        oidc_sub=oidc_sub or user.oidc_sub,
        claims=dict(claims or {}),
        expires_at=expires_at
        or issued_at
        + timedelta(seconds=selected_settings.auth_session_max_age_seconds),
        last_seen_at=issued_at,
    )
    db.add(auth_session)
    db.flush()
    return CreatedAuthSession(
        session_token=session_token,
        row=auth_session,
    )


def load_auth_session(
    db: Session,
    *,
    session_token: str | None,
    settings: _AuthSessionSettings | None = None,
    now: datetime | None = None,
) -> AuthSession | None:
    selected_settings = _selected_settings(settings)
    checked_at = _utc_now(now)
    try:
        session_hash = hash_auth_token(session_token, settings=selected_settings)
    except ValueError:
        return None
    auth_session = db.execute(
        select(AuthSession).where(AuthSession.session_token_hash == session_hash)
    ).scalar_one_or_none()
    if auth_session is None:
        return None
    if not hmac.compare_digest(session_hash, auth_session.session_token_hash):
        return None
    if auth_session.revoked_at is not None or _is_past(
        auth_session.expires_at, checked_at
    ):
        return None
    return auth_session


def load_user_for_auth_session(
    db: Session,
    *,
    auth_session: AuthSession,
) -> User | None:
    return db.get(User, auth_session.user_id)


def refresh_auth_session_last_seen(
    db: Session,
    *,
    auth_session: AuthSession,
    settings: _AuthSessionSettings | None = None,
    now: datetime | None = None,
) -> AuthSession:
    selected_settings = _selected_settings(settings)
    touched_at = _utc_now(now)
    if _auth_session_last_seen_is_fresh(
        auth_session.last_seen_at,
        now=touched_at,
        settings=selected_settings,
    ):
        return auth_session

    _touch_auth_session_last_seen(
        db,
        auth_session_id=auth_session.id,
        touched_at=touched_at,
        settings=selected_settings,
    )
    return auth_session


def _auth_session_last_seen_is_fresh(
    last_seen_at: datetime,
    *,
    now: datetime,
    settings: _AuthSessionSettings,
) -> bool:
    interval_seconds = _last_seen_min_interval_seconds(settings)
    if interval_seconds <= 0:
        return False
    stale_cutoff = now - timedelta(seconds=interval_seconds)
    return not _is_past(last_seen_at, stale_cutoff)


def _touch_auth_session_last_seen(
    db: Session,
    *,
    auth_session_id: uuid.UUID,
    touched_at: datetime,
    settings: _AuthSessionSettings,
) -> None:
    stale_cutoff = touched_at - timedelta(
        seconds=max(_last_seen_min_interval_seconds(settings), 0)
    )
    with _auth_session_touch_connection(db) as connection:
        transaction = connection.begin()
        try:
            _apply_auth_session_touch_timeouts(connection, settings=settings)
            expires_at = touched_at + timedelta(
                seconds=settings.auth_session_max_age_seconds
            )
            connection.execute(
                update(AuthSession)
                .where(
                    AuthSession.id == auth_session_id,
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > touched_at,
                    AuthSession.last_seen_at <= stale_cutoff,
                )
                .values(last_seen_at=touched_at, expires_at=expires_at)
                .execution_options(synchronize_session=False)
            )
            transaction.commit()
        except DBAPIError as exc:
            if transaction.is_active:
                transaction.rollback()
            if _is_auth_session_touch_timeout(exc):
                logger.info(
                    "Skipped auth session %s last_seen touch after database timeout "
                    "sqlstate=%s",
                    auth_session_id,
                    _dbapi_sqlstate(exc),
                )
                return
            raise


@contextmanager
def _auth_session_touch_connection(db: Session) -> Iterator[Connection]:
    bind = db.get_bind()
    engine = bind if isinstance(bind, Engine) else bind.engine
    with engine.connect() as connection:
        yield connection


def _apply_auth_session_touch_timeouts(
    connection: Connection,
    *,
    settings: _AuthSessionSettings,
) -> None:
    if connection.dialect.name != "postgresql":
        return

    lock_timeout_ms = _non_negative_int_setting(
        settings,
        "auth_session_last_seen_lock_timeout_ms",
        AUTH_SESSION_LAST_SEEN_LOCK_TIMEOUT_MS,
    )
    statement_timeout_ms = _non_negative_int_setting(
        settings,
        "auth_session_last_seen_statement_timeout_ms",
        AUTH_SESSION_LAST_SEEN_STATEMENT_TIMEOUT_MS,
    )

    if lock_timeout_ms > 0:
        connection.execute(
            text("SELECT set_config('lock_timeout', :timeout, true)"),
            {"timeout": f"{lock_timeout_ms}ms"},
        )
    if statement_timeout_ms > 0:
        connection.execute(
            text("SELECT set_config('statement_timeout', :timeout, true)"),
            {"timeout": f"{statement_timeout_ms}ms"},
        )


def _is_auth_session_touch_timeout(exc: DBAPIError) -> bool:
    return _dbapi_sqlstate(exc) in _AUTH_SESSION_TOUCH_TIMEOUT_SQLSTATES


def _dbapi_sqlstate(exc: DBAPIError) -> str | None:
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return sqlstate if isinstance(sqlstate, str) else None


def _last_seen_min_interval_seconds(settings: _AuthSessionSettings) -> int:
    return _non_negative_int_setting(
        settings,
        "auth_session_last_seen_min_interval_seconds",
        AUTH_SESSION_LAST_SEEN_MIN_INTERVAL_SECONDS,
    )


def _non_negative_int_setting(
    settings: _AuthSessionSettings,
    name: str,
    default: int,
) -> int:
    return max(0, int(getattr(settings, name, default)))


def rotate_auth_session_csrf_token(
    db: Session,
    *,
    auth_session: AuthSession,
    settings: _AuthSessionSettings | None = None,
) -> str:
    selected_settings = _selected_settings(settings)
    csrf_token = generate_urlsafe_token()
    auth_session.csrf_token_hash = hash_auth_token(
        csrf_token,
        settings=selected_settings,
    )
    db.add(auth_session)
    db.flush()
    return csrf_token


def revoke_auth_session(
    db: Session,
    *,
    session_token: str | None,
    settings: _AuthSessionSettings | None = None,
    now: datetime | None = None,
) -> bool:
    try:
        session_hash = hash_auth_token(session_token, settings=settings)
    except ValueError:
        return False
    revoked_at = _utc_now(now)
    result = db.execute(
        update(AuthSession)
        .where(
            AuthSession.session_token_hash == session_hash,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=revoked_at)
        .execution_options(synchronize_session=False)
    )
    db.flush()
    return result.rowcount == 1


def expire_auth_session(
    db: Session,
    *,
    session_token: str | None,
    settings: _AuthSessionSettings | None = None,
    now: datetime | None = None,
) -> bool:
    try:
        session_hash = hash_auth_token(session_token, settings=settings)
    except ValueError:
        return False
    expires_at = _utc_now(now)
    result = db.execute(
        update(AuthSession)
        .where(AuthSession.session_token_hash == session_hash)
        .values(expires_at=expires_at)
        .execution_options(synchronize_session=False)
    )
    db.flush()
    return result.rowcount == 1


def delete_auth_session(
    db: Session,
    *,
    session_token: str | None,
    settings: _AuthSessionSettings | None = None,
) -> bool:
    try:
        session_hash = hash_auth_token(session_token, settings=settings)
    except ValueError:
        return False
    result = db.execute(
        delete(AuthSession).where(AuthSession.session_token_hash == session_hash)
    )
    db.flush()
    return result.rowcount == 1


def delete_expired_auth_sessions(
    db: Session,
    *,
    now: datetime | None = None,
) -> int:
    cutoff = _utc_now(now)
    result = db.execute(delete(AuthSession).where(AuthSession.expires_at <= cutoff))
    db.flush()
    return int(result.rowcount or 0)


def verify_auth_session_csrf_token(
    auth_session: AuthSession,
    *,
    csrf_token: str | None,
    session_token: str | None = None,
    settings: _AuthSessionSettings | None = None,
) -> bool:
    if constant_time_verify_auth_token(
        csrf_token,
        auth_session.csrf_token_hash,
        settings=settings,
    ):
        return True
    if session_token is None:
        return False
    try:
        session_hash = hash_auth_token(session_token, settings=settings)
        stable_csrf_hash = hash_auth_token(
            stable_auth_session_csrf_token(session_token, settings=settings),
            settings=settings,
        )
    except ValueError:
        return False
    if not hmac.compare_digest(session_hash, auth_session.session_token_hash):
        return False
    return constant_time_verify_auth_token(
        csrf_token,
        stable_csrf_hash,
        settings=settings,
    )


def sanitize_return_to(value: str | None) -> str:
    if value is None:
        return "/"
    trimmed = value.strip()
    if not trimmed:
        return "/"
    parsed = urlsplit(trimmed)
    if parsed.scheme or parsed.netloc:
        return "/"
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    return urlunsplit(("", "", path, parsed.query, parsed.fragment)) or "/"


def _auth_session_hash_key(
    *,
    settings: _AuthSessionSettings | None = None,
) -> bytes:
    selected_settings = _selected_settings(settings)
    secret = selected_settings.auth_session_hash_secret
    if not secret.strip():
        raise RuntimeError("AUTH_SESSION_HASH_SECRET is not set")
    return secret.encode("utf-8")


def _selected_settings(
    settings: _AuthSessionSettings | None,
) -> _AuthSessionSettings:
    return settings if settings is not None else get_settings()


def _utc_now(value: datetime | None) -> datetime:
    return value if value is not None else datetime.now(timezone.utc)


def _is_past(value: datetime, now: datetime) -> bool:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value <= now.replace(tzinfo=None)
    return value <= now
