from __future__ import annotations

import hashlib
import hmac
import re
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.core.config import Settings
from app.core import service_tokens


_HASH_SECRET = "test-service-token-hash-secret"


def _settings(secret: str = _HASH_SECRET) -> SimpleNamespace:
    return SimpleNamespace(service_token_hash_secret=secret)


def _token(secret: str = "a" * 43) -> str:
    return f"{service_tokens.SERVICE_TOKEN_PREFIX}{secret}"


def _same_stored_prefix_token(secret_suffix: str) -> str:
    shared_secret_prefix = "a" * (
        service_tokens.SERVICE_TOKEN_STORED_PREFIX_CHARS
        - len(service_tokens.SERVICE_TOKEN_PREFIX)
    )
    return _token(f"{shared_secret_prefix}{secret_suffix}")


def _production_settings(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "app_env": "production",
        "oidc_issuer": "https://auth.example",
        "oidc_audience": "arena",
        "oidc_client_id": "arena-client",
        "oidc_client_secret": "oidc-client-secret",
        "auth_session_hash_secret": "auth-session-hash-secret",
        "public_base_url": "https://arena.example",
        "arena_master_key": "secret-key",
        "service_token_hash_secret": _HASH_SECRET,
        "database_url": "postgresql+psycopg://prod:prod@db:5432/arena",
        "cors_allow_origins": "https://arena.example",
        "web_concurrency": 1,
    }
    defaults.update(overrides)
    return defaults


def test_generate_service_token_uses_urlsafe_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_nbytes: list[int] = []

    def fake_token_urlsafe(nbytes: int) -> str:
        requested_nbytes.append(nbytes)
        return "abc-DEF_123"

    monkeypatch.setattr(service_tokens.secrets, "token_urlsafe", fake_token_urlsafe)

    token = service_tokens.generate_service_token()

    assert token == "osa_bot_abc-DEF_123"
    assert requested_nbytes == [service_tokens.SERVICE_TOKEN_RANDOM_BYTES]
    assert re.fullmatch(r"osa_bot_[A-Za-z0-9_-]+", token)


def test_hash_service_token_returns_prefix_and_hmac_digest_only() -> None:
    token = _token()

    stored = service_tokens.hash_service_token(token, settings=_settings())

    expected_hash = hmac.new(
        _HASH_SECRET.encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert stored.token_prefix == token[: service_tokens.SERVICE_TOKEN_STORED_PREFIX_CHARS]
    assert stored.token_prefix != token
    assert stored.token_hash == expected_hash
    assert len(stored.token_hash) == 64
    assert stored.token_hash != token


def test_hash_service_token_requires_configured_secret() -> None:
    with pytest.raises(RuntimeError, match="SERVICE_TOKEN_HASH_SECRET"):
        service_tokens.hash_service_token(_token(), settings=_settings(" "))


def test_hash_service_token_rejects_invalid_prefix() -> None:
    with pytest.raises(ValueError, match="osa_bot_"):
        service_tokens.hash_service_token("not-a-service-token", settings=_settings())


def test_constant_time_verify_accepts_matching_token() -> None:
    token = _token()
    stored = service_tokens.hash_service_token(token, settings=_settings())

    assert service_tokens.constant_time_verify(
        token, stored.token_hash, settings=_settings()
    )


def test_constant_time_verify_rejects_one_character_modified_token() -> None:
    token = _token("a" * 42 + "b")
    stored = service_tokens.hash_service_token(token, settings=_settings())

    assert not service_tokens.constant_time_verify(
        _token("a" * 42 + "c"), stored.token_hash, settings=_settings()
    )


def test_constant_time_verify_rejects_same_prefix_different_secret() -> None:
    token = _same_stored_prefix_token("A" * 32)
    candidate = _same_stored_prefix_token("B" * 32)
    stored = service_tokens.hash_service_token(token, settings=_settings())

    assert token[: service_tokens.SERVICE_TOKEN_STORED_PREFIX_CHARS] == candidate[
        : service_tokens.SERVICE_TOKEN_STORED_PREFIX_CHARS
    ]
    assert not service_tokens.constant_time_verify(
        candidate, stored.token_hash, settings=_settings()
    )


def test_constant_time_verify_returns_false_for_malformed_candidate() -> None:
    stored = service_tokens.hash_service_token(_token(), settings=_settings())

    assert not service_tokens.constant_time_verify(
        "not-a-service-token", stored.token_hash, settings=_settings()
    )


def test_constant_time_verify_uses_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    token = _token()
    stored = service_tokens.hash_service_token(token, settings=_settings())
    original_compare_digest = hmac.compare_digest
    calls: list[tuple[str, str]] = []

    def fake_compare_digest(candidate_hash: str, stored_hash: str) -> bool:
        calls.append((candidate_hash, stored_hash))
        return original_compare_digest(candidate_hash, stored_hash)

    monkeypatch.setattr(service_tokens.hmac, "compare_digest", fake_compare_digest)

    assert service_tokens.constant_time_verify(
        token, stored.token_hash, settings=_settings()
    )
    assert calls == [(stored.token_hash, stored.token_hash)]


def test_normalize_scopes_deduplicates_in_allowed_order() -> None:
    assert service_tokens.normalize_scopes(
        ["vote:create", "battle:read", "vote:create", "battle:create"]
    ) == ("battle:create", "battle:read", "vote:create")
    assert service_tokens.normalize_scopes("vote:create battle:create,vote:create") == (
        "battle:create",
        "vote:create",
    )


@pytest.mark.parametrize(
    ("scope", "message"),
    [
        ("admin:*", "Admin scopes"),
        ("admin:tokens", "Admin scopes"),
        ("battle:*", "Unsupported service token scope"),
        ("model:read", "Unsupported service token scope"),
    ],
)
def test_normalize_scopes_rejects_unknown_admin_and_wildcard_scopes(
    scope: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        service_tokens.normalize_scopes([scope])


def test_normalize_scopes_rejects_empty_and_non_string_items() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        service_tokens.normalize_scopes(["battle:create", ""])
    with pytest.raises(ValueError, match="must be strings"):
        service_tokens.normalize_scopes(cast(Any, ["battle:create", 123]))


def test_has_required_scopes_checks_subset_containment() -> None:
    assert service_tokens.has_required_scopes(
        ["battle:create", "vote:create"], ["vote:create"]
    )
    assert service_tokens.has_required_scopes(["battle:execute"], [])
    assert not service_tokens.has_required_scopes(["battle:read"], ["battle:execute"])


def test_production_requires_service_token_hash_secret() -> None:
    with pytest.raises(ValueError, match="SERVICE_TOKEN_HASH_SECRET"):
        Settings(**_production_settings(service_token_hash_secret=""))


def test_development_allows_empty_service_token_hash_secret() -> None:
    settings = Settings(app_env="dev", service_token_hash_secret="")

    assert settings.service_token_hash_secret == ""
