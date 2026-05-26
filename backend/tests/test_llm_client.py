from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.llm_client import (
    LLMClient,
    StreamTotalTimeoutError,
    _extract_upstream_error,
    _redact_sensitive_text,
    _iter_lines_from_bytes,
    _iter_sse_data_events,
)


@pytest.fixture(autouse=True)
def _default_llm_client_mode_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.llm_client.get_settings",
        lambda: _Settings(llm_client_mode="legacy"),
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


def test_chat_completion_retries_retryable_status_pre_response() -> None:
    call_count = 0

    async def _run():
        nonlocal call_count
        client = LLMClient()
        request = httpx.Request("POST", "https://llm.example/v1/chat/completions")

        async def _fake_post(url, **kwargs):
            nonlocal call_count
            _ = (url, kwargs)
            call_count += 1
            if call_count == 1:
                return httpx.Response(
                    503,
                    json={"error": {"message": "gateway busy"}},
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "non-stream ok"}, "finish_reason": "stop"}
                    ]
                },
                headers={"x-request-id": "req-chat-1"},
                request=request,
            )

        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.post = _fake_post
        client._http_client = mock_http

        with patch("app.services.llm_client.asyncio.sleep", new_callable=AsyncMock):
            return await client.chat_completion(**_BASE_KWARGS, timeout_seconds=7.0)

    result = asyncio.run(_run())
    assert result["choices"][0]["message"]["content"] == "non-stream ok"
    assert result["request_id"] == "req-chat-1"
    assert call_count == 2


def test_http_client_is_reused_until_aclose(monkeypatch: pytest.MonkeyPatch) -> None:
    created_clients: list[object] = []

    class _ReusableAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.is_closed = False

        async def aclose(self) -> None:
            self.is_closed = True

    def _fake_async_client(**kwargs: object) -> _ReusableAsyncClient:
        reusable_client = _ReusableAsyncClient(**kwargs)
        created_clients.append(reusable_client)
        return reusable_client

    monkeypatch.setattr(
        "app.services.llm_client.httpx.AsyncClient",
        _fake_async_client,
    )

    async def _run():
        client = LLMClient()
        first_client = await client._get_http_client()
        second_client = await client._get_http_client()
        await client.aclose()
        third_client = await client._get_http_client()
        return first_client, second_client, third_client, client._http_client

    first_client, second_client, third_client, active_client = asyncio.run(_run())

    assert first_client is second_client
    assert getattr(first_client, "is_closed") is True
    assert third_client is active_client
    assert third_client is created_clients[1]
    assert len(created_clients) == 2


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


class _Settings:
    def __init__(
        self,
        *,
        llm_client_mode: str = "legacy",
        connect_timeout: float = 1.5,
        model_timeout: float = 9.5,
    ) -> None:
        self.llm_client_mode = llm_client_mode
        self.openai_connect_timeout_seconds = connect_timeout
        self.openai_model_timeout_seconds = model_timeout


def test_legacy_stream_uses_separate_connect_and_model_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_timeout: httpx.Timeout | None = None
    monkeypatch.setattr(
        "app.services.llm_client.get_settings",
        lambda: _Settings(connect_timeout=2.5, model_timeout=99.0),
    )

    async def _run() -> list[str]:
        client = LLMClient()
        body = _make_sse_body(
            ['{"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}']
        )

        def _fake_stream(method, url, **kwargs):
            nonlocal captured_timeout
            _ = (method, url)
            captured_timeout = kwargs["timeout"]
            return _FakeResponse(200, body)

        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.stream = _fake_stream
        client._http_client = mock_http
        return await _collect_stream(client, timeout_seconds=17.0)

    result = asyncio.run(_run())

    assert result == ["ok"]
    assert isinstance(captured_timeout, httpx.Timeout)
    assert captured_timeout.connect == 2.5
    assert captured_timeout.read == 17.0
    assert captured_timeout.write == 2.5
    assert captured_timeout.pool == 2.5


class _FakeAsyncOpenAI:
    created: list["_FakeAsyncOpenAI"] = []
    responses: list[object] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(side_effect=self._create))
        )
        self.created.append(self)

    async def _create(self, **kwargs: object) -> object:
        self.last_create_kwargs = kwargs
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class _SdkObject:
    def __init__(self, payload: dict[str, object], request_id: str | None = None) -> None:
        self._payload = payload
        self._request_id = request_id
        if "usage" in payload:
            self.usage = payload["usage"]

    def model_dump(self) -> dict[str, object]:
        return self._payload


class _AsyncChunkStream:
    def __init__(self, items: list[object]) -> None:
        self.items = items

    async def __aiter__(self):
        for item in self.items:
            if isinstance(item, BaseException):
                raise item
            yield item


def _enable_async_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAsyncOpenAI.created = []
    _FakeAsyncOpenAI.responses = []
    monkeypatch.setattr(
        "app.services.llm_client.get_settings",
        lambda: _Settings(llm_client_mode="async_openai"),
    )
    monkeypatch.setattr("openai.AsyncOpenAI", _FakeAsyncOpenAI)


class _InlineQueue:
    def __init__(self) -> None:
        self.submit_calls: list[dict[str, object]] = []
        self.stream_calls: list[dict[str, object]] = []

    async def submit(self, func, *args, queue_priority=0, **kwargs):
        self.submit_calls.append(
            {"func_name": func.__name__, "queue_priority": queue_priority}
        )
        return await func(*args, **kwargs)

    async def stream(self, func, *args, queue_priority=0, **kwargs):
        self.stream_calls.append(
            {"func_name": func.__name__, "queue_priority": queue_priority}
        )
        async for item in func(*args, **kwargs):
            yield item


def test_openai_base_url_normalization_variants() -> None:
    cases = {
        "https://llm.example": "https://llm.example/v1",
        "https://llm.example/": "https://llm.example/v1",
        "https://llm.example/v1": "https://llm.example/v1",
        "https://llm.example/v1/": "https://llm.example/v1",
        "https://llm.example/v1/chat/completions": "https://llm.example/v1",
        "https://llm.example/v1/v1": "https://llm.example/v1",
        "https://llm.example/v1/v1/chat/completions": "https://llm.example/v1",
    }

    for raw, expected in cases.items():
        assert LLMClient._openai_base_url(raw) == expected
        assert LLMClient._chat_completions_url(raw) == f"{expected}/chat/completions"


def test_queue_wraps_legacy_non_streaming_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inline_queue = _InlineQueue()
    monkeypatch.setattr(
        "app.services.llm_client.get_llm_request_queue",
        lambda: inline_queue,
    )

    async def _run() -> dict[str, object]:
        client = LLMClient()
        request = httpx.Request("POST", "https://llm.example/v1/chat/completions")

        async def _fake_post(url, **kwargs):
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "queued"}}]},
                request=request,
            )

        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.post = _fake_post
        client._http_client = mock_http
        return await client.chat_completion(**_BASE_KWARGS)

    result = asyncio.run(_run())

    assert result["choices"][0]["message"]["content"] == "queued"
    assert inline_queue.submit_calls == [
        {"func_name": "_post_once", "queue_priority": 0}
    ]
    assert inline_queue.stream_calls == []


def test_queue_wraps_legacy_streaming_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inline_queue = _InlineQueue()
    monkeypatch.setattr(
        "app.services.llm_client.get_llm_request_queue",
        lambda: inline_queue,
    )

    async def _run() -> list[str]:
        client = LLMClient()
        body = _make_sse_body(
            ['{"choices":[{"delta":{"content":"stream-queued"},"finish_reason":null}]}']
        )

        def _fake_stream(method, url, **kwargs):
            return _FakeResponse(200, body)

        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.stream = _fake_stream
        client._http_client = mock_http
        return await _collect_stream(client)

    result = asyncio.run(_run())

    assert result == ["stream-queued"]
    assert inline_queue.stream_calls == [
        {"func_name": "_attempt_stream", "queue_priority": 0}
    ]
    assert inline_queue.submit_calls == []


def test_queue_wraps_async_openai_non_streaming_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_async_openai(monkeypatch)
    inline_queue = _InlineQueue()
    monkeypatch.setattr(
        "app.services.llm_client.get_llm_request_queue",
        lambda: inline_queue,
    )

    async def _run() -> dict[str, object]:
        client = LLMClient()
        _FakeAsyncOpenAI.responses.append(
            _SdkObject({"choices": [{"message": {"content": "sdk-queued"}}]})
        )
        return await client.chat_completion(**_BASE_KWARGS)

    result = asyncio.run(_run())

    assert result["choices"][0]["message"]["content"] == "sdk-queued"
    assert inline_queue.submit_calls == [
        {"func_name": "_create_completion", "queue_priority": 0}
    ]
    assert inline_queue.stream_calls == []


def test_queue_wraps_async_openai_streaming_provider_call_and_no_retry_after_yield(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_async_openai(monkeypatch)
    inline_queue = _InlineQueue()
    monkeypatch.setattr(
        "app.services.llm_client.get_llm_request_queue",
        lambda: inline_queue,
    )
    import openai

    async def _run() -> list[str]:
        client = LLMClient()
        request = httpx.Request("POST", "https://llm.example/v1/chat/completions")
        _FakeAsyncOpenAI.responses.append(
            _AsyncChunkStream(
                [
                    _SdkObject(
                        {
                            "choices": [
                                {
                                    "delta": {"content": "partial"},
                                    "finish_reason": None,
                                }
                            ]
                        }
                    ),
                    openai.APIConnectionError(request=request),
                ]
            )
        )
        chunks: list[str] = []
        with pytest.raises(openai.APIConnectionError):
            async for chunk in client.stream_chat_completion(**_BASE_KWARGS):
                if chunk.text_delta:
                    chunks.append(chunk.text_delta)
        return chunks

    chunks = asyncio.run(_run())

    assert chunks == ["partial"]
    assert inline_queue.stream_calls == [
        {"func_name": "_attempt_stream", "queue_priority": 0}
    ]
    assert _FakeAsyncOpenAI.created[0].chat.completions.create.await_count == 1


def test_async_openai_client_uses_shared_http_client_timeouts_and_no_sdk_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_async_openai(monkeypatch)

    async def _run() -> dict[str, object]:
        client = LLMClient()
        shared_http_client = AsyncMock()
        shared_http_client.is_closed = False
        client._http_client = shared_http_client
        _FakeAsyncOpenAI.responses.append(
            _SdkObject(
                {
                    "choices": [
                        {"message": {"content": "ok"}, "finish_reason": "stop"}
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 2,
                        "total_tokens": 3,
                    },
                },
                request_id="req-sdk-1",
            )
        )
        result = await client.chat_completion(**_BASE_KWARGS)
        return {"result": result, "http_client": shared_http_client}

    data = asyncio.run(_run())

    assert data["result"]["request_id"] == "req-sdk-1"
    created = _FakeAsyncOpenAI.created[0]
    assert created.kwargs["api_key"] == "key"
    assert str(created.kwargs["base_url"]).rstrip("/") == "https://llm.example/v1"
    assert created.kwargs["http_client"] is data["http_client"]
    assert created.kwargs["max_retries"] == 0
    timeout = created.kwargs["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 1.5
    assert timeout.read == 9.5


def test_async_openai_non_streaming_uses_caller_timeout_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_async_openai(monkeypatch)

    async def _run() -> _FakeAsyncOpenAI:
        client = LLMClient()
        _FakeAsyncOpenAI.responses.append(_SdkObject({"choices": []}))
        await client.chat_completion(**_BASE_KWARGS, timeout_seconds=20.0)
        return _FakeAsyncOpenAI.created[0]

    created = asyncio.run(_run())

    constructor_timeout = created.kwargs["timeout"]
    assert isinstance(constructor_timeout, httpx.Timeout)
    assert constructor_timeout.connect == 1.5
    assert constructor_timeout.read == 9.5

    request_timeout = created.last_create_kwargs["timeout"]
    assert isinstance(request_timeout, httpx.Timeout)
    assert request_timeout.connect == 1.5
    assert request_timeout.read == 20.0
    assert request_timeout.write == 1.5
    assert request_timeout.pool == 1.5
    assert created.kwargs["max_retries"] == 0


def test_async_openai_non_streaming_timeout_error_reports_caller_timeout_safely(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _enable_async_openai(monkeypatch)
    import openai

    prompt = "private prompt"
    secret_key = "sk-private"

    async def _run() -> None:
        client = LLMClient()
        request = httpx.Request("POST", "https://llm.example/v1/chat/completions")
        for _ in range(3):
            timeout_error = openai.APITimeoutError(request=request)
            timeout_error.__context__ = httpx.ReadTimeout(
                "Authorization: Bearer sk-private saw private prompt",
                request=request,
            )
            _FakeAsyncOpenAI.responses.append(timeout_error)

        with pytest.raises(openai.APITimeoutError) as exc_info:
            with caplog.at_level("WARNING", logger="app.services.llm_client"):
                with patch("app.services.llm_client.asyncio.sleep", new_callable=AsyncMock):
                    await client.chat_completion(
                        base_url="https://llm.example",
                        model="test-model",
                        api_key=secret_key,
                        messages=[{"role": "user", "content": prompt}],
                        timeout_seconds=20.0,
                    )
        assert getattr(exc_info.value, "timeout_layer") == "llm_read"
        assert getattr(exc_info.value, "timeout_seconds") == 20.0
        assert str(exc_info.value) == "LLM timeout layer=llm_read exceeded after 20s"

    asyncio.run(_run())

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "timeout_layer=llm_read" in logged
    assert secret_key not in logged
    assert prompt not in logged
    assert secret_key not in str(caplog.text)
    assert prompt not in str(caplog.text)


def test_async_openai_stream_yields_chunks_usage_and_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_async_openai(monkeypatch)

    async def _run() -> list[object]:
        client = LLMClient()
        _FakeAsyncOpenAI.responses.append(
            _AsyncChunkStream(
                [
                    _SdkObject(
                        {
                            "choices": [
                                {
                                    "delta": {"content": "tok"},
                                    "finish_reason": None,
                                }
                            ]
                        },
                        request_id="req-stream-1",
                    ),
                    _SdkObject(
                        {
                            "choices": [],
                            "usage": {
                                "prompt_tokens": 4,
                                "completion_tokens": 5,
                                "total_tokens": 9,
                            },
                        },
                        request_id="req-stream-1",
                    ),
                    _SdkObject(
                        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                        request_id="req-stream-1",
                    ),
                ]
            )
        )
        chunks = []
        async for chunk in client.stream_chat_completion(**_BASE_KWARGS):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_run())

    assert [chunk.text_delta for chunk in chunks] == ["tok", None, None]
    assert chunks[0].request_id == "req-stream-1"
    assert chunks[1].usage is not None
    assert chunks[1].usage["prompt_tokens"] == 4
    assert chunks[1].usage["completion_tokens"] == 5
    assert chunks[1].usage["total_tokens"] == 9
    assert chunks[2].finish_reason == "stop"


def test_async_openai_stream_retries_before_first_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_async_openai(monkeypatch)
    import openai

    async def _run() -> list[str]:
        client = LLMClient()
        request = httpx.Request("POST", "https://llm.example/v1/chat/completions")
        _FakeAsyncOpenAI.responses.extend(
            [
                openai.APIConnectionError(request=request),
                _AsyncChunkStream(
                    [
                        _SdkObject(
                            {
                                "choices": [
                                    {
                                        "delta": {"content": "after-retry"},
                                        "finish_reason": None,
                                    }
                                ]
                            }
                        )
                    ]
                ),
            ]
        )
        with patch("app.services.llm_client.asyncio.sleep", new_callable=AsyncMock):
            return await _collect_stream(client)

    result = asyncio.run(_run())

    assert result == ["after-retry"]
    assert _FakeAsyncOpenAI.created[0].chat.completions.create.await_count == 2


def test_async_openai_stream_does_not_retry_after_chunk_yielded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_async_openai(monkeypatch)
    import openai

    async def _run() -> list[str]:
        client = LLMClient()
        request = httpx.Request("POST", "https://llm.example/v1/chat/completions")
        _FakeAsyncOpenAI.responses.append(
            _AsyncChunkStream(
                [
                    _SdkObject(
                        {
                            "choices": [
                                {
                                    "delta": {"content": "partial"},
                                    "finish_reason": None,
                                }
                            ]
                        }
                    ),
                    openai.APIConnectionError(request=request),
                ]
            )
        )
        chunks: list[str] = []
        with pytest.raises(openai.APIConnectionError):
            async for chunk in client.stream_chat_completion(**_BASE_KWARGS):
                if chunk.text_delta:
                    chunks.append(chunk.text_delta)
        return chunks

    chunks = asyncio.run(_run())

    assert chunks == ["partial"]
    assert _FakeAsyncOpenAI.created[0].chat.completions.create.await_count == 1


def test_async_openai_reserved_params_cannot_override_required_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_async_openai(monkeypatch)

    async def _run() -> dict[str, object]:
        client = LLMClient()
        _FakeAsyncOpenAI.responses.append(_SdkObject({"choices": []}))
        await client.chat_completion(
            **_BASE_KWARGS,
            params={
                "model": "evil-model",
                "messages": [],
                "stream": True,
                "temperature": 0,
            },
        )
        return _FakeAsyncOpenAI.created[0].last_create_kwargs

    kwargs = asyncio.run(_run())

    assert kwargs["model"] == "test-model"
    assert kwargs["messages"] == _BASE_KWARGS["messages"]
    assert kwargs["stream"] is False
    assert kwargs["temperature"] == 0


def test_legacy_mode_remains_default_httpx_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.llm_client.get_settings",
        lambda: _Settings(llm_client_mode="legacy"),
    )
    openai_constructor = MagicMock()
    monkeypatch.setattr("openai.AsyncOpenAI", openai_constructor)

    async def _run() -> dict[str, object]:
        client = LLMClient()
        request = httpx.Request("POST", "https://llm.example/v1/chat/completions")

        async def _fake_post(url, **kwargs):
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "legacy"}}]},
                request=request,
            )

        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.post = _fake_post
        client._http_client = mock_http
        return await client.chat_completion(**_BASE_KWARGS)

    result = asyncio.run(_run())

    assert result["choices"][0]["message"]["content"] == "legacy"
    openai_constructor.assert_not_called()


def test_redaction_removes_api_key_authorization_prompt_and_completion() -> None:
    secret_key = "sk-test-secret"
    prompt = "sensitive prompt text"
    completion = "sensitive completion text"
    structured_prompt = "structured prompt text"
    raw = (
        "Authorization: Bearer sk-test-secret api_key=sk-test-secret "
        "Cookie: session-token X-Provider-Token: provider-secret "
        "{'Cookie': 'session=abc; csrf=def'} "
        '{"X-Provider-Token":"json-provider-secret"} '
        "sensitive prompt text sensitive completion text structured prompt text"
    )

    redacted = _redact_sensitive_text(
        raw,
        secret_key,
        [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": completion},
            {"role": "user", "content": [{"type": "text", "text": structured_prompt}]},
        ],
    )

    assert secret_key not in redacted
    assert prompt not in redacted
    assert completion not in redacted
    assert structured_prompt not in redacted
    assert "session-token" not in redacted
    assert "session=abc" not in redacted
    assert "csrf=def" not in redacted
    assert "provider-secret" not in redacted
    assert "json-provider-secret" not in redacted


def test_async_openai_errors_are_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_async_openai(monkeypatch)
    prompt = "private prompt"
    secret_key = "sk-private"

    async def _run() -> None:
        client = LLMClient()
        _FakeAsyncOpenAI.responses.append(
            RuntimeError(
                "Authorization: Bearer sk-private saw private prompt and token abc123"
            )
        )
        with pytest.raises(RuntimeError) as exc_info:
            await client.chat_completion(
                base_url="https://llm.example",
                model="test-model",
                api_key=secret_key,
                messages=[{"role": "user", "content": prompt}],
            )
        message = str(exc_info.value)
        assert secret_key not in message
        assert prompt not in message
        assert "Bearer sk-private" not in message

    asyncio.run(_run())



def test_legacy_malformed_stream_log_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    prompt = "private legacy prompt"
    completion = "private legacy completion"
    secret_key = "sk-legacy-secret"

    async def _run() -> list[str]:
        client = LLMClient()
        body = _make_sse_body(
            [
                (
                    "not-json Authorization: Bearer sk-legacy-secret "
                    "private legacy prompt private legacy completion"
                ),
                '{"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}',
            ]
        )

        def _fake_stream(method, url, **kwargs):
            return _FakeResponse(200, body)

        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.stream = _fake_stream
        client._http_client = mock_http
        with caplog.at_level("WARNING", logger="app.services.llm_client"):
            return await _collect_stream(
                client,
                api_key=secret_key,
                messages=[
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": completion},
                ],
            )

    result = asyncio.run(_run())

    assert result == ["ok"]
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert secret_key not in logged
    assert prompt not in logged
    assert completion not in logged
    assert "Bearer sk-legacy-secret" not in logged


def test_legacy_malformed_stream_log_omits_raw_payload(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_payload = "raw malformed completion payload"

    async def _run() -> list[str]:
        client = LLMClient()
        body = _make_sse_body(
            [
                secret_payload,
                '{"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}',
            ]
        )

        def _fake_stream(method, url, **kwargs):
            return _FakeResponse(200, body)

        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.stream = _fake_stream
        client._http_client = mock_http
        with caplog.at_level("WARNING", logger="app.services.llm_client"):
            return await _collect_stream(client)

    result = asyncio.run(_run())

    assert result == ["ok"]
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert secret_payload not in logged
    assert "Skipping malformed SSE chunk from upstream" in logged
