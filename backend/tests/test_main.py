from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.routes import health
import app.core.config as config_module
from app.core import logging as app_logging
import app.main as main
from app.utils import redis as redis_utils
import app.utils.process_guard as process_guard_module


def _settings(
    *,
    access_log_enabled: bool = False,
    leaderboard_refresh_enabled: bool = False,
    turnstile_secret_key: str = "",
    web_concurrency: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        app_name="OpenSakura Arena API (tests)",
        app_env="test",
        leaderboard_refresh_enabled=leaderboard_refresh_enabled,
        access_log_enabled=access_log_enabled,
        turnstile_secret_key=turnstile_secret_key,
        cors_allow_origins=["http://localhost:3000"],
        api_v1_prefix="/api/v1",
        # Defaults for settings referenced by middleware/health checks.
        trust_x_forwarded_for=False,
        rate_limit_redis_url="",
        rate_limit_redis_timeout_seconds=0.5,
        web_concurrency=web_concurrency,
    )


def _create_test_app(
    monkeypatch,
    *,
    access_log_enabled: bool = False,
    leaderboard_refresh_enabled: bool = False,
    turnstile_secret_key: str = "",
    web_concurrency: int = 1,
    acquire_guard=None,
    release_guard=None,
):
    settings_obj = _settings(
        access_log_enabled=access_log_enabled,
        leaderboard_refresh_enabled=leaderboard_refresh_enabled,
        turnstile_secret_key=turnstile_secret_key,
        web_concurrency=web_concurrency,
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
    monkeypatch.setattr(main, "configure_logging", lambda _settings: None)
    return main.create_app()


class _CapturingLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[object, ...], dict[str, object]]] = []

    def info(self, msg: str, *args: object, **kwargs: object) -> None:
        self.calls.append(("info", msg, args, dict(kwargs)))

    def warning(self, msg: str, *args: object, **kwargs: object) -> None:
        self.calls.append(("warning", msg, args, dict(kwargs)))


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
    assert response.json() == {"anon_battle_turnstile_required": True}


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

    _level, message, args, kwargs = logger.calls[0]
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

    main._emit_startup_warnings(settings_obj)

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
        main._emit_startup_warnings(settings_obj)


def test_worker_mode_raises_at_startup_in_dev_when_web_concurrency_exceeds_one(
    monkeypatch,
) -> None:
    import pytest

    logger = _CapturingLogger()
    monkeypatch.setattr(main, "logger", logger)

    settings_obj = _settings(web_concurrency=2)
    settings_obj.app_env = "dev"

    with pytest.raises(RuntimeError, match="WEB_CONCURRENCY"):
        main._emit_startup_warnings(settings_obj)


def test_worker_mode_zero_concurrency_treated_as_one(monkeypatch) -> None:
    logger = _CapturingLogger()
    monkeypatch.setattr(main, "logger", logger)

    settings_obj = _settings(web_concurrency=0)
    settings_obj.app_env = "dev"

    main._emit_startup_warnings(settings_obj)

    warning_messages = [msg for level, msg, _, _ in logger.calls if level == "warning"]
    assert not any("WEB_CONCURRENCY" in m for m in warning_messages)


def test_no_startup_warnings_in_dev(monkeypatch) -> None:
    logger = _CapturingLogger()
    monkeypatch.setattr(main, "logger", logger)

    settings_obj = _settings()
    settings_obj.app_env = "dev"

    main._emit_startup_warnings(settings_obj)

    warning_messages = [msg for level, msg, _, _ in logger.calls if level == "warning"]
    assert warning_messages == []


def test_cors_expose_headers_includes_request_id(monkeypatch) -> None:
    app = _create_test_app(monkeypatch)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/healthz",
            headers={"Origin": "http://localhost:3000"},
        )

    assert response.status_code == 200
    expose = response.headers.get("access-control-expose-headers", "")
    assert "x-request-id" in expose.lower()


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


def test_healthz_is_backward_compatible_alias_for_readyz(monkeypatch) -> None:
    app = _create_test_app(monkeypatch)

    with TestClient(app) as client:
        readyz = client.get("/api/v1/readyz")
        healthz = client.get("/api/v1/healthz")

    assert readyz.status_code == healthz.status_code
    assert readyz.json() == healthz.json()


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

    app = main.create_app()

    with TestClient(app) as client:
        assert "acquire" in sequence, (
            "guard must be acquired before requests are served"
        )
        client.get("/api/v1/livez")
