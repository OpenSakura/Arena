"""app.services.sampling

Battle pair sampling helpers inspired by FastChat arena selection.

Notes:
- Keep sampling logic isolated and deterministic for testing.
- Match models by ``model_name`` so config remains stable across UI renames.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
import random
import uuid


@dataclass(slots=True)
class CandidateModel:
    id: uuid.UUID
    model_name: str
    games_played: int


@dataclass(slots=True)
class SamplingPolicy:
    weights: dict[str, float]
    targets: dict[str, list[str]]
    strict_targets: dict[str, list[str]]
    outage_models: set[str]
    boost_models: set[str]


def select_battle_pair(
    *,
    candidates: list[CandidateModel],
    policy: SamplingPolicy,
    randomizer: random.Random | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Select a battle pair with FastChat-inspired weighted sampling.

    Strategy:
    1) Sample one model from weighted pool (outage excluded, boosts applied).
    2) Sample rival from remaining models with optional target/strict constraints.
    3) Randomly swap A/B sides to avoid side bias.
    """

    if len(candidates) < 2:
        raise ValueError("At least two candidate models are required")

    # Treat outage models as hard-excluded from scheduling. If fewer than two
    # healthy candidates remain, callers should surface a retryable error.
    candidates = [
        candidate
        for candidate in candidates
        if candidate.model_name not in policy.outage_models
    ]
    if len(candidates) < 2:
        raise ValueError("At least two non-outage candidate models are required")

    rng = randomizer or random.Random()

    first_weights_raw = [
        _sample_weight(candidate, policy=policy, include_boost=True)
        for candidate in candidates
    ]
    first_weights = _normalize_weights(first_weights_raw)
    first_idx = _weighted_index(first_weights, rng)
    chosen = candidates[first_idx]

    total_weight = sum(first_weights_raw)
    target_list = policy.targets.get(chosen.model_name, [])
    target_set = set(target_list)

    rival_candidates: list[CandidateModel] = []
    rival_weights_raw: list[float] = []

    for candidate in candidates:
        if candidate.id == chosen.id:
            continue
        if not _strict_target_match(
            chosen_model=chosen.model_name,
            rival_model=candidate.model_name,
            strict_targets=policy.strict_targets,
        ):
            continue

        weight = _sample_weight(candidate, policy=policy, include_boost=False)

        if (
            total_weight > 0
            and target_list
            and candidate.model_name in target_set
            and weight > 0
        ):
            # FastChat-style target boost: keep targeted matchups likely.
            weight = 0.5 * total_weight / len(target_list)

        if weight <= 0:
            continue

        rival_candidates.append(candidate)
        rival_weights_raw.append(weight)

    if not rival_candidates:
        # Fallback: relax strict/target constraints, keep outage exclusion if possible.
        for candidate in candidates:
            if candidate.id == chosen.id:
                continue
            weight = _sample_weight(candidate, policy=policy, include_boost=False)
            if weight > 0:
                rival_candidates.append(candidate)
                rival_weights_raw.append(weight)

    if not rival_candidates:
        # Last resort: relax all preference constraints but still honour explicit
        # weight=0 (disabled) models — only include candidates whose base weight
        # is positive so that administratively disabled models never re-enter.
        for candidate in candidates:
            if candidate.id == chosen.id:
                continue
            base_weight = policy.weights.get(candidate.model_name)
            if base_weight is not None and float(base_weight) <= 0:
                continue
            rival_candidates.append(candidate)
        rival_weights = [1.0] * len(rival_candidates)
    else:
        rival_weights = _normalize_weights(rival_weights_raw)

    if not rival_candidates:
        # All remaining candidates are administratively disabled (weight=0).
        # Raise with a clear message rather than letting _weighted_index raise
        # an opaque "weights cannot be empty" ValueError.
        raise ValueError(
            f"No valid rival found for '{chosen.model_name}': all other "
            f"candidates are outage-excluded or have weight=0"
        )

    rival_idx = _weighted_index(rival_weights, rng)
    rival = rival_candidates[rival_idx]

    if rng.randint(0, 1) == 0:
        return chosen.id, rival.id
    return rival.id, chosen.id


def _sample_weight(
    candidate: CandidateModel,
    *,
    policy: SamplingPolicy,
    include_boost: bool,
) -> float:
    if candidate.model_name in policy.outage_models:
        return 0.0

    # ``policy.weights`` is a *multiplier* on the games-played decay so that
    # configured and unconfigured models share the same scale.  Without this
    # the two paths drift apart as games accumulate and a static override
    # eventually dominates (or vanishes against) the decaying base.
    base = 1.0 / (1.0 + float(max(candidate.games_played, 0)))
    multiplier = policy.weights.get(candidate.model_name, 1.0)
    weight = base * max(float(multiplier), 0.0)

    if include_boost and candidate.model_name in policy.boost_models:
        weight *= 5.0

    return min(max(weight, 0.0), 1e6)


def _normalize_weights(raw: list[float]) -> list[float]:
    if not raw:
        return []

    total = sum(raw)
    if total <= 0:
        return [1.0 / len(raw)] * len(raw)
    return [value / total for value in raw]


def _weighted_index(weights: list[float], rng: random.Random) -> int:
    if not weights:
        raise ValueError("weights cannot be empty")

    # ``random.Random`` and the module both expose ``choices``.
    return rng.choices(range(len(weights)), weights=weights, k=1)[0]


def _strict_target_match(
    *,
    chosen_model: str,
    rival_model: str,
    strict_targets: dict[str, list[str]],
) -> bool:
    chosen_patterns = strict_targets.get(chosen_model)
    if chosen_patterns and not _match_any(rival_model, chosen_patterns):
        return False

    rival_patterns = strict_targets.get(rival_model)
    if rival_patterns and not _match_any(chosen_model, rival_patterns):
        return False

    return True


def _match_any(model_name: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if fnmatchcase(model_name, pattern):
            return True
    return False
