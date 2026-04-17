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

from app.core.config import Settings, get_settings
from app.core.security import Principal, get_principal_required
from app.db.session import get_db
from app.models.battle import Battle, Run
from app.models.model_registry import Model
from app.models.vote import Vote
from app.schemas.votes import VoteCreate, VoteSubmitResponse
from app.utils.id import parse_uuid_or_422
from app.utils.requester_identity import RequesterIdentity, find_existing_battle_vote
from app.utils.rate_limit import (
    RollingWindowRateLimiter,
    build_auth_rate_limit_key,
)
from app.utils.redis import get_rate_limit_redis_client

router = APIRouter(prefix="/battles", tags=["votes"])


@router.post("/{battle_id}/vote", status_code=201)
def submit_vote(
    battle_id: str,
    payload: VoteCreate,
    response: Response,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal_required),
    settings: Settings = Depends(get_settings),
) -> VoteSubmitResponse:
    battle_uuid = parse_uuid_or_422(battle_id, "battle_id")

    battle = db.get(Battle, battle_uuid)
    if battle is None:
        raise HTTPException(status_code=404, detail="Battle not found")
    if battle.status != "completed":
        raise HTTPException(status_code=409, detail="Battle is not ready for voting")

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

    if winner in ("A", "B"):
        chosen_run = run_map.get(winner)
        if chosen_run is None or not chosen_run.output_text:
            raise HTTPException(
                status_code=422,
                detail=f"Side {winner} has no rendered output",
            )

    voter_user_id = uuid.UUID(principal.user_id)
    requester_identity = RequesterIdentity(voter_user_id=voter_user_id)

    existing_vote = find_existing_battle_vote(
        db,
        battle_id=battle_uuid,
        requester_identity=requester_identity,
    )
    if existing_vote is not None:
        response.status_code = 200

        if existing_vote.revealed:
            # Vote has been revealed — no more changes allowed.
            if existing_vote.winner != winner:
                raise HTTPException(
                    status_code=409,
                    detail="Vote already revealed and cannot be changed",
                )
            return VoteSubmitResponse(
                vote_id=str(existing_vote.id),
                battle_id=str(battle_uuid),
                winner=existing_vote.winner,
                reveal=_build_vote_submit_response(
                    db,
                    vote_id=existing_vote.id,
                    battle_id=battle_uuid,
                    winner=existing_vote.winner,
                    model_a_id=run_a.model_id,
                    model_b_id=run_b.model_id,
                ).reveal,
            )

        # Compatibility-safe path: unrevealed historical votes are updated,
        # then revealed immediately to match the current contract.
        if (
            existing_vote.winner != winner
            or existing_vote.rubric
            != (payload.rubric.model_dump() if payload.rubric else None)
            or existing_vote.comment != payload.comment
        ):
            existing_vote.winner = winner
            existing_vote.rubric = (
                payload.rubric.model_dump() if payload.rubric else None
            )
            existing_vote.comment = payload.comment
        existing_vote.revealed = True
        db.commit()

        return _build_vote_submit_response(
            db,
            vote_id=existing_vote.id,
            battle_id=battle_uuid,
            winner=existing_vote.winner,
            model_a_id=run_a.model_id,
            model_b_id=run_b.model_id,
        )

    _enforce_auth_vote_rate_limit(
        voter_user_id=voter_user_id,
        settings=settings,
    )

    vote = Vote(
        battle_id=battle_uuid,
        winner=winner,
        # Store a plain JSON object in JSONB (not a Pydantic model instance).
        rubric=(payload.rubric.model_dump() if payload.rubric else None),
        comment=payload.comment,
        voter_user_id=voter_user_id,
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
            rubric=(payload.rubric.model_dump() if payload.rubric else None),
            comment=payload.comment,
            requester_identity=requester_identity,
            model_a_id=run_a.model_id,
            model_b_id=run_b.model_id,
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
            rubric=(payload.rubric.model_dump() if payload.rubric else None),
            comment=payload.comment,
            requester_identity=requester_identity,
            model_a_id=run_a.model_id,
            model_b_id=run_b.model_id,
        )

    return _build_vote_submit_response(
        db,
        vote_id=vote.id,
        battle_id=battle_uuid,
        winner=winner,
        model_a_id=run_a.model_id,
        model_b_id=run_b.model_id,
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
) -> VoteSubmitResponse:
    existing_vote = find_existing_battle_vote(
        db,
        battle_id=battle_id,
        requester_identity=requester_identity,
    )
    if existing_vote is None:
        raise HTTPException(status_code=500, detail="Failed to persist vote")

    response.status_code = 200

    if existing_vote.revealed and existing_vote.winner != winner:
        raise HTTPException(
            status_code=409,
            detail="Vote already revealed and cannot be changed",
        )

    # Persist the latest payload for unrevealed conflicts so that a
    # duplicate-key race does not silently discard the caller's data.
    if not existing_vote.revealed:
        if existing_vote.winner != winner:
            existing_vote.winner = winner
        if existing_vote.rubric != rubric:
            existing_vote.rubric = rubric
        if existing_vote.comment != comment:
            existing_vote.comment = comment
        existing_vote.revealed = True

    # Commit explicitly so that identity upgrades and payload updates are
    # durably persisted regardless of whether the caller's session teardown
    # auto-commit fires (e.g. if the handler raises after this point or the
    # get_db() auto-commit semantics change).
    db.commit()

    return _build_vote_submit_response(
        db,
        vote_id=existing_vote.id,
        battle_id=battle_id,
        winner=existing_vote.winner,
        model_a_id=model_a_id,
        model_b_id=model_b_id,
    )


def _build_vote_submit_response(
    db: Session,
    *,
    vote_id: uuid.UUID,
    battle_id: uuid.UUID,
    winner: str,
    model_a_id: uuid.UUID,
    model_b_id: uuid.UUID,
) -> VoteSubmitResponse:
    model_a = db.get(Model, model_a_id)
    model_b = db.get(Model, model_b_id)
    if model_a is None or model_b is None:
        raise HTTPException(status_code=500, detail="Model not found")

    return VoteSubmitResponse(
        vote_id=str(vote_id),
        battle_id=str(battle_id),
        winner=cast(Literal["A", "B", "tie"], winner),
        reveal={
            "A": {"model_id": str(model_a.id), "display_name": model_a.display_name},
            "B": {"model_id": str(model_b.id), "display_name": model_b.display_name},
        },
    )


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
) -> None:
    limiter = _get_auth_vote_submit_rate_limiter()
    key = build_auth_rate_limit_key(
        scope="auth_vote_submit",
        user_id=str(voter_user_id),
    )
    if limiter.is_limited(key):
        raise HTTPException(
            status_code=429,
            detail="Too many vote submissions",
            headers={
                "Retry-After": str(settings.auth_vote_submit_rate_limit_window_seconds)
            },
        )
