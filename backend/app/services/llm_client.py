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
import json
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
_RETRYABLE_CONNECTION_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.PoolTimeout,
    httpx.ReadError,
    httpx.RemoteProtocolError,
    httpx.WriteTimeout,
)
_MAX_RETRIES = 2
_RETRY_BASE_DELAY = 1.0
_RETRY_MAX_DELAY = 5.0


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


def _annotate_httpx_timeout(
    exc: BaseException,
    *,
    timeout: httpx.Timeout,
) -> BaseException:
    timeout_layer = _timeout_layer_for_httpx_exception(exc)
    if timeout_layer is None:
        return exc
    timeout_seconds = _timeout_seconds_for_layer(timeout, timeout_layer)
    setattr(exc, "timeout_layer", timeout_layer)
    setattr(exc, "timeout_seconds", timeout_seconds)
    exc.args = (
        f"LLM timeout layer={timeout_layer} exceeded after {timeout_seconds:.3g}s",
    )
    return exc


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
    def _chat_completions_url(cls, base_url: str) -> str:
        """Return a chat completions URL from a configured base_url."""

        return f"{cls._openai_base_url(base_url)}/chat/completions"

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
        timeout_seconds: float = 120.0,
        total_timeout_seconds: float | None = None,
        max_sse_event_chars: int = 128_000,
        max_sse_line_bytes: int = 256_000,
    ) -> AsyncIterator[LLMStreamChunk]:
        if get_settings().llm_client_mode == "async_openai":
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
            return

        async for chunk in self._stream_chat_completion_legacy(
            base_url=base_url,
            model=model,
            api_key=api_key,
            messages=messages,
            params=params,
            extra_headers=extra_headers,
            timeout_seconds=timeout_seconds,
            total_timeout_seconds=total_timeout_seconds,
            max_sse_event_chars=max_sse_event_chars,
            max_sse_line_bytes=max_sse_line_bytes,
        ):
            yield chunk

    async def _stream_chat_completion_legacy(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None,
        messages: list[dict[str, Any]],
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout_seconds: float = 120.0,
        total_timeout_seconds: float | None = None,
        max_sse_event_chars: int = 128_000,
        max_sse_line_bytes: int = 256_000,
    ) -> AsyncIterator[LLMStreamChunk]:
        """Yield OpenAI-compatible streaming chunks.

        The caller is responsible for collecting deltas and handling finish states.

        ``total_timeout_seconds`` enforces an overall wall-clock deadline for the
        entire stream (connect + read + processing).  Defaults to
        ``timeout_seconds * 3`` when not set explicitly.
        """

        url = self._chat_completions_url(base_url)
        safe_url = _safe_url_label(url)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        payload.update(self._sanitize_params(params))

        headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if extra_headers:
            for key, value in extra_headers.items():
                if key and value:
                    headers[key] = value

        timeout = _httpx_timeout(model_timeout_seconds=timeout_seconds)

        if total_timeout_seconds is None:
            total_timeout_seconds = timeout_seconds * 3
        wall_clock_deadline = time.monotonic() + total_timeout_seconds

        # Once we have received *any* response bytes from the upstream, we must
        # not retry.  Retrying a mid-stream request can duplicate text in the UI
        # and corrupt persisted outputs.  ``response_bytes_received`` is set as
        # soon as the HTTP response status line arrives (before SSE parsing).
        response_bytes_received = False

        async def _attempt_stream() -> AsyncIterator[LLMStreamChunk]:
            nonlocal response_bytes_received

            client = await self._get_http_client()
            request_headers = _headers_with_trace_context(dict(headers))
            async with client.stream(
                "POST",
                url,
                json=payload,
                headers=request_headers,
                timeout=timeout,
            ) as response:
                response.raise_for_status()
                response_bytes_received = True

                request_id = (
                    response.headers.get("x-request-id")
                    or response.headers.get("openai-request-id")
                    or response.headers.get("x-openai-request-id")
                )

                async for data in _iter_sse_data_events(
                    _iter_lines_from_bytes(
                        response.aiter_raw(),
                        max_line_bytes=max_sse_line_bytes,
                    ),
                    max_event_chars=max_sse_event_chars,
                ):
                    if time.monotonic() > wall_clock_deadline:
                        exc = StreamTotalTimeoutError(
                            timeout_seconds=total_timeout_seconds
                        )
                        _record_llm_error(
                            attributes=_llm_trace_attributes(
                                operation="stream",
                                provider="legacy_httpx",
                                model=model,
                                base_url=url,
                                timeout_layer=exc.timeout_layer,
                            ),
                            exc=exc,
                            status="timeout",
                        )
                        raise exc

                    if data.strip() == "[DONE]":
                        break

                    try:
                        chunk_payload = json.loads(data)
                    except json.JSONDecodeError:
                        logger.warning("Skipping malformed SSE chunk from upstream")
                        continue

                    if not isinstance(chunk_payload, dict):
                        continue

                    upstream_error = _extract_upstream_error(chunk_payload)
                    if upstream_error:
                        exc = RuntimeError(
                            f"Upstream error: "
                            f"{_redact_sensitive_text(upstream_error, api_key, messages)}"
                        )
                        _record_llm_error(
                            attributes=_llm_trace_attributes(
                                operation="stream",
                                provider="legacy_httpx",
                                model=model,
                                base_url=url,
                            ),
                            exc=exc,
                        )
                        raise exc

                    usage = chunk_payload.get("usage")
                    if usage is not None and not isinstance(usage, dict):
                        usage = None
                    usage = normalize_usage(usage)

                    delta_text, finish_reason = _extract_choice_delta(chunk_payload)
                    if delta_text is None and usage is None and finish_reason is None:
                        continue

                    yield LLMStreamChunk(
                        text_delta=delta_text,
                        usage=usage,
                        finish_reason=finish_reason,
                        request_id=request_id,
                    )

        for attempt in range(_MAX_RETRIES + 1):
            span_attributes = _llm_trace_attributes(
                operation="stream",
                provider="legacy_httpx",
                model=model,
                base_url=url,
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

            except httpx.HTTPStatusError as exc:
                if (
                    exc.response.status_code in _RETRYABLE_STATUS_CODES
                    and attempt < _MAX_RETRIES
                    and not response_bytes_received
                ):
                    delay = min(
                        _RETRY_BASE_DELAY * (2**attempt),
                        _RETRY_MAX_DELAY,
                    )
                    logger.warning(
                        "Retryable HTTP %s from %s (attempt %d/%d), retrying in %.1fs",
                        exc.response.status_code,
                        safe_url,
                        attempt + 1,
                        _MAX_RETRIES + 1,
                        delay,
                    )
                    _record_retry_attempt(
                        attributes=span_attributes,
                        exc=exc,
                        delay_seconds=delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                _record_llm_error(attributes=span_attributes, exc=exc)
                raise
            except _RETRYABLE_CONNECTION_ERRORS as exc:
                annotated_exc = _annotate_httpx_timeout(exc, timeout=timeout)
                if getattr(annotated_exc, "timeout_layer", None):
                    span_attributes["timeout_layer"] = getattr(
                        annotated_exc,
                        "timeout_layer",
                    )
                if attempt < _MAX_RETRIES and not response_bytes_received:
                    delay = min(
                        _RETRY_BASE_DELAY * (2**attempt),
                        _RETRY_MAX_DELAY,
                    )
                    logger.warning(
                        "Connection error %s timeout_layer=%s from %s "
                        "(attempt %d/%d), retrying in %.1fs",
                        type(annotated_exc).__name__,
                        getattr(annotated_exc, "timeout_layer", None) or "none",
                        safe_url,
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
                raise annotated_exc

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
        if get_settings().llm_client_mode == "async_openai":
            return await self._chat_completion_openai(
                base_url=base_url,
                model=model,
                api_key=api_key,
                messages=messages,
                params=params,
                extra_headers=extra_headers,
                timeout_seconds=timeout_seconds,
            )
        return await self._chat_completion_legacy(
            base_url=base_url,
            model=model,
            api_key=api_key,
            messages=messages,
            params=params,
            extra_headers=extra_headers,
            timeout_seconds=timeout_seconds,
        )

    async def _chat_completion_legacy(
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
        """Non-streaming OpenAI-compatible chat completion call.

        This is mainly used for admin connectivity testing.
        """

        url = self._chat_completions_url(base_url)
        safe_url = _safe_url_label(url)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        payload.update(self._sanitize_params(params))

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if extra_headers:
            for key, value in extra_headers.items():
                if key and value:
                    headers[key] = value

        timeout = _httpx_timeout(model_timeout_seconds=timeout_seconds)

        response: httpx.Response | None = None

        async def _post_once() -> httpx.Response:
            client = await self._get_http_client()
            request_headers = _headers_with_trace_context(dict(headers))
            return await client.post(
                url,
                json=payload,
                headers=request_headers,
                timeout=timeout,
            )

        for attempt in range(_MAX_RETRIES + 1):
            span_attributes = _llm_trace_attributes(
                operation="chat",
                provider="legacy_httpx",
                model=model,
                base_url=url,
                attempt=attempt,
            )
            try:
                with traced_span("llm.provider_call", span_attributes):
                    response = await get_llm_request_queue().submit(
                        _post_once,
                        queue_priority=-attempt,
                    )
                    response.raise_for_status()
                    set_span_attributes({"status": "ok"})
                break  # Success — fall through to response processing.
            except httpx.HTTPStatusError as exc:
                if (
                    exc.response.status_code in _RETRYABLE_STATUS_CODES
                    and attempt < _MAX_RETRIES
                ):
                    delay = min(
                        _RETRY_BASE_DELAY * (2**attempt),
                        _RETRY_MAX_DELAY,
                    )
                    logger.warning(
                        "Retryable HTTP %s from %s (attempt %d/%d), retrying in %.1fs",
                        exc.response.status_code,
                        safe_url,
                        attempt + 1,
                        _MAX_RETRIES + 1,
                        delay,
                    )
                    _record_retry_attempt(
                        attributes=span_attributes,
                        exc=exc,
                        delay_seconds=delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                _record_llm_error(attributes=span_attributes, exc=exc)
                raise
            except _RETRYABLE_CONNECTION_ERRORS as exc:
                annotated_exc = _annotate_httpx_timeout(exc, timeout=timeout)
                if getattr(annotated_exc, "timeout_layer", None):
                    span_attributes["timeout_layer"] = getattr(
                        annotated_exc,
                        "timeout_layer",
                    )
                if attempt < _MAX_RETRIES:
                    delay = min(
                        _RETRY_BASE_DELAY * (2**attempt),
                        _RETRY_MAX_DELAY,
                    )
                    logger.warning(
                        "Connection error %s timeout_layer=%s from %s "
                        "(attempt %d/%d), retrying in %.1fs",
                        type(annotated_exc).__name__,
                        getattr(annotated_exc, "timeout_layer", None) or "none",
                        safe_url,
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
                raise annotated_exc

        # The loop always either breaks on success or raises on final failure,
        # so response is guaranteed to be set here.
        if response is None:
            # Unreachable: the retry loop always either assigns response (on
            # a successful break) or raises (on the final attempt).  Guard
            # against future refactors that might introduce a new code path
            # that exits the loop without either.
            raise RuntimeError(
                "chat_completion: response is None after retry loop; "
                "this is a programming error"
            )
        body = response.json()

        if not isinstance(body, dict):
            exc = RuntimeError("Chat completion response was not a JSON object")
            _record_llm_error(
                attributes=_llm_trace_attributes(
                    operation="chat",
                    provider="legacy_httpx",
                    model=model,
                    base_url=url,
                ),
                exc=exc,
            )
            raise exc

        upstream_error = _extract_upstream_error(body)
        if upstream_error:
            exc = RuntimeError(
                f"Upstream error: "
                f"{_redact_sensitive_text(upstream_error, api_key, messages)}"
            )
            _record_llm_error(
                attributes=_llm_trace_attributes(
                    operation="chat",
                    provider="legacy_httpx",
                    model=model,
                    base_url=url,
                ),
                exc=exc,
            )
            raise exc

        usage = body.get("usage")
        if isinstance(usage, dict):
            body["usage"] = normalize_usage(usage)

        request_id = (
            response.headers.get("x-request-id")
            or response.headers.get("openai-request-id")
            or response.headers.get("x-openai-request-id")
        )
        body.setdefault("request_id", request_id)
        return body

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
        timeout_seconds: float = 120.0,
        total_timeout_seconds: float | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        request.update(self._sanitize_params(params))

        if total_timeout_seconds is None:
            total_timeout_seconds = timeout_seconds * 3
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
            )
            async for sdk_chunk in stream:
                if time.monotonic() > wall_clock_deadline:
                    exc = StreamTotalTimeoutError(timeout_seconds=total_timeout_seconds)
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

                chunk_payload = _sdk_object_to_dict(sdk_chunk)
                if not isinstance(chunk_payload, dict):
                    continue
                upstream_error = _extract_upstream_error(chunk_payload)
                if upstream_error:
                    exc = RuntimeError(
                        f"Upstream error: "
                        f"{_redact_sensitive_text(upstream_error, api_key, messages)}"
                    )
                    _record_llm_error(
                        attributes=_llm_trace_attributes(
                            operation="stream",
                            provider="async_openai",
                            model=model,
                            base_url=self._openai_base_url(base_url),
                        ),
                        exc=exc,
                    )
                    raise exc

                usage = _sdk_object_to_dict(getattr(sdk_chunk, "usage", None))
                if not usage:
                    usage = chunk_payload.get("usage")
                if usage is not None and not isinstance(usage, dict):
                    usage = None
                usage = normalize_usage(usage)

                delta_text, finish_reason = _extract_choice_delta(chunk_payload)
                if delta_text is None and usage is None and finish_reason is None:
                    continue

                request_id = _extract_sdk_request_id(sdk_chunk)
                yielded_to_caller = True
                yield LLMStreamChunk(
                    text_delta=delta_text,
                    usage=usage,
                    finish_reason=finish_reason,
                    request_id=request_id,
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
                annotated_exc = _annotate_openai_timeout(exc)
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
        request.update(self._sanitize_params(params))

        async def _create_completion() -> Any:
            client = await self._get_openai_client(base_url=base_url, api_key=api_key)
            return await client.chat.completions.create(
                **request,
                extra_headers=(
                    _headers_with_trace_context(dict(extra_headers or {})) or None
                ),
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


async def _iter_lines_from_bytes(
    chunks: AsyncIterator[bytes], *, max_line_bytes: int
) -> AsyncIterator[str]:
    """Split a byte stream into lines with a safety cap.

    We prefer parsing the raw response bytes (instead of relying on
    ``httpx.Response.aiter_lines()``) so we can guard against pathological
    upstream lines and avoid unbounded buffering.
    """

    max_line_bytes = max(int(max_line_bytes), 1)
    buffer = b""
    skipping = False

    async for chunk in chunks:
        if not chunk:
            continue

        if skipping:
            consumed = _skip_to_line_break(chunk)
            if consumed is None:
                continue
            chunk = chunk[consumed:]
            skipping = False

        buffer += chunk

        while True:
            line_info = _read_complete_line_bytes(buffer)
            if line_info is None:
                break

            line_bytes, next_index = line_info
            buffer = buffer[next_index:]

            if len(line_bytes) > max_line_bytes:
                # Drop only this oversized line and continue with any
                # additional data already buffered after its newline.
                continue

            yield line_bytes.decode("utf-8", errors="replace")

        if not skipping and len(buffer) > max_line_bytes:
            buffer = b""
            skipping = True

    if not skipping and buffer:
        # Best-effort flush of trailing data without a final newline.
        yield buffer.decode("utf-8", errors="replace")


def _skip_to_line_break(data: bytes) -> int | None:
    """Return index after the next line break, or None if absent."""

    idx_n = data.find(b"\n")
    idx_r = data.find(b"\r")

    if idx_n < 0 and idx_r < 0:
        return None

    if idx_r >= 0 and (idx_n < 0 or idx_r < idx_n):
        # CRLF counts as one separator.
        if idx_r + 1 < len(data) and data[idx_r + 1 : idx_r + 2] == b"\n":
            return idx_r + 2
        return idx_r + 1

    # LF separator.
    return idx_n + 1


def _read_complete_line_bytes(buffer: bytes) -> tuple[bytes, int] | None:
    """Return (line_bytes, next_index) when a full line is available."""

    idx_n = buffer.find(b"\n")
    idx_r = buffer.find(b"\r")

    if idx_n < 0 and idx_r < 0:
        return None

    if idx_r >= 0 and (idx_n < 0 or idx_r < idx_n):
        if idx_r + 1 >= len(buffer):
            return None
        if buffer[idx_r + 1 : idx_r + 2] == b"\n":
            return buffer[:idx_r], idx_r + 2
        return buffer[:idx_r], idx_r + 1

    return buffer[:idx_n], idx_n + 1


async def _iter_sse_data_events(
    lines: AsyncIterator[str], *, max_event_chars: int
) -> AsyncIterator[str]:
    """Yield complete SSE ``data:`` payloads from a stream of lines.

    Supports multi-line SSE data events and drops oversized events to avoid
    unbounded memory growth on malformed upstream streams.
    """

    max_event_chars = max(int(max_event_chars), 1)
    data_lines: list[str] = []
    event_char_count = 0
    dropping_event = False

    async for raw_line in lines:
        line = raw_line.rstrip("\r")

        if line == "":
            if not dropping_event and data_lines:
                yield "\n".join(data_lines)

            data_lines = []
            event_char_count = 0
            dropping_event = False
            continue

        if line.startswith(":"):
            continue

        if not line.startswith("data:"):
            continue

        data = line[5:]
        if data.startswith(" "):
            data = data[1:]

        if dropping_event:
            continue

        event_char_count += len(data)
        if event_char_count > max_event_chars:
            data_lines = []
            dropping_event = True
            continue

        data_lines.append(data)

    if not dropping_event and data_lines:
        yield "\n".join(data_lines)


def _sdk_object_to_dict(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return value


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
    return RuntimeError(f"{type(exc).__name__}: {text}")


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
        r"(?i)(authorization\s*[:=]\s*)bearer\s+[^\s,;}]+",
        r"\1Bearer [REDACTED]",
        redacted,
    )
    redacted = re.sub(r"(?i)bearer\s+[^\s,;}]+", "Bearer [REDACTED]", redacted)
    redacted = re.sub(
        r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;}]+",
        r"\1[REDACTED]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)(cookie\s*[:=]\s*)[^\s,;}]+",
        r"\1[REDACTED]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)((?:x-)?provider[_-]?token\s*[:=]\s*)[^\s,;}]+",
        r"\1[REDACTED]",
        redacted,
    )
    for header in ("cookie", "x-provider-token", "provider-token", "provider_token"):
        header_pattern = re.escape(header)
        redacted = re.sub(
            rf"(?i)(['\"]{header_pattern}['\"]\s*:\s*['\"])[^'\"]*(['\"])",
            r"\1[REDACTED]\2",
            redacted,
        )
    return redacted


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
