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
from app.core.config import get_settings
from app.core.logging import clear_request_id, configure_logging, set_request_id
from app.services.leaderboard_refresh import get_leaderboard_refresher
from app.services.battle_orchestrator import get_battle_orchestrator
from app.services.oidc import get_oidc_verifier


logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
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
                await orchestrator._llm_client.aclose()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    if settings.app_env.lower() == "production":
        app.openapi_url = None

    _REQUEST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

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
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Content-Security-Policy"] = "default-src 'none'"
            response.headers["Permissions-Policy"] = (
                "camera=(), microphone=(), geolocation=()"
            )
            if settings.app_env.lower() == "production":
                response.headers["Strict-Transport-Security"] = (
                    "max-age=63072000; includeSubDomains; preload"
                )
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
            error_response.headers["X-Request-ID"] = request_id
            error_response.headers["X-Content-Type-Options"] = "nosniff"
            error_response.headers["X-Frame-Options"] = "DENY"
            error_response.headers["Referrer-Policy"] = (
                "strict-origin-when-cross-origin"
            )
            error_response.headers["Content-Security-Policy"] = "default-src 'none'"
            error_response.headers["Permissions-Policy"] = (
                "camera=(), microphone=(), geolocation=()"
            )
            if settings.app_env.lower() == "production":
                error_response.headers["Strict-Transport-Security"] = (
                    "max-age=63072000; includeSubDomains; preload"
                )
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
                        "client_ip": (
                            request.client.host if request.client is not None else None
                        ),
                    },
                )
            clear_request_id(token)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
