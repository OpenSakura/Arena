"""app.services.leaderboard_refresh

Periodic leaderboard refresh service.

Notes:
- Refreshes persisted Elo snapshots in ``model_ratings`` from vote history.
- Applies optional per-judge daily vote caps (FastChat-inspired anti-abuse signal).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import logging
import random
import threading
from typing import Any, cast
import uuid

from sqlalchemy import and_, func, select, text  # pyright: ignore[reportMissingImports]
from sqlalchemy.orm import Session, aliased  # pyright: ignore[reportMissingImports]

from app.core.config import get_settings
from app.db.session import get_engine
from app.models.battle import Run
from app.models.model_registry import Model
from app.models.rating import ModelRating
from app.models.vote import Vote
from app.services.ratings import elo_update
from app.utils.requester_identity import RequesterIdentity
from app.utils.stats import percentile as _percentile

logger = logging.getLogger(__name__)

_EloEvent = tuple[uuid.UUID, uuid.UUID, str]


@dataclass(slots=True)
class VoteSample:
    vote_id: uuid.UUID
    created_at: datetime
    winner: str
    model_a_id: uuid.UUID
    model_b_id: uuid.UUID
    judge_key: str


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
    ) -> None:
        self._enabled = enabled
        self._interval_seconds = max(interval_seconds, 5)
        self._daily_vote_cap = max(daily_vote_cap, 0)
        self._elo_k = max(float(elo_k), 1.0)
        self._elo_shuffle_rounds = max(int(elo_shuffle_rounds), 0)
        self._elo_shuffle_seed = int(elo_shuffle_seed)

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

        engine = get_engine()
        lock_key = _advisory_lock_key("arena_leaderboard_refresh")

        with engine.connect() as conn:
            locked = bool(
                conn.execute(
                    text("SELECT pg_try_advisory_lock(:key)"),
                    {"key": lock_key},
                ).scalar_one()
            )
            if not locked:
                logger.debug("Leaderboard refresh skipped (lock busy)")
                return

            db = Session(bind=conn)
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
                try:
                    db.close()
                finally:
                    # Always release the advisory lock, even if db.close()
                    # raises, to prevent the lock from leaking on the pooled
                    # connection.
                    try:
                        conn.execute(
                            text("SELECT pg_advisory_unlock(:key)"),
                            {"key": lock_key},
                        )
                    except Exception:  # noqa: BLE001
                        # If unlock itself fails (e.g. broken connection), log
                        # and discard the connection from the pool so the
                        # leaked advisory lock does not affect future callers
                        # that receive this same pooled connection.
                        logger.exception(
                            "Failed to release advisory lock %s; "
                            "invalidating connection to prevent lock leak",
                            lock_key,
                        )
                        conn.invalidate()

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
        return load_vote_samples(db, daily_vote_cap=self._daily_vote_cap)

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

        # Remove ratings for models that no longer exist.
        stale_stmt = select(ModelRating).order_by(ModelRating.model_id.asc())
        if model_id_set:
            stale_stmt = stale_stmt.where(~ModelRating.model_id.in_(model_id_set))
        stale_rows = db.execute(stale_stmt.with_for_update()).scalars().all()
        for row in stale_rows:
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


def load_vote_samples(db: Session, *, daily_vote_cap: int = 0) -> list[VoteSample]:
    """Load pairwise vote samples in battle execution order."""

    if daily_vote_cap <= 0:
        rows = db.execute(_vote_sample_stmt()).all()
        return _rows_to_vote_samples(rows)

    bounds = db.execute(_vote_sample_bounds_stmt()).one()
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
            _vote_sample_stmt(day_start=current_day_start, day_end=current_day_end)
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
):
    run_a = aliased(Run)
    run_b = aliased(Run)

    stmt = (
        select(
            Vote.id,
            Vote.created_at,
            Vote.winner,
            Vote.voter_user_id,
            Vote.voter_anon_id,
            Vote.ip_hash,
            Vote.user_agent_hash,
            run_a.model_id,
            run_b.model_id,
        )
        .join(
            run_a,
            and_(run_a.battle_id == Vote.battle_id, run_a.side == "A"),
        )
        .join(
            run_b,
            and_(run_b.battle_id == Vote.battle_id, run_b.side == "B"),
        )
        .where(Vote.revealed.is_(True))
    )

    if day_start is not None and day_end is not None:
        stmt = stmt.where(
            Vote.created_at >= day_start,
            Vote.created_at < day_end,
        )

    return stmt.order_by(Vote.created_at.asc(), Vote.id.asc())


def _vote_sample_bounds_stmt():
    run_a = aliased(Run)
    run_b = aliased(Run)

    return (
        select(func.min(Vote.created_at), func.max(Vote.created_at))
        .join(
            run_a,
            and_(run_a.battle_id == Vote.battle_id, run_a.side == "A"),
        )
        .join(
            run_b,
            and_(run_b.battle_id == Vote.battle_id, run_b.side == "B"),
        )
        .where(Vote.revealed.is_(True))
    )


type _VoteSampleRow = tuple[
    uuid.UUID,
    datetime,
    str,
    uuid.UUID | None,
    str | None,
    str | None,
    str | None,
    uuid.UUID,
    uuid.UUID,
]


def _rows_to_vote_samples(rows: list[tuple[Any, ...]]) -> list[VoteSample]:
    samples: list[VoteSample] = []
    for raw_row in rows:
        row = cast(_VoteSampleRow, raw_row)
        samples.append(
            VoteSample(
                vote_id=row[0],
                created_at=_ensure_utc(row[1]),
                winner=row[2],
                judge_key=RequesterIdentity(
                    voter_user_id=row[3],
                    ip_hash=row[5],
                    user_agent_hash=row[6],
                    voter_anon_id=row[4],
                ).judge_key(fallback_vote_id=row[0]),
                model_a_id=row[7],
                model_b_id=row[8],
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

    ``shuffle_rounds`` of 0 or 1 falls back to the original single-pass path
    (no shuffling, no averaging) for backwards compatibility.

    ``shuffle_seed`` controls the RNG used for shuffling.  A value of 0 means
    use ``shuffle_seed=0`` as the literal seed (still deterministic).
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
    if shuffle_rounds <= 1 or not events:
        return _compute_elo_ratings_from_events(
            events=events,
            k=k,
            sampled_indices=sampled_indices,
        )

    base_indices = (
        list(sampled_indices)
        if sampled_indices is not None
        else list(range(len(events)))
    )

    accumulated: dict[uuid.UUID, float] = {}
    for _ in range(shuffle_rounds):
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
        model_id: total / shuffle_rounds for model_id, total in accumulated.items()
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
    )


def _advisory_lock_key(name: str) -> int:
    digest = hashlib.sha256(name.encode("utf-8")).digest()[:8]
    # Postgres uses signed bigint for advisory locks.
    return int.from_bytes(digest, "big", signed=True)
