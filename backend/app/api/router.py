"""app.api.router

Top-level API router.

Notes:
- Mount all versioned endpoints here.
- Keep path organization stable; prefer additive changes to avoid breaking
  existing clients.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    admin_battle_prepopulation,
    admin_exports,
    admin_leaderboard,
    admin_models,
    admin_service_accounts,
    admin_tasks,
    auth,
    battles,
    bot_battles,
    health,
    leaderboard,
    me,
    votes,
)

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(health.router)
api_router.include_router(me.router)
api_router.include_router(battles.router)
api_router.include_router(votes.router)
api_router.include_router(leaderboard.router)
api_router.include_router(bot_battles.router)

api_router.include_router(admin_models.router)
api_router.include_router(admin_service_accounts.router)
api_router.include_router(admin_tasks.router)
api_router.include_router(admin_exports.router)
api_router.include_router(admin_battle_prepopulation.router)
api_router.include_router(admin_leaderboard.router)
