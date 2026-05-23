from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import hashlib
import hmac
import secrets
from typing import Protocol

from app.core.config import get_settings


SERVICE_TOKEN_PREFIX = "osa_bot_"
SERVICE_TOKEN_RANDOM_BYTES = 32
SERVICE_TOKEN_STORED_PREFIX_CHARS = len(SERVICE_TOKEN_PREFIX) + 12
SERVICE_TOKEN_HASH_ALGORITHM = "sha256"

ALLOWED_SERVICE_TOKEN_SCOPES = (
    "battle:create",
    "battle:read",
    "battle:execute",
    "vote:create",
)
_ALLOWED_SERVICE_TOKEN_SCOPE_SET = frozenset(ALLOWED_SERVICE_TOKEN_SCOPES)


class _ServiceTokenSettings(Protocol):
    service_token_hash_secret: str


@dataclass(frozen=True)
class HashedServiceToken:
    token_prefix: str
    token_hash: str


def generate_service_token() -> str:
    return f"{SERVICE_TOKEN_PREFIX}{secrets.token_urlsafe(SERVICE_TOKEN_RANDOM_BYTES)}"


def hash_service_token(
    token: str,
    *,
    settings: _ServiceTokenSettings | None = None,
) -> HashedServiceToken:
    return HashedServiceToken(
        token_prefix=token[:SERVICE_TOKEN_STORED_PREFIX_CHARS],
        token_hash=_service_token_digest(token, settings=settings),
    )


def constant_time_verify(
    candidate_token: str,
    stored_token_hash: str,
    *,
    settings: _ServiceTokenSettings | None = None,
) -> bool:
    try:
        candidate_hash = _service_token_digest(candidate_token, settings=settings)
    except ValueError:
        return False

    return hmac.compare_digest(candidate_hash, stored_token_hash)


def normalize_scopes(scopes: Iterable[str] | str | None) -> tuple[str, ...]:
    normalized: set[str] = set()
    for scope in _iter_scope_values(scopes):
        if not isinstance(scope, str):
            raise ValueError("Service token scopes must be strings")

        value = scope.strip()
        if not value:
            raise ValueError("Service token scope cannot be empty")
        if value.startswith("admin:"):
            raise ValueError("Admin scopes are not supported for service tokens")
        if value not in _ALLOWED_SERVICE_TOKEN_SCOPE_SET:
            raise ValueError(f"Unsupported service token scope: {value}")

        normalized.add(value)

    return tuple(scope for scope in ALLOWED_SERVICE_TOKEN_SCOPES if scope in normalized)


def has_required_scopes(
    granted_scopes: Iterable[str] | str | None,
    required_scopes: Iterable[str] | str | None,
) -> bool:
    granted = set(normalize_scopes(granted_scopes))
    required = set(normalize_scopes(required_scopes))
    return required.issubset(granted)


def _service_token_digest(
    token: str,
    *,
    settings: _ServiceTokenSettings | None = None,
) -> str:
    _validate_service_token(token)
    return hmac.new(
        _service_token_hash_key(settings=settings),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _service_token_hash_key(
    *, settings: _ServiceTokenSettings | None = None
) -> bytes:
    selected_settings = settings if settings is not None else get_settings()
    secret = selected_settings.service_token_hash_secret
    if not secret.strip():
        raise RuntimeError("SERVICE_TOKEN_HASH_SECRET is not set")
    return secret.encode("utf-8")


def _validate_service_token(token: str) -> None:
    if not token.startswith(SERVICE_TOKEN_PREFIX):
        raise ValueError(f"Service token must start with {SERVICE_TOKEN_PREFIX}")
    if len(token) <= SERVICE_TOKEN_STORED_PREFIX_CHARS:
        raise ValueError("Service token secret is too short")


def _iter_scope_values(scopes: Iterable[str] | str | None) -> Iterable[object]:
    if scopes is None:
        return ()
    if isinstance(scopes, str):
        return scopes.replace(",", " ").split()
    return scopes
