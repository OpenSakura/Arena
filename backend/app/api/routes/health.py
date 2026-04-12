"""app.api.routes.health

Liveness, readiness, and public-config endpoints.

Notes:
- Keep these endpoints unauthenticated and fast.
- ``/livez`` is a pure liveness probe — always returns 200 when the process
  is running. Load balancers should use this to decide whether to *restart*.
- ``/readyz`` is a readiness probe — checks critical dependencies (DB, Redis).
  Load balancers should use this to decide whether to *route traffic*.
- ``/healthz`` is kept as a backward-compatible alias for ``/readyz``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    ok: bool
    checks: dict[str, bool] | None = None


class PublicConfigResponse(BaseModel):
    anon_battle_turnstile_required: bool


@router.get("/livez", response_model_exclude_none=True)
def livez() -> HealthResponse:
    """Liveness probe — always 200 if the process is up.

    No dependency checks.  Use ``/readyz`` for readiness.
    """
    return HealthResponse(ok=True)


def _check_readiness() -> tuple[dict[str, bool], bool]:
    """Run dependency health checks.  Returns (checks, healthy)."""
    checks: dict[str, bool] = {}
    healthy = True

    # Database connectivity check.
    try:
        from app.db.session import _engine  # noqa: PLC0415

        if _engine is not None:
            from sqlalchemy import text  # noqa: PLC0415

            with _engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                conn.commit()
            checks["database"] = True
    except Exception:  # noqa: BLE001
        checks["database"] = False
        healthy = False

    # Redis check (only if configured).
    try:
        from app.utils.redis import get_rate_limit_redis_client

        redis_client = get_rate_limit_redis_client()
        if redis_client is not None:
            redis_client.ping()
            checks["redis"] = True
    except Exception:  # noqa: BLE001
        logger.warning("Health check: redis unreachable", exc_info=True)
        checks["redis"] = False
        healthy = False

    return checks, healthy


@router.get("/readyz")
def readyz() -> HealthResponse:
    """Readiness probe — verifies that DB and Redis (if configured) are up.

    Returns HTTP 503 when a critical dependency is unreachable so that load
    balancers stop routing traffic to unhealthy instances.
    """
    checks, healthy = _check_readiness()

    if not healthy:
        from fastapi.responses import JSONResponse

        return JSONResponse(  # type: ignore[return-value]
            content={"ok": False, "checks": checks},
            status_code=503,
        )

    return HealthResponse(ok=True, checks=checks)


@router.get("/healthz")
def healthz() -> HealthResponse:
    """Backward-compatible alias for ``/readyz``."""
    return readyz()


@router.get("/public-config")
def public_config(
    settings: Settings = Depends(get_settings),
) -> PublicConfigResponse:
    return PublicConfigResponse(
        anon_battle_turnstile_required=bool(
            (settings.turnstile_secret_key or "").strip()
        )
    )
