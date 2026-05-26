from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
import pytest

from app.core import logging as app_logging
from app.services.llm_client import LLMClient, LLMStreamChunk
import app.utils.llm_queue as llm_queue_module
from app.utils.llm_queue import LLMQueueFullError, LLMRequestQueue
from app.utils import tracing
from app.services.battle_orchestrator import BattleOrchestrator, BattleSnapshot, PreparedRun, RunSnapshot


class _RecordingExporter:
    def __init__(self) -> None:
        self.spans: list[Any] = []

    def export(self, spans: object) -> SpanExportResult:
        self.spans.extend(spans)  # type: ignore[arg-type]
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


class _LLMSettings:
    llm_client_mode = "legacy"
    openai_connect_timeout_seconds = 1.0
    openai_model_timeout_seconds = 5.0


def _tracing_settings() -> SimpleNamespace:
    return SimpleNamespace(
        otlp_disabled=False,
        otlp_endpoint="http://collector.example:4318",
        otel_exporter_otlp_traces_endpoint="",
        otel_exporter_otlp_endpoint="",
        otlp_auth_header="",
        otlp_project_name="",
        otel_service_name="opensakura-arena-test",
        otel_resource_attributes="",
        otlp_exporter_timeout_seconds=1.0,
        otlp_batch_export_timeout_millis=1000,
        openinference_hide_inputs=True,
        openinference_hide_outputs=True,
        openinference_hide_input_messages=True,
        openinference_hide_output_messages=True,
        openinference_hide_input_text=True,
        openinference_hide_output_text=True,
        openinference_hide_llm_invocation_parameters=True,
        otel_instrumentation_genai_capture_message_content=False,
    )


def _queue_settings() -> SimpleNamespace:
    return SimpleNamespace(
        max_concurrent_llm_requests=1,
        max_llm_requests=10,
        llm_queue_wait_timeout_seconds=1.0,
        llm_queue_shutdown_timeout_seconds=0.1,
    )


@pytest.fixture(autouse=True)
def _reset_tracing_and_queue(monkeypatch: pytest.MonkeyPatch):
    tracing.shutdown_tracing()
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace._TRACER_PROVIDER_SET_ONCE, "_done", False)
    asyncio.run(llm_queue_module.reset_llm_request_queue_for_tests())
    monkeypatch.setattr(llm_queue_module, "get_settings", _queue_settings)
    yield
    asyncio.run(llm_queue_module.reset_llm_request_queue_for_tests())
    tracing.shutdown_tracing()


def _start_recording_tracing() -> _RecordingExporter:
    exporter = _RecordingExporter()

    def factory(**_kwargs: object) -> _RecordingExporter:
        return exporter

    assert tracing.init_tracing(
        settings=_tracing_settings(),
        span_exporter_factory=factory,
        span_processor_factory=lambda wrapped: SimpleSpanProcessor(wrapped),
        instrument_openai=False,
    )
    return exporter


def _span_payloads(exporter: _RecordingExporter) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for span in exporter.spans:
        payloads.append(
            {
                "name": span.name,
                "trace_id": f"{span.context.trace_id:032x}",
                "attributes": dict(span.attributes or {}),
                "events": [
                    {"name": event.name, "attributes": dict(event.attributes or {})}
                    for event in span.events
                ],
            }
        )
    return payloads


def _span_blob(exporter: _RecordingExporter) -> str:
    return repr(_span_payloads(exporter))


def _make_sse_body(chunks: list[str]) -> bytes:
    parts = [f"data: {chunk}\n\n" for chunk in chunks]
    parts.append("data: [DONE]\n\n")
    return "".join(parts).encode()


class _FakeResponse:
    def __init__(self, status_code: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = MagicMock(status_code=self.status_code)
            raise httpx.HTTPStatusError("error", request=MagicMock(), response=response)

    async def aiter_raw(self) -> AsyncIterator[bytes]:
        yield self._body

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


async def _collect_stream(client: LLMClient, **kwargs: object) -> list[str]:
    chunks: list[str] = []
    async for chunk in client.stream_chat_completion(
        base_url="https://llm.example",
        model="test-model",
        api_key="sk-test-secret",
        messages=[{"role": "user", "content": "PROMPT_BODY_MUST_NOT_EXPORT"}],
        **kwargs,
    ):
        if chunk.text_delta:
            chunks.append(chunk.text_delta)
    return chunks


def test_queue_context_survives_request_id_and_trace_context() -> None:
    exporter = _start_recording_tracing()

    async def run() -> tuple[str, dict[str, str | int] | None, dict[str, str | int] | None]:
        queue = LLMRequestQueue(
            name="trace_context_queue",
            max_concurrent=1,
            capacity=2,
            wait_timeout_seconds=1.0,
            shutdown_timeout_seconds=0.1,
        )

        async def provider() -> tuple[str, dict[str, str | int] | None]:
            return app_logging.get_request_id(), tracing.current_trace_context()

        request_token = app_logging.set_request_id("req-queue-context")
        try:
            with tracing.create_span("queue_context_parent"):
                parent_context = tracing.current_trace_context()
                request_id, provider_context = await queue.submit(provider)
        finally:
            app_logging.clear_request_id(request_token)
            await queue.stop()
        return request_id, parent_context, provider_context

    request_id, parent_context, provider_context = asyncio.run(run())
    assert request_id == "req-queue-context"
    assert parent_context is not None
    assert provider_context is not None
    assert provider_context["trace_id"] == parent_context["trace_id"]

    queue_spans = [
        payload
        for payload in _span_payloads(exporter)
        if payload["name"] in {"opensakura_arena.llm.queue.wait", "opensakura_arena.llm.queue.execute"}
    ]
    assert {payload["name"] for payload in queue_spans} == {
        "opensakura_arena.llm.queue.wait",
        "opensakura_arena.llm.queue.execute",
    }
    assert {payload["trace_id"] for payload in queue_spans} == {parent_context["trace_id"]}
    assert all(
        payload["attributes"].get("request_id") == "req-queue-context"
        for payload in queue_spans
    )


def test_provider_call_retry_timeout_layer_privacy_and_traceparent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = _start_recording_tracing()
    monkeypatch.setattr("app.services.llm_client.get_settings", lambda: _LLMSettings())
    captured_headers: list[dict[str, str]] = []
    call_count = 0

    async def run() -> list[str]:
        nonlocal call_count
        client = LLMClient()
        body = _make_sse_body(
            ['{"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}']
        )

        def fake_stream(_method: str, _url: str, **kwargs: object) -> _FakeResponse:
            nonlocal call_count
            call_count += 1
            captured_headers.append(dict(kwargs["headers"]))
            if call_count == 1:
                raise httpx.ReadTimeout(
                    "PROMPT_BODY_MUST_NOT_EXPORT sk-test-secret private completion"
                )
            return _FakeResponse(200, body)

        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.stream = fake_stream
        client._http_client = mock_http
        request_token = app_logging.set_request_id("req-provider-trace")
        try:
            with tracing.create_span("provider_parent"):
                with patch("app.services.llm_client.asyncio.sleep", new_callable=AsyncMock):
                    return await _collect_stream(client)
        finally:
            app_logging.clear_request_id(request_token)

    assert asyncio.run(run()) == ["ok"]
    assert call_count == 2
    assert all("traceparent" in headers for headers in captured_headers)

    blob = _span_blob(exporter)
    assert "opensakura_arena.llm.provider_call" in blob
    assert "opensakura_arena.llm.retry" in blob
    assert "llm_read" in blob
    assert "req-provider-trace" in blob
    assert "PROMPT_BODY_MUST_NOT_EXPORT" not in blob
    assert "sk-test-secret" not in blob
    assert "private completion" not in blob


def test_stream_upstream_error_span_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    exporter = _start_recording_tracing()
    monkeypatch.setattr("app.services.llm_client.get_settings", lambda: _LLMSettings())

    async def run() -> None:
        client = LLMClient()
        body = _make_sse_body(
            [
                '{"error":{"message":"PROMPT_BODY_MUST_NOT_EXPORT sk-test-secret","type":"upstream"}}'
            ]
        )

        def fake_stream(_method: str, _url: str, **_kwargs: object) -> _FakeResponse:
            return _FakeResponse(200, body)

        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.stream = fake_stream
        client._http_client = mock_http
        with pytest.raises(RuntimeError, match="Upstream error"):
            await _collect_stream(client)

    asyncio.run(run())
    blob = _span_blob(exporter)
    assert "opensakura_arena.llm.upstream_error" in blob
    assert "RuntimeError" in blob
    assert "PROMPT_BODY_MUST_NOT_EXPORT" not in blob
    assert "sk-test-secret" not in blob


def test_queue_full_and_cancellation_spans_preserve_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = _start_recording_tracing()
    monkeypatch.setattr("app.services.llm_client.get_settings", lambda: _LLMSettings())

    async def queue_full() -> None:
        queue = LLMRequestQueue(
            name="queue_full_trace",
            max_concurrent=1,
            capacity=1,
            wait_timeout_seconds=1.0,
            shutdown_timeout_seconds=0.1,
        )
        blocker = asyncio.Event()

        async def provider() -> None:
            await blocker.wait()

        first = asyncio.create_task(queue.submit(provider))
        await asyncio.sleep(0)
        second = asyncio.create_task(queue.submit(provider))
        await asyncio.sleep(0)
        with pytest.raises(LLMQueueFullError):
            await queue.submit(provider)
        blocker.set()
        await asyncio.gather(first, second)
        await queue.stop()

    async def cancellation() -> None:
        client = LLMClient()

        def fake_stream(_method: str, _url: str, **_kwargs: object) -> _FakeResponse:
            raise asyncio.CancelledError

        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.stream = fake_stream
        client._http_client = mock_http
        with pytest.raises(asyncio.CancelledError):
            await _collect_stream(client)

    asyncio.run(queue_full())
    asyncio.run(cancellation())

    blob = _span_blob(exporter)
    assert "opensakura_arena.llm.queue.full" in blob
    assert "queue_full" in blob
    assert "CancelledError" in blob
    assert "cancelled" in blob


def test_battle_and_run_execution_spans_are_privacy_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = _start_recording_tracing()
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    run_id = uuid.uuid4()
    prepared = PreparedRun(
        battle_id=battle_id,
        run_id=run_id,
        side="A",
        model_id=uuid.uuid4(),
        base_url="https://gateway.example/v1",
        model_name="model-a",
        api_key="sk-battle-secret",
        messages=[{"role": "user", "content": "private battle prompt"}],
        params={},
        request_id="req-battle-trace",
    )

    class _StreamingClient:
        async def stream_chat_completion(self, **_kwargs: object) -> AsyncIterator[LLMStreamChunk]:
            yield LLMStreamChunk(text_delta="translated", request_id="upstream-req")
            yield LLMStreamChunk(finish_reason="stop")

    async def emit(_event: str, _data: object) -> None:
        return None

    persist_calls: list[dict[str, object]] = []
    mark_calls: list[dict[str, object]] = []
    emitted: list[tuple[str, object]] = []

    async def record_emit(event: str, data: object) -> None:
        emitted.append((event, data))

    orchestrator._llm_client = _StreamingClient()  # type: ignore[assignment]
    monkeypatch.setattr(
        orchestrator,
        "_persist_run_result",
        lambda **kwargs: persist_calls.append(dict(kwargs)),
    )
    monkeypatch.setattr(
        orchestrator,
        "_load_battle_and_runs",
        lambda _battle_id: (
            BattleSnapshot(
                id=battle_id,
                task_id=uuid.uuid4(),
                status="pending",
                metadata_json=None,
            ),
            [RunSnapshot(id=run_id, battle_id=battle_id, side="A", model_id=prepared.model_id)],
        ),
    )
    monkeypatch.setattr(orchestrator, "_prepare_runs_for_execution", lambda **_: [prepared])
    monkeypatch.setattr(orchestrator, "_execute_runs_synced", AsyncMock(return_value=[True]))
    monkeypatch.setattr(
        orchestrator,
        "_mark_battle_status",
        lambda **kwargs: mark_calls.append(dict(kwargs)),
    )

    async def run() -> None:
        assert await orchestrator._execute_run(prepared=prepared, emit=emit) is True
        await orchestrator._execute_owned_battle(
            battle_id=battle_id,
            emit=record_emit,
            request_id="req-battle-trace",
        )

    asyncio.run(run())

    assert persist_calls[0]["output_text"] == "translated"
    assert mark_calls == [
        {"battle_id": battle_id, "status": "running"},
        {"battle_id": battle_id, "status": "completed"},
    ]
    assert ("battle.completed", {"battle_id": str(battle_id)}) in emitted

    blob = _span_blob(exporter)
    assert "opensakura_arena.run.execute" in blob
    assert "opensakura_arena.battle.execute" in blob
    assert "req-battle-trace" in blob
    assert "model-a" in blob
    assert "private battle prompt" not in blob
    assert "sk-battle-secret" not in blob


def test_queue_wait_timeout_span_records_timeout_layer() -> None:
    exporter = _start_recording_tracing()

    async def run() -> None:
        queue = LLMRequestQueue(
            name="queue_wait_timeout_trace",
            max_concurrent=1,
            capacity=2,
            wait_timeout_seconds=0.01,
            shutdown_timeout_seconds=0.1,
        )
        blocker = asyncio.Event()

        async def first_provider() -> None:
            await blocker.wait()

        async def timed_out_provider() -> None:
            raise AssertionError("timed-out provider should not execute")

        first = asyncio.create_task(queue.submit(first_provider))
        await asyncio.sleep(0)
        with pytest.raises(Exception, match="llm_queue_wait"):
            await queue.submit(timed_out_provider)
        blocker.set()
        await first
        await queue.stop()

    asyncio.run(run())
    blob = _span_blob(exporter)
    assert "opensakura_arena.llm.queue.wait_timeout" in blob
    assert "llm_queue_wait" in blob
