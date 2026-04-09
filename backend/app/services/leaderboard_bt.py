"""app.services.leaderboard_bt

Bradley-Terry leaderboard helpers with optional bootstrap confidence intervals.

Notes:
- Elo remains the default leaderboard path for backward compatibility.
- This module is pure and DB-agnostic so it is easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import random
import uuid

from app.utils.stats import percentile as _percentile

logger = logging.getLogger(__name__)


_WeightedObservation = tuple[int, int, float, int]


@dataclass(slots=True)
class PairwiseVote:
    model_a_id: uuid.UUID
    model_b_id: uuid.UUID
    winner: str


def compute_bt_ratings(
    *,
    model_ids: list[uuid.UUID],
    votes: list[PairwiseVote],
    max_iterations: int = 200,
    tolerance: float = 1e-6,
    prior: float = 1e-6,
    elo_scale: float = 400.0,
    elo_init: float = 1000.0,
) -> dict[uuid.UUID, tuple[float, int]]:
    """Compute Bradley-Terry ratings mapped onto an Elo-like scale.

    Returns ``model_id -> (rating, games_played)``.
    """

    if not model_ids:
        return {}

    model_index = {model_id: idx for idx, model_id in enumerate(model_ids)}
    observations = _aggregate_vote_observations(
        model_index=model_index,
        votes=votes,
    )

    rating_values, games_played = _solve_bt(
        model_count=len(model_ids),
        observations=observations,
        max_iterations=max_iterations,
        tolerance=tolerance,
        prior=prior,
        elo_scale=elo_scale,
        elo_init=elo_init,
    )

    ratings: dict[uuid.UUID, tuple[float, int]] = {
        model_id: (rating_values[idx], games_played[idx])
        for idx, model_id in enumerate(model_ids)
    }

    return ratings


def compute_bt_confidence_intervals(
    *,
    model_ids: list[uuid.UUID],
    votes: list[PairwiseVote],
    bootstrap_rounds: int,
    seed: int,
    max_iterations: int = 200,
    tolerance: float = 1e-6,
    prior: float = 1e-6,
    elo_scale: float = 400.0,
    elo_init: float = 1000.0,
    confidence_level: float = 0.95,
) -> dict[uuid.UUID, tuple[float, float]]:
    """Compute percentile bootstrap confidence intervals for BT ratings."""

    if not model_ids:
        return {}

    if bootstrap_rounds <= 0:
        return {}

    model_index = {model_id: idx for idx, model_id in enumerate(model_ids)}
    observations = _aggregate_vote_observations(
        model_index=model_index,
        votes=votes,
    )

    baseline_ratings, _ = _solve_bt(
        model_count=len(model_ids),
        observations=observations,
        max_iterations=max_iterations,
        tolerance=tolerance,
        prior=prior,
        elo_scale=elo_scale,
        elo_init=elo_init,
    )

    if not observations:
        return {
            model_id: (baseline_ratings[idx], baseline_ratings[idx])
            for idx, model_id in enumerate(model_ids)
        }

    confidence_level = min(max(confidence_level, 0.0), 1.0)
    lower_quantile = max(0.0, min((1.0 - confidence_level) / 2.0, 0.5))
    upper_quantile = 1.0 - lower_quantile

    keys = [(entry[0], entry[1], entry[2]) for entry in observations]
    weights = [entry[3] for entry in observations]
    sample_size = sum(weights)

    rng = random.Random(seed)
    per_model_samples: dict[int, list[float]] = {
        idx: [] for idx in range(len(model_ids))
    }

    for _ in range(bootstrap_rounds):
        sampled_observations = _bootstrap_sample_observations(
            rng=rng,
            keys=keys,
            weights=weights,
            sample_size=sample_size,
        )
        sampled_ratings, _ = _solve_bt(
            model_count=len(model_ids),
            observations=sampled_observations,
            max_iterations=max_iterations,
            tolerance=tolerance,
            prior=prior,
            elo_scale=elo_scale,
            elo_init=elo_init,
        )

        for idx, rating in enumerate(sampled_ratings):
            per_model_samples[idx].append(rating)

    intervals: dict[uuid.UUID, tuple[float, float]] = {}
    for idx, model_id in enumerate(model_ids):
        samples = per_model_samples[idx]
        samples.sort()
        intervals[model_id] = (
            _percentile(samples, lower_quantile),
            _percentile(samples, upper_quantile),
        )

    return intervals


def _aggregate_vote_observations(
    *,
    model_index: dict[uuid.UUID, int],
    votes: list[PairwiseVote],
) -> list[_WeightedObservation]:
    counts: dict[tuple[int, int, float], int] = {}

    for vote in votes:
        idx_a = model_index.get(vote.model_a_id)
        idx_b = model_index.get(vote.model_b_id)
        if idx_a is None or idx_b is None or idx_a == idx_b:
            continue

        score_a_maybe = _winner_score_for_a(vote.winner)
        if score_a_maybe is None:
            continue
        score_a: float = score_a_maybe
        key = (idx_a, idx_b, score_a)
        counts[key] = counts.get(key, 0) + 1

    observations = [
        (idx_a, idx_b, score_a, count)
        for (idx_a, idx_b, score_a), count in counts.items()
    ]
    observations.sort(key=lambda item: (item[0], item[1], item[2]))
    return observations


def _solve_bt(
    *,
    model_count: int,
    observations: list[_WeightedObservation],
    max_iterations: int,
    tolerance: float,
    prior: float,
    elo_scale: float,
    elo_init: float,
) -> tuple[list[float], list[int]]:
    wins, games, neighbors = _build_stats(
        model_count=model_count,
        observations=observations,
    )

    active_indices = {idx for idx, game_count in enumerate(games) if game_count > 0}

    strengths = [1.0] * model_count
    eps = 1e-12
    regularizer = max(prior, 0.0)
    max_log_delta = float("inf")

    for _ in range(max(max_iterations, 1)):
        updated = strengths.copy()
        max_log_delta = 0.0

        for idx, adjacency in enumerate(neighbors):
            denom = 0.0
            strength_i = strengths[idx]
            for rival_idx, count in adjacency.items():
                denom += count / max(strength_i + strengths[rival_idx], eps)

            if denom <= 0.0:
                continue

            next_strength = (wins[idx] + regularizer) / (denom + regularizer)
            next_strength = max(next_strength, eps)
            updated[idx] = next_strength
            max_log_delta = max(
                max_log_delta,
                abs(math.log(next_strength / max(strength_i, eps))),
            )

        strengths = _normalize_strengths(
            updated,
            active_indices=active_indices,
        )
        if max_log_delta < tolerance:
            break
    else:
        logger.warning(
            "Bradley-Terry solver did not converge after %d iterations (max_delta=%s)",
            max_iterations,
            max_log_delta,
        )

    ratings = [
        (
            elo_init + (elo_scale * math.log10(max(strength, eps)))
            if idx in active_indices
            else elo_init
        )
        for idx, strength in enumerate(strengths)
    ]
    return ratings, games


def _build_stats(
    *,
    model_count: int,
    observations: list[_WeightedObservation],
) -> tuple[list[float], list[int], list[dict[int, float]]]:
    wins = [0.0] * model_count
    games = [0] * model_count
    neighbors: list[dict[int, float]] = [dict() for _ in range(model_count)]

    for idx_a, idx_b, score_a, count in observations:
        if count <= 0:
            continue

        weight = float(count)
        games[idx_a] += count
        games[idx_b] += count

        neighbors[idx_a][idx_b] = neighbors[idx_a].get(idx_b, 0.0) + weight
        neighbors[idx_b][idx_a] = neighbors[idx_b].get(idx_a, 0.0) + weight

        wins[idx_a] += weight * score_a
        wins[idx_b] += weight * (1.0 - score_a)

    return wins, games, neighbors


def _winner_score_for_a(winner: str) -> float | None:
    if winner == "A":
        return 1.0
    if winner == "B":
        return 0.0
    if winner == "tie":
        return 0.5
    # Exclude corrupt/unexpected values instead of counting them as ties.
    logger.warning("Unexpected winner value %r, excluding from ratings", winner)
    return None


def _bootstrap_sample_observations(
    *,
    rng: random.Random,
    keys: list[tuple[int, int, float]],
    weights: list[int],
    sample_size: int,
) -> list[_WeightedObservation]:
    if sample_size <= 0 or not keys:
        return []

    sampled_counts = [0] * len(keys)
    sampled_indices = rng.choices(range(len(keys)), weights=weights, k=sample_size)
    for sampled_idx in sampled_indices:
        sampled_counts[sampled_idx] += 1

    return [
        (idx_a, idx_b, score_a, count)
        for (idx_a, idx_b, score_a), count in zip(keys, sampled_counts)
        if count > 0
    ]


def _normalize_strengths(
    strengths: list[float], *, active_indices: set[int] | None = None
) -> list[float]:
    if not strengths:
        return strengths

    if active_indices is None:
        active_indices = set(range(len(strengths)))

    active = [idx for idx in sorted(active_indices) if 0 <= idx < len(strengths)]
    if not active:
        return [1.0] * len(strengths)

    logs = [math.log(max(strengths[idx], 1e-12)) for idx in active]
    log_mean = sum(logs) / len(active)
    if math.isnan(log_mean) or math.isinf(log_mean):
        logger.warning(
            "BT normalization encountered non-finite log_mean, resetting strengths"
        )
        return [1.0] * len(strengths)
    scale = math.exp(log_mean)

    normalized = list(strengths)
    # math.exp() always returns > 0 for finite inputs; no need to guard.
    for idx in active:
        normalized[idx] = max(strengths[idx] / scale, 1e-12)
    return normalized
