from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
import logging
import os
import sys
from typing import Any
from urllib.parse import urlparse

from app.core.config import Settings, get_settings


logger = logging.getLogger(__name__)

_TRACES_PATH = "/v1/traces"

_tracer_provider: Any | None = None
_span_processor: Any | None = None
_tracing_enabled = False
_instrumentors: list[Any] = []


def _setting(settings: Any, name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def _str_setting(settings: Any, name: str, default: str = "") -> str:
    value = _setting(settings, name, default)
    if value is None:
        return default
    return str(value).strip()


def _bool_setting(settings: Any, name: str, default: bool) -> bool:
    value = _setting(settings, name, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _bool_env(value: bool) -> str:
    return "true" if value else "false"


def _normalize_otlp_http_traces_endpoint(raw_endpoint: str, *, append_path: bool) -> str:
    endpoint = raw_endpoint.strip()
    if not endpoint or not append_path:
        return endpoint

    parsed = urlparse(endpoint)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return endpoint
    if parsed.query or parsed.fragment:
        return endpoint
    if parsed.path.rstrip("/").endswith(_TRACES_PATH):
        return endpoint.rstrip("/")
    return f"{endpoint.rstrip('/')}{_TRACES_PATH}"


def _resolve_endpoint(
    settings: Any,
    explicit_endpoint: str | None,
) -> tuple[str, str]:
    if explicit_endpoint is not None:
        endpoint = _normalize_otlp_http_traces_endpoint(
            explicit_endpoint,
            append_path=True,
        )
        return endpoint, "explicit"

    candidates = (
        ("OTLP_ENDPOINT", _str_setting(settings, "otlp_endpoint"), True),
        (
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
            _str_setting(settings, "otel_exporter_otlp_traces_endpoint"),
            False,
        ),
        (
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            _str_setting(settings, "otel_exporter_otlp_endpoint"),
            True,
        ),
    )

    for source, raw_endpoint, append_path in candidates:
        if raw_endpoint:
            endpoint = _normalize_otlp_http_traces_endpoint(
                raw_endpoint,
                append_path=append_path,
            )
            return endpoint, source
    return "", "none"


def _coerce_span_attribute_value(key: str, value: Any) -> Any:
    _ = key
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Sequence) and not isinstance(
        value,
        (bytes, bytearray, str),
    ):
        values: list[bool | int | float | str] = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, (bool, int, float, str)):
                values.append(item)
            else:
                values.append(str(item))
        return tuple(values)
    return str(value)


def _current_request_id() -> str | None:
    try:
        from app.core.logging import get_request_id

        request_id = get_request_id()
    except Exception:
        return None
    if not request_id or request_id == "-":
        return None
    return request_id


def _span_safe_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    combined: dict[str, Any] = dict(attributes or {})
    if not combined.get("request_id"):
        request_id = _current_request_id()
        if request_id:
            combined["request_id"] = request_id

    safe: dict[str, Any] = {}
    for key, value in combined.items():
        coerced = _coerce_span_attribute_value(str(key), value)
        if coerced is not None:
            safe[str(key)] = coerced
    return safe


def _set_attributes_on_span(span: Any, attributes: Mapping[str, Any] | None) -> None:
    if span is None or not hasattr(span, "set_attribute"):
        return
    for key, value in _span_safe_attributes(attributes).items():
        with suppress(Exception):
            span.set_attribute(key, value)


def _current_span() -> Any | None:
    if not _tracing_enabled:
        return None

    try:
        from opentelemetry import trace
    except Exception:
        return None

    try:
        return trace.get_current_span()
    except Exception:
        return None


def _parse_resource_attributes(raw_attributes: str) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    for item in raw_attributes.split(","):
        key, separator, value = item.partition("=")
        key = key.strip()
        if not separator or not key:
            continue
        attributes[key] = value.strip()
    return attributes


def _resource_attributes(settings: Any, service_name: str) -> dict[str, Any]:
    attributes = _parse_resource_attributes(
        _str_setting(settings, "otel_resource_attributes"),
    )
    attributes["service.name"] = service_name

    project_name = _str_setting(settings, "otlp_project_name")
    if project_name:
        attributes["service.namespace"] = project_name
        try:
            from openinference.semconv.resource import ResourceAttributes

            attributes[ResourceAttributes.PROJECT_NAME] = project_name
        except Exception:
            pass

    return attributes


def _configure_openai_privacy(settings: Any) -> None:
    hide_inputs = _bool_setting(settings, "openinference_hide_inputs", True)
    hide_outputs = _bool_setting(settings, "openinference_hide_outputs", True)

    values = {
        "OPENINFERENCE_HIDE_INPUTS": hide_inputs,
        "OPENINFERENCE_HIDE_OUTPUTS": hide_outputs,
        "OPENINFERENCE_HIDE_INPUT_MESSAGES": _bool_setting(
            settings,
            "openinference_hide_input_messages",
            True,
        ),
        "OPENINFERENCE_HIDE_OUTPUT_MESSAGES": _bool_setting(
            settings,
            "openinference_hide_output_messages",
            True,
        ),
        "OPENINFERENCE_HIDE_INPUT_TEXT": _bool_setting(
            settings,
            "openinference_hide_input_text",
            True,
        ),
        "OPENINFERENCE_HIDE_OUTPUT_TEXT": _bool_setting(
            settings,
            "openinference_hide_output_text",
            True,
        ),
        "OPENINFERENCE_HIDE_LLM_INVOCATION_PARAMETERS": _bool_setting(
            settings,
            "openinference_hide_llm_invocation_parameters",
            True,
        ),
        "OPENINFERENCE_HIDE_LLM_TOOLS": hide_inputs,
        "OPENINFERENCE_HIDE_PROMPTS": hide_inputs,
        "OPENINFERENCE_HIDE_CHOICES": hide_outputs,
        "OPENINFERENCE_HIDE_EMBEDDINGS_TEXT": hide_inputs,
        "OPENINFERENCE_HIDE_EMBEDDINGS_VECTORS": True,
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": _bool_setting(
            settings,
            "otel_instrumentation_genai_capture_message_content",
            False,
        ),
    }

    for name, value in values.items():
        os.environ[name] = _bool_env(value)


def _openinference_trace_config(settings: Any) -> Any:
    from openinference.instrumentation import TraceConfig

    hide_inputs = _bool_setting(settings, "openinference_hide_inputs", True)
    hide_outputs = _bool_setting(settings, "openinference_hide_outputs", True)
    return TraceConfig(
        hide_inputs=hide_inputs,
        hide_outputs=hide_outputs,
        hide_input_messages=_bool_setting(
            settings,
            "openinference_hide_input_messages",
            True,
        ),
        hide_output_messages=_bool_setting(
            settings,
            "openinference_hide_output_messages",
            True,
        ),
        hide_input_text=_bool_setting(settings, "openinference_hide_input_text", True),
        hide_output_text=_bool_setting(settings, "openinference_hide_output_text", True),
        hide_llm_invocation_parameters=_bool_setting(
            settings,
            "openinference_hide_llm_invocation_parameters",
            True,
        ),
        hide_llm_tools=hide_inputs,
        hide_prompts=hide_inputs,
        hide_choices=hide_outputs,
        hide_embeddings_text=hide_inputs,
        hide_embeddings_vectors=True,
    )


def _instrument_openai(provider: Any, settings: Any) -> None:
    _configure_openai_privacy(settings)

    try:
        from openinference.instrumentation.openai import OpenAIInstrumentor

        instrumentor = OpenAIInstrumentor()
        instrumentor.instrument(
            tracer_provider=provider,
            config=_openinference_trace_config(settings),
        )
        _instrumentors.append(instrumentor)
        logger.info("OpenInference OpenAI instrumentation enabled")
    except Exception as exc:
        logger.info(
            "OpenInference OpenAI instrumentation unavailable",
            extra={"error_type": type(exc).__name__},
        )

    try:
        from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor

        instrumentor = OpenAIInstrumentor()
        instrumentor.instrument(tracer_provider=provider)
        _instrumentors.append(instrumentor)
        logger.info("OpenTelemetry OpenAI instrumentation enabled")
    except Exception as exc:
        logger.info(
            "OpenTelemetry OpenAI instrumentation unavailable",
            extra={"error_type": type(exc).__name__},
        )


def _detach_span_processor(provider: Any, processor: Any) -> None:
    active_processor = getattr(provider, "_active_span_processor", None)
    processors = getattr(active_processor, "_span_processors", None)
    if not isinstance(processors, tuple):
        return
    try:
        active_processor._span_processors = tuple(
            item for item in processors if item is not processor
        )
    except Exception:
        return


def init_tracing(
    *,
    service_name: str | None = None,
    endpoint: str | None = None,
    settings: Settings | Any | None = None,
    instrument_openai: bool = True,
    span_exporter_factory: Callable[..., Any] | None = None,
    span_processor_factory: Callable[[Any], Any] | None = None,
) -> bool:
    global _span_processor, _tracer_provider, _tracing_enabled

    if _tracing_enabled:
        shutdown_tracing()

    if settings is None:
        try:
            settings = get_settings()
        except Exception as exc:
            logger.warning(
                "Tracing disabled: settings could not be loaded",
                extra={"error_type": type(exc).__name__},
            )
            return False

    if _bool_setting(settings, "otlp_disabled", False):
        _tracing_enabled = False
        logger.info("Tracing disabled: OTLP_DISABLED is true")
        return False

    resolved_endpoint, endpoint_source = _resolve_endpoint(settings, endpoint)
    if not resolved_endpoint:
        _tracing_enabled = False
        logger.info("Tracing disabled: no OTLP endpoint configured")
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception as exc:
        _tracing_enabled = False
        logger.info(
            "Tracing disabled: OpenTelemetry dependencies unavailable",
            extra={"error_type": type(exc).__name__},
        )
        return False

    resolved_service_name = (
        service_name or _str_setting(settings, "otel_service_name") or "opensakura-arena-backend"
    )
    resource = Resource.create(_resource_attributes(settings, resolved_service_name))

    try:
        existing_provider = trace.get_tracer_provider()
        if hasattr(existing_provider, "add_span_processor"):
            provider = existing_provider
        else:
            provider = TracerProvider(resource=resource)
            trace.set_tracer_provider(provider)

        auth_header = _str_setting(settings, "otlp_auth_header")
        headers = {"Authorization": auth_header} if auth_header else None
        exporter_factory = span_exporter_factory or OTLPSpanExporter
        exporter = exporter_factory(
            endpoint=resolved_endpoint,
            headers=headers,
            timeout=float(_setting(settings, "otlp_exporter_timeout_seconds", 30.0)),
        )
        if span_processor_factory is not None:
            processor = span_processor_factory(exporter)
        else:
            processor = BatchSpanProcessor(
                exporter,
                export_timeout_millis=int(
                    _setting(settings, "otlp_batch_export_timeout_millis", 10000)
                ),
            )
        provider.add_span_processor(processor)
    except Exception as exc:
        _tracing_enabled = False
        logger.warning(
            "Tracing disabled: OTLP exporter setup failed",
            extra={"error_type": type(exc).__name__},
        )
        return False

    _tracer_provider = provider
    _span_processor = processor
    _tracing_enabled = True

    if instrument_openai:
        try:
            _instrument_openai(provider, settings)
        except Exception as exc:
            logger.warning(
                "OpenAI instrumentation failed; tracing remains enabled",
                extra={"error_type": type(exc).__name__},
            )

    logger.info(
        "OpenTelemetry tracing initialized",
        extra={
            "endpoint_source": endpoint_source,
            "service_name": resolved_service_name,
        },
    )
    return True


def shutdown_tracing() -> None:
    global _instrumentors, _span_processor, _tracer_provider, _tracing_enabled

    if not _tracing_enabled and _span_processor is None and not _instrumentors:
        return

    for instrumentor in reversed(_instrumentors):
        try:
            uninstrument = getattr(instrumentor, "uninstrument", None)
            if uninstrument is not None:
                uninstrument()
        except Exception as exc:
            logger.warning(
                "OpenAI instrumentation shutdown failed",
                extra={"error_type": type(exc).__name__},
            )
    _instrumentors = []

    if _span_processor is not None:
        processor = _span_processor
        provider = _tracer_provider
        if provider is not None:
            _detach_span_processor(provider, processor)
        try:
            force_flush = getattr(processor, "force_flush", None)
            if force_flush is not None:
                force_flush(timeout_millis=30000)
        except Exception as exc:
            logger.warning(
                "Tracing force flush failed",
                extra={"error_type": type(exc).__name__},
            )
        try:
            processor.shutdown()
        except Exception as exc:
            logger.warning(
                "Failed to shutdown tracing cleanly",
                extra={"error_type": type(exc).__name__},
            )

    _span_processor = None
    _tracer_provider = None
    _tracing_enabled = False
    logger.info("Tracing shutdown complete")


def is_tracing_enabled() -> bool:
    return _tracing_enabled


@contextmanager
def create_span(
    name: str,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Any | None]:
    if not _tracing_enabled:
        yield None
        return

    try:
        from opentelemetry import trace
    except Exception:
        yield None
        return

    try:
        tracer = trace.get_tracer("opensakura-arena-backend")
        span_context = tracer.start_as_current_span(f"opensakura_arena.{name}")
    except Exception:
        yield None
        return

    try:
        span = span_context.__enter__()
    except Exception:
        yield None
        return

    try:
        _set_attributes_on_span(span, attributes)
        yield span
    finally:
        with suppress(Exception):
            span_context.__exit__(*sys.exc_info())


@contextmanager
def traced_span(
    name: str,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Any | None]:
    with create_span(name, attributes) as span:
        try:
            yield span
        except asyncio.CancelledError:
            set_span_attributes({"status": "cancelled", "error.type": "CancelledError"})
            add_span_event("cancelled", {"error.type": "CancelledError"})
            raise
        except Exception as exc:
            record_exception(exc)
            raise


def current_trace_context() -> dict[str, str | int] | None:
    span = _current_span()
    if span is None:
        return None
    try:
        context = span.get_span_context()
    except Exception:
        return None
    if not getattr(context, "is_valid", False):
        return None
    return {
        "trace_id": format(context.trace_id, "032x"),
        "span_id": format(context.span_id, "016x"),
        "trace_flags": int(context.trace_flags),
    }


def inject_trace_context(headers: dict[str, str]) -> dict[str, str]:
    if not _tracing_enabled:
        return headers
    try:
        from opentelemetry.propagate import inject

        inject(headers)
    except Exception:
        return headers
    return headers


def record_exception(exc: BaseException) -> None:
    if not _tracing_enabled:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.trace.status import Status, StatusCode
    except Exception:
        return

    span = trace.get_current_span()
    if span is None:
        return
    if hasattr(span, "record_exception"):
        with suppress(Exception):
            span.record_exception(
                exc,
                attributes={"exception.type": type(exc).__name__},
                escaped=False,
            )
    if hasattr(span, "set_status"):
        with suppress(Exception):
            span.set_status(Status(StatusCode.ERROR))


def add_span_event(name: str, attributes: dict[str, Any] | None = None) -> None:
    if not _tracing_enabled:
        return

    try:
        from opentelemetry import trace
    except Exception:
        return

    span = trace.get_current_span()
    if span is not None and hasattr(span, "add_event"):
        with suppress(Exception):
            span.add_event(name, attributes=_span_safe_attributes(attributes))


def set_span_attributes(attributes: dict[str, Any]) -> None:
    if not _tracing_enabled:
        return

    try:
        from opentelemetry import trace
    except Exception:
        return

    span = trace.get_current_span()
    _set_attributes_on_span(span, attributes)
