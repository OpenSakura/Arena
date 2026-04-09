"""app.utils.usage

Usage normalization helpers.

Notes:
- Gateways/providers frequently return usage stats with different field names.
- Normalize into a consistent set of keys for downstream analytics.
"""

from __future__ import annotations

from typing import Any


def normalize_usage(usage: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return usage dict with standardized token fields.

    Adds/overwrites:
    - input_tokens
    - output_tokens
    - total_tokens
    """

    if not usage:
        return usage

    input_tokens = _as_int(
        _first_non_none(
            usage.get("input_tokens"),
            usage.get("prompt_tokens"),
            usage.get("prompt_eval_count"),
            usage.get("prompt_n"),
        )
    )
    output_tokens = _as_int(
        _first_non_none(
            usage.get("output_tokens"),
            usage.get("completion_tokens"),
            usage.get("eval_count"),
            usage.get("predicted_n"),
        )
    )
    total_tokens = _as_int(
        _first_non_none(
            usage.get("total_tokens"),
            input_tokens + output_tokens,
        )
    )

    normalized = dict(usage)
    normalized["input_tokens"] = input_tokens
    normalized["output_tokens"] = output_tokens
    normalized["total_tokens"] = total_tokens
    return normalized


def _first_non_none(*values: Any) -> Any:
    """Return the first value that is not None and is numerically coercible.

    Falls through to the next value if the current one cannot be converted
    to an integer (e.g. ``"N/A"``), ensuring that valid computed fallbacks
    are reached.
    """
    for v in values:
        if v is None:
            continue
        # Accept int/float directly.
        if isinstance(v, (int, float)):
            return v
        # For other types, verify numeric coercibility.
        try:
            int(v)
            return v
        except (TypeError, ValueError):
            continue
    return 0


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
