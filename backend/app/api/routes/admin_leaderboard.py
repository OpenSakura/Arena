"""app.api.routes.admin_leaderboard

Admin endpoints for leaderboard maintenance.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import require_admin
from app.services.leaderboard_refresh import get_leaderboard_refresher

router = APIRouter(
    prefix="/admin/leaderboard",
    tags=["admin", "leaderboard"],
    dependencies=[Depends(require_admin)],
)


@router.get("/status")
def get_refresh_status() -> dict[str, object]:
    refresher = get_leaderboard_refresher()
    status = refresher.get_status()
    return {
        "enabled": status.enabled,
        "interval_seconds": status.interval_seconds,
        "daily_vote_cap": status.daily_vote_cap,
        "last_attempted_at": status.last_attempted_at,
        "last_succeeded_at": status.last_succeeded_at,
        "last_error": status.last_error,
        "total_refreshes": status.total_refreshes,
    }


@router.post("/refresh")
def run_refresh_now() -> dict[str, object]:
    refresher = get_leaderboard_refresher()
    refresher.refresh_once()
    status = refresher.get_status()
    if status.last_error is not None:
        raise HTTPException(
            status_code=500,
            detail=f"Refresh failed: {status.last_error}",
        )
    return {
        "ok": True,
        "last_succeeded_at": status.last_succeeded_at,
        "last_error": status.last_error,
        "total_refreshes": status.total_refreshes,
    }
