"""app.api.routes.votes

Vote ingestion endpoints.

Notes:
- Anonymous voting is allowed; store an anonymous session id + hashed IP/UA.
- Logged-in votes should additionally store `user_id` for higher-trust filtering
  in offline processing.
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import Settings, get_settings
from app.core.security import Principal, get_principal_optional
from app.db.session import get_db
from app.models.battle import Battle, Run
from app.models.model_registry import Model
from app.models.rating import ModelRating, RatingEvent
from app.models.vote import Vote
from app.schemas.votes import VoteCreate, VoteSubmitResponse
from app.services.ratings import elo_update
from app.utils.anon import get_or_set_anon_id
from app.utils.id import parse_uuid
from app.utils.rate_limit import RollingWindowRateLimiter, build_anon_rate_limit_key
from app.utils.redis import get_rate_limit_redis_client

router = APIRouter(prefix="/battles", tags=["votes"])


@router.post("/{battle_id}/vote", status_code=201)
def submit_vote(
    battle_id: str,
    payload: VoteCreate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal_optional),
    settings: Settings = Depends(get_settings),
) -> VoteSubmitResponse:
    battle_uuid = parse_uuid(battle_id, "battle_id")

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

    voter_user_id: uuid.UUID | None = None
    if principal.is_authenticated and principal.user_id is not None:
        voter_user_id = uuid.UUID(principal.user_id)

    # Always bind a stable anonymous session id so identity-mode switches
    # (anonymous <-> logged-in) cannot create duplicate votes for a battle.
    voter_anon_id = get_or_set_anon_id(
        request=request,
        response=response,
        secure=settings.anon_id_cookie_secure,
    )

    ip_hash = _hash_ip(
        request,
        settings.anon_ip_hash_salt,
        trust_x_forwarded_for=settings.trust_x_forwarded_for,
    )
    ua_hash = _hash_user_agent(request, settings.anon_user_agent_hash_salt)

    existing_vote = _find_existing_vote_for_identity(
        db,
        battle_id=battle_uuid,
        voter_user_id=voter_user_id,
        voter_anon_id=voter_anon_id,
        ip_hash=ip_hash,
        user_agent_hash=ua_hash,
    )
    if existing_vote is not None:
        if existing_vote.winner != winner:
            raise HTTPException(
                status_code=409,
                detail="Vote already submitted for this battle",
            )

        return _build_vote_submit_response(
            db,
            vote_id=existing_vote.id,
            battle_id=battle_uuid,
            winner=existing_vote.winner,
            model_a_id=run_a.model_id,
            model_b_id=run_b.model_id,
        )

    # By design: authenticated users are trusted and not rate-limited or
    # required to pass Turnstile verification.
    if voter_user_id is None:
        _enforce_anon_vote_rate_limit(
            request=request,
            settings=settings,
        )
        _verify_turnstile_or_raise(payload=payload, request=request, settings=settings)

    vote = Vote(
        battle_id=battle_uuid,
        winner=winner,
        rubric=payload.rubric,
        comment=payload.comment,
        voter_user_id=voter_user_id,
        voter_anon_id=voter_anon_id,
        ip_hash=ip_hash,
        user_agent_hash=ua_hash,
    )
    db.add(vote)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return _resolve_duplicate_vote_conflict(
            db,
            battle_id=battle_uuid,
            winner=winner,
            voter_user_id=voter_user_id,
            voter_anon_id=voter_anon_id,
            ip_hash=ip_hash,
            user_agent_hash=ua_hash,
            model_a_id=run_a.model_id,
            model_b_id=run_b.model_id,
        )

    model_a_id = run_a.model_id
    model_b_id = run_b.model_id

    try:
        rating_a, rating_b = _lock_ratings_for_vote(
            db,
            model_a_id=model_a_id,
            model_b_id=model_b_id,
        )
    except RuntimeError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500, detail="Failed to load model ratings"
        ) from exc

    # Same-model battles: both sides use the same model, so rating_a and
    # rating_b are the *same* ORM object.  Mutating both would double-count
    # games_played and apply both deltas to a single row.  Record the vote
    # for auditing but skip rating mutations entirely.
    is_same_model = model_a_id == model_b_id

    if is_same_model:
        delta_a = 0.0
        delta_b = 0.0
    else:
        elo_k = max(float(settings.leaderboard_refresh_elo_k), 1.0)
        delta_a, delta_b = elo_update(
            rating_a=rating_a.rating,
            rating_b=rating_b.rating,
            outcome=winner,
            k=elo_k,
        )

        rating_a.rating += delta_a
        rating_b.rating += delta_b
        rating_a.games_played += 1
        rating_b.games_played += 1

    event = RatingEvent(
        vote_id=vote.id,
        model_a_id=model_a_id,
        model_b_id=model_b_id,
        delta_a=delta_a,
        delta_b=delta_b,
    )
    db.add(event)
    if not is_same_model:
        db.add(rating_a)
        db.add(rating_b)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _resolve_duplicate_vote_conflict(
            db,
            battle_id=battle_uuid,
            winner=winner,
            voter_user_id=voter_user_id,
            voter_anon_id=voter_anon_id,
            ip_hash=ip_hash,
            user_agent_hash=ua_hash,
            model_a_id=model_a_id,
            model_b_id=model_b_id,
        )

    return _build_vote_submit_response(
        db,
        vote_id=vote.id,
        battle_id=battle_uuid,
        winner=winner,
        model_a_id=model_a_id,
        model_b_id=model_b_id,
    )


def _get_or_create_rating(db: Session, model_id: uuid.UUID) -> ModelRating:
    rating = db.get(ModelRating, model_id)
    if rating is not None:
        return rating

    try:
        # Savepoint guards concurrent first-insert races without rolling back
        # the surrounding vote transaction.
        with db.begin_nested():
            rating = ModelRating(model_id=model_id)
            db.add(rating)
            db.flush()
        return rating
    except IntegrityError:
        rating = db.get(ModelRating, model_id)
        if rating is None:
            raise
        return rating


def _get_or_create_rating_for_update(db: Session, model_id: uuid.UUID) -> ModelRating:
    stmt = select(ModelRating).where(ModelRating.model_id == model_id).with_for_update()
    rating = db.execute(stmt).scalar_one_or_none()
    if rating is not None:
        return rating

    try:
        # Savepoint guards concurrent first-insert races without rolling back
        # the surrounding vote transaction.
        with db.begin_nested():
            rating = ModelRating(model_id=model_id)
            db.add(rating)
            db.flush()
    except IntegrityError:
        # A concurrent transaction inserted this rating row first.
        pass

    rating = db.execute(stmt).scalar_one_or_none()
    if rating is None:
        raise RuntimeError(f"Model rating row is missing for model_id={model_id}")
    return rating


def _lock_ratings_for_vote(
    db: Session,
    *,
    model_a_id: uuid.UUID,
    model_b_id: uuid.UUID,
) -> tuple[ModelRating, ModelRating]:
    # Lock in deterministic id order to avoid deadlocks if concurrent votes
    # touch the same pair in opposite A/B orientation.
    # Use a list (not a set) to preserve both IDs even when equal.
    ordered_ids = sorted([model_a_id, model_b_id], key=str)
    locked: dict[uuid.UUID, ModelRating] = {}
    for model_id in ordered_ids:
        if model_id not in locked:
            locked[model_id] = _get_or_create_rating_for_update(db, model_id)

    rating_a = locked.get(model_a_id)
    rating_b = locked.get(model_b_id)
    if rating_a is None or rating_b is None:
        raise RuntimeError("Failed to lock rating rows")

    # Same-model battles: rating_a IS rating_b (same ORM instance).
    # The *caller* is responsible for skipping rating mutations;
    # we still return both references so the vote is persisted.
    return rating_a, rating_b


def _find_existing_vote_for_identity(
    db: Session,
    *,
    battle_id: uuid.UUID,
    voter_user_id: uuid.UUID | None,
    voter_anon_id: str | None,
    ip_hash: str | None,
    user_agent_hash: str | None,
) -> Vote | None:
    stmt = select(Vote).where(Vote.battle_id == battle_id)
    anon_fingerprint_predicate = _anon_fingerprint_predicate(
        ip_hash=ip_hash,
        user_agent_hash=user_agent_hash,
    )

    condition: ColumnElement[bool] | None = None
    if voter_user_id is not None:
        condition = Vote.voter_user_id == voter_user_id
        if voter_anon_id:
            condition = or_(condition, Vote.voter_anon_id == voter_anon_id)
        # Also check fingerprint to catch votes cast anonymously before login.
        if anon_fingerprint_predicate is not None:
            condition = or_(condition, anon_fingerprint_predicate)
        stmt = stmt.where(condition)
    else:
        if voter_anon_id:
            condition = Vote.voter_anon_id == voter_anon_id
        if anon_fingerprint_predicate is not None:
            if condition is None:
                condition = anon_fingerprint_predicate
            else:
                condition = or_(condition, anon_fingerprint_predicate)
        if condition is None:
            return None
        stmt = stmt.where(condition)

    return (
        db.execute(stmt.order_by(Vote.created_at.asc(), Vote.id.asc()))
        .scalars()
        .first()
    )


def _anon_fingerprint_predicate(
    *,
    ip_hash: str | None,
    user_agent_hash: str | None,
) -> ColumnElement[bool] | None:
    if not ip_hash or not user_agent_hash:
        return None

    return and_(
        Vote.ip_hash == ip_hash,
        Vote.user_agent_hash == user_agent_hash,
    )


def _resolve_duplicate_vote_conflict(
    db: Session,
    *,
    battle_id: uuid.UUID,
    winner: str,
    voter_user_id: uuid.UUID | None,
    voter_anon_id: str | None,
    ip_hash: str | None,
    user_agent_hash: str | None,
    model_a_id: uuid.UUID,
    model_b_id: uuid.UUID,
) -> VoteSubmitResponse:
    existing_vote = _find_existing_vote_for_identity(
        db,
        battle_id=battle_id,
        voter_user_id=voter_user_id,
        voter_anon_id=voter_anon_id,
        ip_hash=ip_hash,
        user_agent_hash=user_agent_hash,
    )
    if existing_vote is None:
        raise HTTPException(status_code=500, detail="Failed to persist vote")

    if existing_vote.winner != winner:
        raise HTTPException(
            status_code=409,
            detail="Vote already submitted for this battle",
        )

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
        winner=winner,
        reveal={
            "A": {"model_id": str(model_a.id), "display_name": model_a.display_name},
            "B": {"model_id": str(model_b.id), "display_name": model_b.display_name},
        },
    )


def _hash_ip(
    request: Request, salt: str, *, trust_x_forwarded_for: bool = False
) -> str | None:
    ip: str | None = None
    if trust_x_forwarded_for:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            ip = forwarded_for.split(",")[0].strip()
    if not ip:
        ip = request.client.host if request.client is not None else None
    if not ip:
        return None
    return hashlib.sha256(f"{salt}|{ip}".encode("utf-8")).hexdigest()


def _hash_user_agent(request: Request, salt: str) -> str | None:
    ua = request.headers.get("user-agent")
    if not ua:
        return None
    return hashlib.sha256(f"{salt}|{ua}".encode("utf-8")).hexdigest()


def _verify_turnstile_or_raise(
    *,
    payload: VoteCreate,
    request: Request,
    settings: Settings,
) -> None:
    if not settings.turnstile_secret_key:
        return

    if not payload.turnstile_token:
        raise HTTPException(status_code=400, detail="Missing Turnstile token")

    remote_ip: str | None = None
    if settings.trust_x_forwarded_for:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            remote_ip = forwarded_for.split(",")[0].strip()
    if not remote_ip:
        remote_ip = request.client.host if request.client is not None else None
    form_data: dict[str, str] = {
        "secret": settings.turnstile_secret_key,
        "response": payload.turnstile_token,
    }
    if remote_ip:
        form_data["remoteip"] = remote_ip

    try:
        res = httpx.post(settings.turnstile_verify_url, data=form_data, timeout=5.0)
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


@lru_cache(maxsize=1)
def _get_vote_submit_rate_limiter() -> RollingWindowRateLimiter:
    settings = get_settings()
    return RollingWindowRateLimiter(
        limit=settings.anon_vote_submit_rate_limit,
        window_seconds=settings.anon_vote_submit_rate_limit_window_seconds,
        bucket_seconds=settings.anon_rate_limit_bucket_seconds,
        redis_client=get_rate_limit_redis_client(),
        redis_prefix=settings.rate_limit_redis_key_prefix,
    )


def _enforce_anon_vote_rate_limit(
    *,
    request: Request,
    settings: Settings,
) -> None:
    limiter = _get_vote_submit_rate_limiter()
    key = build_anon_rate_limit_key(
        scope="anon_vote_submit",
        request=request,
        trust_x_forwarded_for=settings.trust_x_forwarded_for,
        ip_hash_salt=settings.anon_ip_hash_salt,
    )
    if limiter.is_limited(key):
        raise HTTPException(
            status_code=429,
            detail="Too many anonymous vote submissions",
            headers={
                "Retry-After": str(settings.anon_vote_submit_rate_limit_window_seconds)
            },
        )
