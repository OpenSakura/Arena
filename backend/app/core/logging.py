"""app.core.logging

Logging configuration.

Notes:
- Keep logging structured-friendly.
- Avoid logging secrets (Authorization headers, provider API keys, etc.).
"""

from __future__ import annotations

import contextvars
from datetime import datetime, timezone
import json
import logging
from typing import Any

from app.core.config import Settings


_REQUEST_ID_CTX: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id",
    default="-",
)


class RequestContextFilter(logging.Filter):
    """Inject request correlation fields into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _REQUEST_ID_CTX.get()
        return True


class JSONLogFormatter(logging.Formatter):
    """Compact JSON formatter for machine-friendly logs."""

    _OPTIONAL_KEYS = (
        "method",
        "path",
        "status_code",
        "duration_ms",
        "client_ip",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }

        for key in self._OPTIONAL_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, separators=(",", ":"))


def set_request_id(request_id: str) -> contextvars.Token[str]:
    return _REQUEST_ID_CTX.set(request_id)


def clear_request_id(token: contextvars.Token[str]) -> None:
    _REQUEST_ID_CTX.reset(token)


def get_request_id() -> str:
    return _REQUEST_ID_CTX.get()


def configure_logging(settings: Settings) -> None:
    if settings.log_level:
        configured = settings.log_level.upper()
    else:
        configured = "DEBUG" if settings.app_env.lower() in {"dev", "local"} else "INFO"

    level = getattr(logging, configured, None)
    if level is None:
        import warnings

        warnings.warn(
            f"Invalid LOG_LEVEL '{configured}', falling back to INFO",
            stacklevel=2,
        )
        level = logging.INFO

    handler = logging.StreamHandler()
    handler.addFilter(RequestContextFilter())

    if settings.log_json:
        handler.setFormatter(JSONLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [req=%(request_id)s] %(name)s: %(message)s"
            )
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Route uvicorn logs through the same formatter/filter stack.
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = logger_name != "uvicorn.access"
