from __future__ import annotations

import uuid

import pytest

from app.services.leaderboard_bt import (
    PairwiseVote,
    compute_bt_confidence_intervals,
    compute_bt_ratings,
)


def _vote(model_a: uuid.UUID, model_b: uuid.UUID, winner: str) -> PairwiseVote:
    return PairwiseVote(model_a_id=model_a, model_b_id=model_b, winner=winner)


def test_bt_ratings_rank_stronger_models_higher() -> None:
    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    model_c = uuid.uuid4()

    votes = [
        *[_vote(model_a, model_b, "A") for _ in range(30)],
        *[_vote(model_b, model_c, "A") for _ in range(30)],
        *[_vote(model_a, model_c, "A") for _ in range(20)],
    ]

    ratings = compute_bt_ratings(model_ids=[model_a, model_b, model_c], votes=votes)

    assert ratings[model_a][0] > ratings[model_b][0] > ratings[model_c][0]
    assert ratings[model_a][1] == 50
    assert ratings[model_b][1] == 60
    assert ratings[model_c][1] == 50


def test_bt_ratings_keep_perfect_ties_equal() -> None:
    model_a = uuid.uuid4()
    model_b = uuid.uuid4()

    votes = [_vote(model_a, model_b, "tie") for _ in range(40)]
    ratings = compute_bt_ratings(model_ids=[model_a, model_b], votes=votes)

    assert abs(ratings[model_a][0] - ratings[model_b][0]) < 1e-9


def test_bt_confidence_intervals_cover_all_models() -> None:
    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    model_c = uuid.uuid4()

    votes = [
        *[_vote(model_a, model_b, "A") for _ in range(12)],
        *[_vote(model_b, model_c, "A") for _ in range(12)],
        *[_vote(model_a, model_c, "A") for _ in range(8)],
        *[_vote(model_b, model_a, "tie") for _ in range(4)],
    ]

    intervals = compute_bt_confidence_intervals(
        model_ids=[model_a, model_b, model_c],
        votes=votes,
        bootstrap_rounds=40,
        seed=7,
    )

    assert set(intervals) == {model_a, model_b, model_c}
    for low, high in intervals.values():
        assert low <= high


def test_bt_confidence_intervals_are_deterministic_for_seed() -> None:
    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    model_c = uuid.uuid4()

    votes = [
        *[_vote(model_a, model_b, "A") for _ in range(18)],
        *[_vote(model_b, model_c, "A") for _ in range(14)],
        *[_vote(model_c, model_a, "A") for _ in range(6)],
        *[_vote(model_a, model_c, "tie") for _ in range(5)],
    ]

    first = compute_bt_confidence_intervals(
        model_ids=[model_a, model_b, model_c],
        votes=votes,
        bootstrap_rounds=50,
        seed=123,
    )
    second = compute_bt_confidence_intervals(
        model_ids=[model_a, model_b, model_c],
        votes=votes,
        bootstrap_rounds=50,
        seed=123,
    )

    assert first == second


def test_bt_confidence_intervals_without_votes_return_point_estimate() -> None:
    model_a = uuid.uuid4()
    model_b = uuid.uuid4()

    intervals = compute_bt_confidence_intervals(
        model_ids=[model_a, model_b],
        votes=[],
        bootstrap_rounds=10,
        seed=3,
    )

    assert intervals[model_a] == (1000.0, 1000.0)
    assert intervals[model_b] == (1000.0, 1000.0)


def test_bt_ratings_keep_zero_game_models_at_baseline_without_shifting_active_models() -> (
    None
):
    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    model_c = uuid.uuid4()

    votes = [_vote(model_a, model_b, "A") for _ in range(20)]

    ratings_two_model = compute_bt_ratings(model_ids=[model_a, model_b], votes=votes)
    ratings_three_model = compute_bt_ratings(
        model_ids=[model_a, model_b, model_c],
        votes=votes,
    )

    assert ratings_three_model[model_c][1] == 0
    assert ratings_three_model[model_c][0] == pytest.approx(1000.0)
    assert ratings_three_model[model_a][0] == pytest.approx(
        ratings_two_model[model_a][0]
    )
    assert ratings_three_model[model_b][0] == pytest.approx(
        ratings_two_model[model_b][0]
    )


def test_bt_confidence_intervals_keep_zero_game_models_at_baseline() -> None:
    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    model_c = uuid.uuid4()

    votes = [_vote(model_a, model_b, "A") for _ in range(20)]
    intervals = compute_bt_confidence_intervals(
        model_ids=[model_a, model_b, model_c],
        votes=votes,
        bootstrap_rounds=30,
        seed=0,
    )

    assert intervals[model_c] == pytest.approx((1000.0, 1000.0))


def test_bt_ratings_ignore_votes_for_models_outside_requested_view() -> None:
    public_a = uuid.uuid4()
    public_b = uuid.uuid4()
    hidden_model = uuid.uuid4()

    votes = [
        *[_vote(public_a, hidden_model, "A") for _ in range(12)],
        *[_vote(public_b, public_a, "A") for _ in range(8)],
    ]

    ratings = compute_bt_ratings(model_ids=[public_a, public_b], votes=votes)

    assert ratings[public_a][1] == 8
    assert ratings[public_b][1] == 8
