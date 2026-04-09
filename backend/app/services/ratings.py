"""app.services.ratings

Rating algorithms and helpers.

Notes:
- MVP uses Elo for pairwise battles.
- Keep this logic pure (no DB side effects) so it can be tested easily.
"""

from __future__ import annotations

import math


def _expected_score(r_a: float, r_b: float) -> float:
    if math.isnan(r_a) or math.isnan(r_b):
        raise ValueError("Rating values must be finite numbers, got NaN")
    if math.isinf(r_a) or math.isinf(r_b):
        raise ValueError("Rating values must be finite numbers, got Inf")
    # Clamp the difference to prevent float overflow with extreme ratings.
    diff = min(max(r_b - r_a, -1000.0), 1000.0)
    return 1.0 / (1.0 + 10.0 ** (diff / 400.0))


def elo_update(
    *,
    rating_a: float,
    rating_b: float,
    outcome: str,
    k: float = 32.0,
) -> tuple[float, float]:
    """Return (delta_a, delta_b) for an Elo update.

    outcome: "A" | "B" | "tie"
    """

    e_a = _expected_score(rating_a, rating_b)
    e_b = 1.0 - e_a

    if outcome == "A":
        s_a, s_b = 1.0, 0.0
    elif outcome == "B":
        s_a, s_b = 0.0, 1.0
    elif outcome == "tie":
        s_a, s_b = 0.5, 0.5
    else:
        raise ValueError(f"invalid outcome: {outcome!r}")

    delta_a = k * (s_a - e_a)
    delta_b = k * (s_b - e_b)
    return delta_a, delta_b
