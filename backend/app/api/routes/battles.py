"""app.api.routes.battles

Battle orchestration endpoints.

Notes:
- A "battle" is a pairwise comparison between two model runs on the same task.
- Streaming should be done via SSE so the UI can display partial outputs.
- Anonymous users can create battles; authenticated users enrich audit logs.
"""

from __future__ import annotations

from functools import lru_cache
import random
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import Principal, get_principal_optional
from app.db.session import get_db
from app.models.battle import Battle, Run
from app.models.model_registry import Model
from app.models.rating import ModelRating
from app.models.task import Task
from app.schemas.battles import BattleCreate, BattlePublic, RunPublic
from app.services.battle_orchestrator import BattleOrchestrator, get_battle_orchestrator
from app.services.sampling import CandidateModel, SamplingPolicy, select_battle_pair
from app.utils.anon import get_or_set_anon_id
from app.utils.id import parse_uuid
from app.utils.rate_limit import RollingWindowRateLimiter, build_anon_rate_limit_key
from app.utils.redis import get_rate_limit_redis_client

router = APIRouter(prefix="/battles", tags=["battles"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_battle(
    payload: BattleCreate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal_optional),
    settings: Settings = Depends(get_settings),
) -> BattlePublic:
    # By design: authenticated users are trusted and not rate-limited.
    # Only anonymous users are subject to anti-abuse throttling.
    if not principal.is_authenticated:
        get_or_set_anon_id(
            request=request,
            response=response,
            secure=settings.anon_id_cookie_secure,
        )
        _enforce_anon_battle_rate_limit(
            request=request,
            settings=settings,
        )

    task = _select_task(db=db, payload=payload)
    model_a_id, model_b_id = _select_model_pair(db, settings=settings)

    battle = Battle(
        task_id=task.id,
        mode=payload.mode or "jp2zh_ab",
        status="pending",
        metadata_json={
            "task_snapshot": {
                "source_text": task.source_text,
                "source_lang": task.source_lang,
                "target_lang": task.target_lang,
            },
            "sampling": {
                "task": "weighted_v1",
                "models": "fastchat_weighted_v2",
            },
            "requester_user_id": principal.user_id,
        },
    )
    db.add(battle)
    db.flush()

    run_a = Run(battle_id=battle.id, side="A", model_id=model_a_id)
    run_b = Run(battle_id=battle.id, side="B", model_id=model_b_id)
    db.add_all([run_a, run_b])

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Selected model was removed during battle creation, please retry",
        ) from exc
    db.refresh(battle)
    db.refresh(run_a)
    db.refresh(run_b)

    return _to_battle_public(
        battle=battle,
        source_text=task.source_text,
        source_lang=task.source_lang,
        target_lang=task.target_lang,
        run_a=run_a,
        run_b=run_b,
    )


@router.get("/{battle_id}")
def get_battle(battle_id: str, db: Session = Depends(get_db)) -> BattlePublic:
    battle_uuid = parse_uuid(battle_id, "battle_id")

    battle = db.get(Battle, battle_uuid)
    if battle is None:
        raise HTTPException(status_code=404, detail="Battle not found")

    snapshot = _battle_task_snapshot(battle)
    if snapshot is None:
        task = db.get(Task, battle.task_id)
        if task is None:
            raise HTTPException(status_code=500, detail="Battle task not found")
        source_text = task.source_text
        source_lang = task.source_lang
        target_lang = task.target_lang
    else:
        source_text, source_lang, target_lang = snapshot

    runs = (
        db.execute(
            select(Run).where(Run.battle_id == battle.id).order_by(Run.side.asc())
        )
        .scalars()
        .all()
    )
    run_map = {run.side: run for run in runs}

    return _to_battle_public(
        battle=battle,
        source_text=source_text,
        source_lang=source_lang,
        target_lang=target_lang,
        run_a=run_map.get("A"),
        run_b=run_map.get("B"),
    )


@router.get("/{battle_id}/stream")
async def stream_battle(
    battle_id: str,
    request: Request,
    db: Session = Depends(get_db),
    orchestrator: BattleOrchestrator = Depends(get_battle_orchestrator),
    principal: Principal = Depends(get_principal_optional),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    battle_uuid = parse_uuid(battle_id, "battle_id")

    # Pre-flight check: return a proper 404 instead of streaming an SSE
    # error event over an HTTP 200 response.
    if db.get(Battle, battle_uuid) is None:
        raise HTTPException(status_code=404, detail="Battle not found")

    # Anonymous stream connections are rate-limited to prevent resource
    # exhaustion via many concurrent SSE connections.
    if not principal.is_authenticated:
        _enforce_anon_battle_stream_rate_limit(
            request=request,
            settings=settings,
        )

    request_id = getattr(request.state, "request_id", None)
    return StreamingResponse(
        orchestrator.stream_battle(battle_uuid, request_id=request_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _select_task(*, db: Session, payload: BattleCreate) -> Task:
    if payload.task_id is not None:
        task = db.get(Task, parse_uuid(payload.task_id, "task_id"))
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return task

    stmt = select(Task)
    if payload.task_set_id is not None:
        task_set_uuid = parse_uuid(payload.task_set_id, "task_set_id")
        stmt = stmt.where(Task.task_set_id == task_set_uuid)

    # Use DB-level random sampling with a reasonable limit to avoid loading
    # all tasks into memory.  Fetch task IDs and battle counts in one query.
    task_id_rows = (
        db.execute(stmt.with_only_columns(Task.id).limit(10_000)).scalars().all()
    )
    if not task_id_rows:
        raise HTTPException(status_code=400, detail="No tasks available for battle")

    task_ids = list(task_id_rows)
    counts = {
        task_id: count
        for task_id, count in db.execute(
            select(Battle.task_id, func.count(Battle.id))
            .where(Battle.task_id.in_(task_ids))
            .group_by(Battle.task_id)
        ).all()
    }
    weights = [1.0 / (1.0 + float(counts.get(tid, 0))) for tid in task_ids]
    rng = random.Random(secrets.token_bytes(8))
    selected_id = rng.choices(task_ids, weights=weights, k=1)[0]
    task = db.get(Task, selected_id)
    if task is None:
        raise HTTPException(status_code=500, detail="Selected task not found")
    return task


def _select_model_pair(
    db: Session, *, settings: Settings
) -> tuple[uuid.UUID, uuid.UUID]:
    model_rows = db.execute(
        select(Model.id, Model.model_name, func.coalesce(ModelRating.games_played, 0))
        .outerjoin(ModelRating, ModelRating.model_id == Model.id)
        .where(Model.enabled.is_(True), Model.visibility == "public")
    ).all()

    if len(model_rows) < 2:
        raise HTTPException(
            status_code=400, detail="At least two public models are required"
        )

    candidates = [
        CandidateModel(
            id=row[0],
            model_name=row[1],
            games_played=int(row[2]),
        )
        for row in model_rows
    ]

    policy = SamplingPolicy(
        weights=settings.battle_sampling_weights,
        targets=settings.battle_targets,
        strict_targets=settings.battle_strict_targets,
        outage_models=set(settings.battle_outage_models),
        boost_models=set(settings.battle_sampling_boost_models),
    )

    try:
        return select_battle_pair(candidates=candidates, policy=policy)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="No valid model pair available for sampling",
        ) from exc


def _to_run_public(run: Run | None, *, include_stats: bool = False) -> RunPublic | None:
    if run is None:
        return None
    return RunPublic(
        id=str(run.id),
        side=run.side,
        output_text=run.output_text,
        # Omit stats before vote to prevent model identity inference from
        # provider-specific request_id formats and tokenizer-specific
        # usage.prompt_tokens counts.
        stats=run.stats if include_stats else None,
        error_text=run.error_text,
    )


def _to_battle_public(
    *,
    battle: Battle,
    source_text: str,
    source_lang: str,
    target_lang: str,
    run_a: Run | None,
    run_b: Run | None,
) -> BattlePublic:
    return BattlePublic(
        id=str(battle.id),
        task_id=str(battle.task_id),
        source_text=source_text,
        source_lang=source_lang,
        target_lang=target_lang,
        mode=battle.mode,
        status=battle.status,
        run_a=_to_run_public(run_a),
        run_b=_to_run_public(run_b),
    )


def _battle_task_snapshot(battle: Battle) -> tuple[str, str, str] | None:
    metadata = battle.metadata_json
    if not isinstance(metadata, dict):
        return None

    snapshot = metadata.get("task_snapshot")
    if not isinstance(snapshot, dict):
        return None

    source_text = snapshot.get("source_text")
    source_lang = snapshot.get("source_lang")
    target_lang = snapshot.get("target_lang")
    if (
        not isinstance(source_text, str)
        or not isinstance(source_lang, str)
        or not isinstance(target_lang, str)
    ):
        return None

    return source_text, source_lang, target_lang


@lru_cache(maxsize=1)
def _get_battle_create_rate_limiter() -> RollingWindowRateLimiter:
    settings = get_settings()
    return RollingWindowRateLimiter(
        limit=settings.anon_battle_create_rate_limit,
        window_seconds=settings.anon_battle_create_rate_limit_window_seconds,
        bucket_seconds=settings.anon_rate_limit_bucket_seconds,
        redis_client=get_rate_limit_redis_client(),
        redis_prefix=settings.rate_limit_redis_key_prefix,
    )


def _enforce_anon_battle_rate_limit(
    *,
    request: Request,
    settings: Settings,
) -> None:
    limiter = _get_battle_create_rate_limiter()
    key = build_anon_rate_limit_key(
        scope="anon_battle_create",
        request=request,
        trust_x_forwarded_for=settings.trust_x_forwarded_for,
        ip_hash_salt=settings.anon_ip_hash_salt,
    )
    if limiter.is_limited(key):
        raise HTTPException(
            status_code=429,
            detail="Too many anonymous battle creation requests",
            headers={
                "Retry-After": str(
                    settings.anon_battle_create_rate_limit_window_seconds
                )
            },
        )


@lru_cache(maxsize=1)
def _get_battle_stream_rate_limiter() -> RollingWindowRateLimiter:
    settings = get_settings()
    return RollingWindowRateLimiter(
        limit=settings.anon_battle_stream_rate_limit,
        window_seconds=settings.anon_battle_stream_rate_limit_window_seconds,
        bucket_seconds=settings.anon_rate_limit_bucket_seconds,
        redis_client=get_rate_limit_redis_client(),
        redis_prefix=settings.rate_limit_redis_key_prefix,
    )


def _enforce_anon_battle_stream_rate_limit(
    *,
    request: Request,
    settings: Settings,
) -> None:
    limiter = _get_battle_stream_rate_limiter()
    key = build_anon_rate_limit_key(
        scope="anon_battle_stream",
        request=request,
        trust_x_forwarded_for=settings.trust_x_forwarded_for,
        ip_hash_salt=settings.anon_ip_hash_salt,
    )
    if limiter.is_limited(key):
        raise HTTPException(
            status_code=429,
            detail="Too many anonymous battle stream requests",
            headers={
                "Retry-After": str(
                    settings.anon_battle_stream_rate_limit_window_seconds
                )
            },
        )
