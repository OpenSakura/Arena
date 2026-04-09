from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.routes import health
import app.core.config as config_module
from app.core import logging as app_logging
import app.main as main


def _settings(
    *,
    access_log_enabled: bool = False,
    leaderboard_refresh_enabled: bool = False,
    turnstile_secret_key: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        app_name="OpenSakura Arena API (tests)",
        app_env="test",
        leaderboard_refresh_enabled=leaderboard_refresh_enabled,
        access_log_enabled=access_log_enabled,
        turnstile_secret_key=turnstile_secret_key,
        cors_allow_origins=["http://localhost:3000"],
        api_v1_prefix="/api/v1",
    )


def _create_test_app(
    monkeypatch,
    *,
    access_log_enabled: bool = False,
    leaderboard_refresh_enabled: bool = False,
    turnstile_secret_key: str = "",
):
    settings_obj = _settings(
        access_log_enabled=access_log_enabled,
        leaderboard_refresh_enabled=leaderboard_refresh_enabled,
        turnstile_secret_key=turnstile_secret_key,
    )

    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: settings_obj,
    )
    monkeypatch.setattr(config_module, "get_settings", lambda: settings_obj)
    monkeypatch.setattr(health, "get_settings", lambda: settings_obj)
    monkeypatch.setattr(main, "configure_logging", lambda _settings: None)
    return main.create_app()


class _CapturingLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def info(self, msg: str, *args: object, **kwargs: object) -> None:
        self.calls.append((msg, args, dict(kwargs)))


def test_request_id_reuses_header_value_and_trims_whitespace(monkeypatch) -> None:
    app = _create_test_app(monkeypatch)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/healthz",
            headers={"X-Request-ID": "  req-custom-1  "},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-custom-1"
    assert app_logging.get_request_id() == "-"


def test_request_id_is_generated_when_header_is_missing(monkeypatch) -> None:
    app = _create_test_app(monkeypatch)

    with TestClient(app) as client:
        response = client.get("/api/v1/healthz")

    request_id = response.headers["X-Request-ID"]
    assert len(request_id) == 32
    int(request_id, 16)


def test_public_config_exposes_turnstile_requirement(monkeypatch) -> None:
    from app.core.config import get_settings as core_get_settings

    app = _create_test_app(monkeypatch)
    app.dependency_overrides[core_get_settings] = lambda: _settings(
        turnstile_secret_key="secret"
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/public-config")

    assert response.status_code == 200
    assert response.json() == {"anon_vote_turnstile_required": True}


def test_access_log_is_not_emitted_when_disabled(monkeypatch) -> None:
    logger = _CapturingLogger()
    monkeypatch.setattr(main, "logger", logger)
    app = _create_test_app(monkeypatch, access_log_enabled=False)

    with TestClient(app) as client:
        response = client.get("/api/v1/healthz")

    assert response.status_code == 200
    assert logger.calls == []


def test_access_log_emits_structured_fields_when_enabled(monkeypatch) -> None:
    logger = _CapturingLogger()
    monkeypatch.setattr(main, "logger", logger)
    app = _create_test_app(monkeypatch, access_log_enabled=True)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/healthz",
            headers={"X-Request-ID": "req-access-1"},
        )

    assert response.status_code == 200
    assert len(logger.calls) == 1

    message, args, kwargs = logger.calls[0]
    assert message == "%s %s -> %s (%sms)"
    assert args[0] == "GET"
    assert args[1] == "/api/v1/healthz"
    assert args[2] == 200

    extra = kwargs["extra"]
    assert isinstance(extra, dict)
    assert extra["method"] == "GET"
    assert extra["path"] == "/api/v1/healthz"
    assert extra["status_code"] == 200
    assert isinstance(extra["duration_ms"], int)
    assert extra["duration_ms"] >= 0
    assert extra["client_ip"]
