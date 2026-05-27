"""Battle prepopulation service helpers.

This module creates DB sessions via get_sessionmaker() directly for background
job execution, rather than using the FastAPI request-scoped get_db dependency.
Each background step opens and closes its own session so LLM execution never
holds a request transaction open.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import random
from typing import Any, Sequence
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.routes import battles as battle_routes
from app.core.config import Settings, get_settings
from app.db.session import get_sessionmaker
from app.models.battle import Battle, Run
from app.models.battle_prepopulation import BattlePrepopulationJob
from app.models.model_registry import Model
from app.models.task import Task
from app.models.vote import Vote
from app.schemas.battle_prepopulation import (
    BattlePrepopulationJobCreate,
    BattlePrepopulationJobPublic,
    BattlePrepopulationLatestJobPublic,
    BattlePrepopulationStatsPublic,
)
from app.schemas.battles import BattleCreate
from app.services.battle_orchestrator import (
    BattleOrchestrator,
    get_battle_orchestrator,
)

logger = logging.getLogger(__name__)

_POOL_DELAY_MIN_MS = 10_000
_POOL_DELAY_MAX_MS = 30_000
_POOL_ASSIGNMENT_TTL_SECONDS = 900
_POOL_SOURCES = {"admin_pre_generated", "user_recycled"}


@dataclass(frozen=True, slots=True)
class PoolBattleSelection:
    battle: Battle
    source: str
    display_delay_ms: int


@dataclass(frozen=True, slots=True)
class ValidatedPrepopulationJobRequest:
    amount: int
    model_ids: list[str]


class BattlePrepopulationService:
    def __init__(
        self,
        *,
        orchestrator: BattleOrchestrator | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._settings = settings
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def settings(self) -> Settings:
        return self._settings or get_settings()

    @property
    def orchestrator(self) -> BattleOrchestrator:
        return self._orchestrator or get_battle_orchestrator()

    def create_job(
        self,
        db: Session,
        *,
        amount: int,
        model_ids: Sequence[str] | None,
        requested_by_user_id: str | uuid.UUID,
        settings: Settings | None = None,
    ) -> BattlePrepopulationJob:
        effective_settings = settings or self.settings
        eligible_models = _load_all_models(db)
        request = validate_prepopulation_job_request(
            amount=amount,
            model_ids=list(model_ids or []),
            eligible_models=eligible_models,
            settings=effective_settings,
        )
        requester_uuid = _coerce_uuid(requested_by_user_id, "requested_by_user_id")
        job = BattlePrepopulationJob(
            requested_count=request.amount,
            completed_count=0,
            failed_count=0,
            status="pending",
            requested_by_user_id=requester_uuid,
            model_ids=list(request.model_ids),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    def start_job(
        self,
        job_id: uuid.UUID | str,
        *,
        request_id: str | None = None,
    ) -> asyncio.Task[None]:
        job_uuid = _coerce_uuid(job_id, "job_id")
        task = asyncio.create_task(self.run_job(job_uuid, request_id=request_id))
        self._tasks.add(task)

        def _discard_finished(done_task: asyncio.Task[None]) -> None:
            self._tasks.discard(done_task)
            if done_task.cancelled():
                return
            exc = done_task.exception()
            if exc is not None:
                logger.error(
                    "Battle prepopulation background task failed: %s",
                    exc,
                    exc_info=exc,
                )

        task.add_done_callback(_discard_finished)
        return task

    async def run_job(
        self,
        job_id: uuid.UUID | str,
        *,
        request_id: str | None = None,
    ) -> None:
        await run_prepopulation_job(
            job_id,
            orchestrator=self.orchestrator,
            settings=self.settings,
            request_id=request_id,
        )


def is_battle_pool_eligible(
    battle: Any,
    *,
    has_vote: bool,
    now: datetime | None = None,
    settings: Settings | Any | None = None,
) -> str | None:
    if has_vote or getattr(battle, "status", None) != "completed":
        return None

    current_time = _normalize_datetime(now or datetime.now(timezone.utc))
    metadata = getattr(battle, "metadata_json", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    replay = metadata.get("pooled_replay")
    if isinstance(replay, dict):
        if not _is_expired_locked_pool_replay(replay, current_time):
            return None

    if metadata.get("pre_generated") is True and metadata.get("prepopulation_job_id"):
        return "admin_pre_generated"

    if getattr(battle, "requester_service_account_id", None) is not None:
        return None

    created_at = getattr(battle, "created_at", None)
    if not isinstance(created_at, datetime):
        return None

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    effective_settings = settings or get_settings()
    recycle_hours = int(
        getattr(effective_settings, "battle_pool_user_recycle_after_hours", 24)
    )
    if created_at <= current_time - timedelta(hours=max(recycle_hours, 0)):
        return "user_recycled"
    return None


def can_access_pool_battle(
    battle: Any,
    *,
    principal: Any,
    has_vote: bool,
    now: datetime | None = None,
    settings: Settings | Any | None = None,
) -> bool:
    if not getattr(principal, "is_authenticated", False):
        return False
    if getattr(principal, "actor_type", "human") != "human":
        return False
    if has_vote or getattr(battle, "status", None) != "completed":
        return False

    current_time = _normalize_datetime(now or datetime.now(timezone.utc))
    metadata = getattr(battle, "metadata_json", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    replay = metadata.get("pooled_replay")
    if isinstance(replay, dict):
        if _is_unlocked_pool_replay(replay):
            return _pool_replay_assigned_to_principal(replay, principal)
        if _is_active_locked_pool_replay(replay, current_time):
            return _pool_replay_assigned_to_principal(replay, principal)
        if not _is_expired_locked_pool_replay(replay, current_time):
            return False

    return (
        is_battle_pool_eligible(
            battle,
            has_vote=has_vote,
            now=current_time,
            settings=settings,
        )
        in _POOL_SOURCES
    )


def select_eligible_pool_battle(
    db: Session,
    *,
    now: datetime | None = None,
    settings: Settings | Any | None = None,
) -> PoolBattleSelection | None:
    current_time = now or datetime.now(timezone.utc)
    effective_settings = settings or get_settings()

    fake_battle = getattr(db, "battle", None)
    if fake_battle is not None:
        source = is_battle_pool_eligible(
            fake_battle,
            has_vote=_battle_has_vote(db, getattr(fake_battle, "id", None)),
            now=current_time,
            settings=effective_settings,
        )
        if source is None:
            return None
        return PoolBattleSelection(
            battle=fake_battle,
            source=source,
            display_delay_ms=_random_display_delay_ms(),
        )

    candidates = (
        db.execute(
            select(Battle)
            .where(Battle.status == "completed")
            .order_by(func.random())
            .limit(200)
        )
        .scalars()
        .all()
    )
    for battle in candidates:
        source = is_battle_pool_eligible(
            battle,
            has_vote=_battle_has_vote(db, battle.id),
            now=current_time,
            settings=effective_settings,
        )
        if source is None:
            continue
        return PoolBattleSelection(
            battle=battle,
            source=source,
            display_delay_ms=_random_display_delay_ms(),
        )
    return None


def claim_eligible_pool_battle(
    db: Session,
    principal: Any,
    settings: Settings | Any | None = None,
    now: datetime | None = None,
) -> PoolBattleSelection | None:
    current_time = _normalize_datetime(now or datetime.now(timezone.utc))
    effective_settings = settings or get_settings()
    if not _principal_can_claim_pool(principal):
        return None

    fake_battle = getattr(db, "battle", None)
    if fake_battle is not None:
        return _claim_loaded_pool_battle(
            db,
            battle=fake_battle,
            principal=principal,
            has_vote=_battle_has_vote(db, getattr(fake_battle, "id", None)),
            settings=effective_settings,
            now=current_time,
            allow_existing_assigned=False,
        )

    if not _is_real_session(db):
        return None

    candidates = (
        db.execute(
            select(Battle)
            .where(Battle.status == "completed")
            .order_by(func.random())
            .limit(200)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    for battle in candidates:
        selection = _claim_loaded_pool_battle(
            db,
            battle=battle,
            principal=principal,
            has_vote=_battle_has_vote(db, battle.id),
            settings=effective_settings,
            now=current_time,
            allow_existing_assigned=False,
        )
        if selection is not None:
            return selection

    _rollback_if_possible(db)
    return None


def claim_pool_battle_for_principal(
    db: Session,
    battle_id: uuid.UUID | str,
    principal: Any,
    has_vote: bool,
    settings: Settings | Any | None = None,
    now: datetime | None = None,
) -> PoolBattleSelection | None:
    current_time = _normalize_datetime(now or datetime.now(timezone.utc))
    effective_settings = settings or get_settings()
    if not _principal_can_claim_pool(principal):
        return None

    battle_uuid = _coerce_uuid(battle_id, "battle_id")
    battle = _get_pool_battle_for_update(db, battle_uuid)
    if battle is None:
        return None

    return _claim_loaded_pool_battle(
        db,
        battle=battle,
        principal=principal,
        has_vote=has_vote if not _is_real_session(db) else _battle_has_vote(db, battle_uuid),
        settings=effective_settings,
        now=current_time,
        allow_existing_assigned=True,
    )


def backend_gated_pooled_replay_blocks_vote(
    battle: Any,
    *,
    principal: Any,
    has_vote: bool | None,
    settings: Settings | Any | None = None,
    now: datetime | None = None,
) -> bool:
    current_time = _normalize_datetime(now or datetime.now(timezone.utc))
    metadata = getattr(battle, "metadata_json", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    replay = metadata.get("pooled_replay")
    if isinstance(replay, dict) and replay.get("backend_gated") is True:
        if replay.get("unlocked") is not True:
            return True
        assigned_user_id = replay.get("assigned_user_id")
        return assigned_user_id is not None and assigned_user_id != getattr(principal, "user_id", None)

    if has_vote is None:
        return False

    return (
        is_battle_pool_eligible(
            battle,
            has_vote=has_vote,
            now=current_time,
            settings=settings,
        )
        is not None
    )


def validate_prepopulation_job_request(
    *,
    amount: int,
    model_ids: Sequence[str] | None,
    eligible_models: Sequence[Any],
    settings: Settings | Any | None = None,
) -> BattlePrepopulationJobCreate:
    effective_settings = settings or get_settings()
    max_size = int(getattr(effective_settings, "battle_prepopulation_max_job_size", 50))
    if amount < 1:
        raise HTTPException(status_code=422, detail="amount must be at least 1")
    if amount > max_size:
        raise HTTPException(
            status_code=422,
            detail=f"amount must be less than or equal to max job size {max_size}",
        )

    normalized_ids = [str(_coerce_uuid(model_id, "model_ids")) for model_id in model_ids or []]
    if len(normalized_ids) > 2:
        raise HTTPException(status_code=422, detail="model_ids may contain at most two models")
    if len(set(normalized_ids)) != len(normalized_ids):
        raise HTTPException(status_code=422, detail="model_ids must be distinct")

    eligible_public = [_model for _model in eligible_models if _is_public_enabled(_model)]
    all_by_id = {str(getattr(_model, "id")): _model for _model in eligible_models}
    eligible_by_id = {str(getattr(_model, "id")): _model for _model in eligible_public}

    if not normalized_ids:
        if len(eligible_public) < 2:
            raise HTTPException(
                status_code=400,
                detail="At least two public enabled models are required",
            )
        return BattlePrepopulationJobCreate(amount=amount, model_ids=[])

    for model_id in normalized_ids:
        model = all_by_id.get(model_id)
        if model is None:
            raise HTTPException(status_code=422, detail=f"Model not found: {model_id}")
        if not bool(getattr(model, "enabled", False)):
            raise HTTPException(status_code=422, detail=f"Model is disabled: {model_id}")
        if getattr(model, "visibility", None) != "public":
            raise HTTPException(
                status_code=422,
                detail=f"Model must be public visibility: {model_id}",
            )
        if model_id not in eligible_by_id:
            raise HTTPException(
                status_code=422,
                detail=f"Model is not eligible for prepopulation: {model_id}",
            )

    if len(normalized_ids) == 1:
        selected_id = normalized_ids[0]
        if not any(str(getattr(model, "id")) != selected_id for model in eligible_public):
            raise HTTPException(
                status_code=400,
                detail="Selected model requires at least one distinct eligible opponent",
            )

    return BattlePrepopulationJobCreate(amount=amount, model_ids=normalized_ids)


def get_pool_stats(
    db: Session,
    *,
    settings: Settings | Any | None = None,
) -> BattlePrepopulationStatsPublic:
    effective_settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    available_admin = 0
    available_recycled = 0
    voted_consumed = 0
    total_count = 0
    available_times: list[datetime] = []

    if hasattr(db, "battle") and getattr(db, "battle", None) is None:
        battles = []
    else:
        try:
            battles = list(db.execute(select(Battle)).scalars().all())
        except Exception:  # noqa: BLE001
            battles = []

    for battle in battles:
        if not isinstance(battle, Battle):
            continue
        total_count += 1
        has_vote = _battle_has_vote(db, battle.id)
        source = is_battle_pool_eligible(
            battle,
            has_vote=has_vote,
            now=now,
            settings=effective_settings,
        )
        if source == "admin_pre_generated":
            available_admin += 1
            available_times.append(battle.created_at)
        elif source == "user_recycled":
            available_recycled += 1
            available_times.append(battle.created_at)
        elif has_vote and _is_admin_pregenerated(battle):
            voted_consumed += 1

    jobs = _load_recent_job_rows(db, limit=1)
    latest_job = _job_to_latest_public(jobs[0]) if jobs else None

    generating_count = _count_jobs_by_status(db, {"pending", "running"})
    failed_count = _count_jobs_by_status(db, {"failed"})

    return BattlePrepopulationStatsPublic(
        available_admin_count=available_admin,
        available_recycled_count=available_recycled,
        available_total_count=available_admin + available_recycled,
        generating_count=generating_count,
        failed_count=failed_count,
        voted_consumed_count=voted_consumed,
        total_count=total_count,
        oldest_available_at=_to_iso(min(available_times)) if available_times else None,
        newest_available_at=_to_iso(max(available_times)) if available_times else None,
        latest_job=latest_job,
        max_job_size=int(getattr(effective_settings, "battle_prepopulation_max_job_size", 50)),
    )


def list_recent_jobs(db: Session, *, limit: int = 20) -> list[BattlePrepopulationJobPublic]:
    safe_limit = min(max(int(limit), 1), 100)
    return [_job_to_public(job) for job in _load_recent_job_rows(db, limit=safe_limit)]


async def run_prepopulation_job(
    job_id: uuid.UUID | str,
    *,
    orchestrator: BattleOrchestrator | None = None,
    settings: Settings | None = None,
    request_id: str | None = None,
) -> None:
    job_uuid = _coerce_uuid(job_id, "job_id")
    effective_settings = settings or get_settings()
    effective_orchestrator = orchestrator or get_battle_orchestrator()
    _mark_job_running(job_uuid)

    while True:
        job = _load_job_detached(job_uuid)
        if job is None:
            logger.warning("Prepopulation job missing: %s", job_uuid)
            return
        if job.status not in {"pending", "running"}:
            return
        completed = int(job.completed_count or 0)
        failed = int(job.failed_count or 0)
        if completed + failed >= int(job.requested_count):
            _finish_job(job_uuid, status="completed")
            return

        try:
            battle_id = _create_prepopulation_battle_for_job(job, settings=effective_settings)
        except Exception as exc:  # noqa: BLE001
            _increment_job_failure(job_uuid, exc)
            continue

        try:
            status = await effective_orchestrator.execute_battle_and_wait(
                battle_id,
                timeout_seconds=int(
                    getattr(effective_settings, "battle_prepopulation_job_timeout_seconds", 900)
                ),
                request_id=request_id,
            )
            if status == "completed":
                _increment_job_completed(job_uuid)
            else:
                _increment_job_failure(
                    job_uuid,
                    RuntimeError(f"Battle execution ended with status {status}"),
                )
        except Exception as exc:  # noqa: BLE001
            _increment_job_failure(job_uuid, exc)


def create_prepopulation_job(
    db: Session,
    *,
    amount: int,
    model_ids: Sequence[str] | None,
    requested_by_user_id: str | uuid.UUID,
    settings: Settings | None = None,
) -> BattlePrepopulationJob:
    return get_battle_prepopulation_service(settings=settings).create_job(
        db,
        amount=amount,
        model_ids=model_ids,
        requested_by_user_id=requested_by_user_id,
        settings=settings,
    )


def start_prepopulation_job(
    job_id: uuid.UUID | str,
    *,
    request_id: str | None = None,
) -> asyncio.Task[None]:
    return get_battle_prepopulation_service().start_job(job_id, request_id=request_id)


def _create_prepopulation_battle_for_job(
    job: BattlePrepopulationJob,
    *,
    settings: Settings,
) -> uuid.UUID:
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        task = battle_routes._select_task(db=db, payload=BattleCreate())
        model_a_id, model_b_id = _select_prepopulation_model_pair(
            db,
            model_ids=list(job.model_ids or []),
            settings=settings,
        )
        metadata = {
            "pre_generated": True,
            "prepopulation_job_id": str(job.id),
            "prepopulation_model_ids": list(job.model_ids or []),
            "task_snapshot": _task_snapshot(task),
            "sampling": {
                "task": "weighted_v1",
                "models": _sampling_label_for_model_ids(job.model_ids or []),
            },
            "automatic_retry_count": 0,
        }
        battle = Battle(
            task_id=task.id,
            mode="jp2zh_ab",
            status="pending",
            metadata_json=metadata,
        )
        db.add(battle)
        db.flush()
        db.add_all(
            [
                Run(battle_id=battle.id, side="A", model_id=model_a_id),
                Run(battle_id=battle.id, side="B", model_id=model_b_id),
            ]
        )
        db.commit()
        return battle.id
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Selected model was removed during battle creation, please retry",
        ) from exc
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _select_prepopulation_model_pair(
    db: Session,
    *,
    model_ids: Sequence[str],
    settings: Settings,
) -> tuple[uuid.UUID, uuid.UUID]:
    normalized = [str(_coerce_uuid(model_id, "model_ids")) for model_id in model_ids]
    if not normalized:
        return battle_routes._select_model_pair(db, settings=settings)

    eligible_models = _load_public_enabled_models(db)
    validate_prepopulation_job_request(
        amount=1,
        model_ids=normalized,
        eligible_models=eligible_models,
        settings=settings,
    )
    eligible_by_id = {str(model.id): model for model in eligible_models}

    if len(normalized) == 1:
        selected_id = uuid.UUID(normalized[0])
        opponents = [model for model in eligible_models if str(model.id) != normalized[0]]
        opponent = random.choice(opponents)
        pair = (selected_id, opponent.id)
    else:
        pair = (uuid.UUID(normalized[0]), uuid.UUID(normalized[1]))

    if str(pair[0]) not in eligible_by_id or str(pair[1]) not in eligible_by_id:
        raise HTTPException(status_code=422, detail="Selected models are not eligible")
    if random.randint(0, 1) == 0:
        return pair
    return pair[1], pair[0]


def _mark_job_running(job_id: uuid.UUID) -> None:
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        job = db.get(BattlePrepopulationJob, job_id)
        if job is None:
            return
        if job.status == "pending":
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)
            job.last_error = None
            db.add(job)
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _increment_job_completed(job_id: uuid.UUID) -> None:
    _update_job_progress(job_id, completed_delta=1, failed_delta=0, error=None)


def _increment_job_failure(job_id: uuid.UUID, exc: Exception) -> None:
    _update_job_progress(job_id, completed_delta=0, failed_delta=1, error=str(exc))


def _update_job_progress(
    job_id: uuid.UUID,
    *,
    completed_delta: int,
    failed_delta: int,
    error: str | None,
) -> None:
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        job = db.get(BattlePrepopulationJob, job_id)
        if job is None:
            return
        job.completed_count = int(job.completed_count or 0) + completed_delta
        job.failed_count = int(job.failed_count or 0) + failed_delta
        if error:
            job.last_error = error
        if job.completed_count + job.failed_count >= job.requested_count:
            job.status = "failed" if job.failed_count and not job.completed_count else "completed"
            job.finished_at = datetime.now(timezone.utc)
        db.add(job)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _finish_job(job_id: uuid.UUID, *, status: str) -> None:
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        job = db.get(BattlePrepopulationJob, job_id)
        if job is None:
            return
        job.status = status
        job.finished_at = datetime.now(timezone.utc)
        db.add(job)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _load_job_detached(job_id: uuid.UUID) -> BattlePrepopulationJob | None:
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        return db.get(BattlePrepopulationJob, job_id)
    finally:
        db.close()


def _load_all_models(db: Session) -> list[Model]:
    try:
        return list(db.execute(select(Model)).scalars().all())
    except Exception:  # noqa: BLE001
        models = getattr(db, "models", None)
        if isinstance(models, dict):
            return list(models.values())
        return []


def _load_public_enabled_models(db: Session) -> list[Model]:
    return list(
        db.execute(
            select(Model).where(Model.enabled.is_(True), Model.visibility == "public")
        )
        .scalars()
        .all()
    )


def _load_recent_job_rows(db: Session, *, limit: int) -> list[BattlePrepopulationJob]:
    try:
        rows = (
            db.execute(
                select(BattlePrepopulationJob)
                .order_by(BattlePrepopulationJob.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
    except Exception:  # noqa: BLE001
        return []
    return [row for row in rows if isinstance(row, BattlePrepopulationJob)]


def _count_jobs_by_status(db: Session, statuses: set[str]) -> int:
    try:
        return int(
            db.execute(
                select(func.count(BattlePrepopulationJob.id)).where(
                    BattlePrepopulationJob.status.in_(statuses)
                )
            ).scalar_one()
            or 0
        )
    except Exception:  # noqa: BLE001
        jobs = _load_recent_job_rows(db, limit=100)
        return sum(1 for job in jobs if job.status in statuses)


def _battle_has_vote(db: Session, battle_id: Any) -> bool:
    if battle_id is None:
        return False
    existing_votes = getattr(db, "existing_votes", None)
    if isinstance(existing_votes, list):
        return any(getattr(vote, "battle_id", None) == battle_id for vote in existing_votes)
    if hasattr(db, "execute_rows") or hasattr(db, "runs"):
        return False
    try:
        return (
            db.execute(select(Vote.id).where(Vote.battle_id == battle_id).limit(1))
            .scalar_one_or_none()
            is not None
        )
    except Exception:  # noqa: BLE001
        return False


def _claim_loaded_pool_battle(
    db: Session,
    *,
    battle: Any,
    principal: Any,
    has_vote: bool,
    settings: Settings | Any,
    now: datetime,
    allow_existing_assigned: bool,
) -> PoolBattleSelection | None:
    metadata = getattr(battle, "metadata_json", None)
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    replay = metadata.get("pooled_replay")

    if isinstance(replay, dict):
        if _is_unlocked_pool_replay(replay):
            return None
        if _is_active_locked_pool_replay(replay, now):
            if not allow_existing_assigned or not _pool_replay_assigned_to_principal(replay, principal):
                return None
            source = replay.get("source")
            display_delay_ms = replay.get("display_delay_ms")
            if not isinstance(source, str) or source not in _POOL_SOURCES:
                source = _source_from_battle_metadata(metadata)
            if not isinstance(display_delay_ms, int) or display_delay_ms < 0:
                return None
            return PoolBattleSelection(
                battle=battle,
                source=source,
                display_delay_ms=display_delay_ms,
            )
        if not _is_expired_locked_pool_replay(replay, now):
            return None

    source = is_battle_pool_eligible(
        battle,
        has_vote=has_vote,
        now=now,
        settings=settings,
    )
    if source is None:
        return None

    display_delay_ms = _random_display_delay_ms()
    metadata["pooled_replay"] = build_pooled_replay_metadata(
        principal=principal,
        source=source,
        display_delay_ms=display_delay_ms,
        settings=settings,
        now=now,
    )
    battle.metadata_json = metadata
    if _is_real_session(db):
        db.add(battle)
    db.commit()
    return PoolBattleSelection(
        battle=battle,
        source=source,
        display_delay_ms=display_delay_ms,
    )


def build_pooled_replay_metadata(
    *,
    principal: Any,
    source: str,
    display_delay_ms: int,
    settings: Settings | Any | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    current_time = _normalize_datetime(now or datetime.now(timezone.utc))
    ttl_seconds = int(
        getattr(settings or get_settings(), "battle_pool_assignment_ttl_seconds", _POOL_ASSIGNMENT_TTL_SECONDS)
    )
    expires_at = current_time + timedelta(seconds=max(ttl_seconds, 1))
    return {
        "assigned_at": current_time.isoformat(),
        "expires_at": expires_at.isoformat(),
        "assigned_user_id": getattr(principal, "user_id", None),
        "source": source,
        "display_delay_ms": display_delay_ms,
        "backend_gated": True,
        "unlocked": False,
    }


def _get_pool_battle_for_update(db: Session, battle_id: uuid.UUID) -> Any | None:
    if not _is_real_session(db):
        fake_battle = getattr(db, "battle", None)
        if fake_battle is not None:
            return fake_battle
        get_row = getattr(db, "get", None)
        if callable(get_row):
            return get_row(Battle, battle_id)
        return None
    return db.execute(
        select(Battle)
        .where(Battle.id == battle_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _principal_can_claim_pool(principal: Any) -> bool:
    return (
        getattr(principal, "is_authenticated", False)
        and getattr(principal, "actor_type", "human") == "human"
        and getattr(principal, "user_id", None) is not None
    )


def _is_real_session(db: Session) -> bool:
    return isinstance(db, Session)


def _rollback_if_possible(db: Session) -> None:
    rollback = getattr(db, "rollback", None)
    if callable(rollback):
        rollback()


def _pool_replay_assigned_to_principal(replay: dict[str, Any], principal: Any) -> bool:
    return replay.get("assigned_user_id") == getattr(principal, "user_id", None)


def _is_unlocked_pool_replay(replay: dict[str, Any]) -> bool:
    return replay.get("backend_gated") is True and replay.get("unlocked") is True


def _is_active_locked_pool_replay(replay: dict[str, Any], now: datetime) -> bool:
    return _is_locked_pool_replay(replay) and not _is_pool_replay_expired(replay, now)


def _is_expired_locked_pool_replay(replay: dict[str, Any], now: datetime) -> bool:
    return _is_locked_pool_replay(replay) and _is_pool_replay_expired(replay, now)


def _is_locked_pool_replay(replay: dict[str, Any]) -> bool:
    return replay.get("backend_gated") is True and replay.get("unlocked") is not True


def _is_pool_replay_expired(replay: dict[str, Any], now: datetime) -> bool:
    expires_at = _parse_metadata_datetime(replay.get("expires_at"))
    return expires_at is not None and expires_at <= now


def _parse_metadata_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _normalize_datetime(value)
    if not isinstance(value, str):
        return None
    try:
        return _normalize_datetime(datetime.fromisoformat(value))
    except ValueError:
        return None


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _source_from_battle_metadata(metadata: dict[str, Any]) -> str:
    if metadata.get("pre_generated") is True and metadata.get("prepopulation_job_id"):
        return "admin_pre_generated"
    return "user_recycled"


def _is_admin_pregenerated(battle: Any) -> bool:
    metadata = getattr(battle, "metadata_json", None)
    return isinstance(metadata, dict) and metadata.get("pre_generated") is True


def _is_public_enabled(model: Any) -> bool:
    return bool(getattr(model, "enabled", False)) and getattr(model, "visibility", None) == "public"


def _task_snapshot(task: Task) -> dict[str, str]:
    return {
        "source_text": task.source_text,
        "source_lang": task.source_lang,
        "target_lang": task.target_lang,
    }


def _sampling_label_for_model_ids(model_ids: Sequence[str]) -> str:
    if len(model_ids) == 1:
        return "prepopulation_one_model_v1"
    if len(model_ids) == 2:
        return "prepopulation_two_model_v1"
    return "fastchat_weighted_v2"


def _job_to_public(job: BattlePrepopulationJob) -> BattlePrepopulationJobPublic:
    return BattlePrepopulationJobPublic(**_job_payload(job))


def _job_to_latest_public(job: BattlePrepopulationJob) -> BattlePrepopulationLatestJobPublic:
    return BattlePrepopulationLatestJobPublic(**_job_payload(job))


def _job_payload(job: BattlePrepopulationJob) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "requested_count": int(job.requested_count),
        "completed_count": int(job.completed_count or 0),
        "failed_count": int(job.failed_count or 0),
        "status": job.status,
        "requested_by_user_id": str(job.requested_by_user_id),
        "model_ids": list(job.model_ids or []),
        "last_error": job.last_error,
        "started_at": _to_iso(job.started_at),
        "finished_at": _to_iso(job.finished_at),
        "created_at": _to_iso(job.created_at) or datetime.now(timezone.utc).isoformat(),
        "updated_at": _to_iso(job.updated_at) or datetime.now(timezone.utc).isoformat(),
    }


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _random_display_delay_ms() -> int:
    return random.randint(_POOL_DELAY_MIN_MS, _POOL_DELAY_MAX_MS)


def _coerce_uuid(value: str | uuid.UUID, field_name: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid {field_name}") from exc


_service_singleton: BattlePrepopulationService | None = None


def get_battle_prepopulation_service(
    *,
    settings: Settings | None = None,
) -> BattlePrepopulationService:
    global _service_singleton
    if settings is not None:
        return BattlePrepopulationService(settings=settings)
    if _service_singleton is None:
        _service_singleton = BattlePrepopulationService()
    return _service_singleton
