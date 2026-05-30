from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast
import uuid

from fastapi import Response
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
import pytest

from app.api.routes import admin_models, bot_battles, health
from app.core.config import Settings
import app.core.config as config_module
from app.core.security import Principal
from app.models.battle import Battle
from app.schemas.bot import BotBattleCreateAndWaitRequest
from app.services import battle_orchestrator as orchestrator_module
from app.services.battle_orchestrator import BattleOrchestrator, PreparedRun
from app.services.llm_client import LLMClient, LLMStreamChunk, StreamTotalTimeoutError
import app.main as main
from app.utils import redis as redis_utils
from app.utils import tracing
from app.utils.llm_queue import LLMQueueWaitTimeoutError, LLMRequestQueue


class _Settings:
    def __init__(
        self,
        *,
        connect_timeout: float = 1.25,
        model_timeout: float = 7.5,
    ) -> None:
        self.openai_connect_timeout_seconds = connect_timeout
        self.openai_model_timeout_seconds = model_timeout


async def _noop_async() -> None:
    return None


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "app_name": "OpenSakura Arena API (timeout tests)",
        "api_v1_prefix": "/api/v1",
        "cors_allow_origins": ["http://localhost:3000"],
        "access_log_enabled": False,
        "leaderboard_refresh_enabled": False,
        "trust_x_forwarded_for": False,
        "rate_limit_redis_url": "",
        "rate_limit_redis_timeout_seconds": 0.5,
        "web_concurrency": 1,
        "oidc_issuer": "",
        "llm_queue_shutdown_timeout_seconds": 0.05,
        "admin_model_test_timeout_seconds": 20.0,
        "auth_csrf_header_name": "X-CSRF-Token",
        "battle_prepopulation_enabled": False,
        "otlp_disabled": False,
        "otlp_endpoint": "",
        "otel_exporter_otlp_traces_endpoint": "",
        "otel_exporter_otlp_endpoint": "",
        "otlp_auth_header": "",
        "otlp_project_name": "",
        "otel_service_name": "opensakura-arena-timeout-test",
        "otel_resource_attributes": "",
        "otlp_exporter_timeout_seconds": 1.0,
        "otlp_batch_export_timeout_millis": 1000,
        "openinference_hide_inputs": True,
        "openinference_hide_outputs": True,
        "openinference_hide_input_messages": True,
        "openinference_hide_output_messages": True,
        "openinference_hide_input_text": True,
        "openinference_hide_output_text": True,
        "openinference_hide_llm_invocation_parameters": True,
        "otel_instrumentation_genai_capture_message_content": False,
    }
    values.update(overrides)
    return Settings.model_construct(**values)


def _model_stub(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "base_url": "https://gateway.example/v1",
        "model_name": "gpt-test",
        "encrypted_api_key": None,
        "params": None,
        "temperature": None,
        "frequency_penalty": None,
        "presence_penalty": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _LookupDB:
    def __init__(self, model: object | None) -> None:
        self._model = model

    def get(self, _model_type: type[object], key: uuid.UUID) -> object | None:
        if self._model is None:
            return None
        return self._model if getattr(self._model, "id", None) == key else None


def _redacted_text(*values: object) -> str:
    return repr(values)


def test_queue_wait_timeout_layer_stays_queue_owned_and_safe() -> None:
    async def _run() -> str:
        queue = LLMRequestQueue(
            name="timeout_test_queue",
            max_concurrent=1,
            capacity=2,
            wait_timeout_seconds=0.01,
            shutdown_timeout_seconds=0.1,
        )
        blocker = asyncio.Event()
        executed = False

        async def first_provider() -> None:
            await blocker.wait()

        async def queued_provider() -> None:
            nonlocal executed
            executed = True

        first = asyncio.create_task(queue.submit(first_provider))
        await asyncio.sleep(0)
        with pytest.raises(LLMQueueWaitTimeoutError) as exc_info:
            await queue.submit(queued_provider)
        blocker.set()
        await first
        await queue.stop()
        assert executed is False
        assert exc_info.value.timeout_layer == "llm_queue_wait"
        return str(exc_info.value)

    message = asyncio.run(_run())

    assert message == "LLM timeout layer=llm_queue_wait exceeded after 0.01s"
    assert "private prompt" not in message
    assert "sk-private" not in message


def test_battle_owner_timeout_remains_orchestrator_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    orchestrator._battle_running_wait_timeout_seconds = 3
    battle_id = uuid.uuid4()
    wait_for_timeouts: list[float] = []
    retry_calls: list[uuid.UUID] = []
    fail_calls: list[dict[str, object]] = []

    async def fake_execute_owned_battle(**_kwargs: object) -> None:
        return None

    async def fake_wait_for(awaitable: object, *, timeout: float) -> None:
        wait_for_timeouts.append(timeout)
        cast(Any, awaitable).close()
        raise asyncio.TimeoutError

    async def fake_fail_battle_for_timeout(**kwargs: object) -> None:
        fail_calls.append(dict(kwargs))

    monkeypatch.setattr(orchestrator, "_execute_owned_battle", fake_execute_owned_battle)
    monkeypatch.setattr(orchestrator_module.asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(
        orchestrator,
        "_schedule_automatic_retry_if_available",
        lambda *, battle_id: retry_calls.append(battle_id) or False,
    )
    monkeypatch.setattr(orchestrator, "_fail_battle_for_timeout", fake_fail_battle_for_timeout)

    async def fake_close_live_battle(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(orchestrator, "_close_live_battle", fake_close_live_battle)

    asyncio.run(orchestrator._run_owned_battle(battle_id=battle_id, request_id="req"))

    assert wait_for_timeouts == [3.0]
    assert retry_calls == [battle_id]
    assert fail_calls[0]["detail"] == "runtime_timeout"
    assert fail_calls[0]["error_text"] == "Battle timeout layer=battle_owner exceeded after 3s"


def test_bot_wait_timeout_response_shape_remains_unchanged() -> None:
    battle_id = uuid.uuid4()
    battle = SimpleNamespace(id=battle_id, status="running")
    response = bot_battles._to_bot_timeout_response(battle=cast(Battle, battle))

    assert response.model_dump() == {
        "battle_id": str(battle_id),
        "status": "timeout",
        "status_url": f"/api/v1/bot/battles/{battle_id}",
        "result": None,
    }

    principal = Principal(
        is_authenticated=True,
        actor_type="bot",
        user_id=str(uuid.uuid4()),
        service_account_id=str(uuid.uuid4()),
        scopes=("battle:create", "battle:execute"),
    )
    db_battle = SimpleNamespace(id=battle_id, status="running")
    calls: list[int] = []

    class _DB:
        def expire_all(self) -> None:
            return None

    class _Orchestrator:
        async def execute_battle_and_wait(
            self,
            battle_id: uuid.UUID,
            *,
            timeout_seconds: int,
            request_id: str | None = None,
        ) -> str:
            _ = (battle_id, request_id)
            calls.append(timeout_seconds)
            return "timeout"

    async def _run() -> object:
        return await bot_battles.create_and_wait_battle(
            payload=BotBattleCreateAndWaitRequest(timeout_seconds=9),
            request=SimpleNamespace(state=SimpleNamespace(request_id="req-bot")),
            response=Response(),
            idempotency_key=None,
            db=cast(Any, _DB()),
            principal=principal,
            settings=_settings(),
            orchestrator=cast(Any, _Orchestrator()),
        )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(bot_battles, "_create_bot_battle", lambda **_kwargs: db_battle)
        monkeypatch.setattr(bot_battles, "_load_owned_battle", lambda **_kwargs: db_battle)
        monkeypatch.setattr(bot_battles, "_load_battle_runs", lambda **_kwargs: [])
        routed_response = asyncio.run(_run())

    assert calls == [9]
    assert routed_response.model_dump() == response.model_dump()


def test_admin_model_timeout_default_and_override_are_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _model_stub()
    captured: list[float] = []

    class _Client:
        async def chat_completion(self, **kwargs: object) -> dict[str, object]:
            captured.append(cast(float, kwargs["timeout_seconds"]))
            return {"choices": [{"message": {"content": "ok"}}]}

    class _Orchestrator:
        @property
        def llm_client(self) -> _Client:
            return _Client()

    monkeypatch.setattr(admin_models, "get_battle_orchestrator", lambda: _Orchestrator())

    default_response = asyncio.run(
        admin_models.test_model(
            str(model.id),
            db=cast(Any, _LookupDB(model)),
            settings=_settings(),
        )
    )
    override_response = asyncio.run(
        admin_models.test_model(
            str(model.id),
            db=cast(Any, _LookupDB(model)),
            settings=_settings(admin_model_test_timeout_seconds=4.0),
        )
    )

    assert default_response["ok"] is True
    assert override_response["ok"] is True
    assert captured == [20.0, 4.0]


def test_shutdown_drain_timeout_cancels_queued_and_in_flight_llm_work(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _run() -> tuple[bool, int]:
        queue = LLMRequestQueue(
            name="shutdown_timeout_test_queue",
            max_concurrent=1,
            capacity=2,
            wait_timeout_seconds=1.0,
            shutdown_timeout_seconds=0.01,
        )
        started = asyncio.Event()
        cancelled = asyncio.Event()
        release_cancelled = asyncio.Event()

        async def in_flight_provider() -> None:
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                await release_cancelled.wait()
                raise

        async def queued_provider() -> None:
            raise AssertionError("queued provider should not execute")

        running = asyncio.create_task(queue.submit(in_flight_provider))
        await started.wait()
        queued = asyncio.create_task(queue.submit(queued_provider))
        await asyncio.sleep(0)
        with caplog.at_level("WARNING", logger="app.utils.llm_queue"):
            await queue.stop(timeout_seconds=0.0)
        release_cancelled.set()
        for task in (running, queued):
            with pytest.raises(asyncio.CancelledError):
                await task
        return cancelled.is_set(), queue.pending_task_count()

    assert asyncio.run(_run()) == (True, 0)
    logged = _redacted_text([record.getMessage() for record in caplog.records])
    assert "llm_queue_shutdown" in logged
    assert "private prompt" not in logged


def test_fastapi_shutdown_uses_queue_drain_timeout_and_keeps_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[float | None] = []
    settings = _settings(llm_queue_shutdown_timeout_seconds=0.33)

    class _Closable:
        async def aclose(self) -> None:
            return None

    class _Orchestrator:
        llm_client = _Closable()

    async def fake_stop_llm_request_queue(*, timeout_seconds: float | None) -> None:
        calls.append(timeout_seconds)

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(health, "get_settings", lambda: settings)
    monkeypatch.setattr(redis_utils, "get_settings", lambda: settings)
    redis_utils.get_rate_limit_redis_client.cache_clear()
    monkeypatch.setattr(main, "configure_logging", lambda _settings: None)
    monkeypatch.setattr(main, "bootstrap_schema", lambda: None)
    monkeypatch.setattr(main, "acquire_battle_process_lock", lambda: None)
    monkeypatch.setattr(main, "release_battle_process_lock", lambda: None)
    monkeypatch.setattr(main, "stop_llm_request_queue", fake_stop_llm_request_queue)
    monkeypatch.setattr(main, "get_oidc_confidential_client", lambda: _Closable())
    monkeypatch.setattr(main, "get_battle_orchestrator", lambda: _Orchestrator())
    monkeypatch.setattr(main, "close_all_redis_clients", lambda: None)
    monkeypatch.setattr(
        main,
        "get_battle_prepopulation_service",
        lambda: SimpleNamespace(resume_incomplete_jobs=lambda: [], shutdown=_noop_async),
    )
    monkeypatch.setattr(main, "init_tracing", lambda settings: None)
    monkeypatch.setattr(main, "shutdown_tracing", lambda: None)

    with TestClient(main.create_app()):
        pass

    assert calls == [0.33]


def test_otlp_exporter_timeout_is_configured_and_non_blocking() -> None:
    calls: list[dict[str, object]] = []

    class _Exporter:
        def export(self, spans: object) -> SpanExportResult:
            _ = spans
            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:
            return None

    def _factory(**kwargs: object) -> _Exporter:
        calls.append(dict(kwargs))
        return _Exporter()

    try:
        enabled = tracing.init_tracing(
            settings=_settings(
                otlp_endpoint="http://collector.example:4318",
                otlp_exporter_timeout_seconds=2.5,
            ),
            span_exporter_factory=_factory,
            span_processor_factory=lambda exporter: SimpleSpanProcessor(exporter),
            instrument_openai=False,
        )
        assert enabled is True
        assert calls[-1]["timeout"] == 2.5
        with tracing.create_span("timeout_taxonomy", {"timeout_layer": "otlp_export"}):
            pass
    finally:
        tracing.shutdown_tracing()


def test_async_openai_cancelled_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.llm_client.get_settings",
        lambda: _Settings(),
    )

    class _FakeCompletions:
        async def create(self, **_kwargs: object) -> object:
            raise asyncio.CancelledError

    class _FakeOpenAIClient:
        chat = SimpleNamespace(completions=_FakeCompletions())

    async def _run() -> None:
        client = LLMClient()

        async def fake_get_openai_client(**_kwargs: object) -> _FakeOpenAIClient:
            return _FakeOpenAIClient()

        monkeypatch.setattr(client, "_get_openai_client", fake_get_openai_client)
        with pytest.raises(asyncio.CancelledError):
            await client.chat_completion(
                base_url="https://llm.example/v1",
                model="test-model",
                api_key="sk-private",
                messages=[{"role": "user", "content": "private prompt"}],
            )

    asyncio.run(_run())


def test_battle_cancelled_error_propagates_from_run_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    prepared = PreparedRun(
        battle_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        side="A",
        model_id=uuid.uuid4(),
        base_url="https://gateway.example/v1",
        model_name="gpt-test",
        api_key="sk-private",
        messages=[{"role": "user", "content": "private prompt"}],
        params={},
        request_id="req-cancel",
    )

    class _CancellingClient:
        async def stream_chat_completion(self, **_kwargs: object) -> AsyncIterator[LLMStreamChunk]:
            raise asyncio.CancelledError
            yield LLMStreamChunk(text_delta="never")

    setattr(orchestrator, "_llm_client", _CancellingClient())
    monkeypatch.setattr(
        orchestrator,
        "_persist_run_result",
        lambda **_kwargs: pytest.fail("cancellation must not persist generic failure"),
    )

    async def emit(_event: str, _data: object) -> None:
        return None

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(orchestrator._execute_run(prepared=prepared, emit=emit))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            orchestrator._execute_run_buffered(
                state=orchestrator_module._RunStreamState(
                    prepared=prepared,
                    queue=asyncio.Queue(maxsize=1),
                    text_parts=[],
                    raw_parts=[],
                    provider_payloads=[],
                    usage=None,
                    request_id=None,
                    finish_reason=None,
                    error_text=None,
                    latency_ms=None,
                ),
                emit=emit,
            )
        )


def test_stream_total_timeout_layer_is_safe() -> None:
    exc = StreamTotalTimeoutError(timeout_seconds=1.5)

    assert exc.timeout_layer == "llm_stream_total"
    assert str(exc) == "LLM timeout layer=llm_stream_total total wall-clock timeout exceeded after 1.5s"


def test_config_timeout_taxonomy_defaults_are_separate() -> None:
    settings = Settings(_env_file=None)

    assert settings.openai_connect_timeout_seconds == 10.0
    assert settings.openai_model_timeout_seconds == 120.0
    assert settings.llm_queue_wait_timeout_seconds == 30.0
    assert settings.llm_queue_shutdown_timeout_seconds == 10.0
    assert settings.admin_model_test_timeout_seconds == 20.0
    assert settings.battle_running_wait_timeout_seconds == 600
    assert settings.otlp_exporter_timeout_seconds == 30.0
