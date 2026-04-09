"""app.utils.anon

Shared anonymous identity helpers.
"""

from __future__ import annotations

import re
import uuid

from fastapi import Request, Response

_ANON_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def get_or_set_anon_id(*, request: Request, response: Response, secure: bool) -> str:
    anon_id = normalize_anon_id(request.cookies.get("arena_anon_id"))
    if anon_id is not None:
        return anon_id

    anon_id = uuid.uuid4().hex
    response.set_cookie(
        key="arena_anon_id",
        value=anon_id,
        max_age=60 * 60 * 24 * 365,
        path="/",
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    return anon_id


def normalize_anon_id(raw: str | None) -> str | None:
    if raw is None:
        return None

    value = raw.strip()
    if not value:
        return None

    if _ANON_ID_PATTERN.fullmatch(value) is None:
        return None

    return value
