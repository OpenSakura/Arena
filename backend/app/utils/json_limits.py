from __future__ import annotations

import json
import math
from typing import Any


MAX_JSON_BYTES = 16 * 1024
MAX_JSON_DEPTH = 4
MAX_JSON_OBJECT_KEYS = 64
MAX_JSON_KEY_LENGTH = 128
MAX_JSON_STRING_LENGTH = 4096


def compact_json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def compact_json_size_bytes(value: Any) -> int:
    return len(compact_json_dumps(value).encode("utf-8"))


def validate_bounded_json_object(
    value: Any,
    *,
    field_name: str = "metadata",
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")

    _validate_json_value(value, depth=1, field_name=field_name)
    try:
        size_bytes = compact_json_size_bytes(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must contain valid JSON values") from exc

    if size_bytes > MAX_JSON_BYTES:
        raise ValueError(f"{field_name} JSON must not exceed {MAX_JSON_BYTES} bytes")

    return value


def _validate_json_value(value: Any, *, depth: int, field_name: str) -> None:
    if depth > MAX_JSON_DEPTH:
        raise ValueError(f"{field_name} JSON depth must not exceed {MAX_JSON_DEPTH}")

    if isinstance(value, dict):
        _validate_json_object(value, depth=depth, field_name=field_name)
        return

    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, depth=depth + 1, field_name=field_name)
        return

    if isinstance(value, str):
        if len(value) > MAX_JSON_STRING_LENGTH:
            raise ValueError(
                f"{field_name} JSON strings must be at most "
                f"{MAX_JSON_STRING_LENGTH} characters"
            )
        return

    if value is None or isinstance(value, bool) or isinstance(value, int):
        return

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} JSON numbers must be finite")
        return

    raise ValueError(f"{field_name} must contain only JSON-compatible values")


def _validate_json_object(
    value: dict[Any, Any],
    *,
    depth: int,
    field_name: str,
) -> None:
    if len(value) > MAX_JSON_OBJECT_KEYS:
        raise ValueError(
            f"{field_name} JSON objects must have at most "
            f"{MAX_JSON_OBJECT_KEYS} keys"
        )

    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(f"{field_name} JSON object keys must be strings")
        if len(key) > MAX_JSON_KEY_LENGTH:
            raise ValueError(
                f"{field_name} JSON object keys must be at most "
                f"{MAX_JSON_KEY_LENGTH} characters"
            )
        _validate_json_value(item, depth=depth + 1, field_name=field_name)
