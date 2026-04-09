from __future__ import annotations

import json

from app.utils.sse import sse_event


def test_sse_event_encodes_json_payload_as_utf8() -> None:
    payload = {"text": "こんにちは", "score": 1}

    encoded = sse_event(event="run.delta", data=payload)
    body = encoded.decode("utf-8")

    assert body.startswith("event: run.delta\n")
    assert body.endswith("\n\n")

    data_line = body.splitlines()[1]
    assert data_line.startswith("data: ")
    assert json.loads(data_line.removeprefix("data: ")) == payload


def test_sse_event_handles_scalar_data_values() -> None:
    body = sse_event(event="done", data="ok").decode("utf-8")

    assert body == 'event: done\ndata: "ok"\n\n'
