"""app.api.routes.admin_leaderboard

Admin endpoints for leaderboard maintenance.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.routes import leaderboard as leaderboard_route
from app.core.csrf import require_csrf_for_session
from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.db.session import get_db
from app.schemas.leaderboard import LeaderboardResponse
from app.services.leaderboard_refresh import get_leaderboard_refresher

router = APIRouter(
    prefix="/admin/leaderboard",
    tags=["admin", "leaderboard"],
    dependencies=[Depends(require_admin), Depends(require_csrf_for_session)],
)


@router.get("")
def get_admin_leaderboard(
    method: Annotated[str, Query(pattern="^(elo|bt)$")] = "elo",
    include_confidence: Annotated[bool, Query()] = False,
    judge_type: Annotated[str, Query(pattern="^(all|human|bot)$")] = "all",
    exclude_refusals: Annotated[bool, Query()] = False,
    service_account_id: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> LeaderboardResponse:
    return leaderboard_route.build_leaderboard_response(
        method=method,
        include_confidence=include_confidence,
        judge_type=judge_type,
        service_account_id=leaderboard_route._parse_service_account_id_or_422(
            service_account_id
        ),
        db=db,
        settings=settings,
        exclude_refusals=exclude_refusals,
    )


@router.get("/status")
async def get_refresh_status() -> dict[str, object]:
    refresher = get_leaderboard_refresher()
    status = await asyncio.to_thread(refresher.get_status)
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
async def run_refresh_now() -> dict[str, object]:
    refresher = get_leaderboard_refresher()
    # Snapshot total_refreshes *before* the call so we can detect a no-op
    # skip (advisory lock held by another worker) vs a real completion.
    before_status = await asyncio.to_thread(refresher.get_status)
    before_total = before_status.total_refreshes

    await asyncio.to_thread(refresher.refresh_once)

    status = await asyncio.to_thread(refresher.get_status)
    if status.last_error is not None:
        raise HTTPException(
            status_code=500,
            detail=f"Refresh failed: {status.last_error}",
        )
    if status.total_refreshes == before_total:
        # refresh_once returned early because the advisory lock was already
        # held by a background worker — no work was done.
        raise HTTPException(
            status_code=409,
            detail="Refresh skipped: a refresh is already in progress (advisory lock busy)",
        )
    return {
        "ok": True,
        "last_succeeded_at": status.last_succeeded_at,
        "last_error": status.last_error,
        "total_refreshes": status.total_refreshes,
    }
