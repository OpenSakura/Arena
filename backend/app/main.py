"""app.main

FastAPI application entrypoint.

Notes:
- This service is the arena "control plane": model registry, tasks, battles,
  votes, and leaderboards.
- Inference is performed via outbound HTTP calls to an existing LLM gateway
  (no internal worker queue in the MVP).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import logging
import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.db.bootstrap import bootstrap_schema
from app.core.logging import clear_request_id, configure_logging, set_request_id
from app.services.leaderboard_refresh import get_leaderboard_refresher
from app.services.battle_orchestrator import get_battle_orchestrator
from app.services.battle_prepopulation import get_battle_prepopulation_service
from app.services.oidc_client import get_oidc_confidential_client
from app.utils.client_ip import get_client_ip
from app.utils.process_guard import (
    acquire_battle_process_lock,
    release_battle_process_lock,
)
from app.utils.redis import close_all_redis_clients
from app.utils.llm_queue import stop_llm_request_queue
from app.utils.tracing import init_tracing, shutdown_tracing


logger = logging.getLogger(__name__)
startup_logger = logging.getLogger("app.startup")

_NON_PRODUCTION_ENVS = {"dev", "development", "test", "testing", "local"}
_ROLLOUT_LOG_DEFAULTS: dict[str, object] = {
    "max_concurrent_llm_requests": 40,
    "max_llm_requests": 120,
    "llm_queue_wait_timeout_seconds": 30.0,
    "llm_queue_shutdown_timeout_seconds": 10.0,
    "openai_connect_timeout_seconds": 10.0,
    "openai_model_timeout_seconds": 120.0,
    "otel_service_name": "opensakura-arena-backend",
}


def _rollout_log_value(settings: Settings, name: str) -> object:
    return getattr(settings, name, _ROLLOUT_LOG_DEFAULTS[name])


def _emit_startup_warnings(settings: Settings) -> None:
    env = settings.app_env.lower()
    is_prod = env not in _NON_PRODUCTION_ENVS

    if is_prod and not (settings.rate_limit_redis_url or "").strip():
        logger.warning(
            "RATE_LIMIT_REDIS_URL is not configured in production — "
            "Redis-backed rate limiting and shared confidence caching are disabled. "
            "Set RATE_LIMIT_REDIS_URL to enable Redis-backed protections."
        )

    effective_workers = settings.web_concurrency if settings.web_concurrency > 0 else 1
    if effective_workers > 1:
        raise RuntimeError(
            f"WEB_CONCURRENCY={settings.web_concurrency}: battle execution "
            "relies on in-process singletons that are not shared across "
            "OS processes. Run with exactly one worker (WEB_CONCURRENCY=1)."
        )


def _emit_startup_rollout_settings(settings: Settings, *, otlp_enabled: bool) -> None:
    startup_logger.info(
        "Startup rollout settings: "
        "max_concurrent_llm_requests=%s "
        "max_llm_requests=%s "
        "llm_queue_wait_timeout_seconds=%s "
        "llm_queue_shutdown_timeout_seconds=%s "
        "openai_connect_timeout_seconds=%s "
        "openai_model_timeout_seconds=%s "
        "otlp_enabled=%s "
        "otel_service_name=%s",
        _rollout_log_value(settings, "max_concurrent_llm_requests"),
        _rollout_log_value(settings, "max_llm_requests"),
        _rollout_log_value(settings, "llm_queue_wait_timeout_seconds"),
        _rollout_log_value(settings, "llm_queue_shutdown_timeout_seconds"),
        _rollout_log_value(settings, "openai_connect_timeout_seconds"),
        _rollout_log_value(settings, "openai_model_timeout_seconds"),
        otlp_enabled,
        _rollout_log_value(settings, "otel_service_name"),
    )


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    env = settings.app_env.lower()
    is_production = env not in _NON_PRODUCTION_ENVS

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        _emit_startup_warnings(settings)
        await asyncio.to_thread(bootstrap_schema)
        await asyncio.to_thread(acquire_battle_process_lock)
        otlp_enabled = False
        try:
            otlp_enabled = init_tracing(settings=settings)
        except Exception as exc:
            logger.warning(
                "Tracing initialization failed; continuing without tracing",
                extra={"error_type": type(exc).__name__},
            )
        _emit_startup_rollout_settings(settings, otlp_enabled=otlp_enabled)

        stop_event = asyncio.Event()
        refresh_task: asyncio.Task[None] | None = None

        if settings.leaderboard_refresh_enabled:
            refresher = get_leaderboard_refresher()
            refresh_task = asyncio.create_task(refresher.run_forever(stop_event))

            def _on_refresh_done(task: asyncio.Task[None]) -> None:
                if task.cancelled():
                    return
                exc = task.exception()
                if exc is not None:
                    logger.error(
                        "Leaderboard refresh background task died: %s",
                        exc,
                        exc_info=exc,
                    )

            refresh_task.add_done_callback(_on_refresh_done)

        prepopulation_enabled = bool(
            getattr(settings, "battle_prepopulation_enabled", True)
        )
        if prepopulation_enabled:
            get_battle_prepopulation_service().resume_incomplete_jobs()

        try:
            yield
        finally:
            if refresh_task is not None:
                stop_event.set()
                with suppress(asyncio.CancelledError):
                    await refresh_task

            if prepopulation_enabled:
                with suppress(Exception):
                    await get_battle_prepopulation_service().shutdown()

            try:
                llm_shutdown_timeout = getattr(
                    settings,
                    "llm_queue_shutdown_timeout_seconds",
                    None,
                )
                await stop_llm_request_queue(timeout_seconds=llm_shutdown_timeout)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "LLM request queue shutdown failed",
                    extra={"timeout_layer": "llm_queue_shutdown"},
                )

            # Close long-lived HTTP clients to release pooled connections.
            with suppress(Exception):
                oidc = get_oidc_confidential_client()
                await oidc.aclose()
            with suppress(Exception):
                orchestrator = get_battle_orchestrator()
                await orchestrator.llm_client.aclose()

            with suppress(Exception):
                from app.api.routes.battles import _get_turnstile_http_client

                client = _get_turnstile_http_client()
                client.close()
                _get_turnstile_http_client.cache_clear()

            with suppress(Exception):
                close_all_redis_clients()

            with suppress(Exception):
                await asyncio.to_thread(release_battle_process_lock)

            try:
                shutdown_tracing()
            except Exception as exc:
                logger.warning(
                    "Tracing shutdown failed; continuing",
                    extra={"error_type": type(exc).__name__},
                )

    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        openapi_url=None if is_production else "/openapi.json",
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
    )

    _REQUEST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
    _is_production = is_production

    def _apply_security_headers(response_headers, *, request_id: str) -> None:
        """Apply security headers to a response (shared between success and
        error paths to avoid header duplication drift)."""
        response_headers["X-Request-ID"] = request_id
        response_headers["X-Content-Type-Options"] = "nosniff"
        # In dev, relax CSP so that Swagger UI / ReDoc work correctly.
        if not _is_production:
            response_headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' 'unsafe-inline' "
                "https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:"
            )
        else:
            response_headers["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'none'"
            )
            response_headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request_id = (request.headers.get("x-request-id") or "").strip()
        if not request_id or not _REQUEST_ID_PATTERN.fullmatch(request_id):
            request_id = uuid.uuid4().hex

        token = set_request_id(request_id)
        request.state.request_id = request_id
        started = time.monotonic()
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            _apply_security_headers(response.headers, request_id=request_id)
            return response
        except Exception:
            logger.exception(
                "Unhandled exception in %s %s", request.method, request.url.path
            )
            # Build a minimal 500 response that still carries security headers.
            from starlette.responses import Response as StarletteResponse

            error_response = StarletteResponse(
                content="Internal Server Error",
                status_code=500,
                media_type="text/plain",
            )
            _apply_security_headers(error_response.headers, request_id=request_id)
            return error_response
        finally:
            if settings.access_log_enabled:
                duration_ms = int((time.monotonic() - started) * 1000)
                logger.info(
                    "%s %s -> %s (%sms)",
                    request.method,
                    request.url.path,
                    status_code,
                    duration_ms,
                    extra={
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": status_code,
                        "duration_ms": duration_ms,
                        "client_ip": get_client_ip(
                            request,
                            trust_x_forwarded_for=settings.trust_x_forwarded_for,
                        ),
                    },
                )
            clear_request_id(token)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            getattr(settings, "auth_csrf_header_name", "X-CSRF-Token"),
            "X-Request-ID",
        ],
        expose_headers=["X-Request-ID"],
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
