"""app.api.routes.health

Health and readiness endpoints.

Notes:
- Keep these endpoints unauthenticated and fast.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@router.get("/public-config")
def public_config(settings: Settings = Depends(get_settings)) -> dict[str, bool]:
    return {
        "anon_vote_turnstile_required": bool(
            (settings.turnstile_secret_key or "").strip()
        ),
    }
