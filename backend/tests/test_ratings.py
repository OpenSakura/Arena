from __future__ import annotations

import pytest

from app.services.ratings import elo_update


def test_elo_update_tie_between_equal_ratings_is_neutral() -> None:
    delta_a, delta_b = elo_update(rating_a=1000.0, rating_b=1000.0, outcome="tie")

    assert delta_a == pytest.approx(0.0)
    assert delta_b == pytest.approx(0.0)


def test_elo_update_winner_and_loser_deltas_cancel_out() -> None:
    delta_a, delta_b = elo_update(rating_a=1000.0, rating_b=1200.0, outcome="A")

    assert delta_a == pytest.approx(24.3119, rel=1e-4)
    assert delta_b == pytest.approx(-24.3119, rel=1e-4)
    assert delta_a + delta_b == pytest.approx(0.0)


def test_elo_update_upset_swings_more_than_expected_result() -> None:
    favorite_win_delta, _ = elo_update(rating_a=1200.0, rating_b=1000.0, outcome="A")
    favorite_loss_delta, _ = elo_update(rating_a=1200.0, rating_b=1000.0, outcome="B")

    assert abs(favorite_loss_delta) > abs(favorite_win_delta)


def test_elo_update_tie_between_unequal_ratings_moves_toward_mean() -> None:
    delta_a, delta_b = elo_update(rating_a=1200.0, rating_b=1000.0, outcome="tie")

    assert delta_a < 0.0
    assert delta_b > 0.0
    assert delta_a + delta_b == pytest.approx(0.0)


def test_elo_update_rejects_invalid_outcome() -> None:
    with pytest.raises(ValueError, match="invalid outcome"):
        elo_update(rating_a=1000.0, rating_b=1000.0, outcome="draw")
