"""app.services.llm_client

Client for calling LLM providers (via your existing gateway or direct APIs).

Notes:
- The backend is API-only; it should call external inference endpoints.
- Keep provider differences behind a small adapter interface.
- Support streaming (token deltas) so we can forward them via SSE.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
import logging
import time
from typing import Any

import httpx

from app.utils.usage import normalize_usage

logger = logging.getLogger(__name__)

# Transient HTTP status codes that are safe to retry before any response
# body has been consumed.
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_RETRYABLE_CONNECTION_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.PoolTimeout,
)
_MAX_RETRIES = 2
_RETRY_BASE_DELAY = 1.0
_RETRY_MAX_DELAY = 5.0


class StreamTotalTimeoutError(Exception):
    """Raised when the overall wall-clock time for a streaming request exceeds
    the configured limit."""


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

    @staticmethod
    def _chat_completions_url(base_url: str) -> str:
        """Return a chat completions URL from a configured base_url.

        Accept both styles:
        - https://gateway.example.com
        - https://gateway.example.com/v1

        This avoids the common /v1/v1/chat/completions misconfiguration.
        """

        normalized = base_url.rstrip("/")
        if normalized.endswith("/chat/completions"):
            return normalized
        if normalized.endswith("/v1"):
            return f"{normalized}/chat/completions"
        return f"{normalized}/v1/chat/completions"

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
        """Yield OpenAI-compatible streaming chunks.

        The caller is responsible for collecting deltas and handling finish states.

        ``total_timeout_seconds`` enforces an overall wall-clock deadline for the
        entire stream (connect + read + processing).  Defaults to
        ``timeout_seconds * 3`` when not set explicitly.
        """

        url = self._chat_completions_url(base_url)

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

        timeout = httpx.Timeout(
            connect=min(10.0, timeout_seconds),
            read=timeout_seconds,
            write=min(10.0, timeout_seconds),
            pool=min(10.0, timeout_seconds),
        )

        if total_timeout_seconds is None:
            total_timeout_seconds = timeout_seconds * 3
        wall_clock_deadline = time.monotonic() + total_timeout_seconds

        # Once we have received *any* response bytes from the upstream, we must
        # not retry.  Retrying a mid-stream request can duplicate text in the UI
        # and corrupt persisted outputs.  ``response_bytes_received`` is set as
        # soon as the HTTP response status line arrives (before SSE parsing).
        response_bytes_received = False

        for attempt in range(_MAX_RETRIES + 1):
            try:
                client = await self._get_http_client()
                async with client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers=headers,
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
                            raise StreamTotalTimeoutError(
                                f"Stream exceeded total wall-clock timeout "
                                f"of {total_timeout_seconds}s"
                            )

                        if data.strip() == "[DONE]":
                            break

                        try:
                            chunk_payload = json.loads(data)
                        except json.JSONDecodeError:
                            logger.warning(
                                "Skipping malformed SSE chunk: %s", data[:200]
                            )
                            continue

                        if not isinstance(chunk_payload, dict):
                            continue

                        upstream_error = _extract_upstream_error(chunk_payload)
                        if upstream_error:
                            raise RuntimeError(f"Upstream error: {upstream_error}")

                        usage = chunk_payload.get("usage")
                        if usage is not None and not isinstance(usage, dict):
                            usage = None
                        usage = normalize_usage(usage)

                        delta_text, finish_reason = _extract_choice_delta(chunk_payload)
                        if (
                            delta_text is None
                            and usage is None
                            and finish_reason is None
                        ):
                            continue

                        yield LLMStreamChunk(
                            text_delta=delta_text,
                            usage=usage,
                            finish_reason=finish_reason,
                            request_id=request_id,
                        )

                    # Stream completed successfully; do not retry.
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
                        url,
                        attempt + 1,
                        _MAX_RETRIES + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
            except _RETRYABLE_CONNECTION_ERRORS as exc:
                if attempt < _MAX_RETRIES and not response_bytes_received:
                    delay = min(
                        _RETRY_BASE_DELAY * (2**attempt),
                        _RETRY_MAX_DELAY,
                    )
                    logger.warning(
                        "Connection error %s from %s (attempt %d/%d), retrying in %.1fs",
                        type(exc).__name__,
                        url,
                        attempt + 1,
                        _MAX_RETRIES + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise

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
        """Non-streaming OpenAI-compatible chat completion call.

        This is mainly used for admin connectivity testing.
        """

        url = self._chat_completions_url(base_url)

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

        timeout = httpx.Timeout(timeout_seconds)

        response: httpx.Response | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                client = await self._get_http_client()
                response = await client.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                )
                response.raise_for_status()
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
                        url,
                        attempt + 1,
                        _MAX_RETRIES + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
            except _RETRYABLE_CONNECTION_ERRORS as exc:
                if attempt < _MAX_RETRIES:
                    delay = min(
                        _RETRY_BASE_DELAY * (2**attempt),
                        _RETRY_MAX_DELAY,
                    )
                    logger.warning(
                        "Connection error %s from %s (attempt %d/%d), retrying in %.1fs",
                        type(exc).__name__,
                        url,
                        attempt + 1,
                        _MAX_RETRIES + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise

        # The loop always either breaks on success or raises on final failure,
        # so response is guaranteed to be set here.
        assert response is not None
        body = response.json()

        if not isinstance(body, dict):
            raise RuntimeError("Chat completion response was not a JSON object")

        upstream_error = _extract_upstream_error(body)
        if upstream_error:
            raise RuntimeError(f"Upstream error: {upstream_error}")

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
