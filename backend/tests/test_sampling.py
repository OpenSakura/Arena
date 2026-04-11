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
