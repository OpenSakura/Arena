"""app.services.leaderboard_refresh

Periodic leaderboard refresh service.

Notes:
- Refreshes persisted Elo snapshots in ``model_ratings`` from vote history.
- Applies optional per-judge daily vote caps (FastChat-inspired anti-abuse signal).
- Can optionally filter outlier judges before rating computation.
"""

# NOTE: This module creates DB sessions via get_sessionmaker() directly
# (not via the FastAPI get_db() dependency) because it runs as a background
# asyncio task outside the request/response lifecycle. Each refresh cycle
# opens and closes its own session to avoid long-lived transactions.

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import logging
import math
import random
import threading
from typing import Any, TypeAlias, cast
import uuid

from sqlalchemy import and_, func, or_, select, text  # pyright: ignore[reportMissingImports]
from sqlalchemy.orm import Session, aliased  # pyright: ignore[reportMissingImports]

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.models.battle import Battle, Run
from app.models.model_registry import Model
from app.models.rating import ModelRating
from app.models.user import User
from app.models.vote import Vote
from app.services.ratings import elo_update
from app.utils.stats import percentile as _percentile

logger = logging.getLogger(__name__)

_EloEvent = tuple[uuid.UUID, uuid.UUID, str]
_ModelPair: TypeAlias = tuple[uuid.UUID, uuid.UUID]
VALID_JUDGE_TYPES = frozenset({"all", "human", "bot"})


@dataclass(slots=True)
class VoteSample:
    vote_id: uuid.UUID
    created_at: datetime
    winner: str
    model_a_id: uuid.UUID
    model_b_id: uuid.UUID
    judge_key: str
    voter_actor_type: str = "human"
    service_account_id: uuid.UUID | None = None


@dataclass(slots=True)
class _OutlierPairStats:
    wins: int = 0
    losses: int = 0


@dataclass(slots=True)
class RefreshStatus:
    enabled: bool
    interval_seconds: int
    daily_vote_cap: int
    last_attempted_at: str | None
    last_succeeded_at: str | None
    last_error: str | None
    total_refreshes: int


class LeaderboardRefresher:
    """Background job that periodically rebuilds persisted Elo ratings."""

    def __init__(
        self,
        *,
        enabled: bool,
        interval_seconds: int,
        daily_vote_cap: int,
        elo_k: float,
        elo_shuffle_rounds: int = 1,
        elo_shuffle_seed: int = 0,
        outlier_filter_enabled: bool = False,
        outlier_filter_min_votes: int = 5,
        outlier_filter_max_votes: int = 100,
        outlier_filter_alpha: float = 0.05,
    ) -> None:
        self._enabled = enabled
        self._interval_seconds = max(interval_seconds, 5)
        self._daily_vote_cap = max(daily_vote_cap, 0)
        self._elo_k = max(float(elo_k), 1.0)
        self._elo_shuffle_rounds = max(int(elo_shuffle_rounds), 0)
        self._elo_shuffle_seed = int(elo_shuffle_seed)
        self._outlier_filter_enabled = bool(outlier_filter_enabled)
        self._outlier_filter_min_votes = int(outlier_filter_min_votes)
        self._outlier_filter_max_votes = int(outlier_filter_max_votes)
        self._outlier_filter_alpha = float(outlier_filter_alpha)

        self._status_lock = threading.Lock()
        self._last_attempted_at: datetime | None = None
        self._last_succeeded_at: datetime | None = None
        self._last_error: str | None = None
        self._total_refreshes = 0

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        if not self._enabled:
            logger.info("Leaderboard refresher disabled")
            return

        logger.info(
            "Leaderboard refresher started (interval=%ss, daily_vote_cap=%s)",
            self._interval_seconds,
            self._daily_vote_cap,
        )

        while not stop_event.is_set():
            await asyncio.to_thread(self.refresh_once)

            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=float(self._interval_seconds)
                )
            except TimeoutError:
                continue

        logger.info("Leaderboard refresher stopped")

    def refresh_once(self) -> None:
        started_at = datetime.now(timezone.utc)
        with self._status_lock:
            self._last_attempted_at = started_at
            self._last_error = None

        lock_key = _advisory_lock_key("arena_leaderboard_refresh")
        SessionLocal = get_sessionmaker()
        db = SessionLocal()

        try:
            # Acquire a transaction-scoped advisory lock on the session's
            # underlying connection so the lock is released automatically when
            # the refresh transaction commits or rolls back.
            conn = db.connection()
            locked = bool(
                conn.execute(
                    text("SELECT pg_try_advisory_xact_lock(:key)"),
                    {"key": lock_key},
                ).scalar_one()
            )
            if not locked:
                logger.debug("Leaderboard refresh skipped (lock busy)")
                return

            try:
                model_count, vote_count = self._refresh_locked(db)

                with self._status_lock:
                    self._last_succeeded_at = datetime.now(timezone.utc)
                    self._total_refreshes += 1

                logger.info(
                    "Leaderboard refreshed: models=%s votes=%s",
                    model_count,
                    vote_count,
                )
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.exception("Leaderboard refresh failed")
                with self._status_lock:
                    self._last_error = str(exc)
        finally:
            db.close()

    def get_status(self) -> RefreshStatus:
        with self._status_lock:
            return RefreshStatus(
                enabled=self._enabled,
                interval_seconds=self._interval_seconds,
                daily_vote_cap=self._daily_vote_cap,
                last_attempted_at=_to_iso(self._last_attempted_at),
                last_succeeded_at=_to_iso(self._last_succeeded_at),
                last_error=self._last_error,
                total_refreshes=self._total_refreshes,
            )

    def _refresh_locked(self, db: Session) -> tuple[int, int]:
        # Load vote data and compute ratings *before* acquiring the exclusive
        # table lock, so concurrent vote submissions are not blocked during
        # the O(n) read and CPU-bound Elo computation.
        model_ids = self._load_all_model_ids(db)
        vote_samples = self._load_vote_samples(db)

        ratings = compute_elo_ratings(
            vote_samples,
            k=self._elo_k,
            shuffle_rounds=self._elo_shuffle_rounds,
            shuffle_seed=self._elo_shuffle_seed,
        )

        # Now serialize against live vote writes only for the persist phase.
        self._lock_model_ratings_for_refresh(db)
        # Re-fetch model IDs under lock to avoid FK violations if a model
        # was deleted between the first read and the persist phase.
        model_ids = self._load_all_model_ids(db)
        self._persist_ratings(db, model_ids=model_ids, ratings=ratings)
        return len(model_ids), len(vote_samples)

    @staticmethod
    def _lock_model_ratings_for_refresh(db: Session) -> None:
        # SHARE ROW EXCLUSIVE blocks concurrent INSERTs/UPDATEs/DELETEs
        # (including live vote writes) while allowing plain SELECTs.
        db.execute(text("LOCK TABLE model_ratings IN SHARE ROW EXCLUSIVE MODE"))

    @staticmethod
    def _load_all_model_ids(db: Session) -> list[uuid.UUID]:
        return list(db.execute(select(Model.id)).scalars().all())

    def _load_vote_samples(self, db: Session) -> list[VoteSample]:
        vote_samples = load_vote_samples(db, daily_vote_cap=self._daily_vote_cap)
        if not self._outlier_filter_enabled:
            return vote_samples
        return filter_outlier_judge_votes(
            vote_samples,
            min_votes=self._outlier_filter_min_votes,
            max_votes=self._outlier_filter_max_votes,
            alpha=self._outlier_filter_alpha,
        )

    @staticmethod
    def _persist_ratings(
        db: Session,
        *,
        model_ids: list[uuid.UUID],
        ratings: dict[uuid.UUID, tuple[float, int]],
    ) -> None:
        # Keep lock/update order deterministic to avoid deadlocks with
        # concurrent vote submissions that lock rating rows in sorted order.
        ordered_model_ids = sorted(model_ids, key=str)

        existing: dict[uuid.UUID, ModelRating] = {}
        for model_id in ordered_model_ids:
            row = db.execute(
                select(ModelRating)
                .where(ModelRating.model_id == model_id)
                .with_for_update()
            ).scalar_one_or_none()
            if row is not None:
                existing[model_id] = row

        model_id_set = set(ordered_model_ids)

        for model_id in ordered_model_ids:
            rating_value, games_played = ratings.get(model_id, (1000.0, 0))

            row = existing.get(model_id)
            if row is None:
                db.add(
                    ModelRating(
                        model_id=model_id,
                        rating=rating_value,
                        games_played=games_played,
                    )
                )
                continue

            row.rating = rating_value
            row.games_played = games_played
            db.add(row)

        if model_id_set:
            stale_stmt = (
                select(ModelRating)
                .where(~ModelRating.model_id.in_(model_id_set))
                .order_by(ModelRating.model_id.asc())
                .with_for_update()
            )
            for row in db.execute(stale_stmt).scalars().all():
                db.delete(row)

        db.commit()


def limit_votes_per_judge_per_day(
    vote_samples: list[VoteSample],
    *,
    daily_vote_cap: int,
) -> list[VoteSample]:
    if daily_vote_cap <= 0:
        return vote_samples

    usage: dict[tuple[str, str], int] = {}
    kept: list[VoteSample] = []

    for vote in vote_samples:
        day_key = vote.created_at.date().isoformat()
        key = (vote.judge_key, day_key)
        current = usage.get(key, 0)
        if current >= daily_vote_cap:
            continue
        usage[key] = current + 1
        kept.append(vote)

    return kept


def normalize_judge_type(judge_type: str) -> str:
    if judge_type not in VALID_JUDGE_TYPES:
        raise ValueError("judge_type must be one of: all, human, bot")
    return judge_type


def vote_source_for_sample(vote_sample: object) -> str:
    actor_type = getattr(vote_sample, "voter_actor_type", "human")
    service_account_id = getattr(vote_sample, "service_account_id", None)
    if service_account_id is not None or actor_type == "bot":
        return "bot"
    return "human"


def count_vote_sources(vote_samples: list[object]) -> dict[str, int]:
    counts = {"human": 0, "bot": 0, "total": 0}
    for vote_sample in vote_samples:
        source = vote_source_for_sample(vote_sample)
        counts[source] += 1
        counts["total"] += 1
    return counts


def filter_outlier_judge_votes(
    vote_samples: list[VoteSample],
    *,
    min_votes: int = 5,
    max_votes: int = 100,
    alpha: float = 0.05,
) -> list[VoteSample]:
    """Remove judges whose votes are extreme against pair aggregates.

    The detector adapts FastChat's sequential evidence idea without pandas or
    numpy: for each sufficiently active judge, compare their vote on each model
    pair with the aggregate win/loss distribution for that pair. Judges whose
    cumulative upper- or lower-tail evidence crosses ``1 / alpha`` are removed.
    """

    if not vote_samples:
        return vote_samples

    min_votes = max(int(min_votes), 1)
    max_votes = max(int(max_votes), 1)
    alpha = _normalize_outlier_alpha(alpha)

    votes_by_judge: dict[str, list[VoteSample]] = {}
    for vote_sample in vote_samples:
        votes_by_judge.setdefault(vote_sample.judge_key, []).append(vote_sample)

    candidate_judges = {
        judge_key
        for judge_key, judge_votes in votes_by_judge.items()
        if len(judge_votes) >= min_votes
    }
    if not candidate_judges:
        return vote_samples

    pair_stats = _build_outlier_pair_stats(vote_samples)
    outlier_judges = {
        judge_key
        for judge_key in candidate_judges
        if _judge_is_outlier(
            votes_by_judge[judge_key],
            pair_stats=pair_stats,
            max_votes=max_votes,
            alpha=alpha,
        )
    }

    if not outlier_judges:
        return vote_samples
    return [
        vote_sample
        for vote_sample in vote_samples
        if vote_sample.judge_key not in outlier_judges
    ]


def _build_outlier_pair_stats(
    vote_samples: list[VoteSample],
) -> dict[_ModelPair, _OutlierPairStats]:
    pair_stats: dict[_ModelPair, _OutlierPairStats] = {}
    for vote_sample in vote_samples:
        model_pair = _canonical_model_pair(vote_sample)
        if model_pair is None:
            continue
        vote_score = _canonical_pair_vote_score(vote_sample, model_pair=model_pair)
        if vote_score is None:
            continue

        stats = pair_stats.setdefault(model_pair, _OutlierPairStats())
        if vote_score == 1.0:
            stats.wins += 1
        elif vote_score == 0.0:
            stats.losses += 1

    return pair_stats


def _judge_is_outlier(
    judge_votes: list[VoteSample],
    *,
    pair_stats: dict[_ModelPair, _OutlierPairStats],
    max_votes: int,
    alpha: float,
) -> bool:
    log_upper_evidence = 0.0
    log_lower_evidence = 0.0
    log_threshold = -math.log(alpha)
    inspected_votes = 0

    for vote_sample in judge_votes:
        if inspected_votes >= max_votes:
            break

        model_pair = _canonical_model_pair(vote_sample)
        if model_pair is None:
            continue
        stats = pair_stats.get(model_pair)
        if stats is None:
            continue
        probabilities = _outlier_tail_probabilities(
            vote_sample,
            model_pair=model_pair,
            stats=stats,
        )
        if probabilities is None:
            continue

        p_upper, p_lower = probabilities
        log_upper_evidence += _outlier_log_evidence_factor(p_upper)
        log_lower_evidence += _outlier_log_evidence_factor(p_lower)
        inspected_votes += 1

        if log_upper_evidence > log_threshold or log_lower_evidence > log_threshold:
            return True

    return False


def _outlier_tail_probabilities(
    vote_sample: VoteSample,
    *,
    model_pair: _ModelPair,
    stats: _OutlierPairStats,
) -> tuple[float, float] | None:
    win_loss_total = stats.wins + stats.losses
    if win_loss_total <= 0:
        return None

    vote_score = _canonical_pair_vote_score(vote_sample, model_pair=model_pair)
    if vote_score is None:
        return None

    win_rate = stats.wins / win_loss_total
    loss_rate = stats.losses / win_loss_total

    if vote_score == 1.0:
        return 1.0, win_rate
    if vote_score == 0.0:
        return loss_rate, 1.0
    return loss_rate, win_rate


def _canonical_model_pair(vote_sample: VoteSample) -> _ModelPair | None:
    if vote_sample.model_a_id == vote_sample.model_b_id:
        return None
    model_ids = sorted((vote_sample.model_a_id, vote_sample.model_b_id), key=str)
    return model_ids[0], model_ids[1]


def _canonical_pair_vote_score(
    vote_sample: VoteSample,
    *,
    model_pair: _ModelPair,
) -> float | None:
    if vote_sample.winner == "tie":
        return 0.5
    if vote_sample.winner == "A":
        winning_model_id = vote_sample.model_a_id
    elif vote_sample.winner == "B":
        winning_model_id = vote_sample.model_b_id
    else:
        return None
    return 1.0 if winning_model_id == model_pair[0] else 0.0


def _outlier_log_evidence_factor(probability: float) -> float:
    if probability <= 0.0:
        return float("inf")
    return -math.log(2.0 * probability)


def _normalize_outlier_alpha(alpha: float) -> float:
    if math.isnan(alpha) or math.isinf(alpha):
        return 0.05
    return min(max(float(alpha), 1e-12), 1.0)


def load_vote_samples(
    db: Session,
    *,
    daily_vote_cap: int = 0,
    judge_type: str = "all",
    service_account_id: uuid.UUID | None = None,
) -> list[VoteSample]:
    """Load pairwise vote samples in battle execution order."""

    judge_type = normalize_judge_type(judge_type)

    if daily_vote_cap <= 0:
        rows = db.execute(
            _vote_sample_stmt(
                judge_type=judge_type,
                service_account_id=service_account_id,
            )
        ).all()
        return _rows_to_vote_samples(rows)

    bounds = db.execute(
        _vote_sample_bounds_stmt(
            judge_type=judge_type,
            service_account_id=service_account_id,
        )
    ).one()
    first_created_at, last_created_at = bounds
    if first_created_at is None or last_created_at is None:
        return []

    day_start, _ = _utc_day_bounds(_ensure_utc(first_created_at))
    _, final_day_end = _utc_day_bounds(_ensure_utc(last_created_at))

    samples: list[VoteSample] = []
    current_day_start = day_start
    while current_day_start < final_day_end:
        current_day_end = current_day_start + timedelta(days=1)
        day_rows = db.execute(
            _vote_sample_stmt(
                day_start=current_day_start,
                day_end=current_day_end,
                judge_type=judge_type,
                service_account_id=service_account_id,
            )
        ).all()
        day_samples = _rows_to_vote_samples(day_rows)
        samples.extend(
            _limit_vote_samples_for_single_day(
                day_samples,
                daily_vote_cap=daily_vote_cap,
            )
        )
        current_day_start = current_day_end

    return samples


def _vote_sample_stmt(
    *,
    day_start: datetime | None = None,
    day_end: datetime | None = None,
    judge_type: str = "all",
    service_account_id: uuid.UUID | None = None,
):
    judge_type = normalize_judge_type(judge_type)
    run_a = aliased(Run)
    run_b = aliased(Run)

    stmt = (
        select(
            Vote.id,
            Vote.created_at,
            Vote.winner,
            Vote.voter_user_id,
            User.actor_type,
            Vote.service_account_id,
            run_a.model_id,
            run_b.model_id,
        )
        .join(User, User.id == Vote.voter_user_id)
        .join(Battle, Battle.id == Vote.battle_id)
        .join(
            run_a,
            and_(run_a.battle_id == Vote.battle_id, run_a.side == "A"),
        )
        .join(
            run_b,
            and_(run_b.battle_id == Vote.battle_id, run_b.side == "B"),
        )
        .where(
            *_vote_sample_filter_conditions(
                judge_type=judge_type,
                service_account_id=service_account_id,
            )
        )
    )

    if day_start is not None and day_end is not None:
        stmt = stmt.where(
            Vote.created_at >= day_start,
            Vote.created_at < day_end,
        )

    return stmt.order_by(Vote.created_at.asc(), Vote.id.asc())


def _vote_sample_bounds_stmt(
    *,
    judge_type: str = "all",
    service_account_id: uuid.UUID | None = None,
):
    judge_type = normalize_judge_type(judge_type)
    run_a = aliased(Run)
    run_b = aliased(Run)

    return (
        select(func.min(Vote.created_at), func.max(Vote.created_at))
        .join(User, User.id == Vote.voter_user_id)
        .join(Battle, Battle.id == Vote.battle_id)
        .join(
            run_a,
            and_(run_a.battle_id == Vote.battle_id, run_a.side == "A"),
        )
        .join(
            run_b,
            and_(run_b.battle_id == Vote.battle_id, run_b.side == "B"),
        )
        .where(
            *_vote_sample_filter_conditions(
                judge_type=judge_type,
                service_account_id=service_account_id,
            )
        )
    )


def _vote_sample_filter_conditions(
    *,
    judge_type: str,
    service_account_id: uuid.UUID | None,
) -> list[Any]:
    conditions: list[Any] = [Vote.revealed.is_(True), Battle.status == "completed"]
    if judge_type == "human":
        conditions.append(
            and_(Vote.service_account_id.is_(None), User.actor_type == "human")
        )
    elif judge_type == "bot":
        conditions.append(
            or_(Vote.service_account_id.is_not(None), User.actor_type == "bot")
        )
    if service_account_id is not None:
        conditions.append(Vote.service_account_id == service_account_id)
    return conditions


_VoteSampleRow: TypeAlias = tuple[
    uuid.UUID,
    datetime,
    str,
    uuid.UUID | None,
    str,
    uuid.UUID | None,
    uuid.UUID,
    uuid.UUID,
]


def _rows_to_vote_samples(rows: list[tuple[Any, ...]]) -> list[VoteSample]:
    samples: list[VoteSample] = []
    for raw_row in rows:
        row = cast(_VoteSampleRow, raw_row)
        voter_user_id = row[3]
        if voter_user_id is None:
            logger.warning(
                "Skipping vote %s without voter_user_id during leaderboard refresh",
                row[0],
            )
            continue
        voter_actor_type = row[4] if row[4] in {"human", "bot"} else "human"
        samples.append(
            VoteSample(
                vote_id=row[0],
                created_at=_ensure_utc(row[1]),
                winner=row[2],
                judge_key=f"user:{voter_user_id}",
                model_a_id=row[6],
                model_b_id=row[7],
                voter_actor_type=voter_actor_type,
                service_account_id=row[5],
            )
        )

    return samples


def _limit_vote_samples_for_single_day(
    vote_samples: list[VoteSample],
    *,
    daily_vote_cap: int,
) -> list[VoteSample]:
    if daily_vote_cap <= 0:
        return vote_samples

    usage: dict[str, int] = {}
    kept: list[VoteSample] = []

    for vote in vote_samples:
        current = usage.get(vote.judge_key, 0)
        if current >= daily_vote_cap:
            continue
        usage[vote.judge_key] = current + 1
        kept.append(vote)

    return kept


def compute_elo_ratings(
    vote_samples: list[VoteSample],
    *,
    k: float,
    shuffle_rounds: int = 1,
    shuffle_seed: int = 0,
) -> dict[uuid.UUID, tuple[float, int]]:
    """Compute Elo ratings from vote samples.

    When ``shuffle_rounds`` is 2 or more, run that many independently
    shuffled passes over the events and average the resulting ratings.  This
    reduces the order-dependent variance inherent in a single linear Elo pass
    (FastChat shuffle-and-average approach).

    ``shuffle_rounds`` is clamped to at least one pass.
    """

    events = _build_elo_events(vote_samples)
    shuffle_rng = random.Random(shuffle_seed) if shuffle_rounds > 1 else None
    averaged_ratings, games_played = _compute_shuffled_average_elo_from_events(
        events=events,
        k=k,
        shuffle_rounds=shuffle_rounds,
        shuffle_rng=shuffle_rng,
    )

    return {
        model_id: (
            averaged_ratings[model_id],
            games_played.get(model_id, 0),
        )
        for model_id in averaged_ratings
    }


def compute_elo_confidence_intervals(
    vote_samples: list[VoteSample],
    *,
    model_ids: list[uuid.UUID],
    bootstrap_rounds: int,
    seed: int,
    k: float,
    confidence_level: float = 0.95,
    shuffle_rounds: int = 1,
    shuffle_seed: int = 0,
) -> dict[uuid.UUID, tuple[float, float]]:
    """Compute bootstrap confidence intervals for Elo ratings.

    This follows FastChat's bootstrap idea (resampling vote indices with
    replacement), adapted to our pure-Python Elo pipeline.
    """

    if not model_ids:
        return {}

    base_ratings = compute_elo_ratings(
        vote_samples,
        k=k,
        shuffle_rounds=shuffle_rounds,
        shuffle_seed=shuffle_seed,
    )
    if not vote_samples:
        return {
            model_id: _point_interval(base_ratings.get(model_id, (1000.0, 0))[0])
            for model_id in model_ids
        }

    if bootstrap_rounds <= 0:
        return {}

    events = _build_elo_events(vote_samples)
    sample_size = len(events)

    confidence_level = min(max(confidence_level, 0.0), 1.0)
    lower_quantile = max(0.0, min((1.0 - confidence_level) / 2.0, 0.5))
    upper_quantile = 1.0 - lower_quantile

    rng = random.Random(seed)
    shuffle_rng = random.Random(shuffle_seed) if shuffle_rounds > 1 else None
    samples_by_model: dict[uuid.UUID, list[float]] = {
        model_id: [] for model_id in model_ids
    }

    for _ in range(bootstrap_rounds):
        sampled_indices = [rng.randrange(sample_size) for _ in range(sample_size)]
        sampled_ratings, _ = _compute_shuffled_average_elo_from_events(
            events=events,
            k=k,
            shuffle_rounds=shuffle_rounds,
            shuffle_rng=shuffle_rng,
            sampled_indices=sampled_indices,
        )

        for model_id in model_ids:
            samples_by_model[model_id].append(sampled_ratings.get(model_id, 1000.0))

    intervals: dict[uuid.UUID, tuple[float, float]] = {}
    for model_id in model_ids:
        samples = samples_by_model[model_id]
        samples.sort()
        intervals[model_id] = (
            _percentile(samples, lower_quantile),
            _percentile(samples, upper_quantile),
        )

    return intervals


def _build_elo_events(vote_samples: list[VoteSample]) -> list[_EloEvent]:
    events: list[_EloEvent] = []
    for vote in vote_samples:
        # Skip self-matches to avoid inflating games_played.
        if vote.model_a_id == vote.model_b_id:
            continue
        outcome = _winner_to_outcome(vote.winner)
        if outcome is None:
            continue
        events.append((vote.model_a_id, vote.model_b_id, outcome))
    return events


def _compute_elo_ratings_from_events(
    *,
    events: list[_EloEvent],
    k: float,
    sampled_indices: list[int] | None = None,
) -> tuple[dict[uuid.UUID, float], dict[uuid.UUID, int]]:
    ratings: dict[uuid.UUID, float] = {}
    games_played: dict[uuid.UUID, int] = {}

    if sampled_indices is None:
        event_iter = iter(events)
    else:
        event_iter = (events[index] for index in sampled_indices)

    for model_a_id, model_b_id, outcome in event_iter:
        rating_a = ratings.get(model_a_id, 1000.0)
        rating_b = ratings.get(model_b_id, 1000.0)

        delta_a, delta_b = elo_update(
            rating_a=rating_a,
            rating_b=rating_b,
            outcome=outcome,
            k=k,
        )

        ratings[model_a_id] = rating_a + delta_a
        ratings[model_b_id] = rating_b + delta_b

        games_played[model_a_id] = games_played.get(model_a_id, 0) + 1
        games_played[model_b_id] = games_played.get(model_b_id, 0) + 1

    return ratings, games_played


def _compute_shuffled_average_elo_from_events(
    *,
    events: list[_EloEvent],
    k: float,
    shuffle_rounds: int,
    shuffle_rng: random.Random | None,
    sampled_indices: list[int] | None = None,
) -> tuple[dict[uuid.UUID, float], dict[uuid.UUID, int]]:
    if not events:
        return _compute_elo_ratings_from_events(
            events=events,
            k=k,
            sampled_indices=sampled_indices,
        )

    rounds = max(int(shuffle_rounds), 1)

    base_indices = (
        list(sampled_indices)
        if sampled_indices is not None
        else list(range(len(events)))
    )

    accumulated: dict[uuid.UUID, float] = {}
    for _ in range(rounds):
        round_indices = list(base_indices)
        if shuffle_rng is not None:
            shuffle_rng.shuffle(round_indices)
        round_ratings, _ = _compute_elo_ratings_from_events(
            events=events,
            k=k,
            sampled_indices=round_indices,
        )
        for model_id, rating in round_ratings.items():
            accumulated[model_id] = accumulated.get(model_id, 0.0) + rating

    _, games_played = _compute_elo_ratings_from_events(
        events=events,
        k=k,
        sampled_indices=base_indices,
    )
    averaged_ratings = {
        model_id: total / rounds for model_id, total in accumulated.items()
    }
    return averaged_ratings, games_played


def _point_interval(value: float) -> tuple[float, float]:
    return value, value


def _winner_to_outcome(winner: str) -> str | None:
    if winner == "A":
        return "A"
    if winner == "B":
        return "B"
    if winner == "tie":
        return "tie"
    # Exclude corrupt/unexpected values instead of counting them as ties.
    logger.warning("Unexpected winner value %r, excluding from ratings", winner)
    return None


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_day_bounds(value: datetime) -> tuple[datetime, datetime]:
    value = _ensure_utc(value)
    day_start = value.replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start, day_start + timedelta(days=1)


@lru_cache(maxsize=1)
def get_leaderboard_refresher() -> LeaderboardRefresher:
    settings = get_settings()
    return LeaderboardRefresher(
        enabled=settings.leaderboard_refresh_enabled,
        interval_seconds=settings.leaderboard_refresh_interval_seconds,
        daily_vote_cap=settings.leaderboard_refresh_daily_vote_cap,
        elo_k=settings.leaderboard_refresh_elo_k,
        elo_shuffle_rounds=settings.leaderboard_elo_shuffle_rounds,
        elo_shuffle_seed=settings.leaderboard_elo_shuffle_seed,
        outlier_filter_enabled=settings.leaderboard_outlier_filter_enabled,
        outlier_filter_min_votes=settings.leaderboard_outlier_min_votes,
        outlier_filter_max_votes=settings.leaderboard_outlier_max_votes,
        outlier_filter_alpha=settings.leaderboard_outlier_alpha,
    )


def _advisory_lock_key(name: str) -> int:
    digest = hashlib.sha256(name.encode("utf-8")).digest()[:8]
    # Postgres uses signed bigint for advisory locks.
    return int.from_bytes(digest, "big", signed=True)
