"""app.api.routes.leaderboard

Leaderboard and rating endpoints.

Notes
~~~~~
- MVP rating can be Elo with tie support.
- Provide filters (task set, time window, zh variant) later.

Rate limiting
~~~~~~~~~~~~~
Confidence-enabled leaderboard requests (``?include_confidence=true``) are
CPU-expensive because they run bootstrap resampling.  To prevent abuse:

1. **Short-lived shared cache** — When Redis is configured, computed
   results are cached in Redis for
   ``leaderboard_confidence_cache_ttl_seconds`` (default 15 s). Repeated
   identical requests within the TTL window are served from the shared
   cache without recomputation. When Redis is absent or unavailable,
   requests fall back to direct recomputation.

2. **Redis-backed rate limiter** — Uncached recomputations are throttled
   globally via ``RollingWindowRateLimiter`` (Redis-backed).  This enforces
   the limit consistently across all API workers, unlike a process-local
   counter.  The limiter uses a per-method key
   (``"leaderboard_confidence:elo"`` / ``"leaderboard_confidence:bt"``)
   so the two methods have independent budgets.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Protocol, cast
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query  # pyright: ignore[reportMissingImports]
from pydantic import ValidationError
from sqlalchemy import func, select  # pyright: ignore[reportMissingImports]
from sqlalchemy.orm import Session  # pyright: ignore[reportMissingImports]

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models.model_registry import Model
from app.models.rating import ModelRating
from app.schemas.leaderboard import (
    LeaderboardResponse,
    LeaderboardRow,
    VoteSourceCounts,
)
from app.services.leaderboard_bt import (
    PairwiseVote,
    compute_bt_confidence_intervals,
    compute_bt_ratings,
)
from app.services.leaderboard_refresh import (
    VoteSample,
    compute_elo_confidence_intervals,
    compute_elo_ratings,
    count_vote_sources,
    filter_outlier_judge_votes,
    load_vote_samples,
    normalize_judge_type,
)
from app.utils.rate_limit import RollingWindowRateLimiter
from app.utils import redis as redis_utils

router = APIRouter(tags=["leaderboard"])


class _ConfidenceCacheClient(Protocol):
    def get(self, key: str) -> str | bytes | None: ...

    def set(self, key: str, value: str, *, ex: int) -> object: ...


# ---------------------------------------------------------------------------
# Confidence leaderboard cache
# ---------------------------------------------------------------------------
_CONFIDENCE_CACHE_NAMESPACE = "leaderboard:confidence"


def _confidence_cache_ttl_seconds(settings: Settings) -> int:
    """Return the TTL for cached confidence leaderboard responses.

    Controlled by ``LEADERBOARD_CONFIDENCE_CACHE_TTL_SECONDS``.  A value
    of ``0`` disables caching entirely (every request triggers a full
    recomputation).
    """

    return max(int(settings.leaderboard_confidence_cache_ttl_seconds), 0)


def _confidence_cache_key(
    *,
    method: str,
    settings: Settings,
    judge_type: str = "all",
    service_account_id: uuid.UUID | None = None,
) -> str:
    """Build a cache key that incorporates all parameters affecting the result.

    When any setting changes (e.g. ``elo_k``, ``bootstrap_rounds``), the
    cache key changes and the old cached result is naturally evicted (or
    becomes a dead entry that is cleaned up on the next eviction pass).
    """

    outlier_fragment = _confidence_cache_outlier_fragment(settings)
    source_fragment = _confidence_cache_source_fragment(
        judge_type=judge_type,
        service_account_id=service_account_id,
    )

    if method == "elo":
        return (
            "elo"
            f":{source_fragment}"
            f":{settings.leaderboard_refresh_daily_vote_cap}"
            f":{settings.leaderboard_refresh_elo_k}"
            f":{settings.leaderboard_elo_shuffle_rounds}"
            f":{settings.leaderboard_elo_shuffle_seed}"
            f":{settings.leaderboard_elo_bootstrap_rounds}"
            f":{settings.leaderboard_elo_bootstrap_seed}"
            f":{settings.leaderboard_elo_confidence_level}"
            f":{outlier_fragment}"
        )

    return (
        "bt"
        f":{source_fragment}"
        f":{settings.leaderboard_refresh_daily_vote_cap}"
        f":{settings.leaderboard_bt_max_iterations}"
        f":{settings.leaderboard_bt_tolerance}"
        f":{settings.leaderboard_bt_prior}"
        f":{settings.leaderboard_bt_bootstrap_rounds}"
        f":{settings.leaderboard_bt_bootstrap_seed}"
        f":{settings.leaderboard_bt_confidence_level}"
        f":{outlier_fragment}"
    )


def _confidence_cache_source_fragment(
    *, judge_type: str, service_account_id: uuid.UUID | None
) -> str:
    return f"judge:{judge_type}:service_account:{service_account_id or 'all'}"


def _confidence_cache_outlier_fragment(settings: Settings) -> str:
    return (
        f"outlier:{int(settings.leaderboard_outlier_filter_enabled)}"
        f":{settings.leaderboard_outlier_min_votes}"
        f":{settings.leaderboard_outlier_max_votes}"
        f":{settings.leaderboard_outlier_alpha}"
    )


def _load_cached_confidence_leaderboard(
    *, cache_key: str, settings: Settings
) -> LeaderboardResponse | None:
    """Return a cached response from Redis when shared caching is available."""

    ttl_seconds = _confidence_cache_ttl_seconds(settings)
    if ttl_seconds <= 0:
        return None

    redis_client = _get_confidence_cache_client()
    if redis_client is None:
        return None
    redis_client = cast(_ConfidenceCacheClient, redis_client)

    try:
        payload = redis_client.get(
            _confidence_cache_redis_key(cache_key=cache_key, settings=settings)
        )
    except redis_utils.RedisError:
        return None

    if payload is None:
        return None

    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        response = LeaderboardResponse.model_validate_json(payload)
        _finalize_leaderboard_rows(response.models, include_confidence=response.ci)
        return response
    except (UnicodeDecodeError, ValidationError, ValueError):
        return None


def _store_cached_confidence_leaderboard(
    *,
    cache_key: str,
    response: LeaderboardResponse,
    settings: Settings,
) -> None:
    """Store a confidence leaderboard response in Redis when available."""

    ttl_seconds = _confidence_cache_ttl_seconds(settings)
    if ttl_seconds <= 0:
        return

    redis_client = _get_confidence_cache_client()
    if redis_client is None:
        return
    redis_client = cast(_ConfidenceCacheClient, redis_client)

    try:
        redis_client.set(
            _confidence_cache_redis_key(cache_key=cache_key, settings=settings),
            response.model_dump_json(),
            ex=ttl_seconds,
        )
    except redis_utils.RedisError:
        return


def _get_confidence_cache_client() -> object | None:
    return redis_utils.get_confidence_cache_redis_client()


def _confidence_cache_redis_key(*, cache_key: str, settings: Settings) -> str:
    prefix = settings.rate_limit_redis_key_prefix.strip().strip(":") or "arena"
    return f"{prefix}:{_CONFIDENCE_CACHE_NAMESPACE}:{cache_key}"


def _finalize_leaderboard_rows(
    rows: list[LeaderboardRow],
    *,
    include_confidence: bool,
) -> None:
    _assign_ordinal_ranks(rows)
    for row in rows:
        row.confidence_rank = None
    if include_confidence:
        _assign_confidence_ranks(rows)


def _assign_ordinal_ranks(rows: list[LeaderboardRow]) -> None:
    for index, row in enumerate(rows, start=1):
        row.rank = index


def _assign_confidence_ranks(rows: list[LeaderboardRow]) -> None:
    for row in rows:
        if row.rating_lower is None or row.rating_upper is None:
            row.confidence_rank = None
            continue

        row.confidence_rank = 1 + sum(
            1
            for other_row in rows
            if other_row is not row
            and other_row.rating_lower is not None
            and other_row.rating_upper is not None
            and other_row.rating_lower > row.rating_upper
        )


def _filter_vote_samples_for_leaderboard(
    vote_samples: list[VoteSample],
    *,
    settings: Settings,
) -> list[VoteSample]:
    if not settings.leaderboard_outlier_filter_enabled:
        return vote_samples
    return filter_outlier_judge_votes(
        vote_samples,
        min_votes=settings.leaderboard_outlier_min_votes,
        max_votes=settings.leaderboard_outlier_max_votes,
        alpha=settings.leaderboard_outlier_alpha,
    )


def _parse_judge_type_or_422(judge_type: str) -> str:
    try:
        return normalize_judge_type(judge_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _parse_service_account_id_or_422(value: str | None) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid service_account_id") from exc


def _load_vote_samples_and_counts(
    *,
    db: Session,
    settings: Settings,
    judge_type: str,
    service_account_id: uuid.UUID | None,
) -> tuple[list[VoteSample], VoteSourceCounts]:
    if judge_type != "all" or service_account_id is not None:
        vote_samples = load_vote_samples(
            db,
            daily_vote_cap=settings.leaderboard_refresh_daily_vote_cap,
            judge_type=judge_type,
            service_account_id=service_account_id,
        )
    else:
        vote_samples = load_vote_samples(
            db,
            daily_vote_cap=settings.leaderboard_refresh_daily_vote_cap,
        )
    vote_samples = _filter_vote_samples_for_leaderboard(
        vote_samples,
        settings=settings,
    )
    return vote_samples, VoteSourceCounts(**count_vote_sources(vote_samples))


# ---------------------------------------------------------------------------
# Confidence leaderboard rate limiter (Redis-backed)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _get_confidence_rate_limiter() -> RollingWindowRateLimiter:
    """Return a singleton Redis-backed rate limiter for confidence requests.

    Uses the same Redis instance and key prefix as the battle/vote rate
    limiters, ensuring consistent multi-worker enforcement.

    Controlled by:
    - ``LEADERBOARD_CONFIDENCE_RATE_LIMIT`` (default 6)
    - ``LEADERBOARD_CONFIDENCE_RATE_LIMIT_WINDOW_SECONDS`` (default 60)
    """

    settings = get_settings()
    return RollingWindowRateLimiter(
        limit=settings.leaderboard_confidence_rate_limit,
        window_seconds=settings.leaderboard_confidence_rate_limit_window_seconds,
        bucket_seconds=max(
            settings.leaderboard_confidence_rate_limit_window_seconds // 6, 1
        ),
        redis_client=redis_utils.get_rate_limit_redis_client(),
        redis_prefix=settings.rate_limit_redis_key_prefix,
    )


def _enforce_confidence_request_rate_limit(
    *,
    method: str,
    settings: Settings,
    judge_type: str = "all",
    service_account_id: uuid.UUID | None = None,
) -> None:
    """Raise HTTP 429 if the global confidence recomputation rate is exceeded.

    This is a **global** (not per-user) rate limit because the expensive
    resource being protected is server CPU, not a per-user quota.  The
    Redis-backed limiter ensures the limit is shared across all API workers.

    The rate limit key is per-method to prevent one method from starving
    the other.
    """

    if settings.leaderboard_confidence_rate_limit <= 0:
        return

    limiter = _get_confidence_rate_limiter()
    # Use a global key for CPU protection — intentionally not per-user.
    key = (
        f"leaderboard_confidence_global:{method}:"
        f"judge:{judge_type}:service_account:{service_account_id or 'all'}"
    )
    if limiter.is_limited(key):
        raise HTTPException(
            status_code=429,
            detail="Too many confidence leaderboard requests",
            headers={
                "Retry-After": str(
                    settings.leaderboard_confidence_rate_limit_window_seconds
                )
            },
        )


# ---------------------------------------------------------------------------
# Public endpoint
# ---------------------------------------------------------------------------


@router.get("/leaderboard")
def get_leaderboard(
    method: Annotated[str, Query(pattern="^(elo|bt)$")] = "elo",
    include_confidence: Annotated[bool, Query()] = False,
    judge_type: Annotated[str, Query(pattern="^(all|human|bot)$")] = "all",
    service_account_id: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> LeaderboardResponse:
    """Return the model leaderboard.

    Parameters
    ----------
    method:
        Rating method — ``"elo"`` (default) or ``"bt"``.
    include_confidence:
        When ``True``, compute bootstrap confidence intervals.  This is
        CPU-expensive and is therefore cached and rate-limited.
    """

    if service_account_id is not None:
        raise HTTPException(
            status_code=403,
            detail="service_account_id filter is admin-only",
        )
    return build_leaderboard_response(
        method=method,
        include_confidence=include_confidence,
        judge_type=judge_type,
        service_account_id=None,
        db=db,
        settings=settings,
    )


def build_leaderboard_response(
    *,
    method: str,
    include_confidence: bool,
    judge_type: str,
    service_account_id: uuid.UUID | None,
    db: Session,
    settings: Settings,
) -> LeaderboardResponse:
    judge_type = _parse_judge_type_or_422(judge_type)
    if method not in {"elo", "bt"}:
        raise HTTPException(status_code=422, detail="method must be one of: elo, bt")

    cache_key: str | None = None
    if include_confidence:
        cache_key = _confidence_cache_key(
            method=method,
            settings=settings,
            judge_type=judge_type,
            service_account_id=service_account_id,
        )
        cached = _load_cached_confidence_leaderboard(
            cache_key=cache_key,
            settings=settings,
        )
        if cached is not None:
            return cached

        _enforce_confidence_request_rate_limit(
            method=method,
            settings=settings,
            judge_type=judge_type,
            service_account_id=service_account_id,
        )

    if method == "bt":
        response = _get_leaderboard_bt(
            db=db,
            include_confidence=include_confidence,
            settings=settings,
            judge_type=judge_type,
            service_account_id=service_account_id,
        )
    else:
        response = _get_leaderboard_elo(
            db=db,
            include_confidence=include_confidence,
            settings=settings,
            judge_type=judge_type,
            service_account_id=service_account_id,
        )

    if include_confidence and cache_key is not None:
        _store_cached_confidence_leaderboard(
            cache_key=cache_key,
            response=response,
            settings=settings,
        )

    return response


# ---------------------------------------------------------------------------
# Elo leaderboard
# ---------------------------------------------------------------------------


def _get_leaderboard_elo(
    *,
    db: Session,
    include_confidence: bool,
    settings: Settings,
    judge_type: str = "all",
    service_account_id: uuid.UUID | None = None,
) -> LeaderboardResponse:
    # Use an outer join so public models with no votes still appear.
    model_rows = db.execute(
        select(Model.id, Model.display_name)
        .where(Model.visibility == "public", Model.enabled.is_(True))
        .order_by(Model.created_at.asc())
    ).all()

    if not model_rows:
        return LeaderboardResponse(models=[], method="elo", ci=False)

    public_model_ids = [row[0] for row in model_rows]
    public_model_names = {row[0]: row[1] for row in model_rows}
    vote_samples, vote_source_counts = _load_vote_samples_and_counts(
        db=db,
        settings=settings,
        judge_type=judge_type,
        service_account_id=service_account_id,
    )

    if not include_confidence and judge_type == "all" and service_account_id is None:
        ratings = db.execute(
            select(
                Model.id,
                Model.display_name,
                func.coalesce(ModelRating.rating, 1000.0),
                func.coalesce(ModelRating.games_played, 0),
            )
            .outerjoin(ModelRating, ModelRating.model_id == Model.id)
            .where(Model.visibility == "public", Model.enabled.is_(True))
            .order_by(
                func.coalesce(ModelRating.rating, 1000.0).desc(),
                Model.created_at.asc(),
                Model.id.asc(),
            )
        ).all()

        rows = [
            LeaderboardRow(
                model_id=str(model_id),
                display_name=display_name,
                rating=float(rating),
                games_played=int(games_played),
            )
            for model_id, display_name, rating, games_played in ratings
        ]
        _finalize_leaderboard_rows(rows, include_confidence=False)
        return LeaderboardResponse(
            models=rows,
            method="elo",
            ci=False,
            vote_source_counts=vote_source_counts,
        )

    baseline = compute_elo_ratings(
        vote_samples,
        k=settings.leaderboard_refresh_elo_k,
        shuffle_rounds=settings.leaderboard_elo_shuffle_rounds,
        shuffle_seed=settings.leaderboard_elo_shuffle_seed,
    )

    intervals: dict[uuid.UUID, tuple[float, float]] = {}
    if include_confidence:
        intervals = compute_elo_confidence_intervals(
            vote_samples,
            model_ids=public_model_ids,
            bootstrap_rounds=settings.leaderboard_elo_bootstrap_rounds,
            seed=settings.leaderboard_elo_bootstrap_seed,
            k=settings.leaderboard_refresh_elo_k,
            confidence_level=settings.leaderboard_elo_confidence_level,
            shuffle_rounds=settings.leaderboard_elo_shuffle_rounds,
            shuffle_seed=settings.leaderboard_elo_shuffle_seed,
        )

    rows: list[LeaderboardRow] = []
    for model_id in public_model_ids:
        rating_value, games_played = baseline.get(model_id, (1000.0, 0))
        interval = intervals.get(model_id)
        rows.append(
            LeaderboardRow(
                model_id=str(model_id),
                display_name=public_model_names[model_id],
                rating=rating_value,
                rating_lower=interval[0] if interval else None,
                rating_upper=interval[1] if interval else None,
                games_played=games_played,
            )
        )

    rows.sort(key=lambda row: row.rating, reverse=True)
    _finalize_leaderboard_rows(rows, include_confidence=include_confidence)
    return LeaderboardResponse(
        models=rows,
        method="elo",
        ci=include_confidence,
        bootstrap_rounds=(
            settings.leaderboard_elo_bootstrap_rounds if include_confidence else None
        ),
        vote_source_counts=vote_source_counts,
    )


# ---------------------------------------------------------------------------
# Bradley-Terry leaderboard
# ---------------------------------------------------------------------------


def _get_leaderboard_bt(
    *,
    db: Session,
    include_confidence: bool,
    settings: Settings,
    judge_type: str = "all",
    service_account_id: uuid.UUID | None = None,
) -> LeaderboardResponse:
    model_rows = db.execute(
        select(Model.id, Model.display_name)
        .where(Model.visibility == "public", Model.enabled.is_(True))
        .order_by(Model.created_at.asc())
    ).all()

    if not model_rows:
        return LeaderboardResponse(models=[], method="bt", ci=False)

    public_model_ids = [row[0] for row in model_rows]
    public_models = [(row[0], row[1]) for row in model_rows]

    vote_samples, vote_source_counts = _load_vote_samples_and_counts(
        db=db,
        settings=settings,
        judge_type=judge_type,
        service_account_id=service_account_id,
    )

    votes = [
        PairwiseVote(
            model_a_id=s.model_a_id,
            model_b_id=s.model_b_id,
            winner=s.winner,
        )
        for s in vote_samples
    ]

    ratings = compute_bt_ratings(
        model_ids=public_model_ids,
        votes=votes,
        max_iterations=settings.leaderboard_bt_max_iterations,
        tolerance=settings.leaderboard_bt_tolerance,
        prior=settings.leaderboard_bt_prior,
    )

    confidence: dict[uuid.UUID, tuple[float, float]] = {}
    if include_confidence:
        intervals = compute_bt_confidence_intervals(
            model_ids=public_model_ids,
            votes=votes,
            bootstrap_rounds=settings.leaderboard_bt_bootstrap_rounds,
            seed=settings.leaderboard_bt_bootstrap_seed,
            max_iterations=settings.leaderboard_bt_max_iterations,
            tolerance=settings.leaderboard_bt_tolerance,
            prior=settings.leaderboard_bt_prior,
            confidence_level=settings.leaderboard_bt_confidence_level,
        )
        confidence = dict(intervals)

    rows: list[LeaderboardRow] = []
    for model_id, display_name in public_models:
        interval = confidence.get(model_id)
        row = LeaderboardRow(
            model_id=str(model_id),
            display_name=display_name,
            rating=ratings.get(model_id, (1000.0, 0))[0],
            rating_lower=interval[0] if interval else None,
            rating_upper=interval[1] if interval else None,
            games_played=ratings.get(model_id, (1000.0, 0))[1],
        )
        rows.append(row)
    rows.sort(key=lambda row: row.rating, reverse=True)
    _finalize_leaderboard_rows(rows, include_confidence=include_confidence)

    return LeaderboardResponse(
        models=rows,
        method="bt",
        ci=include_confidence,
        bootstrap_rounds=(
            settings.leaderboard_bt_bootstrap_rounds if include_confidence else None
        ),
        vote_source_counts=vote_source_counts,
    )
