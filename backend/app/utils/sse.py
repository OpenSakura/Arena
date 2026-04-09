"""app.utils.sse

Server-Sent Events (SSE) helpers.

Notes:
- SSE is a good fit for long streaming text in browsers.
- Use `fetch()` + stream reader on the frontend so we can attach Authorization.
"""

from __future__ import annotations

import json
from typing import Any


def sse_event(*, event: str, data: Any, id: str | None = None) -> bytes:
    """Encode an SSE event with JSON data.

    When *id* is provided the ``id:`` field is included so that clients can
    track the last received event for stream resumption.
    """

    payload = json.dumps(data, ensure_ascii=False)
    parts: list[str] = []
    if id is not None:
        # Strip newlines from id to prevent SSE field injection.
        parts.append(f"id: {id.replace(chr(10), '').replace(chr(13), '')}\n")
    # Strip newlines from event name to prevent SSE field injection.
    parts.append(f"event: {event.replace(chr(10), '').replace(chr(13), '')}\n")
    parts.append(f"data: {payload}\n\n")
    return "".join(parts).encode("utf-8")
