from __future__ import annotations

from contextlib import contextmanager
import json
import logging
from types import SimpleNamespace

import pytest

from app.core import logging as app_logging


def _capture_state(logger: logging.Logger) -> tuple[list[logging.Handler], bool, int]:
    return (list(logger.handlers), logger.propagate, logger.level)


def _restore_state(
    logger: logging.Logger,
    state: tuple[list[logging.Handler], bool, int],
) -> None:
    handlers, propagate, level = state
    logger.handlers = handlers
    logger.propagate = propagate
    logger.setLevel(level)


@contextmanager
def _preserve_logging_state():
    root = logging.getLogger()
    uvicorn = logging.getLogger("uvicorn")
    uvicorn_error = logging.getLogger("uvicorn.error")
    uvicorn_access = logging.getLogger("uvicorn.access")

    snapshots = {
        root: _capture_state(root),
        uvicorn: _capture_state(uvicorn),
        uvicorn_error: _capture_state(uvicorn_error),
        uvicorn_access: _capture_state(uvicorn_access),
    }

    try:
        yield
    finally:
        for logger, state in snapshots.items():
            _restore_state(logger, state)


def test_request_id_context_round_trip() -> None:
    token = app_logging.set_request_id("req-123")

    try:
        assert app_logging.get_request_id() == "req-123"
    finally:
        app_logging.clear_request_id(token)

    assert app_logging.get_request_id() == "-"


def test_request_context_filter_injects_request_id() -> None:
    record = logging.LogRecord(
        name="arena",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    token = app_logging.set_request_id("req-filter")

    try:
        assert app_logging.RequestContextFilter().filter(record) is True
    finally:
        app_logging.clear_request_id(token)

    assert getattr(record, "request_id") == "req-filter"


def test_json_log_formatter_includes_expected_fields_and_exception() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        exc_info = (type(exc), exc, exc.__traceback__)

    record = logging.LogRecord(
        name="arena.requests",
        level=logging.INFO,
        pathname=__file__,
        lineno=42,
        msg="handled %s",
        args=("request",),
        exc_info=exc_info,
    )
    record.request_id = "req-json"
    record.method = "GET"
    record.path = "/readyz"
    record.status_code = 200
    record.duration_ms = 9
    record.client_ip = None

    payload = json.loads(app_logging.JSONLogFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "arena.requests"
    assert payload["message"] == "handled request"
    assert payload["request_id"] == "req-json"
    assert payload["method"] == "GET"
    assert payload["path"] == "/readyz"
    assert payload["status_code"] == 200
    assert payload["duration_ms"] == 9
    assert "client_ip" not in payload
    assert "ts" in payload
    assert "RuntimeError: boom" in payload["exception"]


def test_configure_logging_honors_explicit_log_level_and_json_mode() -> None:
    settings = SimpleNamespace(log_level="warning", app_env="prod", log_json=True)

    with _preserve_logging_state():
        app_logging.configure_logging(settings)  # type: ignore[arg-type]

        root = logging.getLogger()
        assert root.level == logging.WARNING
        assert len(root.handlers) == 1

        handler = root.handlers[0]
        assert isinstance(handler.formatter, app_logging.JSONLogFormatter)
        assert any(
            isinstance(log_filter, app_logging.RequestContextFilter)
            for log_filter in handler.filters
        )

        for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            uvicorn_logger = logging.getLogger(logger_name)
            assert uvicorn_logger.handlers == []
            assert uvicorn_logger.propagate is True


@pytest.mark.parametrize(
    ("app_env", "expected_level"),
    [
        ("dev", logging.DEBUG),
        ("local", logging.DEBUG),
        ("prod", logging.INFO),
    ],
)
def test_configure_logging_uses_environment_defaults(
    app_env: str,
    expected_level: int,
) -> None:
    settings = SimpleNamespace(log_level="", app_env=app_env, log_json=False)

    with _preserve_logging_state():
        app_logging.configure_logging(settings)  # type: ignore[arg-type]

        root = logging.getLogger()
        assert root.level == expected_level
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, logging.Formatter)
