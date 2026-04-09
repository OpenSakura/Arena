from __future__ import annotations

import asyncio

from app.services.llm_client import (
    _extract_upstream_error,
    _iter_lines_from_bytes,
    _iter_sse_data_events,
)


async def _collect_sse_events(
    lines: list[str], *, max_event_chars: int = 128_000
) -> list[str]:
    async def line_iter():
        for line in lines:
            yield line

    collected: list[str] = []
    async for payload in _iter_sse_data_events(
        line_iter(),
        max_event_chars=max_event_chars,
    ):
        collected.append(payload)
    return collected


async def _collect_lines_from_chunks(
    chunks: list[bytes], *, max_line_bytes: int = 256_000
) -> list[str]:
    async def chunk_iter():
        for chunk in chunks:
            yield chunk

    collected: list[str] = []
    async for line in _iter_lines_from_bytes(
        chunk_iter(),
        max_line_bytes=max_line_bytes,
    ):
        collected.append(line)
    return collected


def test_extract_upstream_error_from_openai_error_object() -> None:
    payload = {
        "error": {
            "message": "bad request",
            "type": "invalid_request_error",
            "code": "invalid_prompt",
        }
    }

    detail = _extract_upstream_error(payload)
    assert detail == "bad request type=invalid_request_error code=invalid_prompt"


def test_extract_upstream_error_from_fastchat_style_payload() -> None:
    payload = {
        "error_code": 40001,
        "text": "controller unavailable",
    }

    detail = _extract_upstream_error(payload)
    assert detail == "error_code=40001 text=controller unavailable"


def test_extract_upstream_error_returns_none_without_error_fields() -> None:
    payload = {
        "id": "chatcmpl-123",
        "choices": [{"index": 0}],
    }

    assert _extract_upstream_error(payload) is None


def test_iter_sse_data_events_supports_multiline_payloads() -> None:
    events = asyncio.run(
        _collect_sse_events(
            [
                ": keepalive",
                "event: ignored",
                'data: {"delta":',
                'data: "ok"}',
                "",
            ]
        )
    )

    assert events == ['{"delta":\n"ok"}']


def test_iter_sse_data_events_drops_oversized_events_and_recovers() -> None:
    events = asyncio.run(
        _collect_sse_events(
            [
                "data: this-is-way-too-large",
                "",
                'data: {"ok":true}',
                "",
            ],
            max_event_chars=16,
        )
    )

    assert events == ['{"ok":true}']


def test_iter_sse_data_events_flushes_final_unterminated_event() -> None:
    events = asyncio.run(_collect_sse_events(["data: final"]))
    assert events == ["final"]


def test_iter_lines_from_bytes_splits_crlf_and_flushes_tail() -> None:
    lines = asyncio.run(
        _collect_lines_from_chunks(
            [
                b"data: one\r\n",
                b"data: two\npartial",
            ]
        )
    )

    assert lines == ["data: one", "data: two", "partial"]


def test_iter_lines_from_bytes_drops_oversized_line() -> None:
    lines = asyncio.run(
        _collect_lines_from_chunks(
            [
                b"x" * 32,
                b"\n",
                b"data: ok\n",
            ],
            max_line_bytes=8,
        )
    )

    assert lines == ["data: ok"]


def test_iter_lines_from_bytes_preserves_same_chunk_data_after_oversized_line() -> None:
    lines = asyncio.run(
        _collect_lines_from_chunks(
            [
                b"x" * 32 + b"\ndata: ok\n",
            ],
            max_line_bytes=8,
        )
    )

    assert lines == ["data: ok"]
