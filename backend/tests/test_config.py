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


def _production_settings(**overrides) -> dict:
    defaults = dict(
        app_env="production",
        anon_ip_hash_salt="salt1",
        anon_user_agent_hash_salt="salt2",
        oidc_issuer="https://auth.example",
        oidc_audience="arena",
        arena_master_key="secret-key",
        database_url="postgresql+psycopg://prod:prod@db:5432/arena",
        turnstile_secret_key="turnstile-secret",
        anon_id_cookie_secure=True,
        cors_allow_origins="https://arena.example",
        web_concurrency=1,
    )
    defaults.update(overrides)
    return defaults


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
