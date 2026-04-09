"""app.utils.id

ID helpers.

Notes:
- Use UUIDs for primary identifiers exposed to clients.
"""

from __future__ import annotations

import uuid


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def parse_uuid(raw: str, field_name: str) -> uuid.UUID:
    """Parse a UUID string or raise a 422 HTTPException."""
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail=f"Invalid {field_name}") from exc


def parse_optional_uuid(raw: str | None, field_name: str) -> uuid.UUID | None:
    if raw is None:
        return None
    return parse_uuid(raw, field_name)
