"""app.main

FastAPI application entrypoint.

Notes:
- This service is the arena "control plane": model registry, tasks, battles,
  votes, and leaderboards.
- Inference is performed via outbound HTTP calls to an existing LLM gateway or
  provider APIs (no internal worker queue in the MVP).
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
from app.services.oidc import get_oidc_verifier
from app.utils.client_ip import get_client_ip
from app.utils.process_guard import (
    acquire_battle_process_lock,
    release_battle_process_lock,
)
from app.utils.redis import close_all_redis_clients


logger = logging.getLogger(__name__)

_NON_PRODUCTION_ENVS = {"dev", "development", "test", "testing", "local"}


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

        try:
            yield
        finally:
            if refresh_task is not None:
                stop_event.set()
                with suppress(asyncio.CancelledError):
                    await refresh_task

            # Close long-lived HTTP clients to release pooled connections.
            with suppress(Exception):
                oidc = get_oidc_verifier()
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
            "X-CSRF-Token",
            "X-Request-ID",
        ],
        expose_headers=["X-Request-ID"],
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
