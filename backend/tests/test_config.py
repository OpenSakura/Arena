from __future__ import annotations

import logging

import pytest

from app.core.config import Settings


def test_settings_accepts_csv_cors_origins() -> None:
    settings = Settings(
        cors_allow_origins="http://localhost:3000, https://arena.example"
    )

    assert settings.cors_allow_origins == [
        "http://localhost:3000",
        "https://arena.example",
    ]


def test_settings_accepts_json_array_cors_origins() -> None:
    settings = Settings(
        cors_allow_origins='["http://localhost:3000", "https://arena.example"]'
    )

    assert settings.cors_allow_origins == [
        "http://localhost:3000",
        "https://arena.example",
    ]


def _production_settings(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = dict(
        app_env="production",
        public_base_url="https://arena.example",
        oidc_issuer="https://auth.example",
        oidc_client_id="arena-client",
        oidc_client_secret="oidc-client-secret",
        auth_session_hash_secret="auth-session-hash-secret",
        arena_master_key="secret-key",
        service_token_hash_secret="service-token-secret",
        database_url="postgresql+psycopg://prod:prod@db:5432/arena",
        turnstile_secret_key="turnstile-secret",
        cors_allow_origins="https://arena.example",
        web_concurrency=1,
    )
    defaults.update(overrides)
    return defaults


def test_settings_exposes_confidential_client_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env_name in (
        "PUBLIC_BASE_URL",
        "OIDC_CLIENT_ID",
        "OIDC_CLIENT_SECRET",
        "OIDC_CLIENT_AUTH_METHOD",
        "OIDC_SCOPE",
        "OIDC_REDIRECT_PATH",
        "OIDC_LOGIN_STATE_MAX_AGE_SECONDS",
        "AUTH_SESSION_COOKIE_NAME",
        "AUTH_LOGIN_STATE_COOKIE_NAME",
        "AUTH_SESSION_MAX_AGE_SECONDS",
        "AUTH_SESSION_HASH_SECRET",
        "AUTH_CSRF_HEADER_NAME",
        "AUTH_COOKIE_SECURE",
        "OIDC_ISSUER",
        "OIDC_ADMIN_GROUP_CLAIM",
        "OIDC_ADMIN_GROUP_NAME",
    ):
        monkeypatch.delenv(env_name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.public_base_url == ""
    assert settings.oidc_client_id == ""
    assert settings.oidc_client_secret == ""
    assert settings.oidc_client_auth_method == "client_secret_basic"
    assert settings.oidc_scope == "openid email profile"
    assert settings.oidc_redirect_path == "/api/v1/auth/callback"
    assert settings.oidc_login_state_max_age_seconds == 600
    assert settings.auth_session_cookie_name == "arena_session"
    assert settings.auth_login_state_cookie_name == "arena_oauth_state"
    assert settings.auth_session_max_age_seconds == 28800
    assert settings.auth_session_hash_secret == ""
    assert settings.auth_csrf_header_name == "X-CSRF-Token"
    assert settings.auth_cookie_secure is None
    assert settings.oidc_issuer == ""
    assert settings.oidc_admin_group_claim == "groups"
    assert settings.oidc_admin_group_name == "arena_admin"


def test_production_accepts_confidential_client_settings_without_public_client_requirement() -> None:
    settings = Settings(**_production_settings())

    assert settings.public_base_url == "https://arena.example"
    assert settings.oidc_issuer == "https://auth.example"
    assert settings.oidc_client_id == "arena-client"
    assert settings.oidc_client_secret == "oidc-client-secret"
    assert settings.auth_session_hash_secret == "auth-session-hash-secret"


def test_production_warns_when_redis_url_missing(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        Settings(**_production_settings(rate_limit_redis_url=""))

    assert any("RATE_LIMIT_REDIS_URL" in r.message for r in caplog.records)


def test_production_no_redis_warning_when_configured(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        Settings(
            **_production_settings(rate_limit_redis_url="redis://localhost:6379/0")
        )

    redis_warnings = [r for r in caplog.records if "RATE_LIMIT_REDIS_URL" in r.message]
    assert redis_warnings == []


def test_production_rejects_web_concurrency_greater_than_one() -> None:
    with pytest.raises(ValueError, match="WEB_CONCURRENCY"):
        Settings(**_production_settings(web_concurrency=4))


def test_production_rejects_web_concurrency_of_two() -> None:
    with pytest.raises(ValueError, match="WEB_CONCURRENCY"):
        Settings(**_production_settings(web_concurrency=2))


def test_production_accepts_web_concurrency_of_one() -> None:
    settings = Settings(**_production_settings(web_concurrency=1))
    assert settings.web_concurrency == 1


def test_production_treats_zero_web_concurrency_as_one() -> None:
    settings = Settings(**_production_settings(web_concurrency=0))
    assert settings.web_concurrency == 0


def test_dev_allows_web_concurrency_of_one() -> None:
    settings = Settings(app_env="dev", web_concurrency=1)
    assert settings.web_concurrency == 1


def test_production_accepts_empty_turnstile_secret_key() -> None:
    settings = Settings(**_production_settings(turnstile_secret_key=""))
    assert settings.turnstile_secret_key == ""


def test_production_rejects_missing_service_token_hash_secret() -> None:
    with pytest.raises(ValueError, match="SERVICE_TOKEN_HASH_SECRET"):
        Settings(**_production_settings(service_token_hash_secret=""))


@pytest.mark.parametrize(
    ("field", "setting_name"),
    [
        ("oidc_client_id", "OIDC_CLIENT_ID"),
        ("oidc_client_secret", "OIDC_CLIENT_SECRET"),
        ("auth_session_hash_secret", "AUTH_SESSION_HASH_SECRET"),
        ("public_base_url", "PUBLIC_BASE_URL"),
    ],
)
def test_production_rejects_missing_confidential_client_setting(
    field: str, setting_name: str
) -> None:
    with pytest.raises(ValueError, match=setting_name):
        Settings(**_production_settings(**{field: ""}))


@pytest.mark.parametrize(
    ("field", "setting_name"),
    [("oidc_issuer", "OIDC_ISSUER")],
)
def test_production_rejects_missing_oidc_issuer(
    field: str, setting_name: str
) -> None:
    with pytest.raises(ValueError, match=setting_name):
        Settings(**_production_settings(**{field: ""}))


def test_production_rejects_missing_arena_master_key() -> None:
    with pytest.raises(ValueError, match="ARENA_MASTER_KEY"):
        Settings(**_production_settings(arena_master_key=""))


def test_production_rejects_default_database_url() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings(
            **_production_settings(
                database_url="postgresql+psycopg://postgres:postgres@localhost:5432/arena"
            )
        )


def test_production_rejects_wildcard_cors_origin() -> None:
    with pytest.raises(ValueError, match="CORS_ALLOW_ORIGINS"):
        Settings(**_production_settings(cors_allow_origins="*"))
