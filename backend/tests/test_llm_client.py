from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.llm_client import (
    LLMClient,
    StreamTotalTimeoutError,
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


_BASE_KWARGS = dict(
    base_url="https://llm.example",
    model="test-model",
    api_key="key",
    messages=[{"role": "user", "content": "hello"}],
)


def _make_sse_body(chunks: list[str]) -> bytes:
    parts: list[str] = []
    for c in chunks:
        parts.append(f"data: {c}\n\n")
    parts.append("data: [DONE]\n\n")
    return "".join(parts).encode()


class _FakeResponse:
    def __init__(self, status_code: int, body: bytes, headers: dict | None = None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self._raised = False

    def raise_for_status(self):
        if self.status_code >= 400:
            resp = MagicMock(status_code=self.status_code)
            raise httpx.HTTPStatusError(
                "error",
                request=MagicMock(),
                response=resp,
            )

    async def aiter_raw(self):
        yield self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


async def _collect_stream(client: LLMClient, **extra_kwargs) -> list[str]:
    chunks: list[str] = []
    kwargs = {**_BASE_KWARGS, **extra_kwargs}
    async for chunk in client.stream_chat_completion(**kwargs):
        if chunk.text_delta:
            chunks.append(chunk.text_delta)
    return chunks


def test_readtimeout_is_retried_pre_stream() -> None:
    call_count = 0

    async def _run():
        nonlocal call_count
        client = LLMClient()
        body = _make_sse_body(
            ['{"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}']
        )

        def _fake_stream(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise httpx.ReadTimeout("timed out")
            return _FakeResponse(200, body)

        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.stream = _fake_stream
        client._http_client = mock_http

        with patch("app.services.llm_client.asyncio.sleep", new_callable=AsyncMock):
            result = await _collect_stream(client, timeout_seconds=5.0)
        return result

    result = asyncio.run(_run())
    assert result == ["ok"]
    assert call_count == 2


def test_pooltimeout_is_retried_pre_stream() -> None:
    call_count = 0

    async def _run():
        nonlocal call_count
        client = LLMClient()
        body = _make_sse_body(
            ['{"choices":[{"delta":{"content":"pool-ok"},"finish_reason":null}]}']
        )

        def _fake_stream(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise httpx.PoolTimeout("pool full")
            return _FakeResponse(200, body)

        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.stream = _fake_stream
        client._http_client = mock_http

        with patch("app.services.llm_client.asyncio.sleep", new_callable=AsyncMock):
            result = await _collect_stream(client, timeout_seconds=5.0)
        return result

    result = asyncio.run(_run())
    assert result == ["pool-ok"]
    assert call_count == 2


def test_does_not_retry_after_receiving_bytes() -> None:
    call_count = 0

    async def _run():
        nonlocal call_count
        client = LLMClient()

        class _FailMidStream:
            def __init__(self):
                self.status_code = 200
                self.headers = {}

            def raise_for_status(self):
                pass

            async def aiter_raw(self):
                yield b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
                raise httpx.ReadTimeout("mid-stream timeout")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        def _fake_stream(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            return _FailMidStream()

        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.stream = _fake_stream
        client._http_client = mock_http

        with pytest.raises(httpx.ReadTimeout):
            await _collect_stream(client, timeout_seconds=5.0)

    asyncio.run(_run())
    assert call_count == 1


def test_readerror_is_retried_pre_stream() -> None:
    call_count = 0

    async def _run():
        nonlocal call_count
        client = LLMClient()
        body = _make_sse_body(
            ['{"choices":[{"delta":{"content":"read-ok"},"finish_reason":null}]}']
        )

        def _fake_stream(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise httpx.ReadError("read error")
            return _FakeResponse(200, body)

        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.stream = _fake_stream
        client._http_client = mock_http

        with patch("app.services.llm_client.asyncio.sleep", new_callable=AsyncMock):
            result = await _collect_stream(client, timeout_seconds=5.0)
        return result

    result = asyncio.run(_run())
    assert result == ["read-ok"]
    assert call_count == 2


def test_remoteprotocolerror_is_retried_pre_stream() -> None:
    call_count = 0

    async def _run():
        nonlocal call_count
        client = LLMClient()
        body = _make_sse_body(
            ['{"choices":[{"delta":{"content":"proto-ok"},"finish_reason":null}]}']
        )

        def _fake_stream(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise httpx.RemoteProtocolError("remote protocol error")
            return _FakeResponse(200, body)

        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.stream = _fake_stream
        client._http_client = mock_http

        with patch("app.services.llm_client.asyncio.sleep", new_callable=AsyncMock):
            result = await _collect_stream(client, timeout_seconds=5.0)
        return result

    result = asyncio.run(_run())
    assert result == ["proto-ok"]
    assert call_count == 2


def test_writetimeout_is_retried_pre_stream() -> None:
    call_count = 0

    async def _run():
        nonlocal call_count
        client = LLMClient()
        body = _make_sse_body(
            ['{"choices":[{"delta":{"content":"write-ok"},"finish_reason":null}]}']
        )

        def _fake_stream(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise httpx.WriteTimeout("write timed out")
            return _FakeResponse(200, body)

        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.stream = _fake_stream
        client._http_client = mock_http

        with patch("app.services.llm_client.asyncio.sleep", new_callable=AsyncMock):
            result = await _collect_stream(client, timeout_seconds=5.0)
        return result

    result = asyncio.run(_run())
    assert result == ["write-ok"]
    assert call_count == 2


def test_propagates_after_retry_budget_exhausted() -> None:
    call_count = 0

    async def _run():
        nonlocal call_count
        client = LLMClient()

        def _fake_stream(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            raise httpx.ReadError("always fails")

        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.stream = _fake_stream
        client._http_client = mock_http

        with patch("app.services.llm_client.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(httpx.ReadError):
                await _collect_stream(client, timeout_seconds=5.0)

    asyncio.run(_run())
    assert call_count == 3


def test_total_timeout_raises_stream_total_timeout_error() -> None:
    async def _run():
        client = LLMClient()

        class _SlowStream:
            def __init__(self):
                self.status_code = 200
                self.headers = {}

            def raise_for_status(self):
                pass

            async def aiter_raw(self):
                chunk = '{"choices":[{"delta":{"content":"tok"},"finish_reason":null}]}'
                for _ in range(100):
                    yield f"data: {chunk}\n\n".encode()
                    await asyncio.sleep(0.05)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        def _fake_stream(method, url, **kwargs):
            return _SlowStream()

        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.stream = _fake_stream
        client._http_client = mock_http

        with pytest.raises(StreamTotalTimeoutError, match="total wall-clock timeout"):
            await _collect_stream(
                client,
                timeout_seconds=5.0,
                total_timeout_seconds=0.1,
            )

    asyncio.run(_run())
