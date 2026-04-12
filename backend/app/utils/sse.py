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

    *data* **must** be JSON-serialisable.  ``json.dumps`` always produces a
    single-line string (all internal newlines are escaped as ``\n``), so the
    ``data:`` field is safe per the SSE spec.  If this function is ever changed
    to accept arbitrary strings, multi-line payloads must be split so that each
    line is prefixed with its own ``data:`` field — the defensive splitlines()
    loop below already handles that case.

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
    # json.dumps always produces a single-line string, so splitlines() yields
    # exactly one element in practice.  The loop is a defensive measure: if a
    # future caller passes a pre-serialised multi-line string, each line is
    # correctly emitted as a separate ``data:`` field per the SSE spec.
    for line in payload.splitlines() or ['']:
        parts.append(f"data: {line}\n")
    parts.append("\n")
    return "".join(parts).encode("utf-8")
