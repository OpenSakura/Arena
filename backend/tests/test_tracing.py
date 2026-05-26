from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
import pytest

from app.api.routes import health
from app.core import logging as app_logging
import app.core.config as config_module
import app.main as main
from app.utils import redis as redis_utils
from app.utils import tracing


class _RecordingExporter:
    def __init__(self) -> None:
        self.spans: list[Any] = []
        self.shutdown_called = False

    def export(self, spans):
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.shutdown_called = True


class _FailingExporter:
    def export(self, spans):
        raise RuntimeError("export failed")

    def shutdown(self) -> None:
        return None


class _HealthyConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def execute(self, _statement: object) -> None:
        return None

    def commit(self) -> None:
        return None


class _HealthyEngine:
    def connect(self) -> _HealthyConnection:
        return _HealthyConnection()


def _settings(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "app_name": "OpenSakura Arena API (tests)",
        "app_env": "test",
        "api_v1_prefix": "/api/v1",
        "cors_allow_origins": ["http://localhost:3000"],
        "access_log_enabled": False,
        "leaderboard_refresh_enabled": False,
        "trust_x_forwarded_for": False,
        "rate_limit_redis_url": "",
        "rate_limit_redis_timeout_seconds": 0.5,
        "web_concurrency": 1,
        "oidc_issuer": "",
        "otlp_disabled": False,
        "otlp_endpoint": "",
        "otel_exporter_otlp_traces_endpoint": "",
        "otel_exporter_otlp_endpoint": "",
        "otlp_auth_header": "",
        "otlp_project_name": "",
        "otel_service_name": "opensakura-arena-test",
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
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def _reset_tracing(monkeypatch):
    tracing.shutdown_tracing()
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace._TRACER_PROVIDER_SET_ONCE, "_done", False)
    yield
    tracing.shutdown_tracing()


def _factory_for(exporter: _RecordingExporter | _FailingExporter):
    calls: list[dict[str, Any]] = []

    def _factory(**kwargs: Any):
        calls.append(kwargs)
        return exporter

    return _factory, calls


def _simple_processor(exporter: Any) -> SimpleSpanProcessor:
    return SimpleSpanProcessor(exporter)


def _span_blob(exporter: _RecordingExporter) -> str:
    payload: list[Any] = []
    for span in exporter.spans:
        payload.append(
            {
                "name": span.name,
                "attributes": dict(span.attributes),
                "events": [
                    {"name": event.name, "attributes": dict(event.attributes or {})}
                    for event in span.events
                ],
                "resource": dict(span.resource.attributes),
            }
        )
    return repr(payload)


def test_init_tracing_is_noop_when_no_endpoint_is_configured() -> None:
    exporter = _RecordingExporter()
    factory, calls = _factory_for(exporter)

    enabled = tracing.init_tracing(
        settings=_settings(),
        span_exporter_factory=factory,
        instrument_openai=False,
    )

    assert enabled is False
    assert tracing.is_tracing_enabled() is False
    assert calls == []


def test_otlp_disabled_overrides_configured_endpoints() -> None:
    exporter = _RecordingExporter()
    factory, calls = _factory_for(exporter)

    enabled = tracing.init_tracing(
        settings=_settings(
            otlp_disabled=True,
            otlp_endpoint="http://collector.example:4318",
            otel_exporter_otlp_traces_endpoint="http://collector.example/custom",
            otel_exporter_otlp_endpoint="http://fallback.example:4318",
        ),
        span_exporter_factory=factory,
        instrument_openai=False,
    )

    assert enabled is False
    assert tracing.is_tracing_enabled() is False
    assert calls == []


def test_endpoint_precedence_and_http_base_path_normalization() -> None:
    exporter = _RecordingExporter()
    factory, calls = _factory_for(exporter)

    assert tracing.init_tracing(
        settings=_settings(
            otlp_endpoint="http://primary.example:4318",
            otel_exporter_otlp_traces_endpoint="http://traces.example/custom",
            otel_exporter_otlp_endpoint="http://base.example:4318",
        ),
        span_exporter_factory=factory,
        instrument_openai=False,
    )
    assert calls[-1]["endpoint"] == "http://primary.example:4318/v1/traces"

    tracing.shutdown_tracing()
    assert tracing.init_tracing(
        settings=_settings(
            otel_exporter_otlp_traces_endpoint="http://traces.example/custom",
            otel_exporter_otlp_endpoint="http://base.example:4318",
        ),
        span_exporter_factory=factory,
        instrument_openai=False,
    )
    assert calls[-1]["endpoint"] == "http://traces.example/custom"

    tracing.shutdown_tracing()
    assert tracing.init_tracing(
        settings=_settings(otel_exporter_otlp_endpoint="http://base.example:4318/root"),
        span_exporter_factory=factory,
        instrument_openai=False,
    )
    assert calls[-1]["endpoint"] == "http://base.example:4318/root/v1/traces"

    tracing.shutdown_tracing()
    assert tracing.init_tracing(
        settings=_settings(otel_exporter_otlp_endpoint="http://base.example:4318/v1/traces"),
        span_exporter_factory=factory,
        instrument_openai=False,
    )
    assert calls[-1]["endpoint"] == "http://base.example:4318/v1/traces"


def test_auth_header_is_passed_to_exporter_without_span_leakage() -> None:
    exporter = _RecordingExporter()
    factory, calls = _factory_for(exporter)

    assert tracing.init_tracing(
        settings=_settings(
            otlp_endpoint="http://collector.example:4318",
            otlp_auth_header="Bearer collector-token-secret",
        ),
        span_exporter_factory=factory,
        span_processor_factory=_simple_processor,
        instrument_openai=False,
    )

    assert calls[-1]["headers"] == {"Authorization": "Bearer collector-token-secret"}
    with tracing.create_span("auth_header", {"safe.attribute": "visible"}):
        pass

    exported = _span_blob(exporter)
    assert "collector-token-secret" not in exported
    assert "visible" in exported


def test_span_helpers_add_request_id_context_and_sanitize_events() -> None:
    exporter = _RecordingExporter()
    factory, _calls = _factory_for(exporter)

    assert tracing.init_tracing(
        settings=_settings(otlp_endpoint="http://collector.example:4318"),
        span_exporter_factory=factory,
        span_processor_factory=_simple_processor,
        instrument_openai=False,
    )

    token = app_logging.set_request_id("req-helper")
    try:
        with tracing.traced_span("helper", {"safe.attribute": "visible"}):
            context = tracing.current_trace_context()
            assert context is not None
            tracing.add_span_event(
                "helper.event",
                {
                    "source_text": "SECRET_SOURCE_TEXT",
                    "safe.event": "event-visible",
                },
            )
    finally:
        app_logging.clear_request_id(token)

    exported = _span_blob(exporter)
    assert "req-helper" in exported
    assert "visible" in exported
    assert "event-visible" in exported
    assert "SECRET_SOURCE_TEXT" not in exported


def test_exporter_setup_failure_is_non_fatal() -> None:
    def _raising_factory(**_kwargs: Any):
        raise RuntimeError("factory failed")

    enabled = tracing.init_tracing(
        settings=_settings(otlp_endpoint="http://collector.example:4318"),
        span_exporter_factory=_raising_factory,
        instrument_openai=False,
    )

    assert enabled is False
    assert tracing.is_tracing_enabled() is False


def test_export_failure_does_not_escape_span_context() -> None:
    exporter = _FailingExporter()
    factory, _calls = _factory_for(exporter)

    assert tracing.init_tracing(
        settings=_settings(otlp_endpoint="http://collector.example:4318"),
        span_exporter_factory=factory,
        span_processor_factory=_simple_processor,
        instrument_openai=False,
    )

    with tracing.create_span("export_failure", {"safe.attribute": "visible"}):
        pass

    tracing.shutdown_tracing()


def test_openai_instrumentation_can_be_disabled(monkeypatch) -> None:
    exporter = _RecordingExporter()
    factory, _calls = _factory_for(exporter)

    def _unexpected_instrumentation(_provider: Any, _settings: Any) -> None:
        raise AssertionError("instrumentation should not run")

    monkeypatch.setattr(tracing, "_instrument_openai", _unexpected_instrumentation)

    assert tracing.init_tracing(
        settings=_settings(otlp_endpoint="http://collector.example:4318"),
        span_exporter_factory=factory,
        span_processor_factory=_simple_processor,
        instrument_openai=False,
    )


def test_openai_instrumentation_failure_is_non_fatal(monkeypatch) -> None:
    exporter = _RecordingExporter()
    factory, _calls = _factory_for(exporter)

    def _broken_instrumentation(_provider: Any, _settings: Any) -> None:
        raise RuntimeError("instrumentation failed")

    monkeypatch.setattr(tracing, "_instrument_openai", _broken_instrumentation)

    assert tracing.init_tracing(
        settings=_settings(otlp_endpoint="http://collector.example:4318"),
        span_exporter_factory=factory,
        span_processor_factory=_simple_processor,
        instrument_openai=True,
    )
    assert tracing.is_tracing_enabled() is True


def test_privacy_filter_removes_prompt_completion_and_secret_values() -> None:
    exporter = _RecordingExporter()
    factory, _calls = _factory_for(exporter)

    assert tracing.init_tracing(
        settings=_settings(otlp_endpoint="http://collector.example:4318"),
        span_exporter_factory=factory,
        span_processor_factory=_simple_processor,
        instrument_openai=False,
    )

    with tracing.create_span(
        "privacy",
        {
            "llm.prompt": "SECRET_PROMPT_BODY",
            "llm.completion": "SECRET_COMPLETION_BODY",
            "openai.api_key": "sk-secret-api-key",
            "authorization": "Bearer SECRET_AUTH_HEADER",
            "cookie": "session=SECRET_COOKIE",
            "provider_token": "SECRET_PROVIDER_TOKEN",
            "safe.attribute": "visible-value",
        },
    ):
        tracing.add_span_event(
            "privacy.event",
            {
                "message.content": "SECRET_PROMPT_BODY",
                "completion": "SECRET_COMPLETION_BODY",
                "api_key": "sk-secret-api-key",
                "safe.event": "event-visible",
            },
        )

    exported = _span_blob(exporter)
    assert "SECRET_PROMPT_BODY" not in exported
    assert "SECRET_COMPLETION_BODY" not in exported
    assert "sk-secret-api-key" not in exported
    assert "SECRET_AUTH_HEADER" not in exported
    assert "SECRET_COOKIE" not in exported
    assert "SECRET_PROVIDER_TOKEN" not in exported
    assert "visible-value" in exported
    assert "event-visible" in exported


def test_openai_sdk_spans_do_not_export_prompt_completion_or_api_key() -> None:
    import openai

    exporter = _RecordingExporter()
    factory, _calls = _factory_for(exporter)
    prompt = "PROMPT_BODY_MUST_NOT_EXPORT"
    completion = "COMPLETION_BODY_MUST_NOT_EXPORT"
    api_key = "sk-api-key-must-not-export"

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {api_key}"
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": completion},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    assert tracing.init_tracing(
        settings=_settings(otlp_endpoint="http://collector.example:4318"),
        span_exporter_factory=factory,
        span_processor_factory=_simple_processor,
        instrument_openai=True,
    )

    client = openai.OpenAI(
        api_key=api_key,
        base_url="https://openai.example.test/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(_handler)),
    )
    response = client.chat.completions.create(
        model="gpt-test",
        messages=[{"role": "user", "content": prompt}],
    )

    assert response.choices[0].message.content == completion
    exported = _span_blob(exporter)
    assert prompt not in exported
    assert completion not in exported
    assert api_key not in exported
    assert "gpt-test" in exported


def _create_main_test_app(monkeypatch, settings: SimpleNamespace):
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(health, "get_settings", lambda: settings)
    monkeypatch.setattr(redis_utils, "get_settings", lambda: settings)
    redis_utils.get_rate_limit_redis_client.cache_clear()
    monkeypatch.setattr(main, "configure_logging", lambda _settings: None)
    monkeypatch.setattr(main, "bootstrap_schema", lambda: None)
    monkeypatch.setattr(main, "acquire_battle_process_lock", lambda: None)
    monkeypatch.setattr(main, "release_battle_process_lock", lambda: None)

    import app.db.session as session_module

    session_module._engine = None
    session_module._SessionLocal = None
    monkeypatch.setattr(session_module, "get_engine", lambda: _HealthyEngine())
    return main.create_app()


def test_main_lifespan_initializes_and_shuts_down_tracing(monkeypatch) -> None:
    calls: list[str] = []
    settings = _settings()
    monkeypatch.setattr(
        main,
        "init_tracing",
        lambda *, settings: calls.append(f"init:{settings.otel_service_name}") or True,
    )
    monkeypatch.setattr(main, "shutdown_tracing", lambda: calls.append("shutdown"))

    app = _create_main_test_app(monkeypatch, settings)

    with TestClient(app) as client:
        response = client.get("/api/v1/livez", headers={"X-Request-ID": "trace-test"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "trace-test"
    assert calls == ["init:opensakura-arena-test", "shutdown"]


def test_main_lifespan_keeps_serving_when_tracing_init_fails(monkeypatch) -> None:
    def _broken_init(*, settings: Any) -> bool:
        raise RuntimeError("tracing init failed")

    calls: list[str] = []
    monkeypatch.setattr(main, "init_tracing", _broken_init)
    monkeypatch.setattr(main, "shutdown_tracing", lambda: calls.append("shutdown"))

    app = _create_main_test_app(monkeypatch, _settings())

    with TestClient(app) as client:
        response = client.get("/api/v1/livez", headers={"X-Request-ID": "trace-test"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "trace-test"
    assert calls == ["shutdown"]
