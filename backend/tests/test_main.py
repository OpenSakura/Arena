from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.testclient import TestClient

from app.api.routes import health
from app.core.config import Settings
import app.core.config as config_module
from app.core import logging as app_logging
import app.main as main
from app.utils import redis as redis_utils


class _HealthyConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def execute(self, _stmt: object) -> None:
        return None

    def commit(self) -> None:
        return None


class _HealthyEngine:
    def connect(self) -> _HealthyConnection:
        return _HealthyConnection()


class _BrokenEngine:
    def connect(self) -> _HealthyConnection:
        raise ConnectionError("database down")


def _settings(
    *,
    access_log_enabled: bool = False,
    app_env: str = "test",
    leaderboard_refresh_enabled: bool = False,
    battle_prepopulation_enabled: bool = True,
    turnstile_secret_key: str = "",
    web_concurrency: int = 1,
    auth_csrf_header_name: str = "X-CSRF-Token",
) -> SimpleNamespace:
    return SimpleNamespace(
        app_name="OpenSakura Arena API (tests)",
        app_env=app_env,
        leaderboard_refresh_enabled=leaderboard_refresh_enabled,
        battle_prepopulation_enabled=battle_prepopulation_enabled,
        access_log_enabled=access_log_enabled,
        turnstile_secret_key=turnstile_secret_key,
        cors_allow_origins=["http://localhost:3000"],
        api_v1_prefix="/api/v1",
        auth_csrf_header_name=auth_csrf_header_name,
        # Defaults for settings referenced by middleware/health checks.
        trust_x_forwarded_for=False,
        rate_limit_redis_url="",
        rate_limit_redis_timeout_seconds=0.5,
        web_concurrency=web_concurrency,
        oidc_issuer="",
    )


def _create_test_app(
    monkeypatch,
    *,
    access_log_enabled: bool = False,
    app_env: str = "test",
    leaderboard_refresh_enabled: bool = False,
    battle_prepopulation_enabled: bool = True,
    turnstile_secret_key: str = "",
    web_concurrency: int = 1,
    auth_csrf_header_name: str = "X-CSRF-Token",
    acquire_guard=None,
    release_guard=None,
    prepopulation_service=None,
):
    settings_obj = _settings(
        access_log_enabled=access_log_enabled,
        app_env=app_env,
        leaderboard_refresh_enabled=leaderboard_refresh_enabled,
        battle_prepopulation_enabled=battle_prepopulation_enabled,
        turnstile_secret_key=turnstile_secret_key,
        web_concurrency=web_concurrency,
        auth_csrf_header_name=auth_csrf_header_name,
    )

    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: settings_obj,
    )
    monkeypatch.setattr(config_module, "get_settings", lambda: settings_obj)
    monkeypatch.setattr(health, "get_settings", lambda: settings_obj)
    # Health checks call into redis utils; patch its get_settings reference too.
    monkeypatch.setattr(redis_utils, "get_settings", lambda: settings_obj)
    redis_utils.get_rate_limit_redis_client.cache_clear()

    monkeypatch.setattr(main, "bootstrap_schema", lambda: None)

    # Patch out the Postgres process guard so unit tests don't need a live DB.
    monkeypatch.setattr(
        main,
        "acquire_battle_process_lock",
        acquire_guard if acquire_guard is not None else lambda: None,
    )
    monkeypatch.setattr(
        main,
        "release_battle_process_lock",
        release_guard if release_guard is not None else lambda: None,
    )

    # Ensure the health check doesn't try to connect to a stale engine created
    # by another test module.
    import app.db.session as session_module

    session_module._engine = None
    session_module._SessionLocal = None
    monkeypatch.setattr(session_module, "get_engine", lambda: _HealthyEngine())
    monkeypatch.setattr(main, "configure_logging", lambda _settings: None)
    monkeypatch.setattr(
        main,
        "get_battle_prepopulation_service",
        lambda: prepopulation_service or _FakePrepopulationService(),
    )
    return main.create_app()


class _CapturingLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[object, ...], dict[str, object]]] = []

    def info(self, msg: str, *args: object, **kwargs: object) -> None:
        self.calls.append(("info", msg, args, dict(kwargs)))

    def warning(self, msg: str, *args: object, **kwargs: object) -> None:
        self.calls.append(("warning", msg, args, dict(kwargs)))


class _FakePrepopulationService:
    def __init__(self) -> None:
        self.resumed = False
        self.shutdown_called = False

    def resume_incomplete_jobs(self) -> list[object]:
        self.resumed = True
        return []

    async def shutdown(self) -> None:
        self.shutdown_called = True


def test_request_id_reuses_header_value_and_trims_whitespace(monkeypatch) -> None:
    app = _create_test_app(monkeypatch)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/readyz",
            headers={"X-Request-ID": "  req-custom-1  "},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-custom-1"
    assert app_logging.get_request_id() == "-"


def test_request_id_is_generated_when_header_is_missing(monkeypatch) -> None:
    app = _create_test_app(monkeypatch)

    with TestClient(app) as client:
        response = client.get("/api/v1/readyz")

    request_id = response.headers["X-Request-ID"]
    assert len(request_id) == 32
    int(request_id, 16)


FORBIDDEN_PUBLIC_CONFIG_KEYS = {
    "access_token",
    "client_id",
    "client_secret",
    "issuer",
    "redirect_path",
    "refresh_token",
    "scope",
    "secret",
    "token_endpoint",
}


def _flatten_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for item in value.values():
            keys.update(_flatten_keys(item))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(_flatten_keys(item))
        return keys
    return set()


def test_public_config_exposes_backend_session_auth_paths(monkeypatch) -> None:
    from app.core.config import get_settings as core_get_settings

    app = _create_test_app(monkeypatch)
    override = _settings(turnstile_secret_key="secret")
    override.oidc_issuer = "https://auth.example"
    app.dependency_overrides[core_get_settings] = lambda: override

    with TestClient(app) as client:
        response = client.get("/api/v1/public-config")

    assert response.status_code == 200
    body = response.json()
    assert body["anon_battle_turnstile_required"] is False
    assert body["auth"] == {
        "mode": "backend_session",
        "login_path": "/api/v1/auth/login",
        "logout_path": "/api/v1/auth/logout",
        "session_path": "/api/v1/auth/session",
        "csrf_header_name": "X-CSRF-Token",
    }
    assert "oidc" not in body


def test_public_config_exposes_configured_csrf_header_name(monkeypatch) -> None:
    from app.core.config import get_settings as core_get_settings

    app = _create_test_app(monkeypatch, auth_csrf_header_name="X-Arena-CSRF")
    override = _settings(auth_csrf_header_name="X-Arena-CSRF")
    app.dependency_overrides[core_get_settings] = lambda: override

    with TestClient(app) as client:
        response = client.get("/api/v1/public-config")

    assert response.status_code == 200
    assert response.json()["auth"]["csrf_header_name"] == "X-Arena-CSRF"


def test_public_config_no_secret_or_oidc_bootstrap_fields(monkeypatch) -> None:
    app = _create_test_app(monkeypatch)

    with TestClient(app) as client:
        response = client.get("/api/v1/public-config")

    assert response.status_code == 200
    body = response.json()
    assert _flatten_keys(body).isdisjoint(FORBIDDEN_PUBLIC_CONFIG_KEYS)

    serialized = json.dumps(body).lower()
    for forbidden in FORBIDDEN_PUBLIC_CONFIG_KEYS:
        assert forbidden not in serialized


def test_prod_like_env_disables_docs_routes_at_construction(monkeypatch) -> None:
    app = _create_test_app(monkeypatch, app_env="staging")

    route_paths = {route.path for route in app.routes}
    assert app.openapi_url is None
    assert "/openapi.json" not in route_paths
    assert "/docs" not in route_paths
    assert "/redoc" not in route_paths


def test_non_production_env_preserves_docs_routes(monkeypatch) -> None:
    app = _create_test_app(monkeypatch, app_env="development")

    route_paths = {route.path for route in app.routes}
    assert app.openapi_url == "/openapi.json"
    assert "/openapi.json" in route_paths
    assert "/docs" in route_paths
    assert "/redoc" in route_paths


def test_access_log_is_not_emitted_when_disabled(monkeypatch) -> None:
    logger = _CapturingLogger()
    monkeypatch.setattr(main, "logger", logger)
    app = _create_test_app(monkeypatch, access_log_enabled=False)

    with TestClient(app) as client:
        response = client.get("/api/v1/readyz")

    assert response.status_code == 200
    assert logger.calls == []


def test_access_log_emits_structured_fields_when_enabled(monkeypatch) -> None:
    logger = _CapturingLogger()
    monkeypatch.setattr(main, "logger", logger)
    app = _create_test_app(monkeypatch, access_log_enabled=True)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/readyz",
            headers={"X-Request-ID": "req-access-1"},
        )

    assert response.status_code == 200
    assert len(logger.calls) == 1

    _level, message, args, kwargs = logger.calls[0]
    assert message == "%s %s -> %s (%sms)"
    assert args[0] == "GET"
    assert args[1] == "/api/v1/readyz"
    assert args[2] == 200

    extra = kwargs["extra"]
    assert isinstance(extra, dict)
    assert extra["method"] == "GET"
    assert extra["path"] == "/api/v1/readyz"
    assert extra["status_code"] == 200
    assert isinstance(extra["duration_ms"], int)
    assert extra["duration_ms"] >= 0
    assert extra["client_ip"]


def test_closes_redis_on_shutdown(monkeypatch) -> None:
    close_calls: list[bool] = []
    monkeypatch.setattr(
        main, "close_all_redis_clients", lambda: close_calls.append(True)
    )
    app = _create_test_app(monkeypatch)

    with TestClient(app):
        pass

    assert close_calls == [True]


def test_warns_without_rate_limit_redis_in_prod(monkeypatch) -> None:
    logger = _CapturingLogger()
    monkeypatch.setattr(main, "logger", logger)

    settings_obj = _settings()
    settings_obj.app_env = "production"
    settings_obj.rate_limit_redis_url = ""

    main._emit_startup_warnings(cast(Settings, settings_obj))

    warning_messages = [msg for level, msg, _, _ in logger.calls if level == "warning"]
    assert any("RATE_LIMIT_REDIS_URL" in m for m in warning_messages)


def test_worker_mode_raises_at_startup_when_web_concurrency_exceeds_one(
    monkeypatch,
) -> None:
    import pytest

    logger = _CapturingLogger()
    monkeypatch.setattr(main, "logger", logger)

    settings_obj = _settings(web_concurrency=4)
    settings_obj.app_env = "production"
    settings_obj.rate_limit_redis_url = "redis://localhost:6379/0"

    with pytest.raises(RuntimeError, match="WEB_CONCURRENCY"):
        main._emit_startup_warnings(cast(Settings, settings_obj))


def test_worker_mode_raises_at_startup_in_dev_when_web_concurrency_exceeds_one(
    monkeypatch,
) -> None:
    import pytest

    logger = _CapturingLogger()
    monkeypatch.setattr(main, "logger", logger)

    settings_obj = _settings(web_concurrency=2)
    settings_obj.app_env = "dev"

    with pytest.raises(RuntimeError, match="WEB_CONCURRENCY"):
        main._emit_startup_warnings(cast(Settings, settings_obj))


def test_worker_mode_zero_concurrency_treated_as_one(monkeypatch) -> None:
    logger = _CapturingLogger()
    monkeypatch.setattr(main, "logger", logger)

    settings_obj = _settings(web_concurrency=0)
    settings_obj.app_env = "dev"

    main._emit_startup_warnings(cast(Settings, settings_obj))

    warning_messages = [msg for level, msg, _, _ in logger.calls if level == "warning"]
    assert not any("WEB_CONCURRENCY" in m for m in warning_messages)


def test_no_startup_warnings_in_dev(monkeypatch) -> None:
    logger = _CapturingLogger()
    monkeypatch.setattr(main, "logger", logger)

    settings_obj = _settings()
    settings_obj.app_env = "dev"

    main._emit_startup_warnings(cast(Settings, settings_obj))

    warning_messages = [msg for level, msg, _, _ in logger.calls if level == "warning"]
    assert warning_messages == []


def test_cors_expose_headers_includes_request_id(monkeypatch) -> None:
    app = _create_test_app(monkeypatch)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/readyz",
            headers={"Origin": "http://localhost:3000"},
        )

    assert response.status_code == 200
    expose = response.headers.get("access-control-expose-headers", "")
    assert "x-request-id" in expose.lower()


def test_cors_preflight_allows_configured_csrf_header(monkeypatch) -> None:
    app = _create_test_app(monkeypatch, auth_csrf_header_name="X-Arena-CSRF")

    with TestClient(app) as client:
        response = client.options(
            "/api/v1/readyz",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-Arena-CSRF, Content-Type",
            },
        )

    assert response.status_code == 200
    allow_headers = response.headers["access-control-allow-headers"].lower()
    assert "x-arena-csrf" in allow_headers


def test_lifespan_resumes_and_shutdowns_prepopulation_jobs(monkeypatch) -> None:
    service = _FakePrepopulationService()
    app = _create_test_app(monkeypatch, prepopulation_service=service)

    with TestClient(app):
        assert service.resumed is True
        assert service.shutdown_called is False

    assert service.shutdown_called is True


# ── Liveness / readiness split (T9) ──


def test_livez_always_returns_200(monkeypatch) -> None:
    app = _create_test_app(monkeypatch)

    with TestClient(app) as client:
        response = client.get("/api/v1/livez")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_readyz_returns_200_when_deps_are_healthy(monkeypatch) -> None:
    app = _create_test_app(monkeypatch)

    with TestClient(app) as client:
        response = client.get("/api/v1/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["checks"]["database"] is True


def test_readyz_checks_database_on_cold_start(monkeypatch) -> None:
    import app.db.session as session_module

    calls: list[str] = []

    class _RecordingEngine(_HealthyEngine):
        def connect(self) -> _HealthyConnection:
            calls.append("connect")
            return super().connect()

    app = _create_test_app(monkeypatch)
    session_module._engine = None
    monkeypatch.setattr(session_module, "get_engine", lambda: _RecordingEngine())

    with TestClient(app) as client:
        response = client.get("/api/v1/readyz")

    assert response.status_code == 200
    assert response.json()["checks"]["database"] is True
    assert calls == ["connect"]


def test_readyz_returns_503_when_database_is_unreachable(monkeypatch) -> None:
    import app.db.session as session_module

    app = _create_test_app(monkeypatch)
    monkeypatch.setattr(session_module, "get_engine", lambda: _BrokenEngine())

    with TestClient(app) as client:
        response = client.get("/api/v1/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["checks"]["database"] is False


def test_readyz_returns_503_when_redis_is_unreachable(monkeypatch) -> None:
    app = _create_test_app(monkeypatch)

    class _BadRedis:
        def ping(self):
            raise ConnectionError("redis down")

    monkeypatch.setattr(
        redis_utils,
        "get_rate_limit_redis_client",
        lambda: _BadRedis(),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["checks"]["redis"] is False


def test_livez_unaffected_by_redis_failure(monkeypatch) -> None:
    app = _create_test_app(monkeypatch)

    class _BadRedis:
        def ping(self):
            raise ConnectionError("redis down")

    monkeypatch.setattr(
        redis_utils,
        "get_rate_limit_redis_client",
        lambda: _BadRedis(),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/livez")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_healthz_alias_is_removed(monkeypatch) -> None:
    app = _create_test_app(monkeypatch)

    with TestClient(app) as client:
        healthz = client.get("/api/v1/healthz")

    assert healthz.status_code == 404


def test_closes_redis_on_shutdown_exactly_once(monkeypatch) -> None:
    close_calls: list[bool] = []
    monkeypatch.setattr(
        main, "close_all_redis_clients", lambda: close_calls.append(True)
    )
    app = _create_test_app(monkeypatch)

    with TestClient(app):
        pass

    assert close_calls == [True]


# ── Process guard (single-worker advisory lock) ──


def test_process_guard_acquired_during_lifespan(monkeypatch) -> None:
    acquired: list[bool] = []
    released: list[bool] = []

    app = _create_test_app(
        monkeypatch,
        acquire_guard=lambda: acquired.append(True),
        release_guard=lambda: released.append(True),
    )

    with TestClient(app):
        assert acquired == [True]
        assert released == []

    assert released == [True]


def test_schema_bootstrap_runs_before_leaderboard_refresh(monkeypatch) -> None:
    sequence: list[str] = []

    class _FakeRefresher:
        def run_forever(self, stop_event):
            sequence.append("refresh")

            async def _wait_for_shutdown():
                await stop_event.wait()

            return _wait_for_shutdown()

    app = _create_test_app(monkeypatch, leaderboard_refresh_enabled=True)
    monkeypatch.setattr(main, "bootstrap_schema", lambda: sequence.append("bootstrap"))
    monkeypatch.setattr(main, "get_leaderboard_refresher", lambda: _FakeRefresher())

    with TestClient(app):
        assert sequence == ["bootstrap", "refresh"]


def test_process_guard_released_on_shutdown(monkeypatch) -> None:
    released: list[bool] = []

    app = _create_test_app(
        monkeypatch,
        release_guard=lambda: released.append(True),
    )

    with TestClient(app):
        pass

    assert released == [True]


def test_startup_aborts_when_process_guard_unavailable(monkeypatch) -> None:
    def _lock_busy():
        raise RuntimeError("Another process already holds the battle orchestrator lock")

    app = _create_test_app(monkeypatch, acquire_guard=_lock_busy)

    with pytest.raises(RuntimeError, match="battle orchestrator lock"):
        with TestClient(app):
            pass  # pragma: no cover


def test_process_guard_acquire_called_before_serving_requests(monkeypatch) -> None:
    sequence: list[str] = []

    monkeypatch.setattr(
        main, "acquire_battle_process_lock", lambda: sequence.append("acquire")
    )
    monkeypatch.setattr(main, "release_battle_process_lock", lambda: None)
    monkeypatch.setattr(main, "bootstrap_schema", lambda: sequence.append("bootstrap"))

    settings_obj = _settings()
    monkeypatch.setattr(main, "get_settings", lambda: settings_obj)
    monkeypatch.setattr(config_module, "get_settings", lambda: settings_obj)
    monkeypatch.setattr(health, "get_settings", lambda: settings_obj)
    monkeypatch.setattr(redis_utils, "get_settings", lambda: settings_obj)
    redis_utils.get_rate_limit_redis_client.cache_clear()

    import app.db.session as session_module

    session_module._engine = None
    session_module._SessionLocal = None
    monkeypatch.setattr(main, "configure_logging", lambda _: None)
    monkeypatch.setattr(
        main,
        "get_battle_prepopulation_service",
        lambda: _FakePrepopulationService(),
    )

    app = main.create_app()

    with TestClient(app) as client:
        assert "acquire" in sequence, (
            "guard must be acquired before requests are served"
        )
        assert sequence.index("bootstrap") < sequence.index("acquire")
        client.get("/api/v1/livez")
