"""app.utils.id

ID helpers.

Notes:
- Use UUIDs for primary identifiers exposed to clients.
- ``parse_uuid`` / ``parse_optional_uuid`` are pure (raise ``ValueError``).
- Route-layer callers should use ``parse_uuid_or_422`` /
  ``parse_optional_uuid_or_422`` which translate to ``HTTPException(422)``.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException


def parse_uuid(raw: str, field_name: str) -> uuid.UUID:
    """Parse a UUID string or raise *ValueError*."""
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise ValueError(f"Invalid {field_name}")


def parse_optional_uuid(raw: str | None, field_name: str) -> uuid.UUID | None:
    if raw is None:
        return None
    return parse_uuid(raw, field_name)


def parse_uuid_or_422(raw: str, field_name: str) -> uuid.UUID:
    """Parse a UUID string or raise ``HTTPException(422)``."""
    try:
        return parse_uuid(raw, field_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def parse_optional_uuid_or_422(raw: str | None, field_name: str) -> uuid.UUID | None:
    """Parse an optional UUID string or raise ``HTTPException(422)``."""
    try:
        return parse_optional_uuid(raw, field_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
