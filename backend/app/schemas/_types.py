"""app.schemas._types

Reusable Pydantic types for request/response schemas.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import AfterValidator


def _validate_uuid_str(value: str) -> str:
    """Validate that a string is a well-formed UUID.

    Returns the original string (not the normalised UUID form) so that
    round-tripping through serialisation is lossless.
    """
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid UUID format: {value!r}") from exc
    return value


UuidStr = Annotated[str, AfterValidator(_validate_uuid_str)]
"""A ``str`` field that validates the value is a well-formed UUID.

Using ``UuidStr`` instead of bare ``str`` ensures that invalid UUIDs are
rejected at the Pydantic validation layer (HTTP 422) rather than causing
a database-level ``DataError`` (HTTP 500).
"""
