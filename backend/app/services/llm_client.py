"""app.services.llm_client

Client for calling the existing LLM gateway.

Notes:
- The backend is API-only; it calls the gateway's OpenAI-compatible endpoint.
- Support streaming (token deltas) so we can forward them via SSE.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings
from app.utils.llm_queue import get_llm_request_queue
from app.utils.tracing import add_span_event, inject_trace_context, set_span_attributes, traced_span
from app.utils.usage import normalize_usage

logger = logging.getLogger(__name__)

_TIMEOUT_LAYER_LLM_CONNECT = "llm_connect"
_TIMEOUT_LAYER_LLM_READ = "llm_read"
_TIMEOUT_LAYER_LLM_WRITE = "llm_write"
_TIMEOUT_LAYER_LLM_POOL = "llm_pool"
_TIMEOUT_LAYER_LLM_STREAM_TOTAL = "llm_stream_total"

# Transient HTTP status codes that are safe to retry before any response
# body has been consumed.
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_MAX_RETRIES = 2
_RETRY_BASE_DELAY = 1.0
_RETRY_MAX_DELAY = 5.0
_REDACTED_PROVIDER_VALUE = "[REDACTED]"
_SENSITIVE_PROVIDER_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "cookie",
    "set_cookie",
    "client_secret",
    "secret",
    "password",
    "access_token",
    "refresh_token",
    "id_token",
    "provider_token",
    "x_provider_token",
}


class StreamTotalTimeoutError(TimeoutError):
    """Raised when the overall wall-clock time for a streaming request exceeds
    the configured limit."""

    def __init__(self, *, timeout_seconds: float) -> None:
        super().__init__(
            "LLM timeout layer="
            f"{_TIMEOUT_LAYER_LLM_STREAM_TOTAL} total wall-clock timeout exceeded "
            f"after {timeout_seconds:.3g}s"
        )
        self.timeout_layer = _TIMEOUT_LAYER_LLM_STREAM_TOTAL
        self.timeout_seconds = timeout_seconds


def _httpx_timeout(*, model_timeout_seconds: float) -> httpx.Timeout:
    connect_timeout = max(float(get_settings().openai_connect_timeout_seconds), 0.0)
    model_timeout = max(float(model_timeout_seconds), 0.0)
    short_io_timeout = min(connect_timeout, model_timeout)
    return httpx.Timeout(
        connect=connect_timeout,
        read=model_timeout,
        write=short_io_timeout,
        pool=short_io_timeout,
    )


def _safe_url_label(raw_url: str) -> str:
    try:
        parsed = urlparse(raw_url)
        if not parsed.scheme or not parsed.hostname:
            return "configured_endpoint"
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return "configured_endpoint"
    return f"{parsed.scheme}://{parsed.hostname}{port}"


def _timeout_layer_for_httpx_exception(exc: BaseException | None) -> str | None:
    if isinstance(exc, httpx.ConnectTimeout):
        return _TIMEOUT_LAYER_LLM_CONNECT
    if isinstance(exc, httpx.ReadTimeout):
        return _TIMEOUT_LAYER_LLM_READ
    if isinstance(exc, httpx.WriteTimeout):
        return _TIMEOUT_LAYER_LLM_WRITE
    if isinstance(exc, httpx.PoolTimeout):
        return _TIMEOUT_LAYER_LLM_POOL
    return None


def _timeout_seconds_for_layer(timeout: httpx.Timeout, timeout_layer: str) -> float:
    if timeout_layer == _TIMEOUT_LAYER_LLM_CONNECT:
        value = timeout.connect
    elif timeout_layer == _TIMEOUT_LAYER_LLM_READ:
        value = timeout.read
    elif timeout_layer == _TIMEOUT_LAYER_LLM_WRITE:
        value = timeout.write
    elif timeout_layer == _TIMEOUT_LAYER_LLM_POOL:
        value = timeout.pool
    else:
        value = None
    return 0.0 if value is None else float(value)


def _llm_trace_attributes(
    *,
    operation: str,
    provider: str,
    model: str,
    base_url: str,
    attempt: int | None = None,
    timeout_layer: str | None = None,
) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "llm.operation": operation,
        "llm.provider": provider,
        "llm.model": model,
        "llm.endpoint": _safe_url_label(base_url),
    }
    if attempt is not None:
        attributes["retry.attempt"] = int(attempt)
        attributes["retry.max_attempts"] = _MAX_RETRIES + 1
    if timeout_layer is not None:
        attributes["timeout_layer"] = timeout_layer
    return attributes


def _record_retry_attempt(
    *,
    attributes: dict[str, Any],
    exc: BaseException,
    delay_seconds: float,
) -> None:
    retry_attributes = {
        **attributes,
        "retry.delay_seconds": delay_seconds,
        "error.type": type(exc).__name__,
        "timeout_layer": getattr(exc, "timeout_layer", None) or attributes.get("timeout_layer", "none"),
    }
    with traced_span("llm.retry", retry_attributes):
        set_span_attributes({"status": "retry"})
        add_span_event("llm.retry", retry_attributes)


def _record_llm_error(
    *,
    attributes: dict[str, Any],
    exc: BaseException,
    status: str = "error",
) -> None:
    error_attributes = {
        **attributes,
        "status": status,
        "error.type": type(exc).__name__,
        "timeout_layer": getattr(exc, "timeout_layer", None)
        or attributes.get("timeout_layer", "none"),
    }
    with traced_span("llm.upstream_error", error_attributes):
        add_span_event("llm.error", error_attributes)


def _headers_with_trace_context(headers: dict[str, str]) -> dict[str, str]:
    return inject_trace_context(headers)


def _is_openai_timeout_error(exc: BaseException) -> bool:
    try:
        from openai import APITimeoutError
    except Exception:
        return False
    return isinstance(exc, APITimeoutError)


def _annotate_openai_timeout(
    exc: BaseException,
    *,
    timeout: httpx.Timeout | None = None,
) -> BaseException:
    cause_layer = _timeout_layer_for_httpx_exception(
        exc.__cause__
    ) or _timeout_layer_for_httpx_exception(exc.__context__)
    timeout_layer = cause_layer or (
        _TIMEOUT_LAYER_LLM_READ if _is_openai_timeout_error(exc) else None
    )
    if timeout_layer is None:
        return exc
    if timeout is not None:
        timeout_seconds = _timeout_seconds_for_layer(timeout, timeout_layer)
    else:
        settings = get_settings()
        timeout_seconds = (
            float(settings.openai_connect_timeout_seconds)
            if timeout_layer == _TIMEOUT_LAYER_LLM_CONNECT
            else float(settings.openai_model_timeout_seconds)
        )
    setattr(exc, "timeout_layer", timeout_layer)
    setattr(exc, "timeout_seconds", timeout_seconds)
    exc.args = (
        f"LLM timeout layer={timeout_layer} exceeded after {timeout_seconds:.3g}s",
    )
    return exc


@dataclass(slots=True)
class LLMStreamChunk:
    text_delta: str | None = None
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None
    request_id: str | None = None
    provider_payload: dict[str, Any] | None = None


class LLMClient:
    """Adapter interface for OpenAI-compatible chat endpoints (MVP)."""

    _RESERVED_PARAM_KEYS = {"model", "messages", "stream"}

    def __init__(self) -> None:
        self._http_client: httpx.AsyncClient | None = None
        self._openai_clients: dict[tuple[str, str, float, float], Any] = {}
        self._client_lock = asyncio.Lock()

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Return (or lazily create) a long-lived httpx client.

        Re-using a single ``AsyncClient`` enables HTTP connection pooling
        across LLM requests, avoiding redundant TCP+TLS handshakes.

        Uses an asyncio.Lock to prevent concurrent creation from leaking
        connection pools.
        """
        if self._http_client is not None and not self._http_client.is_closed:
            return self._http_client
        async with self._client_lock:
            if self._http_client is not None and not self._http_client.is_closed:
                return self._http_client
            self._http_client = httpx.AsyncClient(
                follow_redirects=True,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
            return self._http_client

    async def aclose(self) -> None:
        """Close the underlying HTTP client.

        Call this during application shutdown (e.g. in the FastAPI lifespan)
        to release pooled connections cleanly.
        """
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None
        self._openai_clients.clear()

    @staticmethod
    def _openai_base_url(base_url: str) -> str:
        """Return an OpenAI SDK base URL rooted at a single trailing /v1."""

        if not base_url.lower().startswith(("http://", "https://")):
            raise ValueError("base_url must use http:// or https:// scheme")

        normalized = base_url.rstrip("/")
        lower = normalized.lower()
        if lower.endswith("/chat/completions"):
            normalized = normalized[: -len("/chat/completions")].rstrip("/")
            lower = normalized.lower()

        while lower.endswith("/v1/v1"):
            normalized = normalized[: -len("/v1")]
            lower = normalized.lower()

        marker = "/v1/"
        marker_index = lower.find(marker)
        if marker_index >= 0:
            return f"{normalized[:marker_index]}/v1"
        if lower.endswith("/v1"):
            return normalized
        return f"{normalized}/v1"

    @classmethod
    def _sanitize_params(cls, params: dict[str, Any] | None) -> dict[str, Any]:
        if not params:
            return {}

        # Prevent accidental overrides of required request fields.
        return {k: v for k, v in params.items() if k not in cls._RESERVED_PARAM_KEYS}

    async def stream_chat_completion(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None,
        messages: list[dict[str, Any]],
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
        total_timeout_seconds: float | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        async for chunk in self._stream_chat_completion_openai(
            base_url=base_url,
            model=model,
            api_key=api_key,
            messages=messages,
            params=params,
            extra_headers=extra_headers,
            timeout_seconds=timeout_seconds,
            total_timeout_seconds=total_timeout_seconds,
        ):
            yield chunk

    async def chat_completion(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None,
        messages: list[dict[str, Any]],
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        return await self._chat_completion_openai(
            base_url=base_url,
            model=model,
            api_key=api_key,
            messages=messages,
            params=params,
            extra_headers=extra_headers,
            timeout_seconds=timeout_seconds,
        )

    async def _get_openai_client(
        self,
        *,
        base_url: str,
        api_key: str | None,
    ) -> Any:
        settings = get_settings()
        normalized_base_url = self._openai_base_url(base_url)
        connect_timeout = float(settings.openai_connect_timeout_seconds)
        model_timeout = float(settings.openai_model_timeout_seconds)
        cache_key = (normalized_base_url, api_key or "", connect_timeout, model_timeout)

        cached = self._openai_clients.get(cache_key)
        if cached is not None:
            return cached

        http_client = await self._get_http_client()
        async with self._client_lock:
            cached = self._openai_clients.get(cache_key)
            if cached is not None:
                return cached

            try:
                from openai import AsyncOpenAI
            except Exception as exc:  # pragma: no cover - dependency is installed
                raise RuntimeError("OpenAI SDK is not installed") from exc

            client = AsyncOpenAI(
                api_key=api_key or "",
                base_url=normalized_base_url,
                http_client=http_client,
                max_retries=0,
                timeout=httpx.Timeout(model_timeout, connect=connect_timeout),
            )
            self._openai_clients[cache_key] = client
            return client

    async def _stream_chat_completion_openai(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None,
        messages: list[dict[str, Any]],
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
        total_timeout_seconds: float | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        resolved_timeout_seconds = (
            float(get_settings().openai_model_timeout_seconds)
            if timeout_seconds is None
            else float(timeout_seconds)
        )
        request_timeout = _httpx_timeout(
            model_timeout_seconds=resolved_timeout_seconds
        )
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        extra_body = self._sanitize_params(params) or None

        if total_timeout_seconds is None:
            total_timeout_seconds = resolved_timeout_seconds * 3
        wall_clock_deadline = time.monotonic() + total_timeout_seconds
        yielded_to_caller = False

        async def _attempt_stream() -> AsyncIterator[LLMStreamChunk]:
            nonlocal yielded_to_caller

            client = await self._get_openai_client(base_url=base_url, api_key=api_key)
            stream = await client.chat.completions.create(
                **request,
                extra_headers=(
                    _headers_with_trace_context(dict(extra_headers or {})) or None
                ),
                extra_body=extra_body,
                timeout=request_timeout,
            )
            async for sdk_chunk in stream:
                chunk_payload = _sdk_object_to_dict(sdk_chunk)
                provider_payload = (
                    _sanitize_provider_payload(chunk_payload)
                    if isinstance(chunk_payload, dict)
                    else None
                )
                if time.monotonic() > wall_clock_deadline:
                    exc = StreamTotalTimeoutError(timeout_seconds=total_timeout_seconds)
                    if provider_payload is not None:
                        setattr(exc, "provider_payload", provider_payload)
                    _record_llm_error(
                        attributes=_llm_trace_attributes(
                            operation="stream",
                            provider="async_openai",
                            model=model,
                            base_url=self._openai_base_url(base_url),
                            timeout_layer=exc.timeout_layer,
                        ),
                        exc=exc,
                        status="timeout",
                    )
                    raise exc

                if not isinstance(chunk_payload, dict):
                    continue
                upstream_error = _extract_upstream_error(chunk_payload)
                if upstream_error:
                    exc = RuntimeError(
                        f"Upstream error: "
                        f"{_redact_sensitive_text(upstream_error, api_key, messages)}"
                    )
                    if provider_payload is not None:
                        setattr(exc, "provider_payload", provider_payload)
                    _record_llm_error(
                        attributes=_llm_trace_attributes(
                            operation="stream",
                            provider="async_openai",
                            model=model,
                            base_url=self._openai_base_url(base_url),
                        ),
                        exc=exc,
                    )
                    yielded_to_caller = True
                    yield LLMStreamChunk(
                        request_id=_extract_sdk_request_id(sdk_chunk),
                        provider_payload=provider_payload,
                    )
                    raise exc

                usage = _sdk_object_to_dict(getattr(sdk_chunk, "usage", None))
                if not usage:
                    usage = chunk_payload.get("usage")
                if usage is not None and not isinstance(usage, dict):
                    usage = None
                usage = normalize_usage(usage)

                delta_text, finish_reason = _extract_choice_delta(chunk_payload)
                request_id = _extract_sdk_request_id(sdk_chunk)
                if delta_text is None and usage is None and finish_reason is None:
                    yielded_to_caller = True
                    yield LLMStreamChunk(
                        request_id=request_id,
                        provider_payload=provider_payload,
                    )
                    continue

                yielded_to_caller = True
                yield LLMStreamChunk(
                    text_delta=delta_text,
                    usage=usage,
                    finish_reason=finish_reason,
                    request_id=request_id,
                    provider_payload=provider_payload,
                )

        for attempt in range(_MAX_RETRIES + 1):
            span_attributes = _llm_trace_attributes(
                operation="stream",
                provider="async_openai",
                model=model,
                base_url=self._openai_base_url(base_url),
                attempt=attempt,
            )
            try:
                with traced_span("llm.provider_call", span_attributes):
                    async for chunk in get_llm_request_queue().stream(
                        _attempt_stream,
                        queue_priority=-attempt,
                    ):
                        yield chunk
                    set_span_attributes({"status": "ok"})
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                annotated_exc = _annotate_openai_timeout(exc, timeout=request_timeout)
                if getattr(annotated_exc, "timeout_layer", None):
                    span_attributes["timeout_layer"] = getattr(
                        annotated_exc,
                        "timeout_layer",
                    )
                if (
                    _is_retryable_openai_error(annotated_exc)
                    and attempt < _MAX_RETRIES
                    and not yielded_to_caller
                ):
                    delay = min(_RETRY_BASE_DELAY * (2**attempt), _RETRY_MAX_DELAY)
                    logger.warning(
                        "Retryable OpenAI SDK error %s timeout_layer=%s from %s "
                        "(attempt %d/%d), retrying in %.1fs",
                        type(annotated_exc).__name__,
                        getattr(annotated_exc, "timeout_layer", None) or "none",
                        _safe_url_label(self._openai_base_url(base_url)),
                        attempt + 1,
                        _MAX_RETRIES + 1,
                        delay,
                    )
                    _record_retry_attempt(
                        attributes=span_attributes,
                        exc=annotated_exc,
                        delay_seconds=delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                _record_llm_error(attributes=span_attributes, exc=annotated_exc)
                raise _sanitize_exception(annotated_exc, api_key, messages) from exc

    async def _chat_completion_openai(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None,
        messages: list[dict[str, Any]],
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        request_timeout = _httpx_timeout(model_timeout_seconds=timeout_seconds)
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        extra_body = self._sanitize_params(params) or None

        async def _create_completion() -> Any:
            client = await self._get_openai_client(base_url=base_url, api_key=api_key)
            return await client.chat.completions.create(
                **request,
                extra_headers=(
                    _headers_with_trace_context(dict(extra_headers or {})) or None
                ),
                extra_body=extra_body,
                timeout=request_timeout,
            )

        for attempt in range(_MAX_RETRIES + 1):
            span_attributes = _llm_trace_attributes(
                operation="chat",
                provider="async_openai",
                model=model,
                base_url=self._openai_base_url(base_url),
                attempt=attempt,
            )
            try:
                with traced_span("llm.provider_call", span_attributes):
                    response = await get_llm_request_queue().submit(
                        _create_completion,
                        queue_priority=-attempt,
                    )
                    set_span_attributes({"status": "ok"})
                body = _sdk_object_to_dict(response)
                if not isinstance(body, dict):
                    raise RuntimeError("Chat completion response was not a JSON object")

                upstream_error = _extract_upstream_error(body)
                if upstream_error:
                    exc = RuntimeError(
                        f"Upstream error: "
                        f"{_redact_sensitive_text(upstream_error, api_key, messages)}"
                    )
                    _record_llm_error(
                        attributes=_llm_trace_attributes(
                            operation="chat",
                            provider="async_openai",
                            model=model,
                            base_url=self._openai_base_url(base_url),
                            attempt=attempt,
                        ),
                        exc=exc,
                    )
                    raise exc

                usage = body.get("usage")
                if isinstance(usage, dict):
                    body["usage"] = normalize_usage(usage)
                body.setdefault("request_id", _extract_sdk_request_id(response))
                return body
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                annotated_exc = _annotate_openai_timeout(exc, timeout=request_timeout)
                if getattr(annotated_exc, "timeout_layer", None):
                    span_attributes["timeout_layer"] = getattr(
                        annotated_exc,
                        "timeout_layer",
                    )
                if _is_retryable_openai_error(annotated_exc) and attempt < _MAX_RETRIES:
                    delay = min(_RETRY_BASE_DELAY * (2**attempt), _RETRY_MAX_DELAY)
                    logger.warning(
                        "Retryable OpenAI SDK error %s timeout_layer=%s from %s "
                        "(attempt %d/%d), retrying in %.1fs",
                        type(annotated_exc).__name__,
                        getattr(annotated_exc, "timeout_layer", None) or "none",
                        _safe_url_label(self._openai_base_url(base_url)),
                        attempt + 1,
                        _MAX_RETRIES + 1,
                        delay,
                    )
                    _record_retry_attempt(
                        attributes=span_attributes,
                        exc=annotated_exc,
                        delay_seconds=delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                _record_llm_error(attributes=span_attributes, exc=annotated_exc)
                raise _sanitize_exception(annotated_exc, api_key, messages) from exc

        raise RuntimeError("chat_completion: exhausted retry loop")


def _sdk_object_to_dict(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json")
        except TypeError:
            return model_dump()
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return value


def _sanitize_provider_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                _REDACTED_PROVIDER_VALUE
                if _is_sensitive_provider_key(str(key))
                else _sanitize_provider_payload(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_provider_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_provider_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_sensitive_text(value, api_key=None, messages=None)
    return value


def _is_sensitive_provider_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_").replace(".", "_")
    return (
        normalized in _SENSITIVE_PROVIDER_KEYS
        or normalized.endswith("_secret")
        or normalized == "token"
        or normalized.endswith("_token")
        or normalized.endswith("_api_key")
        or normalized.endswith("_apikey")
    )


def _extract_sdk_request_id(value: Any) -> str | None:
    request_id = getattr(value, "_request_id", None)
    if isinstance(request_id, str) and request_id:
        return request_id
    response = getattr(value, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    for header in ("x-request-id", "openai-request-id", "x-openai-request-id"):
        candidate = headers.get(header)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _is_retryable_openai_error(exc: BaseException) -> bool:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code in _RETRYABLE_STATUS_CODES

    try:
        from openai import APIConnectionError, APITimeoutError
    except Exception:  # pragma: no cover - dependency is installed
        return False

    return isinstance(exc, (APIConnectionError, APITimeoutError))


def _sanitize_exception(
    exc: BaseException,
    api_key: str | None,
    messages: list[dict[str, Any]],
) -> BaseException:
    text = _redact_sensitive_text(str(exc), api_key, messages)
    if text == str(exc):
        return exc
    sanitized = RuntimeError(f"{type(exc).__name__}: {text}")
    provider_payload = getattr(exc, "provider_payload", None)
    if isinstance(provider_payload, dict):
        setattr(sanitized, "provider_payload", provider_payload)
    return sanitized


def _redact_sensitive_text(
    text: str,
    api_key: str | None,
    messages: list[dict[str, Any]] | None = None,
) -> str:
    redacted = text
    sensitive_values: list[str] = []
    if api_key:
        sensitive_values.append(api_key)
        sensitive_values.append(f"Bearer {api_key}")
    if messages:
        for message in messages:
            content = message.get("content")
            sensitive_values.extend(_extract_message_text_values(content))
    for value in sensitive_values:
        redacted = redacted.replace(value, "[REDACTED]")
    redacted = re.sub(
        r"(?i)(authorization\s*[:=]\s*)(?:bearer|basic)\s+[^\s,;}]+",
        _redact_authorization_match,
        redacted,
    )
    redacted = re.sub(r"(?i)bearer\s+[^\s,;}]+", "Bearer [REDACTED]", redacted)
    redacted = re.sub(r"(?i)basic\s+[^\s,;}]+", "Basic [REDACTED]", redacted)
    redacted = re.sub(r"(?i)\bsk-[A-Za-z0-9_-]+", "[REDACTED]", redacted)
    redacted = re.sub(
        r"(?i)(authorization\s*[:=]\s*)(?!(?:bearer|basic)\s+\[REDACTED\])[^\s,;}]+",
        r"\1[REDACTED]",
        redacted,
    )

    for key in _SENSITIVE_PROVIDER_KEYS:
        if key == "authorization":
            continue
        redacted = _redact_key_value_text(redacted, key)
    redacted = _redact_key_value_text(redacted, "token")
    redacted = _redact_key_value_text(redacted, "x-api-key")
    redacted = _redact_key_value_text(redacted, "x_api_key")
    redacted = _redact_key_value_text(redacted, "secret_key")
    return redacted


def _redact_authorization_match(match: re.Match[str]) -> str:
    prefix = match.group(1)
    scheme = "Basic" if "basic" in match.group(0).lower() else "Bearer"
    return f"{prefix}{scheme} [REDACTED]"


def _redact_key_value_text(text: str, key: str) -> str:
    key_parts = key.lower().replace("-", "_").split("_")
    key_pattern = "[_-]".join(re.escape(part) for part in key_parts)
    unquoted = rf"(?i)(\b{key_pattern}\s*[:=]\s*)[^\s,;}}]+"
    text = re.sub(unquoted, r"\1[REDACTED]", text)

    quoted = rf"(?i)(['\"]{key_pattern}['\"]\s*:\s*['\"])[^'\"]*(['\"])"
    return re.sub(quoted, r"\1[REDACTED]\2", text)


def _extract_message_text_values(content: Any) -> list[str]:
    if isinstance(content, str) and content:
        return [content]
    if not isinstance(content, list):
        return []

    values: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text:
            values.append(text)
    return values


def _extract_choice_delta(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None, None

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return None, None

    finish_reason = first_choice.get("finish_reason")
    if not isinstance(finish_reason, str):
        finish_reason = None

    delta = first_choice.get("delta")
    if not isinstance(delta, dict):
        return None, finish_reason

    content = delta.get("content")
    text_delta = _content_to_text(content)
    return text_delta, finish_reason


def _extract_upstream_error(payload: dict[str, Any]) -> str | None:
    """Best-effort extraction of error details from OpenAI-compatible payloads."""

    error = payload.get("error")
    if error is None:
        error_code = payload.get("error_code")
        if isinstance(error_code, int) and error_code != 0:
            text = payload.get("text")
            if isinstance(text, str) and text:
                return f"error_code={error_code} text={text}"
            return f"error_code={error_code}"
        return None

    if isinstance(error, str):
        return error

    if not isinstance(error, dict):
        return str(error)

    message = error.get("message")
    if not isinstance(message, str):
        message = None

    error_type = error.get("type")
    if not isinstance(error_type, str):
        error_type = None

    code = error.get("code")
    if isinstance(code, (str, int)):
        code_str = str(code)
    else:
        code_str = None

    parts = [message or "unknown_error"]
    if error_type:
        parts.append(f"type={error_type}")
    if code_str:
        parts.append(f"code={code_str}")
    return " ".join(parts)


def _content_to_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content

    if not isinstance(content, list):
        return None

    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str):
            parts.append(text)

    if not parts:
        return None
    return "".join(parts)
