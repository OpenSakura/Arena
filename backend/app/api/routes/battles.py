"""app.api.routes.battles

Battle orchestration endpoints.

Notes:
- A "battle" is a pairwise comparison between two model runs on the same task.
- Streaming should be done via SSE so the UI can display partial outputs.
- Live execution assumes a single API worker/process owns the cached
  ``BattleOrchestrator`` singleton; additional stream consumers are observers.
- Battle creation and retry are authenticated-only operations.
- Completed battle results are public; non-completed battle reads remain
  authenticated-only.
"""

from __future__ import annotations

import asyncio
import datetime
from functools import lru_cache
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import (
    Principal,
    claim_by_path,
    get_principal_optional,
    get_principal_required,
    normalize_groups,
    require_admin,
)
from app.db.session import get_db
from app.models.battle import Battle, Run
from app.models.model_registry import Model
from app.models.rating import ModelRating
from app.models.task import Task
from app.models.vote import Vote
from app.schemas.battles import BattleCreate, BattlePublic, RunPublic
from app.services.battle_orchestrator import BattleOrchestrator, get_battle_orchestrator
from app.services.sampling import CandidateModel, SamplingPolicy, select_battle_pair
from app.utils.client_ip import get_client_ip
from app.utils.id import parse_uuid_or_422
from app.utils.rate_limit import (
    RollingWindowRateLimiter,
    build_auth_rate_limit_key,
)
from app.utils.redis import get_rate_limit_redis_client

router = APIRouter(prefix="/battles", tags=["battles"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_battle(
    payload: BattleCreate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal_required),
    settings: Settings = Depends(get_settings),
) -> BattlePublic:
    _enforce_auth_battle_rate_limit(
        principal=principal,
        settings=settings,
    )

    # Block battle creation when the daily vote cap is reached, to prevent
    # wasting LLM inference on battles whose votes would be silently excluded
    # from ratings.
    _enforce_daily_vote_cap(
        db=db,
        principal=principal,
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
            "automatic_retry_count": 0,
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
        principal=principal,
        run_a=run_a,
        run_b=run_b,
    )


@router.get("/{battle_id}")
def get_battle(
    battle_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal_optional),
) -> BattlePublic:
    battle_uuid = parse_uuid_or_422(battle_id, "battle_id")

    battle = db.get(Battle, battle_uuid)
    if battle is None:
        raise HTTPException(status_code=404, detail="Battle not found")

    if battle.status != "completed" and not principal.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required")

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

    has_vote = (
        db.execute(
            select(Vote.id).where(Vote.battle_id == battle.id).limit(1)
        ).scalar_one_or_none()
        is not None
    )

    return _to_battle_public(
        battle=battle,
        source_text=source_text,
        source_lang=source_lang,
        target_lang=target_lang,
        principal=principal,
        run_a=run_map.get("A"),
        run_b=run_map.get("B"),
        has_vote=has_vote,
        include_stats=has_vote,
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
    battle_uuid = parse_uuid_or_422(battle_id, "battle_id")

    # Pre-flight check: return a proper 404 instead of streaming an SSE
    # error event over an HTTP 200 response.
    battle = db.get(Battle, battle_uuid)
    if battle is None:
        raise HTTPException(status_code=404, detail="Battle not found")

    # Only the battle creator (or an admin) may connect to the stream.
    if not _is_battle_creator(battle, principal=principal):
        settings_for_admin = get_settings()
        claim_value = claim_by_path(
            principal.claims, settings_for_admin.oidc_admin_group_claim
        )
        groups = normalize_groups(claim_value)
        is_admin = (
            principal.is_authenticated
            and settings_for_admin.oidc_admin_group_name in groups
        )
        if not is_admin:
            raise HTTPException(
                status_code=403,
                detail="Only the battle creator may connect to this stream",
            )

    # Battle mutation/stream ownership is authenticated-user scoped.
    # Run the sync Redis call in a thread to avoid blocking the event loop.
    if principal.is_authenticated:
        await asyncio.to_thread(
            _enforce_auth_battle_stream_rate_limit,
            principal=principal,
            settings=settings,
        )

    # The cached orchestrator owns live execution inside this API process.
    # Additional consumers for the same battle attach as read-only observers.
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


@router.post("/{battle_id}/retry")
def retry_battle(
    battle_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal_required),
) -> BattlePublic:
    """Reset a failed, unvoted battle to pending so it can be re-executed.

    Clears all persisted run artifacts (output, error, stats, request_json,
    prompt_rendered) and sets battle status back to ``pending``.  The next
    SSE stream connection will re-execute both runs from scratch under the
    single-owner execution model.

    Allowed for the authenticated battle creator and admins.
    """
    battle_uuid = parse_uuid_or_422(battle_id, "battle_id")
    battle = db.execute(
        select(Battle).where(Battle.id == battle_uuid).with_for_update()
    ).scalar_one_or_none()
    if battle is None:
        raise HTTPException(status_code=404, detail="Battle not found")

    is_creator = _is_battle_creator(battle, principal=principal)
    if not is_creator:
        settings_for_admin = get_settings()
        claim_value = claim_by_path(
            principal.claims, settings_for_admin.oidc_admin_group_claim
        )
        groups = normalize_groups(claim_value)
        is_admin = (
            principal.is_authenticated
            and settings_for_admin.oidc_admin_group_name in groups
        )
        if not is_admin:
            raise HTTPException(
                status_code=403,
                detail="Only the battle creator or an admin may retry",
            )

    if battle.status != "failed":
        raise HTTPException(
            status_code=409,
            detail="Only failed battles can be retried",
        )

    # Check that no vote has been submitted — retrying after voting would
    # invalidate the vote.
    has_vote = (
        db.execute(
            select(Vote.id).where(Vote.battle_id == battle.id).limit(1)
        ).scalar_one_or_none()
        is not None
    )
    if has_vote:
        raise HTTPException(
            status_code=409,
            detail="Cannot retry a battle that already has a vote",
        )

    # Reset all persisted run artifacts so the next execution starts clean.
    # This includes request_json and prompt_rendered which the orchestrator
    # will re-populate on the retry attempt — leaving stale copies from the
    # previous failed attempt would confuse frontend displays and exports.
    runs = (
        db.execute(
            select(Run).where(Run.battle_id == battle.id).order_by(Run.side.asc())
        )
        .scalars()
        .all()
    )
    for run in runs:
        run.output_text = None
        run.output_text_raw = None
        run.error_text = None
        run.stats = None
        run.request_json = None
        run.prompt_rendered = None

    metadata_json = (
        battle.metadata_json if isinstance(battle.metadata_json, dict) else {}
    )
    battle.metadata_json = {
        **metadata_json,
        "automatic_retry_count": 0,
    }

    battle.status = "pending"  # type: ignore[assignment]
    db.add(battle)
    db.commit()
    db.refresh(battle)

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

    run_map = {run.side: run for run in runs}
    return _to_battle_public(
        battle=battle,
        source_text=source_text,
        source_lang=source_lang,
        target_lang=target_lang,
        principal=principal,
        run_a=run_map.get("A"),
        run_b=run_map.get("B"),
    )


def _select_task(*, db: Session, payload: BattleCreate) -> Task:
    if payload.task_id is not None:
        task = db.get(Task, parse_uuid_or_422(payload.task_id, "task_id"))
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return task

    # Single-query weighted random selection: tasks with fewer battles are
    # sampled more often.  `random() / (1 + battle_count)` gives higher
    # scores to under-represented tasks, achieving the same inverse-frequency
    # weighting as the old two-query approach without loading IDs into memory.
    battle_count = (
        select(Battle.task_id, func.count(Battle.id).label("cnt"))
        .group_by(Battle.task_id)
        .subquery()
    )

    stmt = select(Task).outerjoin(battle_count, Task.id == battle_count.c.task_id)
    if payload.task_set_id is not None:
        task_set_uuid = parse_uuid_or_422(payload.task_set_id, "task_set_id")
        stmt = stmt.where(Task.task_set_id == task_set_uuid)

    stmt = stmt.order_by(
        func.random() / (1 + func.coalesce(battle_count.c.cnt, 0))
    ).limit(1)

    task = db.execute(stmt).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=400, detail="No tasks available for battle")
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

    policy = _build_sampling_policy(settings)

    try:
        return select_battle_pair(candidates=candidates, policy=policy)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="No valid model pair available for sampling",
        ) from exc


def _build_sampling_policy(settings: Settings) -> SamplingPolicy:
    """Build a SamplingPolicy from env-backed settings."""
    return SamplingPolicy(
        weights=settings.battle_sampling_weights,
        targets=settings.battle_targets,
        strict_targets=settings.battle_strict_targets,
        outage_models=set(settings.battle_outage_models),
        boost_models=set(settings.battle_sampling_boost_models),
    )


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
    principal: Principal,
    run_a: Run | None,
    run_b: Run | None,
    has_vote: bool = False,
    include_stats: bool = False,
) -> BattlePublic:
    return BattlePublic(
        id=str(battle.id),
        task_id=str(battle.task_id),
        source_text=source_text,
        source_lang=source_lang,
        target_lang=target_lang,
        mode=battle.mode,
        status=battle.status,
        retry_allowed=_retry_allowed_for_battle(
            battle=battle,
            principal=principal,
            has_vote=has_vote,
        ),
        run_a=_to_run_public(run_a, include_stats=include_stats),
        run_b=_to_run_public(run_b, include_stats=include_stats),
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


def _is_battle_creator(
    battle: Battle,
    *,
    principal: Principal,
) -> bool:
    """Check whether the requester is the user who created this battle.

    Matches on ``requester_user_id`` stored at creation time.
    """
    metadata = battle.metadata_json
    if not isinstance(metadata, dict):
        return False

    # Prefer authenticated identity.
    creator_user_id = metadata.get("requester_user_id")
    if (
        creator_user_id is not None
        and principal.is_authenticated
        and principal.user_id == creator_user_id
    ):
        return True

    return False


def _is_admin_principal(principal: Principal) -> bool:
    if not principal.is_authenticated:
        return False

    settings = get_settings()
    claim_value = claim_by_path(principal.claims, settings.oidc_admin_group_claim)
    groups = normalize_groups(claim_value)
    return settings.oidc_admin_group_name in groups


def _retry_allowed_for_battle(
    *,
    battle: Battle,
    principal: Principal,
    has_vote: bool,
) -> bool:
    if battle.status != "failed" or has_vote:
        return False

    if _is_battle_creator(battle, principal=principal):
        return True

    return _is_admin_principal(principal)


@lru_cache(maxsize=1)
def _get_auth_battle_create_rate_limiter() -> RollingWindowRateLimiter:
    settings = get_settings()
    return RollingWindowRateLimiter(
        limit=settings.auth_battle_create_rate_limit,
        window_seconds=settings.auth_battle_create_rate_limit_window_seconds,
        bucket_seconds=settings.anon_rate_limit_bucket_seconds,
        redis_client=get_rate_limit_redis_client(),
        redis_prefix=settings.rate_limit_redis_key_prefix,
    )


def _enforce_auth_battle_rate_limit(
    *,
    principal: Principal,
    settings: Settings,
) -> None:
    if not principal.user_id:
        return
    limiter = _get_auth_battle_create_rate_limiter()
    key = build_auth_rate_limit_key(
        scope="auth_battle_create",
        user_id=principal.user_id,
    )
    if limiter.is_limited(key):
        raise HTTPException(
            status_code=429,
            detail="Too many battle creation requests",
            headers={
                "Retry-After": str(
                    settings.auth_battle_create_rate_limit_window_seconds
                )
            },
        )


@lru_cache(maxsize=1)
def _get_auth_battle_stream_rate_limiter() -> RollingWindowRateLimiter:
    settings = get_settings()
    return RollingWindowRateLimiter(
        limit=settings.auth_battle_stream_rate_limit,
        window_seconds=settings.auth_battle_stream_rate_limit_window_seconds,
        bucket_seconds=settings.anon_rate_limit_bucket_seconds,
        redis_client=get_rate_limit_redis_client(),
        redis_prefix=settings.rate_limit_redis_key_prefix,
    )


def _enforce_auth_battle_stream_rate_limit(
    *,
    principal: Principal,
    settings: Settings,
) -> None:
    if not principal.user_id:
        return
    limiter = _get_auth_battle_stream_rate_limiter()
    key = build_auth_rate_limit_key(
        scope="auth_battle_stream",
        user_id=principal.user_id,
    )
    if limiter.is_limited(key):
        raise HTTPException(
            status_code=429,
            detail="Too many battle stream requests",
            headers={
                "Retry-After": str(
                    settings.auth_battle_stream_rate_limit_window_seconds
                )
            },
        )


@lru_cache(maxsize=1)
def _get_turnstile_http_client() -> httpx.Client:
    """Shared httpx client for Turnstile verification (connection pooling)."""
    return httpx.Client(timeout=5.0)


def _verify_turnstile_or_raise(
    *,
    turnstile_token: str | None,
    request: Request,
    settings: Settings,
) -> None:
    if not settings.turnstile_secret_key:
        return

    if not turnstile_token:
        raise HTTPException(status_code=400, detail="Missing Turnstile token")

    remote_ip = get_client_ip(
        request,
        trust_x_forwarded_for=settings.trust_x_forwarded_for,
    )
    form_data: dict[str, str] = {
        "secret": settings.turnstile_secret_key,
        "response": turnstile_token,
    }
    if remote_ip:
        form_data["remoteip"] = remote_ip

    client = _get_turnstile_http_client()
    try:
        res = client.post(settings.turnstile_verify_url, data=form_data)
        res.raise_for_status()
        body = res.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail="Turnstile verification failed"
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="Turnstile verification response was invalid",
        ) from exc

    if not isinstance(body, dict) or body.get("success") is not True:
        raise HTTPException(status_code=400, detail="Invalid Turnstile token")


def _enforce_daily_vote_cap(
    *,
    db: Session,
    principal: Principal,
    settings: Settings,
) -> None:
    """Block battle creation when the daily vote cap is reached.

    Battle creation is authenticated-only, so the cap applies per user id.
    """
    cap = settings.leaderboard_refresh_daily_vote_cap
    if cap <= 0:
        return

    if not principal.is_authenticated or principal.user_id is None:
        return

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    voter_user_id = uuid.UUID(principal.user_id)

    count = db.execute(
        select(func.count(Vote.id)).where(
            Vote.voter_user_id == voter_user_id,
            Vote.created_at >= today_start,
        )
    ).scalar_one()

    if count >= cap:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Daily vote limit reached ({cap} votes per day). "
                "New battles cannot be created until tomorrow."
            ),
        )
