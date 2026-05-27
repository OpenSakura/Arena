from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.csrf import require_csrf_for_session
from app.core.security import (
    Principal,
    claim_by_path,
    is_bot_principal,
    normalize_groups,
    require_admin,
)
from app.db.session import get_db
from app.models.battle_prepopulation import BattlePrepopulationJob
from app.models.model_registry import Model
from app.schemas.battle_prepopulation import (
    BattlePrepopulationJobCreate,
    BattlePrepopulationJobPublic,
    BattlePrepopulationModelOptionPublic,
    BattlePrepopulationStatsPublic,
)
from app.services.battle_prepopulation import (
    get_battle_prepopulation_service,
    get_pool_stats,
    list_recent_jobs,
)

router = APIRouter(
    prefix="/admin/battle-prepopulation",
    tags=["admin", "battle-prepopulation"],
    dependencies=[Depends(require_admin), Depends(require_csrf_for_session)],
)


@router.post(
    "/jobs",
    response_model=BattlePrepopulationJobPublic,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_prepopulation_job(
    payload: BattlePrepopulationJobCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> BattlePrepopulationJobPublic:
    admin = _require_admin(principal, settings)
    _require_enabled(settings)

    service = get_battle_prepopulation_service()
    job = service.create_job(
        db,
        amount=payload.amount,
        model_ids=payload.model_ids,
        requested_by_user_id=_admin_user_id(admin),
        settings=settings,
    )
    service.start_job(job.id)
    return _to_job_public(job)


@router.get("/stats", response_model=BattlePrepopulationStatsPublic)
def get_prepopulation_stats(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> BattlePrepopulationStatsPublic:
    _require_admin(principal, settings)
    return get_pool_stats(db, settings=settings)


@router.get("/jobs")
def list_prepopulation_jobs(
    limit: Annotated[int, Query(ge=1)] = 20,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> dict[str, list[BattlePrepopulationJobPublic]]:
    _require_admin(principal, settings)
    return {"jobs": list_recent_jobs(db, limit=_clamp_limit(limit))}


@router.get("/model-options")
def list_model_options(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> dict[str, list[BattlePrepopulationModelOptionPublic]]:
    _require_admin(principal, settings)
    stmt = (
        select(Model)
        .where(Model.enabled.is_(True), Model.visibility == "public")
        .order_by(Model.display_name.asc())
    )
    models = db.execute(stmt).scalars().all()
    return {
        "models": [
            BattlePrepopulationModelOptionPublic(
                id=str(model.id),
                display_name=model.display_name,
                model_name=model.model_name,
            )
            for model in models
            if bool(model.enabled) and model.visibility == "public"
        ]
    }


def _require_admin(principal: Principal, settings: Settings | Any) -> Principal:
    if not principal.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required")
    if is_bot_principal(principal):
        raise HTTPException(status_code=403, detail="Human admin principal required")

    claim_value = claim_by_path(principal.claims, settings.oidc_admin_group_claim)
    groups = normalize_groups(claim_value)
    if settings.oidc_admin_group_name not in groups:
        raise HTTPException(status_code=403, detail="Admin group membership required")
    return principal


def _require_enabled(settings: Settings | Any) -> None:
    if not bool(getattr(settings, "battle_prepopulation_enabled", True)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Battle prepopulation is disabled",
        )


def _admin_user_id(principal: Principal) -> str:
    if principal.user_id is None:
        raise HTTPException(status_code=403, detail="Admin user identity required")
    return principal.user_id


def _clamp_limit(limit: int) -> int:
    return min(max(int(limit), 1), 100)


def _to_job_public(job: BattlePrepopulationJob) -> BattlePrepopulationJobPublic:
    return BattlePrepopulationJobPublic(
        id=str(job.id),
        requested_count=int(job.requested_count),
        completed_count=int(job.completed_count or 0),
        failed_count=int(job.failed_count or 0),
        status=job.status,
        requested_by_user_id=str(job.requested_by_user_id),
        model_ids=list(job.model_ids or []),
        last_error=job.last_error,
        started_at=_to_iso(job.started_at),
        finished_at=_to_iso(job.finished_at),
        created_at=_to_iso(job.created_at) or datetime.now(timezone.utc).isoformat(),
        updated_at=_to_iso(job.updated_at) or datetime.now(timezone.utc).isoformat(),
    )


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()
