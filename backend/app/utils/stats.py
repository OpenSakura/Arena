"""app.utils.stats

Shared statistical helpers used by leaderboard modules.
"""

from __future__ import annotations

import math


def percentile(sorted_values: list[float], q: float) -> float:
    """Compute a percentile from a **pre-sorted** list of floats.

    Uses linear interpolation between adjacent elements (the same method
    as NumPy's ``np.percentile(..., interpolation='linear')``).

    Parameters
    ----------
    sorted_values:
        A list of floats sorted in ascending order.
    q:
        Quantile in [0, 1].  Values outside this range are clamped.

    Returns
    -------
    float
        The interpolated percentile value, or 1000.0 for empty inputs
        (matching the Elo default).
    """

    if not sorted_values:
        return 1000.0

    if len(sorted_values) == 1:
        return sorted_values[0]

    q = min(max(q, 0.0), 1.0)
    position = q * (len(sorted_values) - 1)
    lower_idx = int(math.floor(position))
    upper_idx = int(math.ceil(position))
    if lower_idx == upper_idx:
        return sorted_values[lower_idx]

    weight_upper = position - lower_idx
    weight_lower = 1.0 - weight_upper
    return (
        sorted_values[lower_idx] * weight_lower
        + sorted_values[upper_idx] * weight_upper
    )
