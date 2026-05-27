"""app.api.routes.votes

Vote ingestion endpoints.

Notes:
- Vote submission is authenticated-only.
- Authenticated voters are identified by user id only.
- Vote submission immediately reveals model identities and locks the vote.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal, cast
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.routes.battles import (
    _is_admin_principal,
    _require_battle_access,
)
from app.core.csrf import require_csrf_for_session
from app.core.config import Settings, get_settings
from app.core.security import (
    Principal,
    get_principal_required,
    is_bot_principal,
    require_scopes,
)
from app.db.session import get_db
from app.models.battle import Battle, Run
from app.models.model_registry import Model
from app.models.service_account import ServiceAccount
from app.models.vote import Vote
from app.schemas.votes import VoteCreate, VoteSubmitResponse
from app.utils.id import parse_uuid_or_422
from app.utils.requester_identity import (
    RequesterIdentity,
    find_any_battle_vote,
)
from app.utils.rate_limit import (
    RollingWindowRateLimiter,
    build_auth_rate_limit_key,
)
from app.utils.redis import get_rate_limit_redis_client

router = APIRouter(prefix="/battles", tags=["votes"])
require_bot_vote_create_scope = require_scopes(["vote:create"])


def _validated_bot_vote_identity(principal: Principal) -> tuple[uuid.UUID, uuid.UUID]:
    return (
        _required_bot_principal_uuid(principal.service_account_id),
        _required_bot_principal_uuid(principal.token_id),
    )


def _required_bot_principal_uuid(value: str | None) -> uuid.UUID:
    if not value:
        raise HTTPException(
            status_code=403,
            detail="Bot principal service account context required",
        )
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=403,
            detail="Bot principal service account context required",
        ) from exc


@router.post(
    "/{battle_id}/vote",
    status_code=201,
    dependencies=[Depends(require_csrf_for_session)],
)
def submit_vote(
    battle_id: str,
    payload: VoteCreate,
    response: Response,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal_required),
    settings: Settings = Depends(get_settings),
) -> VoteSubmitResponse:
    is_bot_vote = is_bot_principal(principal)
    service_account_id: uuid.UUID | None = None
    service_account_token_id: uuid.UUID | None = None
    if is_bot_vote:
        require_bot_vote_create_scope(principal)
        service_account_id, service_account_token_id = _validated_bot_vote_identity(
            principal
        )
    elif payload.bot_metadata is not None:
        raise HTTPException(
            status_code=403,
            detail="Human principals cannot submit bot_metadata",
        )

    battle_uuid = parse_uuid_or_422(battle_id, "battle_id")

    battle = db.get(Battle, battle_uuid)
    if battle is None:
        raise HTTPException(status_code=404, detail="Battle not found")

    access_has_vote = _require_battle_access(
        battle=battle,
        principal=principal,
        db=db,
        allow_pool=not is_bot_vote,
        settings=settings,
        forbidden_detail="Only the battle creator or an admin may vote on this battle",
    )

    if battle.status != "completed":
        raise HTTPException(status_code=409, detail="Battle is not ready for voting")

    from app.services.battle_prepopulation import backend_gated_pooled_replay_blocks_vote

    if backend_gated_pooled_replay_blocks_vote(
        battle,
        principal=principal,
        has_vote=access_has_vote,
        settings=settings,
    ):
        raise HTTPException(
            status_code=409,
            detail="Battle replay is still streaming; wait for replay to complete before voting",
        )

    runs = (
        db.execute(
            select(Run).where(Run.battle_id == battle_uuid).order_by(Run.side.asc())
        )
        .scalars()
        .all()
    )
    run_map = {run.side: run for run in runs}
    run_a = run_map.get("A")
    run_b = run_map.get("B")
    if run_a is None or run_b is None:
        raise HTTPException(status_code=400, detail="Battle runs not ready")

    # Ensure both runs have actual output (not just error text).
    if not run_a.output_text or not run_b.output_text:
        raise HTTPException(
            status_code=409,
            detail="One or both translation runs failed; voting is not allowed",
        )

    winner = payload.winner
    rubric = payload.rubric.model_dump() if payload.rubric else None

    if winner in ("A", "B"):
        chosen_run = run_map.get(winner)
        if chosen_run is None or not chosen_run.output_text:
            raise HTTPException(
                status_code=422,
                detail=f"Side {winner} has no rendered output",
            )

    voter_user_id = uuid.UUID(principal.user_id)
    bot_metadata = payload.bot_metadata if is_bot_vote else None

    requester_identity = RequesterIdentity(voter_user_id=voter_user_id)

    if find_any_battle_vote(db, battle_id=battle_uuid) is not None:
        raise HTTPException(status_code=409, detail="Battle already has a vote")

    _enforce_auth_vote_rate_limit(
        voter_user_id=voter_user_id,
        service_account_id=service_account_id,
        service_account_token_id=service_account_token_id,
        principal=principal,
        settings=settings,
    )

    vote = Vote(
        battle_id=battle_uuid,
        winner=winner,
        # Store a plain JSON object in JSONB (not a Pydantic model instance).
        rubric=rubric,
        comment=payload.comment,
        voter_user_id=voter_user_id,
        service_account_id=service_account_id,
        service_account_token_id=service_account_token_id,
        bot_metadata=bot_metadata,
        revealed=True,
    )
    db.add(vote)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return _resolve_duplicate_vote_conflict(
            db,
            response=response,
            battle_id=battle_uuid,
            winner=winner,
            rubric=rubric,
            comment=payload.comment,
            requester_identity=requester_identity,
            model_a_id=run_a.model_id,
            model_b_id=run_b.model_id,
            service_account_id=service_account_id,
            service_account_token_id=service_account_token_id,
            bot_metadata=bot_metadata,
            principal=principal,
        )

    # Ratings are updated exclusively by the background leaderboard refresh
    # job — no inline Elo mutation here.  This avoids the inconsistency
    # window where inline updates are later overwritten by a full
    # recomputation.
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _resolve_duplicate_vote_conflict(
            db,
            response=response,
            battle_id=battle_uuid,
            winner=winner,
            rubric=rubric,
            comment=payload.comment,
            requester_identity=requester_identity,
            model_a_id=run_a.model_id,
            model_b_id=run_b.model_id,
            service_account_id=service_account_id,
            service_account_token_id=service_account_token_id,
            bot_metadata=bot_metadata,
            principal=principal,
        )

    return _build_vote_submit_response(
        db,
        vote_id=vote.id,
        battle_id=battle_uuid,
        winner=winner,
        model_a_id=run_a.model_id,
        model_b_id=run_b.model_id,
        vote=vote,
        principal=principal,
    )


def _resolve_duplicate_vote_conflict(
    db: Session,
    *,
    response: Response,
    battle_id: uuid.UUID,
    winner: str,
    rubric: dict[str, Any] | None = None,
    comment: str | None = None,
    requester_identity: RequesterIdentity,
    model_a_id: uuid.UUID,
    model_b_id: uuid.UUID,
    service_account_id: uuid.UUID | None = None,
    service_account_token_id: uuid.UUID | None = None,
    bot_metadata: dict[str, Any] | None = None,
    principal: Principal | None = None,
) -> VoteSubmitResponse:
    existing_vote = find_any_battle_vote(db, battle_id=battle_id)
    if existing_vote is None:
        raise HTTPException(status_code=500, detail="Failed to persist vote")

    raise HTTPException(status_code=409, detail="Battle already has a vote")


def _build_vote_submit_response(
    db: Session,
    *,
    vote_id: uuid.UUID,
    battle_id: uuid.UUID,
    winner: str,
    model_a_id: uuid.UUID,
    model_b_id: uuid.UUID,
    vote: object | None = None,
    principal: Principal | None = None,
) -> VoteSubmitResponse:
    model_a = db.get(Model, model_a_id)
    model_b = db.get(Model, model_b_id)
    if model_a is None or model_b is None:
        raise HTTPException(status_code=500, detail="Model not found")

    voter_actor_type = _vote_actor_type(vote)
    service_account_uuid = _uuid_attr(vote, "service_account_id")
    service_account_token_uuid = _uuid_attr(vote, "service_account_token_id")

    service_account_name = None
    if service_account_uuid is not None:
        service_account_name = _service_account_name_for_response(
            db,
            service_account_id=service_account_uuid,
            principal=principal,
        )

    return VoteSubmitResponse(
        vote_id=str(vote_id),
        battle_id=str(battle_id),
        winner=cast(Literal["A", "B", "tie"], winner),
        reveal={
            "A": {"model_id": str(model_a.id), "display_name": model_a.display_name},
            "B": {"model_id": str(model_b.id), "display_name": model_b.display_name},
        },
        voter_actor_type=voter_actor_type,
        service_account_id=(
            str(service_account_uuid) if service_account_uuid is not None else None
        ),
        service_account_name=service_account_name,
        service_account_token_id=(
            str(service_account_token_uuid)
            if service_account_token_uuid is not None
            else None
        ),
        bot_metadata=(
            getattr(vote, "bot_metadata", None)
            if voter_actor_type == "bot" and _can_echo_bot_metadata(principal)
            else None
        ),
    )


def _vote_actor_type(vote: object | None) -> Literal["human", "bot"]:
    if _uuid_attr(vote, "service_account_id") is not None:
        return "bot"
    return "human"


def _uuid_attr(vote: object | None, attr_name: str) -> uuid.UUID | None:
    if vote is None:
        return None
    value = getattr(vote, attr_name, None)
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _service_account_name_for_response(
    db: Session,
    *,
    service_account_id: uuid.UUID,
    principal: Principal | None,
) -> str | None:
    if principal is not None and principal.service_account_id == str(service_account_id):
        return principal.service_account_name

    service_account = db.get(ServiceAccount, service_account_id)
    if service_account is None:
        return None
    return service_account.name


def _can_echo_bot_metadata(principal: Principal | None) -> bool:
    if principal is None:
        return False
    if is_bot_principal(principal):
        return True
    return _is_admin_principal(principal)


@lru_cache(maxsize=1)
def _get_auth_vote_submit_rate_limiter() -> RollingWindowRateLimiter:
    settings = get_settings()
    return RollingWindowRateLimiter(
        limit=settings.auth_vote_submit_rate_limit,
        window_seconds=settings.auth_vote_submit_rate_limit_window_seconds,
        bucket_seconds=settings.anon_rate_limit_bucket_seconds,
        redis_client=get_rate_limit_redis_client(),
        redis_prefix=settings.rate_limit_redis_key_prefix,
    )


def _enforce_auth_vote_rate_limit(
    *,
    voter_user_id: uuid.UUID,
    settings: Settings,
    principal: Principal | None = None,
    service_account_id: uuid.UUID | str | None = None,
    service_account_token_id: uuid.UUID | str | None = None,
) -> None:
    service_account_key = str(service_account_id) if service_account_id else None
    token_key = str(service_account_token_id) if service_account_token_id else None
    user_key = str(voter_user_id)

    if principal is not None and is_bot_principal(principal):
        service_account_key = service_account_key or principal.service_account_id
        token_key = token_key or principal.token_id
        user_key = ""
        if not service_account_key and not token_key:
            raise HTTPException(
                status_code=403,
                detail="Bot principal service account context required",
            )

    limiter = _get_auth_vote_submit_rate_limiter()
    key = build_auth_rate_limit_key(
        scope="auth_vote_submit",
        user_id=user_key or None,
        service_account_id=service_account_key,
        token_id=token_key,
    )
    if limiter.is_limited(key):
        raise HTTPException(
            status_code=429,
            detail="Too many vote submissions",
            headers={
                "Retry-After": str(settings.auth_vote_submit_rate_limit_window_seconds)
            },
        )
