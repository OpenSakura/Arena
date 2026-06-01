"""app.services.battle_orchestrator

Battle orchestration (select pair, call models, persist, stream).

Notes:
- Live battle execution is coordinated in-process by the cached
  ``BattleOrchestrator`` singleton.
- Only a single API worker/process is supported for live execution. Extra SSE
  consumers in that process are read-only observers of the owner's in-memory
  state, while finished battles still replay from persisted DB state.
- Use asyncio concurrency; persist incremental state sparingly.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from copy import deepcopy
from functools import lru_cache
import json
import logging
import time
from typing import Any
import uuid

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.db.session import get_sessionmaker
from app.models.battle import Battle, Run
from app.models.model_registry import Model
from app.models.task import Task
from app.services.llm_client import LLMClient
from app.services.prompting import (
    build_chat_messages,
    normalize_optional_prompt_text,
    render_prompt_template,
)
from app.utils.llm_queue import LLMQueueFullError, LLMQueueWaitTimeoutError
from app.utils.sse import sse_event
from app.utils.tracing import add_span_event, set_span_attributes, traced_span


DEFAULT_SYSTEM_PROMPT = """# 角色设定
你是一位精通中日文化的轻小说翻译专家，拥有极高的文学素养。你的任务是将日文轻小说原文翻译成流畅、具有沉浸感且符合“二次元”语感的简体中文。


# 核心翻译规则
1. **绝对的行数对齐**
   - **红线**：输入文本有多少行，输出文本必须严格对应多少行。严禁合并对话、拆分长句或遗漏任何一行。

2. **语境推断与主语补全**
   - 日文常省略主语。请根据上下文逻辑，在译文中自然地补全省略的主语（我/你/他/她/它），避免指代不清。
   - 必须通过语境区分“私/俺/僕”对应的中文语气（如“人家/老子/我”）。

3. **特殊排版与符号处理 (关键)**
   - **Ruby/注音处理**：
     - 若原文出现`汉字（注音）`格式（如`強敵（とも）`），且该注音赋予了汉字特殊含义（双关语、魔法名、特殊设定），请在译文中严格保留`汉字（注音）`格式。
     - 若注音仅为普通读音标注，则直接翻译汉字即可。
   - **标点符号**：将日文符号转换为中文规范，如引号「」转换为“”，逗号“、”转换为“，”等。
   - **控制符**：保留原文的行首全角空格、转义符等格式。

4. **文风与内容忠实度**
   - **拒绝翻译腔**：对话必须高度口语化，生动自然。
   - **零审查还原**：即使原文包含露骨、暴力、粗俗语言或过激描写，也必须忠实还原，**严禁删减、美化或淡化**。


# 输出格式
- **不要**输出 JSON 格式。
- **不要**包含任何开场白或结束语。
- 直接输出翻译后的纯文本块，确保换行符位置与原文一致。"""

logger = logging.getLogger(__name__)

EmitFn = Callable[[str, object], Awaitable[None]]
MAX_REPLAY_DELTA_CHARS = 32_000
MAX_LIVE_HISTORY_BYTES = 512_000
SYNC_DISPLAY_DELTA_CHARS = 4
SYNC_REMAINING_DELTA_MIN_INTERVAL_SECONDS = 0.05
POOLED_REPLAY_DELTA_CHARS = 4
POOLED_REPLAY_DELTA_INTERVAL_SECONDS = 0.075
MAX_RESPONSE_FULL_CHUNKS = 512
MAX_RESPONSE_FULL_CHUNK_BYTES = 64_000
MAX_RESPONSE_FULL_TOTAL_BYTES = 1_000_000
MAX_RESPONSE_FULL_DEPTH = 16
_RESPONSE_FULL_TRUNCATED_VALUE = "[TRUNCATED]"
StreamQueue = asyncio.Queue[bytes | None]


@dataclass(slots=True, frozen=True)
class BattleSnapshot:
    id: uuid.UUID
    task_id: uuid.UUID
    status: str
    metadata_json: dict[str, object] | None


@dataclass(slots=True, frozen=True)
class RunSnapshot:
    id: uuid.UUID
    battle_id: uuid.UUID
    side: str
    model_id: uuid.UUID
    output_text: str | None = None
    error_text: str | None = None


@dataclass(slots=True)
class _RunStreamItem:
    kind: str
    text_delta: str | None = None
    error_text: str | None = None


@dataclass(slots=True)
class _RunStreamState:
    prepared: PreparedRun
    queue: asyncio.Queue[_RunStreamItem]
    text_parts: list[str]
    raw_parts: list[str]
    usage: dict[str, object] | None
    request_id: str | None
    finish_reason: str | None
    error_text: str | None
    latency_ms: int | None
    provider_payloads: list[dict[str, Any]] = field(default_factory=list)
    truncated_provider_payload_count: int = 0


def _task_payload_from_battle_metadata(metadata: object) -> tuple[str, str, str] | None:
    if not isinstance(metadata, dict):
        return None

    snapshot = metadata.get("task_snapshot")
    if not isinstance(snapshot, dict):
        return None

    source_text = snapshot.get("source_text")
    source_lang = snapshot.get("source_lang")
    target_lang = snapshot.get("target_lang")
    if (
        not isinstance(source_text, str)
        or not isinstance(source_lang, str)
        or not isinstance(target_lang, str)
    ):
        return None
    return source_text, source_lang, target_lang


def _iter_text_chunks(text: str, chunk_chars: int) -> Iterator[str]:
    chunk_chars = max(int(chunk_chars), 1)
    for start in range(0, len(text), chunk_chars):
        yield text[start : start + chunk_chars]


@dataclass(slots=True, frozen=True)
class _PooledReplayPolicy:
    display_delay_ms: int
    assigned_user_id: str | None
    assigned_service_account_id: str | None
    assigned_at: str | None
    consumer_type: str


def _pooled_replay_policy(
    metadata: dict[str, object] | None,
    *,
    consumer_type: str = "human",
) -> _PooledReplayPolicy | None:
    if not isinstance(metadata, dict):
        return None

    replay = _pooled_replay_for_consumer(metadata, consumer_type)
    if not isinstance(replay, dict) or replay.get("backend_gated") is not True:
        return None
    if replay.get("unlocked") is True:
        return None

    display_delay_ms = replay.get("display_delay_ms")
    if not isinstance(display_delay_ms, int) or display_delay_ms < 0:
        return None

    assigned_user_id = replay.get("assigned_user_id")
    assigned_service_account_id = replay.get("assigned_service_account_id")
    assigned_at = replay.get("assigned_at")
    return _PooledReplayPolicy(
        display_delay_ms=display_delay_ms,
        assigned_user_id=assigned_user_id if isinstance(assigned_user_id, str) else None,
        assigned_service_account_id=(
            assigned_service_account_id
            if isinstance(assigned_service_account_id, str)
            else None
        ),
        assigned_at=assigned_at if isinstance(assigned_at, str) else None,
        consumer_type=consumer_type if consumer_type == "bot" else "human",
    )


def _pooled_replay_for_consumer(
    metadata: dict[str, object],
    consumer_type: str,
) -> dict[str, Any] | None:
    scoped = metadata.get("pooled_replays")
    if isinstance(scoped, dict):
        replay = scoped.get("bot" if consumer_type == "bot" else "human")
        if isinstance(replay, dict):
            return replay
    if consumer_type != "bot":
        replay = metadata.get("pooled_replay")
        if isinstance(replay, dict):
            return replay
    return None


def _llm_queue_error_text(exc: LLMQueueFullError | LLMQueueWaitTimeoutError) -> str:
    if isinstance(exc, LLMQueueWaitTimeoutError):
        return "LLM queue backpressure: timeout_layer=llm_queue_wait"
    return "LLM queue backpressure: queue_full"


def _provider_stream_response_full(
    chunks: list[dict[str, Any]],
    *,
    truncated_chunk_count: int,
) -> dict[str, object] | None:
    if not chunks and truncated_chunk_count == 0:
        return None
    return {
        "provider": "openai_compatible",
        "response_type": "chat.completion.chunk_stream",
        "stream": True,
        "chunks": chunks,
        "truncated_chunk_count": truncated_chunk_count,
    }


def _append_provider_payload(
    chunks: list[dict[str, Any]],
    payload: dict[str, Any],
    *,
    truncated_chunk_count: int,
) -> int:
    if len(chunks) >= MAX_RESPONSE_FULL_CHUNKS:
        return truncated_chunk_count + 1

    bounded_payload, was_truncated = _bound_provider_payload(payload)
    projected_chunks = [*chunks, bounded_payload]
    if _json_size_bytes(projected_chunks) > MAX_RESPONSE_FULL_TOTAL_BYTES:
        return truncated_chunk_count + 1

    chunks.append(bounded_payload)
    return truncated_chunk_count + int(was_truncated)


def _bound_provider_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    bounded, was_truncated = _bound_provider_value(payload, depth=0)
    if not isinstance(bounded, dict):
        return {"value": _RESPONSE_FULL_TRUNCATED_VALUE}, True

    if _json_size_bytes(bounded) <= MAX_RESPONSE_FULL_CHUNK_BYTES:
        return bounded, was_truncated

    compact = {
        "truncated": True,
        "original_keys": sorted(str(key) for key in bounded.keys()),
    }
    return compact, True


def _bound_provider_value(value: Any, *, depth: int) -> tuple[Any, bool]:
    if depth >= MAX_RESPONSE_FULL_DEPTH:
        return _RESPONSE_FULL_TRUNCATED_VALUE, True

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        truncated = False
        for key, item in value.items():
            bounded_item, item_truncated = _bound_provider_value(item, depth=depth + 1)
            result[str(key)] = bounded_item
            truncated = truncated or item_truncated
        return result, truncated

    if isinstance(value, list):
        result = []
        truncated = False
        for item in value:
            bounded_item, item_truncated = _bound_provider_value(item, depth=depth + 1)
            result.append(bounded_item)
            truncated = truncated or item_truncated
        return result, truncated

    if isinstance(value, tuple):
        return _bound_provider_value(list(value), depth=depth)

    return value, False


def _json_size_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _append_exception_provider_payload(
    chunks: list[dict[str, Any]],
    exc: BaseException,
    *,
    truncated_chunk_count: int,
) -> int:
    provider_payload = getattr(exc, "provider_payload", None)
    if not isinstance(provider_payload, dict):
        return truncated_chunk_count
    if chunks and chunks[-1] == provider_payload:
        return truncated_chunk_count
    return _append_provider_payload(
        chunks,
        provider_payload,
        truncated_chunk_count=truncated_chunk_count,
    )


def _battle_trace_attributes(
    *,
    operation: str,
    battle_id: uuid.UUID,
    request_id: str | None = None,
    status: str | None = None,
) -> dict[str, object]:
    attributes: dict[str, object] = {
        "battle.operation": operation,
        "battle.id": str(battle_id),
    }
    if request_id:
        attributes["request_id"] = request_id
    if status:
        attributes["battle.status"] = status
    return attributes


def _run_trace_attributes(*, prepared: PreparedRun) -> dict[str, object]:
    attributes: dict[str, object] = {
        "run.id": str(prepared.run_id),
        "run.side": prepared.side,
        "battle.id": str(prepared.battle_id),
        "llm.model": prepared.model_name,
    }
    if prepared.request_id:
        attributes["request_id"] = prepared.request_id
    return attributes


def _leading_newline_count(text: str) -> int:
    return len(text) - len(text.lstrip("\n"))


@dataclass(slots=True)
class _LeadingNewlineNormalizer:
    target_count: int
    is_initial_run: bool = True

    def normalize_delta(self, text_delta: str) -> str:
        if not self.is_initial_run:
            return text_delta

        newline_count = _leading_newline_count(text_delta)
        if newline_count == len(text_delta):
            return ""

        self.is_initial_run = False
        return "\n" * self.target_count + text_delta[newline_count:]


@dataclass(slots=True)
class PreparedRun:
    battle_id: uuid.UUID
    run_id: uuid.UUID
    side: str
    model_id: uuid.UUID
    base_url: str
    model_name: str
    api_key: str | None
    messages: list[dict[str, str]]
    params: dict[str, object]
    request_id: str | None
    request_json: dict[str, object] | None = None
    prompt_rendered: dict[str, object] | None = None
    source_leading_newline_count: int = 0


@dataclass(slots=True)
class _LiveBattleEntry:
    subscribers: set[StreamQueue]
    history: deque[bytes]
    history_bytes: int = 0
    owner_task: asyncio.Task[None] | None = None


class BattleOrchestrator:
    """Coordinates battle lifecycle and single-process live streaming."""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm_client = llm_client or LLMClient()
        self._SessionLocal = get_sessionmaker()
        self._battle_running_wait_timeout_seconds = max(
            int(get_settings().battle_running_wait_timeout_seconds),
            1,
        )
        # Live execution state is process-local. This intentionally supports a
        # single API worker/process for active battles.
        self._live_battles: dict[uuid.UUID, _LiveBattleEntry] = {}
        self._live_battles_guard = asyncio.Lock()

    @property
    def llm_client(self) -> LLMClient:
        """Public accessor for the LLM client (used during shutdown cleanup)."""
        return self._llm_client

    async def execute_battle_and_wait(
        self,
        battle_id: uuid.UUID,
        *,
        timeout_seconds: int,
        request_id: str | None = None,
    ) -> str:
        deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
        stream = self.stream_battle(battle_id, request_id=request_id)
        try:
            while True:
                remaining_seconds = deadline - time.monotonic()
                if remaining_seconds <= 0:
                    return "timeout"
                try:
                    payload = await asyncio.wait_for(
                        anext(stream), timeout=remaining_seconds
                    )
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    return "timeout"

                if b"event: battle.completed" in payload:
                    return "completed"
                if b"event: battle.failed" in payload:
                    return "failed"
        finally:
            await stream.aclose()

        battle, _runs = await asyncio.to_thread(
            lambda: self._load_battle_and_runs(battle_id)
        )
        if battle is None:
            return "failed"
        return battle.status

    @staticmethod
    def _automatic_retry_count(metadata_json: dict[str, object] | None) -> int:
        if not isinstance(metadata_json, dict):
            return 0
        value = metadata_json.get("automatic_retry_count")
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return max(value, 0)
        return 0

    async def stream_battle(
        self,
        battle_id: uuid.UUID,
        *,
        request_id: str | None = None,
    ) -> AsyncIterator[bytes]:
        queue: StreamQueue = asyncio.Queue()
        observer_task: asyncio.Task[None] | None = None
        finished_replay_task: asyncio.Task[None] | None = None
        finished_replay_terminal = asyncio.Event()

        async def emit(event: str, data: object) -> None:
            await queue.put(sse_event(event=event, data=data))

        async def run_finished_replay(
            *,
            runs: Sequence[RunSnapshot],
            final_event: str,
            replay_policy: _PooledReplayPolicy | None = None,
            unlock_when_completed: bool = False,
        ) -> None:
            async def replay_emit(event: str, data: object) -> None:
                await emit(event, data)
                if event == final_event:
                    finished_replay_terminal.set()

            try:
                await self._replay_finished_runs(
                    battle_id=battle_id,
                    runs=runs,
                    emit=replay_emit,
                    final_event=final_event,
                    replay_policy=replay_policy,
                )
                if unlock_when_completed:
                    await asyncio.to_thread(
                        lambda: self._mark_pooled_replay_unlocked(
                            battle_id=battle_id,
                            replay_policy=replay_policy,
                        )
                    )
            finally:
                await queue.put(None)

        battle, runs = await asyncio.to_thread(
            lambda: self._load_battle_and_runs(battle_id)
        )
        if battle is None:
            await emit(
                "battle.error", {"battle_id": str(battle_id), "detail": "not_found"}
            )
            await queue.put(None)
        elif not runs:
            await asyncio.to_thread(
                lambda: self._mark_battle_status(battle_id=battle_id, status="failed")
            )
            await emit(
                "battle.error",
                {"battle_id": str(battle_id), "detail": "missing_runs"},
            )
            await queue.put(None)
        elif battle.status == "completed":
            replay_policy = _pooled_replay_policy(battle.metadata_json)
            finished_replay_task = asyncio.create_task(
                run_finished_replay(
                    runs=runs,
                    final_event="battle.completed",
                    replay_policy=replay_policy,
                    unlock_when_completed=replay_policy is not None,
                )
            )
        elif battle.status == "failed":
            finished_replay_task = asyncio.create_task(
                run_finished_replay(
                    runs=runs,
                    final_event="battle.failed",
                )
            )
        else:
            observer_task = await self._attach_live_battle_stream(
                battle_id=battle_id,
                battle_status=battle.status,
                subscriber=queue,
                request_id=request_id,
            )

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            task_done = observer_task.done() if observer_task is not None else False

            await self._detach_live_battle_stream(
                battle_id=battle_id,
                subscriber=queue,
            )

            if finished_replay_task is not None:
                if finished_replay_task.done():
                    self._log_background_runner_failure(
                        task=finished_replay_task, battle_id=battle_id
                    )
                elif finished_replay_terminal.is_set():
                    await finished_replay_task
                    self._log_background_runner_failure(
                        task=finished_replay_task, battle_id=battle_id
                    )
                else:
                    finished_replay_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await finished_replay_task

            if observer_task is not None:
                if task_done:
                    self._log_background_runner_failure(
                        task=observer_task, battle_id=battle_id
                    )
                else:
                    observer_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await observer_task

    @staticmethod
    def _log_background_runner_failure(
        *,
        task: asyncio.Task[None],
        battle_id: uuid.UUID,
    ) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            logger.exception(
                "Battle stream runner failed after disconnect for battle_id=%s",
                battle_id,
            )

    async def _attach_live_battle_stream(
        self,
        *,
        battle_id: uuid.UUID,
        battle_status: str,
        subscriber: StreamQueue,
        request_id: str | None,
    ) -> asyncio.Task[None] | None:
        owner_task: asyncio.Task[None] | None = None
        async with self._live_battles_guard:
            entry = self._live_battles.get(battle_id)
            if entry is not None:
                for payload in entry.history:
                    subscriber.put_nowait(payload)
                entry.subscribers.add(subscriber)
                return None

            if battle_status == "pending":
                entry = _LiveBattleEntry(
                    subscribers={subscriber},
                    history=deque(),
                )
                self._live_battles[battle_id] = entry
                owner_task = asyncio.create_task(
                    self._run_owned_battle(
                        battle_id=battle_id,
                        request_id=request_id,
                    )
                )
                entry.owner_task = owner_task
            else:
                return asyncio.create_task(
                    self._observe_running_battle_to_queue(
                        battle_id=battle_id,
                        subscriber=subscriber,
                    )
                )

        if owner_task is not None:
            owner_task.add_done_callback(
                lambda done_task: self._log_background_runner_failure(
                    task=done_task,
                    battle_id=battle_id,
                )
            )
        return None

    async def _detach_live_battle_stream(
        self,
        *,
        battle_id: uuid.UUID,
        subscriber: StreamQueue,
    ) -> None:
        async with self._live_battles_guard:
            entry = self._live_battles.get(battle_id)
            if entry is None:
                return
            entry.subscribers.discard(subscriber)

    async def _run_owned_battle(
        self,
        *,
        battle_id: uuid.UUID,
        request_id: str | None,
    ) -> None:
        async def emit(event: str, data: object) -> None:
            await self._broadcast_live_battle_event(
                battle_id=battle_id,
                event=event,
                data=data,
            )

        with traced_span(
            "battle.owner",
            _battle_trace_attributes(
                operation="owner",
                battle_id=battle_id,
                request_id=request_id,
            ),
        ):
            try:
                while True:
                    try:
                        await asyncio.wait_for(
                            self._execute_owned_battle(
                                battle_id=battle_id,
                                emit=emit,
                                request_id=request_id,
                            ),
                            timeout=float(self._battle_running_wait_timeout_seconds),
                        )
                        set_span_attributes({"status": "ok"})
                        return
                    except asyncio.TimeoutError:
                        set_span_attributes(
                            {
                                "status": "timeout",
                                "timeout_layer": "battle_owner",
                                "timeout_seconds": self._battle_running_wait_timeout_seconds,
                            }
                        )
                        add_span_event(
                            "battle.timeout",
                            {
                                "battle.id": str(battle_id),
                                "timeout_layer": "battle_owner",
                                "timeout_seconds": self._battle_running_wait_timeout_seconds,
                            },
                        )
                        logger.error(
                            "Battle owner task timed out",
                            extra={
                                "battle_id": str(battle_id),
                                "timeout_layer": "battle_owner",
                                "timeout_seconds": self._battle_running_wait_timeout_seconds,
                            },
                        )
                        retry_scheduled = await asyncio.to_thread(
                            lambda: self._schedule_automatic_retry_if_available(
                                battle_id=battle_id
                            )
                        )
                        if retry_scheduled:
                            add_span_event(
                                "battle.retry_scheduled",
                                {"battle.id": str(battle_id), "retry.attempt": 1},
                            )
                            continue

                        with suppress(Exception):
                            await self._fail_battle_for_timeout(
                                battle_id=battle_id,
                                emit=emit,
                                detail="runtime_timeout",
                                error_text=(
                                    "Battle timeout layer=battle_owner exceeded after "
                                    f"{self._battle_running_wait_timeout_seconds}s"
                                ),
                            )
                        return
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        set_span_attributes(
                            {"status": "error", "error.type": type(exc).__name__}
                        )
                        error_text = f"battle_owner_failure: {type(exc).__name__}"
                        logger.exception(
                            "Battle owner task failed for battle_id=%s", battle_id
                        )
                        with suppress(Exception):
                            await asyncio.to_thread(
                                lambda: self._mark_battle_status(
                                    battle_id=battle_id,
                                    status="failed",
                                )
                            )
                        with suppress(Exception):
                            await asyncio.to_thread(
                                lambda: self._persist_battle_run_errors(
                                    battle_id=battle_id,
                                    error_text=error_text,
                                )
                            )
                        with suppress(Exception):
                            await emit(
                                "battle.failed",
                                {
                                    "battle_id": str(battle_id),
                                    "detail": "owner_task_failed",
                                },
                            )
                        return
            finally:
                await self._close_live_battle(battle_id=battle_id)
    async def _fail_battle_for_timeout(
        self,
        *,
        battle_id: uuid.UUID,
        emit: EmitFn,
        detail: str,
        error_text: str,
    ) -> None:
        await asyncio.to_thread(
            lambda: self._mark_battle_status(
                battle_id=battle_id,
                status="failed",
            )
        )
        await asyncio.to_thread(
            lambda: self._persist_battle_run_errors(
                battle_id=battle_id,
                error_text=error_text,
            )
        )
        await emit(
            "battle.failed",
            {
                "battle_id": str(battle_id),
                "detail": detail,
            },
        )

    async def _execute_owned_battle(
        self,
        *,
        battle_id: uuid.UUID,
        emit: EmitFn,
        request_id: str | None,
    ) -> None:
        with traced_span(
            "battle.execute",
            _battle_trace_attributes(
                operation="execute",
                battle_id=battle_id,
                request_id=request_id,
            ),
        ):
            await self._execute_owned_battle_inner(
                battle_id=battle_id,
                emit=emit,
                request_id=request_id,
            )

    async def _execute_owned_battle_inner(
        self,
        *,
        battle_id: uuid.UUID,
        emit: EmitFn,
        request_id: str | None,
    ) -> None:
        battle, runs = await asyncio.to_thread(
            lambda: self._load_battle_and_runs(battle_id)
        )
        if battle is None:
            await emit(
                "battle.error", {"battle_id": str(battle_id), "detail": "not_found"}
            )
            return

        if not runs:
            await asyncio.to_thread(
                lambda: self._mark_battle_status(battle_id=battle_id, status="failed")
            )
            await emit(
                "battle.error",
                {"battle_id": str(battle_id), "detail": "missing_runs"},
            )
            return

        if battle.status == "completed":
            await self._replay_finished_runs(
                battle_id=battle_id,
                runs=runs,
                emit=emit,
                final_event="battle.completed",
                replay_policy=_pooled_replay_policy(battle.metadata_json),
            )
            return

        if battle.status == "failed":
            await self._replay_finished_runs(
                battle_id=battle_id,
                runs=runs,
                emit=emit,
                final_event="battle.failed",
            )
            return

        if battle.status == "running":
            await self._observe_running_battle(battle_id=battle_id, emit=emit)
            return

        await asyncio.to_thread(
            lambda: self._mark_battle_status(battle_id=battle_id, status="running")
        )
        set_span_attributes({"battle.status": "running"})
        await emit("battle.started", {"battle_id": str(battle_id)})

        try:
            prepared_runs = await asyncio.to_thread(
                lambda: self._prepare_runs_for_execution(
                    battle=battle,
                    runs=runs,
                    request_id=request_id,
                )
            )
            results = await self._execute_runs_synced(
                prepared_runs=prepared_runs,
                emit=emit,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            set_span_attributes({"status": "error", "error.type": type(exc).__name__})
            error_text = f"battle_execution_failure: {type(exc).__name__}"
            logger.exception("Battle execution failed for battle_id=%s", battle_id)
            await asyncio.to_thread(
                lambda: self._mark_battle_status(battle_id=battle_id, status="failed")
            )
            await asyncio.to_thread(
                lambda: self._persist_battle_run_errors(
                    battle_id=battle_id,
                    error_text=error_text,
                )
            )
            await emit(
                "battle.failed",
                {"battle_id": str(battle_id), "detail": "execution_failed"},
            )
            return

        run_ok: list[bool] = []
        for idx, result in enumerate(results):
            if isinstance(result, BaseException):
                logger.exception(
                    "Run %s raised an unhandled exception for battle_id=%s",
                    prepared_runs[idx].run_id,
                    battle_id,
                    exc_info=result,
                )
                run_ok.append(False)
            else:
                run_ok.append(bool(result))

        desired_status = "completed" if all(run_ok) else "failed"
        if desired_status == "completed":
            await asyncio.to_thread(
                lambda: self._mark_battle_status(
                    battle_id=battle_id,
                    status="completed",
                )
            )
            set_span_attributes({"status": "ok", "battle.status": "completed"})
            await emit("battle.completed", {"battle_id": str(battle_id)})
            return

        retry_scheduled = await asyncio.to_thread(
            lambda: self._schedule_automatic_retry_if_available(battle_id=battle_id)
        )
        if retry_scheduled:
            add_span_event(
                "battle.retry_scheduled",
                {"battle.id": str(battle_id), "retry.attempt": 1},
            )
            await self._execute_owned_battle(
                battle_id=battle_id,
                emit=emit,
                request_id=request_id,
            )
            return

        await asyncio.to_thread(
            lambda: self._mark_battle_status(
                battle_id=battle_id,
                status="failed",
            )
        )
        await emit(
            "battle.failed", {"battle_id": str(battle_id), "detail": "run_failed"}
        )
        set_span_attributes({"status": "error", "battle.status": "failed"})

    async def _broadcast_live_battle_event(
        self,
        *,
        battle_id: uuid.UUID,
        event: str,
        data: object,
    ) -> None:
        payload = sse_event(event=event, data=data)
        async with self._live_battles_guard:
            entry = self._live_battles.get(battle_id)
            if entry is None:
                return
            entry.history.append(payload)
            entry.history_bytes += len(payload)
            while entry.history and entry.history_bytes > MAX_LIVE_HISTORY_BYTES:
                entry.history_bytes -= len(entry.history.popleft())
            subscribers = tuple(entry.subscribers)

        for subscriber in subscribers:
            subscriber.put_nowait(payload)

    async def _close_live_battle(self, *, battle_id: uuid.UUID) -> None:
        async with self._live_battles_guard:
            entry = self._live_battles.pop(battle_id, None)
        if entry is None:
            return
        for subscriber in tuple(entry.subscribers):
            subscriber.put_nowait(None)

    def _load_battle_and_runs(
        self, battle_id: uuid.UUID
    ) -> tuple[BattleSnapshot | None, list[RunSnapshot]]:
        db: Session = self._SessionLocal()
        try:
            battle = db.get(Battle, battle_id)
            if battle is None:
                return None, []
            runs = (
                db.execute(
                    select(Run)
                    .where(Run.battle_id == battle_id)
                    .order_by(Run.side.asc())
                )
                .scalars()
                .all()
            )
            return (
                BattleSnapshot(
                    id=battle.id,
                    task_id=battle.task_id,
                    status=battle.status,
                    metadata_json=deepcopy(battle.metadata_json),
                ),
                [
                    RunSnapshot(
                        id=run.id,
                        battle_id=run.battle_id,
                        side=run.side,
                        model_id=run.model_id,
                        output_text=run.output_text,
                        error_text=run.error_text,
                    )
                    for run in runs
                ],
            )
        finally:
            db.close()

    async def _replay_finished_runs(
        self,
        *,
        battle_id: uuid.UUID,
        runs: Sequence[RunSnapshot | Run],
        emit: EmitFn,
        final_event: str,
        replay_policy: _PooledReplayPolicy | None = None,
    ) -> None:
        if replay_policy is not None and final_event == "battle.completed":
            await self._replay_pooled_finished_runs(
                battle_id=battle_id,
                runs=runs,
                emit=emit,
                replay_policy=replay_policy,
            )
            return

        for run in runs:
            if run.output_text:
                for chunk_index, chunk in enumerate(
                    _iter_text_chunks(run.output_text, MAX_REPLAY_DELTA_CHARS)
                ):
                    await emit(
                        "run.delta",
                        {
                            "battle_id": str(battle_id),
                            "run_id": str(run.id),
                            "side": run.side,
                            "text_delta": chunk,
                            "replay": True,
                            "chunk_index": chunk_index,
                        },
                    )
            if run.error_text:
                await emit(
                    "run.error",
                    {
                        "battle_id": str(battle_id),
                        "run_id": str(run.id),
                        "side": run.side,
                        "error": run.error_text,
                    },
                )

        payload: dict[str, object] = {"battle_id": str(battle_id), "replay": True}
        if final_event == "battle.failed":
            payload["detail"] = "replay_failed"
        await emit(final_event, payload)

    async def _replay_pooled_finished_runs(
        self,
        *,
        battle_id: uuid.UUID,
        runs: Sequence[RunSnapshot | Run],
        emit: EmitFn,
        replay_policy: _PooledReplayPolicy,
    ) -> None:
        if replay_policy.display_delay_ms > 0:
            await asyncio.sleep(replay_policy.display_delay_ms / 1000)

        positions = {run.id: 0 for run in runs}
        chunk_indexes = {run.id: 0 for run in runs}

        while True:
            emitted_this_round = False
            for run in runs:
                text = run.output_text or ""
                position = positions[run.id]
                if position >= len(text):
                    continue

                next_position = min(position + POOLED_REPLAY_DELTA_CHARS, len(text))
                chunk = text[position:next_position]
                positions[run.id] = next_position
                await emit(
                    "run.delta",
                    {
                        "battle_id": str(battle_id),
                        "run_id": str(run.id),
                        "side": run.side,
                        "text_delta": chunk,
                        "replay": True,
                        "chunk_index": chunk_indexes[run.id],
                    },
                )
                chunk_indexes[run.id] += 1
                emitted_this_round = True

            if not emitted_this_round:
                break

            await asyncio.sleep(POOLED_REPLAY_DELTA_INTERVAL_SECONDS)

        await emit("battle.completed", {"battle_id": str(battle_id), "replay": True})

    async def _observe_running_battle(
        self,
        *,
        battle_id: uuid.UUID,
        emit: EmitFn,
    ) -> None:
        poll_interval_seconds = 0.5
        deadline = time.monotonic() + float(self._battle_running_wait_timeout_seconds)
        # Unsupported deployment fallback: another process marked the battle
        # running, but this process has no local owner task. Stay read-only and
        # wait for persisted terminal state for a bounded period.
        while True:
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                await self._fail_battle_for_timeout(
                    battle_id=battle_id,
                    emit=emit,
                    detail="observer_timeout",
                    error_text=(
                        "Battle observer exceeded timeout of "
                        f"{self._battle_running_wait_timeout_seconds}s"
                    ),
                )
                return

            await asyncio.sleep(min(poll_interval_seconds, remaining_seconds))
            battle, runs = await asyncio.to_thread(
                lambda: self._load_battle_and_runs(battle_id)
            )
            if battle is None:
                await emit(
                    "battle.error", {"battle_id": str(battle_id), "detail": "not_found"}
                )
                return

            if battle.status == "completed":
                await self._replay_finished_runs(
                    battle_id=battle_id,
                    runs=runs,
                    emit=emit,
                    final_event="battle.completed",
                    replay_policy=_pooled_replay_policy(getattr(battle, "metadata_json", None)),
                )
                return

            if battle.status == "failed":
                await self._replay_finished_runs(
                    battle_id=battle_id,
                    runs=runs,
                    emit=emit,
                    final_event="battle.failed",
                )
                return

    async def _observe_running_battle_to_queue(
        self,
        *,
        battle_id: uuid.UUID,
        subscriber: StreamQueue,
    ) -> None:
        async def emit(event: str, data: object) -> None:
            await subscriber.put(sse_event(event=event, data=data))

        try:
            await self._observe_running_battle(battle_id=battle_id, emit=emit)
        finally:
            await subscriber.put(None)

    def _prepare_runs_for_execution(
        self,
        *,
        battle: BattleSnapshot,
        runs: list[RunSnapshot],
        request_id: str | None,
    ) -> list[PreparedRun]:
        db: Session = self._SessionLocal()
        try:
            source_text, source_lang, target_lang = self._resolve_task_payload(
                db=db,
                battle=battle,
            )
            source_leading_newline_count = _leading_newline_count(source_text)

            prepared: list[PreparedRun] = []
            for run in runs:
                model = db.get(Model, run.model_id)
                if model is None:
                    raise RuntimeError(f"Model not found for run: {run.id}")

                system_prompt = self._build_system_prompt(
                    model=model,
                    source_text=source_text,
                    source_lang=source_lang,
                    target_lang=target_lang,
                )
                user_prompt = self._build_user_prompt(
                    model=model,
                    source_text=source_text,
                    source_lang=source_lang,
                    target_lang=target_lang,
                )
                messages = build_chat_messages(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )

                params = self._build_model_params(model)
                params = LLMClient._sanitize_params(params)

                request_json = {
                    "model": model.model_name,
                    "messages": messages,
                    "stream": True,
                    **params,
                }
                prompt_rendered: dict[str, object] = {
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "source_lang": source_lang,
                    "target_lang": target_lang,
                }

                api_key = (
                    decrypt_secret(model.encrypted_api_key)
                    if model.encrypted_api_key is not None
                    else None
                )

                prepared.append(
                    PreparedRun(
                        battle_id=battle.id,
                        run_id=run.id,
                        side=run.side,
                        model_id=model.id,
                        base_url=model.base_url,
                        model_name=model.model_name,
                        api_key=api_key,
                        messages=messages,
                        params=params,
                        request_id=request_id,
                        request_json=request_json,
                        prompt_rendered=prompt_rendered,
                        source_leading_newline_count=source_leading_newline_count,
                    )
                )

            return prepared
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _build_system_prompt(
        self,
        *,
        model: Model,
        source_text: str,
        source_lang: str,
        target_lang: str,
    ) -> str:
        prompt_inputs = self._build_prompt_inputs(
            source_text=source_text,
            source_lang=source_lang,
            target_lang=target_lang,
        )
        prompt_template = normalize_optional_prompt_text(model.system_prompt)
        if prompt_template is None:
            return render_prompt_template(DEFAULT_SYSTEM_PROMPT, prompt_inputs)
        return render_prompt_template(prompt_template, prompt_inputs)

    def _build_user_prompt(
        self,
        *,
        model: Model,
        source_text: str,
        source_lang: str,
        target_lang: str,
    ) -> str:
        prompt_inputs = self._build_prompt_inputs(
            source_text=source_text,
            source_lang=source_lang,
            target_lang=target_lang,
        )
        prompt_template = normalize_optional_prompt_text(model.user_prompt)
        if prompt_template is None:
            return source_text
        return render_prompt_template(prompt_template, prompt_inputs)

    @staticmethod
    def _build_prompt_inputs(
        *,
        source_text: str,
        source_lang: str,
        target_lang: str,
    ) -> dict[str, str]:
        return {
            "source_text": source_text,
            "source_lang": source_lang,
            "target_lang": target_lang,
        }

    @staticmethod
    def _build_model_params(model: Model) -> dict[str, object]:
        params: dict[str, object] = {}
        if model.params:
            params.update(model.params)

        if model.temperature is not None:
            params["temperature"] = model.temperature
        if model.frequency_penalty is not None:
            params["frequency_penalty"] = model.frequency_penalty
        if model.presence_penalty is not None:
            params["presence_penalty"] = model.presence_penalty

        return params

    async def _execute_run(
        self,
        *,
        prepared: PreparedRun,
        emit: EmitFn,
    ) -> bool:
        with traced_span("run.execute", _run_trace_attributes(prepared=prepared)):
            return await self._execute_run_inner(prepared=prepared, emit=emit)

    async def _execute_run_inner(
        self,
        *,
        prepared: PreparedRun,
        emit: EmitFn,
    ) -> bool:
        started = time.monotonic()

        text_parts: list[str] = []
        raw_parts: list[str] = []
        provider_payloads: list[dict[str, Any]] = []
        truncated_provider_payload_count = 0
        usage: dict[str, object] | None = None
        request_id: str | None = None
        finish_reason: str | None = None
        error_text: str | None = None
        newline_normalizer = _LeadingNewlineNormalizer(
            target_count=prepared.source_leading_newline_count
        )

        try:
            upstream_headers: dict[str, str] = {
                "X-Arena-Battle-ID": str(prepared.battle_id),
                "X-Arena-Run-ID": str(prepared.run_id),
            }
            if prepared.request_id:
                upstream_headers["X-Request-ID"] = prepared.request_id

            async for chunk in self._llm_client.stream_chat_completion(
                base_url=prepared.base_url,
                model=prepared.model_name,
                api_key=prepared.api_key,
                messages=prepared.messages,
                params=prepared.params,
                extra_headers=upstream_headers,
            ):
                if chunk.provider_payload is not None:
                    truncated_provider_payload_count = _append_provider_payload(
                        provider_payloads,
                        chunk.provider_payload,
                        truncated_chunk_count=truncated_provider_payload_count,
                    )
                if chunk.request_id is not None and request_id is None:
                    request_id = chunk.request_id
                if chunk.usage is not None:
                    usage = chunk.usage
                if chunk.finish_reason is not None:
                    finish_reason = chunk.finish_reason
                if chunk.text_delta:
                    raw_parts.append(chunk.text_delta)
                    text_delta = newline_normalizer.normalize_delta(chunk.text_delta)
                    if not text_delta:
                        continue
                    text_parts.append(text_delta)
                    await emit(
                        "run.delta",
                        {
                            "battle_id": str(prepared.battle_id),
                            "run_id": str(prepared.run_id),
                            "side": prepared.side,
                            "text_delta": text_delta,
                        },
                    )
        except asyncio.CancelledError:
            raise
        except httpx.HTTPError as exc:
            truncated_provider_payload_count = _append_exception_provider_payload(
                provider_payloads,
                exc,
                truncated_chunk_count=truncated_provider_payload_count,
            )
            set_span_attributes({"status": "error", "error.type": type(exc).__name__})
            timeout_layer = getattr(exc, "timeout_layer", None)
            if timeout_layer is None:
                error_text = f"LLM HTTP error: {exc}"
            else:
                set_span_attributes({"timeout_layer": timeout_layer})
                error_text = f"LLM HTTP error: timeout_layer={timeout_layer}"
        except (LLMQueueFullError, LLMQueueWaitTimeoutError) as exc:
            truncated_provider_payload_count = _append_exception_provider_payload(
                provider_payloads,
                exc,
                truncated_chunk_count=truncated_provider_payload_count,
            )
            status = "timeout" if isinstance(exc, LLMQueueWaitTimeoutError) else "queue_full"
            set_span_attributes(
                {
                    "status": status,
                    "error.type": type(exc).__name__,
                    "timeout_layer": getattr(exc, "timeout_layer", "none"),
                }
            )
            add_span_event(
                "run.queue_backpressure",
                {
                    "run.id": str(prepared.run_id),
                    "battle.id": str(prepared.battle_id),
                    "status": status,
                    "error.type": type(exc).__name__,
                    "timeout_layer": getattr(exc, "timeout_layer", "none"),
                },
            )
            error_text = _llm_queue_error_text(exc)
        except Exception as exc:  # noqa: BLE001
            truncated_provider_payload_count = _append_exception_provider_payload(
                provider_payloads,
                exc,
                truncated_chunk_count=truncated_provider_payload_count,
            )
            set_span_attributes({"status": "error", "error.type": type(exc).__name__})
            error_text = f"LLM stream error: {type(exc).__name__}"

        latency_ms = int((time.monotonic() - started) * 1000)
        output_text = "".join(text_parts) if text_parts else None
        output_text_raw = "".join(raw_parts) if raw_parts else None
        response_full = _provider_stream_response_full(
            provider_payloads,
            truncated_chunk_count=truncated_provider_payload_count,
        )

        # Treat empty/whitespace-only output as a failure so the battle can't
        # end up `completed` but non-votable.
        if error_text is None and (output_text is None or not output_text.strip()):
            error_text = "LLM produced empty output"
            output_text = None

        stats: dict[str, object] = {
            "latency_ms": latency_ms,
            "request_id": request_id,
            "usage": usage,
            "finish_reason": finish_reason,
            "arena_request_id": prepared.request_id,
        }

        await asyncio.to_thread(
            lambda: self._persist_run_result(
                run_id=prepared.run_id,
                output_text=output_text,
                output_text_raw=output_text_raw,
                response_full=response_full,
                stats=stats,
                error_text=error_text,
                request_json=prepared.request_json,
                prompt_rendered=prepared.prompt_rendered,
            )
        )

        if error_text is not None:
            set_span_attributes({"status": "error", "run.latency_ms": latency_ms})
            await emit(
                "run.error",
                {
                    "battle_id": str(prepared.battle_id),
                    "run_id": str(prepared.run_id),
                    "side": prepared.side,
                    "error": error_text,
                },
            )
            return False

        set_span_attributes({"status": "ok", "run.latency_ms": latency_ms})
        await emit(
            "run.completed",
            {
                "battle_id": str(prepared.battle_id),
                "run_id": str(prepared.run_id),
                "side": prepared.side,
                "latency_ms": latency_ms,
            },
        )
        return True

    @staticmethod
    def _raise_cancelled_result(results: Sequence[bool | BaseException]) -> None:
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                raise result

    async def _execute_runs_synced(
        self,
        *,
        prepared_runs: list[PreparedRun],
        emit: EmitFn,
    ) -> list[bool | BaseException]:
        """Execute runs while synchronizing streamed deltas.

        FastChat's arena UI intentionally synchronizes display to reduce model
        identity leakage via timing differences. We mimic that behavior by
        buffering per-run deltas and emitting them in lockstep.

        Notes:
        - This only applies to the standard two-sided arena (A/B).
        - We still persist per-run results/stats; we just avoid leaking
          completion timing via extra SSE events.
        """

        prepared_sorted = sorted(prepared_runs, key=lambda run: run.side)
        sides = {run.side for run in prepared_sorted}
        if len(prepared_runs) != 2 or sides != {"A", "B"}:
            # Fallback: keep behavior for any future non-A/B modes.
            results = await asyncio.gather(
                *[
                    self._execute_run(prepared=prepared, emit=emit)
                    for prepared in prepared_runs
                ],
                return_exceptions=True,
            )
            self._raise_cancelled_result(results)
            return list(results)

        states: list[_RunStreamState] = []
        for prepared in prepared_sorted:
            states.append(
                _RunStreamState(
                    prepared=prepared,
                    queue=asyncio.Queue(maxsize=1),
                    text_parts=[],
                    raw_parts=[],
                    provider_payloads=[],
                    truncated_provider_payload_count=0,
                    usage=None,
                    request_id=None,
                    finish_reason=None,
                    error_text=None,
                    latency_ms=None,
                )
            )

        producers = [
            asyncio.create_task(self._execute_run_buffered(state=state, emit=emit))
            for state in states
        ]
        drainer = asyncio.create_task(
            self._drain_synced_deltas(states=states, emit=emit)
        )

        try:
            results = await asyncio.gather(*producers, return_exceptions=True)
        finally:
            # Ensure the drainer exits even if producers fail unexpectedly.
            for state in states:
                # Best-effort: do not block if queue is full.
                try:
                    state.queue.put_nowait(_RunStreamItem(kind="done"))
                except asyncio.QueueFull:
                    pass
            with suppress(asyncio.CancelledError):
                await drainer

        self._raise_cancelled_result(results)
        results_by_side = {
            state.prepared.side: result for state, result in zip(states, results)
        }
        # Preserve the caller's order for downstream bookkeeping.
        return [results_by_side.get(run.side, False) for run in prepared_runs]

    async def _execute_run_buffered(
        self,
        *,
        state: _RunStreamState,
        emit: EmitFn,
    ) -> bool:
        """Stream a single run into an internal queue and persist result."""

        prepared = state.prepared
        with traced_span("run.execute", _run_trace_attributes(prepared=prepared)):
            return await self._execute_run_buffered_inner(state=state, emit=emit)

    async def _execute_run_buffered_inner(
        self,
        *,
        state: _RunStreamState,
        emit: EmitFn,
    ) -> bool:
        prepared = state.prepared
        started = time.monotonic()
        newline_normalizer = _LeadingNewlineNormalizer(
            target_count=prepared.source_leading_newline_count
        )

        try:
            upstream_headers: dict[str, str] = {
                "X-Arena-Battle-ID": str(prepared.battle_id),
                "X-Arena-Run-ID": str(prepared.run_id),
            }
            if prepared.request_id:
                upstream_headers["X-Request-ID"] = prepared.request_id

            async for chunk in self._llm_client.stream_chat_completion(
                base_url=prepared.base_url,
                model=prepared.model_name,
                api_key=prepared.api_key,
                messages=prepared.messages,
                params=prepared.params,
                extra_headers=upstream_headers,
            ):
                if chunk.provider_payload is not None:
                    state.truncated_provider_payload_count = _append_provider_payload(
                        state.provider_payloads,
                        chunk.provider_payload,
                        truncated_chunk_count=state.truncated_provider_payload_count,
                    )
                if chunk.request_id is not None and state.request_id is None:
                    state.request_id = chunk.request_id
                if chunk.usage is not None:
                    state.usage = chunk.usage
                if chunk.finish_reason is not None:
                    state.finish_reason = chunk.finish_reason

                if not chunk.text_delta:
                    continue
                state.raw_parts.append(chunk.text_delta)
                text_delta = newline_normalizer.normalize_delta(chunk.text_delta)
                if not text_delta:
                    continue
                state.text_parts.append(text_delta)
                for display_delta in _iter_text_chunks(
                    text_delta,
                    SYNC_DISPLAY_DELTA_CHARS,
                ):
                    await state.queue.put(
                        _RunStreamItem(kind="delta", text_delta=display_delta)
                    )

        except asyncio.CancelledError:
            raise
        except httpx.HTTPError as exc:
            state.truncated_provider_payload_count = _append_exception_provider_payload(
                state.provider_payloads,
                exc,
                truncated_chunk_count=state.truncated_provider_payload_count,
            )
            set_span_attributes({"status": "error", "error.type": type(exc).__name__})
            timeout_layer = getattr(exc, "timeout_layer", None)
            if timeout_layer is None:
                state.error_text = f"LLM HTTP error: {exc}"
            else:
                set_span_attributes({"timeout_layer": timeout_layer})
                state.error_text = f"LLM HTTP error: timeout_layer={timeout_layer}"
        except (LLMQueueFullError, LLMQueueWaitTimeoutError) as exc:
            state.truncated_provider_payload_count = _append_exception_provider_payload(
                state.provider_payloads,
                exc,
                truncated_chunk_count=state.truncated_provider_payload_count,
            )
            status = "timeout" if isinstance(exc, LLMQueueWaitTimeoutError) else "queue_full"
            set_span_attributes(
                {
                    "status": status,
                    "error.type": type(exc).__name__,
                    "timeout_layer": getattr(exc, "timeout_layer", "none"),
                }
            )
            add_span_event(
                "run.queue_backpressure",
                {
                    "run.id": str(prepared.run_id),
                    "battle.id": str(prepared.battle_id),
                    "status": status,
                    "error.type": type(exc).__name__,
                    "timeout_layer": getattr(exc, "timeout_layer", "none"),
                },
            )
            state.error_text = _llm_queue_error_text(exc)
        except Exception as exc:  # noqa: BLE001
            state.truncated_provider_payload_count = _append_exception_provider_payload(
                state.provider_payloads,
                exc,
                truncated_chunk_count=state.truncated_provider_payload_count,
            )
            set_span_attributes({"status": "error", "error.type": type(exc).__name__})
            state.error_text = f"LLM stream error: {type(exc).__name__}"
        finally:
            await state.queue.put(_RunStreamItem(kind="done"))

        state.latency_ms = int((time.monotonic() - started) * 1000)
        output_text = "".join(state.text_parts) if state.text_parts else None
        output_text_raw = "".join(state.raw_parts) if state.raw_parts else None
        response_full = _provider_stream_response_full(
            state.provider_payloads,
            truncated_chunk_count=state.truncated_provider_payload_count,
        )

        if state.error_text is None and (
            output_text is None or not output_text.strip()
        ):
            state.error_text = "LLM produced empty output"
            output_text = None
        stats: dict[str, object] = {
            "latency_ms": state.latency_ms,
            "request_id": state.request_id,
            "usage": state.usage,
            "finish_reason": state.finish_reason,
            "arena_request_id": prepared.request_id,
        }

        await asyncio.to_thread(
            lambda: self._persist_run_result(
                run_id=prepared.run_id,
                output_text=output_text,
                output_text_raw=output_text_raw,
                response_full=response_full,
                stats=stats,
                error_text=state.error_text,
                request_json=prepared.request_json,
                prompt_rendered=prepared.prompt_rendered,
            )
        )

        if state.error_text is not None:
            set_span_attributes({"status": "error", "run.latency_ms": state.latency_ms})
            await emit(
                "run.error",
                {
                    "battle_id": str(prepared.battle_id),
                    "run_id": str(prepared.run_id),
                    "side": prepared.side,
                    "error": state.error_text,
                },
            )
            return False

        set_span_attributes({"status": "ok", "run.latency_ms": state.latency_ms})
        return True

    async def _drain_synced_deltas(
        self,
        *,
        states: list[_RunStreamState],
        emit: EmitFn,
    ) -> None:
        """Drain per-run delta queues, emitting deltas in lockstep."""

        state_by_side = {state.prepared.side: state for state in states}
        sides = sorted(state_by_side.keys())
        done: set[str] = set()
        last_delta_emitted_at: float | None = None

        async def wait_for_display_interval() -> None:
            if last_delta_emitted_at is None:
                return
            elapsed = time.monotonic() - last_delta_emitted_at
            delay = SYNC_REMAINING_DELTA_MIN_INTERVAL_SECONDS - elapsed
            if delay > 0:
                await asyncio.sleep(delay)

        async def emit_delta(
            *,
            prepared: PreparedRun,
            text_delta: str,
            enforce_interval: bool,
        ) -> None:
            nonlocal last_delta_emitted_at
            if enforce_interval:
                await wait_for_display_interval()
            await emit(
                "run.delta",
                {
                    "battle_id": str(prepared.battle_id),
                    "run_id": str(prepared.run_id),
                    "side": prepared.side,
                    "text_delta": text_delta,
                },
            )
            last_delta_emitted_at = time.monotonic()

        while len(done) < len(sides):
            remaining = [side for side in sides if side not in done]
            if not remaining:
                return

            if len(remaining) >= 2:
                # Deterministic ordering (A then B) + sequential awaits so the
                # emitted stream is paced by the slowest side.
                items: list[tuple[str, _RunStreamItem]] = []
                for side in remaining:
                    items.append((side, await state_by_side[side].queue.get()))

                peer_finished = any(item.kind == "done" for _, item in items)
                emits_active_round = any(
                    item.kind == "delta" and item.text_delta for _, item in items
                )
                if not peer_finished and emits_active_round:
                    await wait_for_display_interval()
                for side, item in items:
                    prepared = state_by_side[side].prepared
                    if item.kind == "delta" and item.text_delta:
                        await emit_delta(
                            prepared=prepared,
                            text_delta=item.text_delta,
                            enforce_interval=peer_finished,
                        )
                    elif item.kind == "done":
                        done.add(side)
                continue

            side = remaining[0]
            prepared = state_by_side[side].prepared
            item = await state_by_side[side].queue.get()
            if item.kind == "delta" and item.text_delta:
                await emit_delta(
                    prepared=prepared,
                    text_delta=item.text_delta,
                    enforce_interval=True,
                )
            elif item.kind == "done":
                done.add(side)

    def _persist_run_result(
        self,
        *,
        run_id: uuid.UUID,
        output_text: str | None,
        output_text_raw: str | None,
        response_full: dict[str, object] | None,
        stats: dict[str, object],
        error_text: str | None,
        request_json: dict[str, object] | None = None,
        prompt_rendered: dict[str, object] | None = None,
    ) -> None:
        db: Session = self._SessionLocal()
        try:
            run = db.get(Run, run_id)
            if run is None:
                return

            if run.error_text is None:
                run.error_text = error_text
            elif error_text is not None:
                # Preserve the first persisted run error and log any later
                # duplicate failure for diagnostics.
                logger.warning(
                    "Run %s already has persisted error_text, duplicate error discarded: %s",
                    run_id,
                    error_text,
                )

            run.output_text = output_text
            run.output_text_raw = output_text_raw
            run.response_full = response_full
            run.stats = stats
            if request_json is not None:
                run.request_json = request_json
            if prompt_rendered is not None:
                run.prompt_rendered = prompt_rendered
            db.add(run)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _schedule_automatic_retry_if_available(self, *, battle_id: uuid.UUID) -> bool:
        db: Session = self._SessionLocal()
        try:
            battle = db.get(Battle, battle_id)
            if battle is None:
                return False

            metadata_json = (
                battle.metadata_json if isinstance(battle.metadata_json, dict) else {}
            )
            retry_count = self._automatic_retry_count(metadata_json)
            if retry_count >= 1:
                return False

            runs = (
                db.execute(
                    select(Run)
                    .where(Run.battle_id == battle_id)
                    .order_by(Run.side.asc())
                )
                .scalars()
                .all()
            )
            for run in runs:
                run.output_text = None
                run.output_text_raw = None
                run.response_full = None
                run.error_text = None
                run.stats = None
                run.request_json = None
                run.prompt_rendered = None
                db.add(run)

            battle.metadata_json = {
                **metadata_json,
                "automatic_retry_count": retry_count + 1,
            }
            battle.status = "pending"
            db.add(battle)
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _mark_battle_status(self, *, battle_id: uuid.UUID, status: str) -> None:
        db: Session = self._SessionLocal()
        try:
            battle = db.get(Battle, battle_id)
            if battle is None:
                return
            battle.status = status
            db.add(battle)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _mark_pooled_replay_unlocked(
        self,
        *,
        battle_id: uuid.UUID,
        replay_policy: _PooledReplayPolicy | None = None,
    ) -> None:
        db: Session = self._SessionLocal()
        try:
            battle = db.get(Battle, battle_id)
            if battle is None:
                return
            metadata = battle.metadata_json if isinstance(battle.metadata_json, dict) else {}
            replay = _pooled_replay_for_consumer(
                metadata,
                replay_policy.consumer_type if replay_policy is not None else "human",
            )
            if not isinstance(replay, dict) or replay.get("backend_gated") is not True:
                return
            if replay_policy is not None:
                if replay.get("assigned_user_id") != replay_policy.assigned_user_id:
                    return
                if (
                    replay_policy.consumer_type == "bot"
                    and replay.get("assigned_service_account_id")
                    != replay_policy.assigned_service_account_id
                ):
                    return
                if replay.get("assigned_at") != replay_policy.assigned_at:
                    return
            unlocked_replay = {**replay, "unlocked": True, "expires_at": None}
            if replay_policy is not None and replay_policy.consumer_type == "bot":
                scoped = metadata.get("pooled_replays")
                scoped_replays = dict(scoped) if isinstance(scoped, dict) else {}
                scoped_replays["bot"] = unlocked_replay
                battle.metadata_json = {**metadata, "pooled_replays": scoped_replays}
            else:
                scoped = metadata.get("pooled_replays")
                if isinstance(scoped, dict):
                    scoped_replays = dict(scoped)
                    scoped_replays["human"] = unlocked_replay
                    battle.metadata_json = {
                        **metadata,
                        "pooled_replay": unlocked_replay,
                        "pooled_replays": scoped_replays,
                    }
                else:
                    battle.metadata_json = {
                        **metadata,
                        "pooled_replay": unlocked_replay,
                    }
            db.add(battle)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _persist_battle_run_errors(
        self, *, battle_id: uuid.UUID, error_text: str
    ) -> None:
        db: Session = self._SessionLocal()
        try:
            db.execute(
                update(Run)
                .where(Run.battle_id == battle_id, Run.error_text.is_(None))
                .values(error_text=error_text)
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _resolve_task_payload(
        *,
        db: Session,
        battle: BattleSnapshot,
    ) -> tuple[str, str, str]:
        from_metadata = _task_payload_from_battle_metadata(battle.metadata_json)
        if from_metadata is not None:
            return from_metadata

        task = db.get(Task, battle.task_id)
        if task is None:
            raise RuntimeError("Battle task not found")
        return task.source_text, task.source_lang, task.target_lang


@lru_cache(maxsize=1)
def get_battle_orchestrator() -> BattleOrchestrator:
    return BattleOrchestrator()


def reset_battle_orchestrator() -> None:
    """Clear the cached singleton.

    This is intended for test teardown: the cached instance holds
    ``asyncio.Lock`` objects that are bound to the event loop that was
    active at creation time.  Calling this between tests (or when
    switching event loops) prevents "attached to a different loop"
    errors.
    """
    try:
        orchestrator = get_battle_orchestrator()
    except Exception:  # noqa: BLE001
        pass
    else:
        try:
            import asyncio as _asyncio

            loop = _asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(orchestrator.llm_client.aclose())
            else:
                loop.run_until_complete(orchestrator.llm_client.aclose())
        except Exception:  # noqa: BLE001
            pass
    get_battle_orchestrator.cache_clear()
