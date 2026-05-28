from __future__ import annotations

from collections import Counter
from dataclasses import replace
import random
import uuid

from app.services.sampling import CandidateModel, SamplingPolicy, select_battle_pair


def _candidate(name: str, games: int = 0) -> CandidateModel:
    seed = uuid.uuid5(uuid.NAMESPACE_DNS, name)
    return CandidateModel(id=seed, model_name=name, games_played=games)


def _policy(**overrides: object) -> SamplingPolicy:
    base = SamplingPolicy(
        weights={},
        targets={},
        strict_targets={},
        outage_models=set(),
        boost_models=set(),
    )
    return replace(base, **overrides)


def test_sampling_respects_outage_models_when_alternatives_exist() -> None:
    candidates = [_candidate("model-a"), _candidate("model-b"), _candidate("model-c")]
    policy = _policy(
        weights={"model-a": 1.0, "model-b": 1.0, "model-c": 100.0},
        outage_models={"model-c"},
    )

    pair = select_battle_pair(
        candidates=candidates,
        policy=policy,
        randomizer=random.Random(7),
    )

    id_to_name = {candidate.id: candidate.model_name for candidate in candidates}
    names = {id_to_name[pair[0]], id_to_name[pair[1]]}
    assert names == {"model-a", "model-b"}


def test_sampling_applies_strict_targets() -> None:
    candidates = [_candidate("model-a"), _candidate("model-b"), _candidate("model-c")]
    policy = _policy(
        weights={"model-a": 10.0, "model-b": 1.0, "model-c": 5.0},
        outage_models={"model-c"},
        strict_targets={
            "model-a": ["model-b"],
            "model-b": ["model-a"],
        },
    )

    pair = select_battle_pair(
        candidates=candidates,
        policy=policy,
        randomizer=random.Random(11),
    )

    id_to_name = {candidate.id: candidate.model_name for candidate in candidates}
    names = {id_to_name[pair[0]], id_to_name[pair[1]]}
    assert names == {"model-a", "model-b"}


def test_sampling_target_boost_increases_target_matchups() -> None:
    candidates = [_candidate("model-a"), _candidate("model-b"), _candidate("model-c")]

    base_policy = _policy(weights={"model-a": 100.0, "model-b": 1.0, "model-c": 1.0})
    boosted_policy = _policy(
        weights={"model-a": 100.0, "model-b": 1.0, "model-c": 1.0},
        targets={"model-a": ["model-b"]},
    )

    base_rng = random.Random(101)
    boosted_rng = random.Random(101)

    base_counts: Counter[str] = Counter()
    boosted_counts: Counter[str] = Counter()

    id_to_name = {candidate.id: candidate.model_name for candidate in candidates}

    for _ in range(400):
        base_pair = select_battle_pair(
            candidates=candidates,
            policy=base_policy,
            randomizer=base_rng,
        )
        boosted_pair = select_battle_pair(
            candidates=candidates,
            policy=boosted_policy,
            randomizer=boosted_rng,
        )

        base_names = {id_to_name[base_pair[0]], id_to_name[base_pair[1]]}
        boosted_names = {id_to_name[boosted_pair[0]], id_to_name[boosted_pair[1]]}

        if "model-a" in base_names:
            base_names.remove("model-a")
            base_counts[next(iter(base_names))] += 1

        if "model-a" in boosted_names:
            boosted_names.remove("model-a")
            boosted_counts[next(iter(boosted_names))] += 1

    assert boosted_counts["model-b"] > base_counts["model-b"]


def test_sampling_rejects_pairs_when_outage_excludes_all_models() -> None:
    candidates = [_candidate("model-a"), _candidate("model-b")]
    policy = _policy(outage_models={"model-a", "model-b"})

    try:
        select_battle_pair(
            candidates=candidates,
            policy=policy,
            randomizer=random.Random(3),
        )
    except ValueError as exc:
        assert "non-outage" in str(exc)
    else:
        raise AssertionError("Expected ValueError when all candidates are in outage")


def test_configured_weight_multiplies_decay_so_ratio_holds_with_games() -> None:
    """A configured weight must scale the games-played decay, not replace it.

    With the old replacement semantic a static ``0.3`` for ``a`` would beat
    ``b``'s decayed weight ``1/(1+games)`` once ``b`` had even a few games,
    inverting the operator's intent.  With the multiplier semantic the per-pick
    ratio between a and b stays at ``0.3`` regardless of game count.
    """

    games = 9
    candidates = [
        _candidate("model-a", games=games),
        _candidate("model-b", games=games),
    ]
    policy = _policy(weights={"model-a": 0.3})

    rng = random.Random(42)
    counts: Counter[str] = Counter()
    id_to_name = {c.id: c.model_name for c in candidates}

    iterations = 4000
    for _ in range(iterations):
        pair = select_battle_pair(
            candidates=candidates,
            policy=policy,
            randomizer=rng,
        )
        for cid in pair:
            counts[id_to_name[cid]] += 1

    # Each battle picks both candidates exactly once, so per-model counts equal
    # the number of battles.  The ratio assertion exists for documentation: it
    # would still hold even without the fix because the pair only has 2 models.
    # The real protection is the unit-level check below.
    assert counts["model-a"] == iterations
    assert counts["model-b"] == iterations

    # Unit-level: the first-pick weight ratio must match the configured 0.3
    # multiplier, not the legacy "0.3 vs 1/(1+games)=0.1" inversion.
    from app.services.sampling import _sample_weight  # noqa: PLC0415

    w_a = _sample_weight(candidates[0], policy=policy, include_boost=False)
    w_b = _sample_weight(candidates[1], policy=policy, include_boost=False)
    assert w_b > 0
    assert abs(w_a / w_b - 0.3) < 1e-9


def test_zero_weight_fallback_never_selects_disabled_model() -> None:
    candidates = [
        _candidate("model-a"),
        _candidate("model-b"),
        _candidate("model-c"),
    ]
    policy = _policy(
        weights={"model-a": 1.0, "model-b": 0, "model-c": 1.0},
        strict_targets={
            "model-a": ["model-b"],
            "model-c": ["model-b"],
        },
    )

    id_to_name = {c.id: c.model_name for c in candidates}

    for seed in range(200):
        pair = select_battle_pair(
            candidates=candidates,
            policy=policy,
            randomizer=random.Random(seed),
        )
        names = {id_to_name[pair[0]], id_to_name[pair[1]]}
        assert "model-b" not in names, (
            f"seed={seed}: zero-weight model-b was selected in pair {names}"
        )
