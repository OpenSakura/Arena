from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from collections import deque
from types import SimpleNamespace
from typing import cast
import uuid

import httpx
import pytest

from app.services import battle_orchestrator as orchestrator_module
from app.services.battle_orchestrator import (
    BattleSnapshot,
    BattleOrchestrator,
    DEFAULT_SYSTEM_PROMPT,
    MAX_RESPONSE_FULL_CHUNK_BYTES,
    MAX_RESPONSE_FULL_DEPTH,
    PreparedRun,
    RunSnapshot,
    _append_provider_payload,
    _iter_text_chunks,
    _provider_stream_response_full,
    _task_payload_from_battle_metadata,
)
from app.models.battle import Run
from app.services.llm_client import LLMStreamChunk
from app.utils.llm_queue import LLMQueueFullError, LLMQueueWaitTimeoutError
from app.utils.sse import sse_event


def test_task_payload_from_battle_metadata_reads_snapshot() -> None:
    metadata = {
        "task_snapshot": {
            "source_text": "JP text",
            "source_lang": "ja",
            "target_lang": "zh",
        }
    }

    assert _task_payload_from_battle_metadata(metadata) == ("JP text", "ja", "zh")


def test_task_payload_from_battle_metadata_ignores_invalid_payloads() -> None:
    assert _task_payload_from_battle_metadata(None) is None
    assert _task_payload_from_battle_metadata({"task_snapshot": None}) is None
    assert (
        _task_payload_from_battle_metadata({"task_snapshot": {"source_text": "x"}})
        is None
    )


def test_iter_text_chunks_splits_text_into_bounded_chunks() -> None:
    text = "abcdefghij"
    chunks = list(_iter_text_chunks(text, 4))
    assert chunks == ["abcd", "efgh", "ij"]
    assert list(_iter_text_chunks("abc", 0)) == ["a", "b", "c"]


def test_provider_stream_response_full_records_truncation_metadata() -> None:
    response = _provider_stream_response_full(
        [{"id": "chunk-1"}],
        truncated_chunk_count=3,
    )

    assert response == {
        "provider": "openai_compatible",
        "response_type": "chat.completion.chunk_stream",
        "stream": True,
        "chunks": [{"id": "chunk-1"}],
        "truncated_chunk_count": 3,
    }


def test_append_provider_payload_truncates_oversized_chunk() -> None:
    chunks: list[dict[str, object]] = []

    truncated_count = _append_provider_payload(
        chunks,
        {"huge": "x" * (MAX_RESPONSE_FULL_CHUNK_BYTES + 1)},
        truncated_chunk_count=0,
    )

    assert truncated_count == 1
    assert chunks == [{"truncated": True, "original_keys": ["huge"]}]


def test_append_provider_payload_truncates_deeply_nested_values() -> None:
    value: object = "leaf"
    for _ in range(MAX_RESPONSE_FULL_DEPTH + 1):
        value = {"nested": value}
    chunks: list[dict[str, object]] = []

    truncated_count = _append_provider_payload(
        chunks,
        {"payload": value},
        truncated_chunk_count=0,
    )

    assert truncated_count == 1
    assert "[TRUNCATED]" in str(chunks[0])


def test_observe_running_battle_replays_terminal_state_without_mutating_battle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    run_id = uuid.uuid4()
    emitted: list[tuple[str, object]] = []
    write_calls: list[tuple[str, object]] = []
    load_calls = 0

    async def immediate_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(orchestrator_module.asyncio, "sleep", immediate_sleep)

    def load_battle(_battle_id: uuid.UUID):
        nonlocal load_calls
        load_calls += 1
        if load_calls < 3:
            return SimpleNamespace(status="running"), []
        return (
            SimpleNamespace(status="completed"),
            [
                SimpleNamespace(
                    id=run_id,
                    side="A",
                    output_text="translated",
                    error_text=None,
                )
            ],
        )

    monkeypatch.setattr(orchestrator, "_load_battle_and_runs", load_battle)
    monkeypatch.setattr(
        orchestrator,
        "_mark_battle_status",
        lambda **kwargs: write_calls.append(("mark", kwargs)),
    )
    monkeypatch.setattr(
        orchestrator,
        "_persist_battle_run_errors",
        lambda **kwargs: write_calls.append(("persist", kwargs)),
    )

    async def emit(event: str, data: object) -> None:
        emitted.append((event, data))

    asyncio.run(
        orchestrator._observe_running_battle(
            battle_id=battle_id,
            emit=emit,
        )
    )

    assert write_calls == []
    assert emitted == [
        (
            "run.delta",
            {
                "battle_id": str(battle_id),
                "run_id": str(run_id),
                "side": "A",
                "text_delta": "translated",
                "replay": True,
                "chunk_index": 0,
            },
        ),
        (
            "battle.completed",
            {
                "battle_id": str(battle_id),
                "replay": True,
            },
        ),
    ]


def test_observer_timeout_fails_stuck_running_battle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    orchestrator._battle_running_wait_timeout_seconds = 1
    battle_id = uuid.uuid4()
    emitted: list[tuple[str, object]] = []
    write_calls: list[tuple[str, object]] = []
    monotonic_values = iter([100.0, 100.0, 101.1])

    async def immediate_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(orchestrator_module.asyncio, "sleep", immediate_sleep)
    monkeypatch.setattr(
        orchestrator_module,
        "time",
        SimpleNamespace(monotonic=lambda: next(monotonic_values)),
    )
    monkeypatch.setattr(
        orchestrator,
        "_load_battle_and_runs",
        lambda _battle_id: (SimpleNamespace(status="running"), []),
    )
    monkeypatch.setattr(
        orchestrator,
        "_mark_battle_status",
        lambda **kwargs: write_calls.append(("mark", kwargs)),
    )
    monkeypatch.setattr(
        orchestrator,
        "_persist_battle_run_errors",
        lambda **kwargs: write_calls.append(("persist", kwargs)),
    )

    async def emit(event: str, data: object) -> None:
        emitted.append((event, data))

    asyncio.run(
        orchestrator._observe_running_battle(
            battle_id=battle_id,
            emit=emit,
        )
    )

    assert write_calls == [
        ("mark", {"battle_id": battle_id, "status": "failed"}),
        (
            "persist",
            {
                "battle_id": battle_id,
                "error_text": "Battle observer exceeded timeout of 1s",
            },
        ),
    ]
    assert emitted == [
        (
            "battle.failed",
            {
                "battle_id": str(battle_id),
                "detail": "observer_timeout",
            },
        )
    ]


def test_bounded_history_discards_oldest_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    payload_one = sse_event(
        event="run.delta",
        data={"battle_id": str(battle_id), "index": 1, "text_delta": "xxxxx"},
    )
    payload_two = sse_event(
        event="run.delta",
        data={"battle_id": str(battle_id), "index": 2, "text_delta": "xxxxx"},
    )
    payload_three = sse_event(
        event="run.delta",
        data={"battle_id": str(battle_id), "index": 3, "text_delta": "xxxxx"},
    )

    monkeypatch.setattr(
        orchestrator_module,
        "MAX_LIVE_HISTORY_BYTES",
        len(payload_one) * 2,
    )
    orchestrator._live_battles[battle_id] = orchestrator_module._LiveBattleEntry(
        subscribers=set(),
        history=deque(),
    )

    async def exercise() -> None:
        await orchestrator._broadcast_live_battle_event(
            battle_id=battle_id,
            event="run.delta",
            data={"battle_id": str(battle_id), "index": 1, "text_delta": "xxxxx"},
        )
        await orchestrator._broadcast_live_battle_event(
            battle_id=battle_id,
            event="run.delta",
            data={"battle_id": str(battle_id), "index": 2, "text_delta": "xxxxx"},
        )
        await orchestrator._broadcast_live_battle_event(
            battle_id=battle_id,
            event="run.delta",
            data={"battle_id": str(battle_id), "index": 3, "text_delta": "xxxxx"},
        )

    asyncio.run(exercise())

    entry = orchestrator._live_battles[battle_id]
    assert list(entry.history) == [payload_two, payload_three]
    assert entry.history_bytes == len(payload_two) + len(payload_three)
    assert entry.history_bytes <= orchestrator_module.MAX_LIVE_HISTORY_BYTES


def test_load_battle_and_runs_returns_detached_safe_snapshots() -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    task_id = uuid.uuid4()
    model_id = uuid.uuid4()

    battle_row = SimpleNamespace(
        id=battle_id,
        task_id=task_id,
        status="completed",
        metadata_json={
            "task_snapshot": {
                "source_text": "JP text",
                "source_lang": "ja",
                "target_lang": "zh",
            }
        },
    )
    run_row = SimpleNamespace(
        id=uuid.uuid4(),
        battle_id=battle_id,
        side="A",
        model_id=model_id,
        output_text="translated",
        error_text=None,
    )

    class _FakeResult:
        def scalars(self) -> "_FakeResult":
            return self

        def all(self) -> list[object]:
            return [run_row]

    class _FakeDB:
        def get(self, model: type[object], key: uuid.UUID) -> object | None:
            assert model is orchestrator_module.Battle
            assert key == battle_id
            return battle_row

        def execute(self, _stmt: object) -> _FakeResult:
            return _FakeResult()

        def close(self) -> None:
            battle_row.status = "mutated"
            battle_row.metadata_json["task_snapshot"]["source_text"] = "mutated"  # type: ignore[index]
            run_row.output_text = "mutated"

    orchestrator._SessionLocal = lambda: _FakeDB()  # type: ignore[assignment]

    battle_snapshot, run_snapshots = orchestrator._load_battle_and_runs(battle_id)

    assert battle_snapshot == BattleSnapshot(
        id=battle_id,
        task_id=task_id,
        status="completed",
        metadata_json={
            "task_snapshot": {
                "source_text": "JP text",
                "source_lang": "ja",
                "target_lang": "zh",
            }
        },
    )
    assert run_snapshots == [
        RunSnapshot(
            id=run_row.id,
            battle_id=battle_id,
            side="A",
            model_id=model_id,
            output_text="translated",
            error_text=None,
        )
    ]


def test_stream_battle_disconnect_detaches_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()

    allow_finish = asyncio.Event()
    runner_finished = asyncio.Event()

    monkeypatch.setattr(
        orchestrator,
        "_load_battle_and_runs",
        lambda _battle_id: (SimpleNamespace(status="pending"), [object(), object()]),
    )

    async def fake_execute_owned_battle(
        *,
        battle_id: uuid.UUID,
        emit,
        request_id: str | None,
    ) -> None:
        _ = request_id
        await emit("battle.started", {"battle_id": str(battle_id)})
        await allow_finish.wait()
        runner_finished.set()

    monkeypatch.setattr(
        orchestrator,
        "_execute_owned_battle",
        fake_execute_owned_battle,
    )

    async def exercise() -> None:
        stream = cast(
            AsyncGenerator[bytes, None], orchestrator.stream_battle(battle_id)
        )
        first = await anext(stream)
        assert b"event: battle.started" in first

        # Client disconnect should return immediately without waiting for
        # the underlying run lifecycle to finish.
        await asyncio.wait_for(stream.aclose(), timeout=0.2)
        assert not runner_finished.is_set()

        allow_finish.set()
        await asyncio.wait_for(runner_finished.wait(), timeout=1.0)

    asyncio.run(exercise())


def test_stream_battle_second_consumer_reuses_single_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    owner_calls = 0

    owner_started = asyncio.Event()
    allow_finish = asyncio.Event()

    monkeypatch.setattr(
        orchestrator,
        "_load_battle_and_runs",
        lambda _battle_id: (SimpleNamespace(status="pending"), [object(), object()]),
    )

    async def fake_execute_owned_battle(
        *,
        battle_id: uuid.UUID,
        emit,
        request_id: str | None,
    ) -> None:
        nonlocal owner_calls
        _ = request_id
        owner_calls += 1
        await emit("battle.started", {"battle_id": str(battle_id)})
        owner_started.set()
        await allow_finish.wait()
        await emit("battle.completed", {"battle_id": str(battle_id)})

    monkeypatch.setattr(
        orchestrator,
        "_execute_owned_battle",
        fake_execute_owned_battle,
    )

    async def exercise() -> None:
        stream_a = cast(
            AsyncGenerator[bytes, None], orchestrator.stream_battle(battle_id)
        )
        first_a = await anext(stream_a)
        assert b"event: battle.started" in first_a

        await asyncio.wait_for(owner_started.wait(), timeout=1.0)

        stream_b = cast(
            AsyncGenerator[bytes, None], orchestrator.stream_battle(battle_id)
        )
        first_b = await anext(stream_b)
        assert b"event: battle.started" in first_b
        assert owner_calls == 1

        allow_finish.set()

        second_a = await anext(stream_a)
        second_b = await anext(stream_b)
        assert b"event: battle.completed" in second_a
        assert b"event: battle.completed" in second_b

        with pytest.raises(StopAsyncIteration):
            await anext(stream_a)
        with pytest.raises(StopAsyncIteration):
            await anext(stream_b)

        assert owner_calls == 1
        assert orchestrator._live_battles == {}

    asyncio.run(exercise())


def test_execute_battle_and_wait_uses_stream_terminal_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    calls: list[tuple[uuid.UUID, str | None]] = []

    async def fake_stream_battle(
        battle_id: uuid.UUID,
        *,
        request_id: str | None = None,
    ):
        calls.append((battle_id, request_id))
        yield b"event: battle.started\ndata: {}\n\n"
        yield b"event: battle.completed\ndata: {}\n\n"

    monkeypatch.setattr(orchestrator, "stream_battle", fake_stream_battle)

    result = asyncio.run(
        orchestrator.execute_battle_and_wait(
            battle_id,
            timeout_seconds=5,
            request_id="req-123",
        )
    )

    assert result == "completed"
    assert calls == [(battle_id, "req-123")]


def test_execute_battle_and_wait_returns_timeout_without_terminal_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    closed = False
    monotonic_values = iter([100.0, 100.0, 101.1])

    monkeypatch.setattr(
        orchestrator_module,
        "time",
        SimpleNamespace(monotonic=lambda: next(monotonic_values)),
    )

    async def fake_stream_battle(
        battle_id: uuid.UUID,
        *,
        request_id: str | None = None,
    ):
        _ = (battle_id, request_id)
        nonlocal closed
        try:
            yield b"event: battle.started\ndata: {}\n\n"
            await asyncio.Event().wait()
        finally:
            closed = True

    monkeypatch.setattr(orchestrator, "stream_battle", fake_stream_battle)

    result = asyncio.run(
        orchestrator.execute_battle_and_wait(
            battle_id,
            timeout_seconds=1,
        )
    )

    assert result == "timeout"
    assert closed is True


def test_execute_run_emits_deltas_and_persists_stats(
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
        api_key="secret",
        messages=[{"role": "user", "content": "Translate this"}],
        params={"temperature": 0.2},
        request_id="arena-req-1",
    )

    captured_request: dict[str, object] = {}

    class _StreamingClient:
        async def stream_chat_completion(self, **kwargs: object):
            captured_request.update(kwargs)
            yield LLMStreamChunk(text_delta="hello ", request_id="upstream-req")
            yield LLMStreamChunk(
                text_delta="world",
                usage={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
                finish_reason="stop",
                provider_payload={
                    "id": "chunk-2",
                    "choices": [
                        {
                            "delta": {"content": "world"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )

    persist_calls: list[dict[str, object]] = []

    def fake_persist(**kwargs: object) -> None:
        persist_calls.append(dict(kwargs))

    emitted: list[tuple[str, object]] = []

    async def emit(event: str, data: object) -> None:
        emitted.append((event, data))

    orchestrator._llm_client = _StreamingClient()  # type: ignore[assignment]
    monkeypatch.setattr(orchestrator, "_persist_run_result", fake_persist)

    ok = asyncio.run(orchestrator._execute_run(prepared=prepared, emit=emit))

    assert ok is True
    assert captured_request["base_url"] == "https://gateway.example/v1"
    assert captured_request["model"] == "gpt-test"
    assert captured_request["api_key"] == "secret"
    assert captured_request["messages"] == [
        {"role": "user", "content": "Translate this"}
    ]
    assert captured_request["params"] == {"temperature": 0.2}
    assert captured_request["extra_headers"] == {
        "X-Arena-Battle-ID": str(prepared.battle_id),
        "X-Arena-Run-ID": str(prepared.run_id),
        "X-Request-ID": "arena-req-1",
    }

    assert len(persist_calls) == 1
    persisted = persist_calls[0]
    assert persisted["run_id"] == prepared.run_id
    assert persisted["output_text"] == "hello world"
    assert persisted["output_text_raw"] == "hello world"
    assert persisted["response_full"] == {
        "provider": "openai_compatible",
        "response_type": "chat.completion.chunk_stream",
        "stream": True,
        "chunks": [
            {
                "id": "chunk-2",
                "choices": [
                    {
                        "delta": {"content": "world"},
                        "finish_reason": "stop",
                    }
                ],
            }
        ],
        "truncated_chunk_count": 0,
    }
    assert persisted["error_text"] is None

    stats = persisted["stats"]
    assert isinstance(stats, dict)
    assert stats["request_id"] == "upstream-req"
    assert stats["usage"] == {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
    assert stats["finish_reason"] == "stop"
    assert stats["arena_request_id"] == "arena-req-1"
    assert isinstance(stats["latency_ms"], int)
    assert stats["latency_ms"] >= 0

    assert [event for event, _ in emitted] == [
        "run.delta",
        "run.delta",
        "run.completed",
    ]
    first_delta = emitted[0][1]
    second_delta = emitted[1][1]
    completed = emitted[2][1]

    assert first_delta == {
        "battle_id": str(prepared.battle_id),
        "run_id": str(prepared.run_id),
        "side": "A",
        "text_delta": "hello ",
    }
    assert second_delta == {
        "battle_id": str(prepared.battle_id),
        "run_id": str(prepared.run_id),
        "side": "A",
        "text_delta": "world",
    }
    assert isinstance(completed, dict)
    assert completed["battle_id"] == str(prepared.battle_id)
    assert completed["run_id"] == str(prepared.run_id)
    assert completed["side"] == "A"
    assert isinstance(completed["latency_ms"], int)
    assert completed["latency_ms"] >= 0


@pytest.mark.parametrize(
    ("source_text", "upstream_parts", "expected_output"),
    [
        (
            "JP text",
            ["\ntranslated", "\n\nbody\n"],
            "translated\n\nbody\n",
        ),
        (
            "\nJP text",
            ["translated", "\n\nbody\n"],
            "\ntranslated\n\nbody\n",
        ),
        (
            "\n\nJP text",
            ["\ntranslated", "\n\nbody\n"],
            "\n\ntranslated\n\nbody\n",
        ),
    ],
)
def test_execute_run_preserves_source_leading_newline_count_in_output(
    monkeypatch: pytest.MonkeyPatch,
    source_text: str,
    upstream_parts: list[str],
    expected_output: str,
) -> None:
    orchestrator = BattleOrchestrator()
    prepared = PreparedRun(
        battle_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        side="A",
        model_id=uuid.uuid4(),
        base_url="https://gateway.example/v1",
        model_name="gpt-test",
        api_key="secret",
        messages=[{"role": "user", "content": source_text}],
        params={},
        request_id="arena-req-newlines",
        source_leading_newline_count=len(source_text) - len(source_text.lstrip("\n")),
    )

    class _StreamingClient:
        async def stream_chat_completion(self, **kwargs: object):
            _ = kwargs
            for part in upstream_parts:
                yield LLMStreamChunk(text_delta=part)
            yield LLMStreamChunk(finish_reason="stop")

    persist_calls: list[dict[str, object]] = []

    def fake_persist(**kwargs: object) -> None:
        persist_calls.append(dict(kwargs))

    emitted: list[tuple[str, object]] = []

    async def emit(event: str, data: object) -> None:
        emitted.append((event, data))

    orchestrator._llm_client = _StreamingClient()  # type: ignore[assignment]
    monkeypatch.setattr(orchestrator, "_persist_run_result", fake_persist)

    ok = asyncio.run(orchestrator._execute_run(prepared=prepared, emit=emit))

    assert ok is True
    persisted = persist_calls[0]
    upstream_output = "".join(upstream_parts)
    assert persisted["output_text"] == expected_output
    assert persisted["output_text_raw"] == upstream_output
    assert persisted["response_full"] is None

    deltas = [
        cast(dict, payload)["text_delta"]
        for event, payload in emitted
        if event == "run.delta"
    ]
    assert "".join(deltas) == expected_output
    assert expected_output.lstrip("\n") == upstream_output.lstrip("\n")


def test_execute_run_emits_error_and_returns_false_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    prepared = PreparedRun(
        battle_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        side="B",
        model_id=uuid.uuid4(),
        base_url="https://gateway.example/v1",
        model_name="gpt-test",
        api_key=None,
        messages=[{"role": "user", "content": "Translate this"}],
        params={},
        request_id="arena-req-2",
    )

    class _FailingClient:
        async def stream_chat_completion(self, **kwargs: object):
            _ = kwargs
            if False:
                yield LLMStreamChunk(text_delta="never")
            raise httpx.ConnectError("gateway timeout")

    persist_calls: list[dict[str, object]] = []

    def fake_persist(**kwargs: object) -> None:
        persist_calls.append(dict(kwargs))

    emitted: list[tuple[str, object]] = []

    async def emit(event: str, data: object) -> None:
        emitted.append((event, data))

    orchestrator._llm_client = _FailingClient()  # type: ignore[assignment]
    monkeypatch.setattr(orchestrator, "_persist_run_result", fake_persist)

    ok = asyncio.run(orchestrator._execute_run(prepared=prepared, emit=emit))

    assert ok is False
    assert len(persist_calls) == 1
    persisted = persist_calls[0]
    assert persisted["run_id"] == prepared.run_id
    assert persisted["output_text"] is None
    assert persisted["response_full"] is None
    assert isinstance(persisted["error_text"], str)
    assert "LLM HTTP error:" in persisted["error_text"]

    stats = persisted["stats"]
    assert isinstance(stats, dict)
    assert stats["request_id"] is None
    assert stats["usage"] is None
    assert stats["finish_reason"] is None
    assert stats["arena_request_id"] == "arena-req-2"
    assert isinstance(stats["latency_ms"], int)
    assert stats["latency_ms"] >= 0

    assert [event for event, _ in emitted] == ["run.error"]
    error_event = emitted[0][1]
    assert error_event == {
        "battle_id": str(prepared.battle_id),
        "run_id": str(prepared.run_id),
        "side": "B",
        "error": persisted["error_text"],
    }


def test_execute_run_persists_exception_provider_payload(
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
        api_key=None,
        messages=[{"role": "user", "content": "Translate this"}],
        params={},
        request_id="arena-req-provider-error",
    )
    provider_payload = {
        "error": {"message": "provider failed", "type": "server_error"}
    }

    class _FailingClient:
        async def stream_chat_completion(self, **kwargs: object):
            _ = kwargs
            if False:
                yield LLMStreamChunk(text_delta="never")
            exc = RuntimeError("provider failed")
            setattr(exc, "provider_payload", provider_payload)
            raise exc

    persist_calls: list[dict[str, object]] = []

    async def emit(_event: str, _data: object) -> None:
        return None

    orchestrator._llm_client = _FailingClient()  # type: ignore[assignment]
    monkeypatch.setattr(
        orchestrator,
        "_persist_run_result",
        lambda **kwargs: persist_calls.append(dict(kwargs)),
    )

    ok = asyncio.run(orchestrator._execute_run(prepared=prepared, emit=emit))

    assert ok is False
    assert persist_calls[0]["response_full"] == {
        "provider": "openai_compatible",
        "response_type": "chat.completion.chunk_stream",
        "stream": True,
        "chunks": [provider_payload],
        "truncated_chunk_count": 0,
    }


def test_execute_run_persists_partial_provider_payload_on_later_error(
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
        api_key=None,
        messages=[{"role": "user", "content": "Translate this"}],
        params={},
        request_id="arena-req-partial-provider-error",
    )
    first_payload = {
        "id": "chunk-before-error",
        "choices": [{"delta": {"content": "partial"}, "finish_reason": None}],
    }

    class _FailingAfterChunkClient:
        async def stream_chat_completion(self, **kwargs: object):
            _ = kwargs
            yield LLMStreamChunk(text_delta="partial", provider_payload=first_payload)
            raise RuntimeError("stream interrupted")

    persist_calls: list[dict[str, object]] = []
    emitted: list[tuple[str, object]] = []

    async def emit(event: str, data: object) -> None:
        emitted.append((event, data))

    orchestrator._llm_client = _FailingAfterChunkClient()  # type: ignore[assignment]
    monkeypatch.setattr(
        orchestrator,
        "_persist_run_result",
        lambda **kwargs: persist_calls.append(dict(kwargs)),
    )

    ok = asyncio.run(orchestrator._execute_run(prepared=prepared, emit=emit))

    assert ok is False
    assert persist_calls[0]["output_text"] == "partial"
    assert persist_calls[0]["output_text_raw"] == "partial"
    assert persist_calls[0]["response_full"] == {
        "provider": "openai_compatible",
        "response_type": "chat.completion.chunk_stream",
        "stream": True,
        "chunks": [first_payload],
        "truncated_chunk_count": 0,
    }
    assert [event for event, _ in emitted] == ["run.delta", "run.error"]


def test_execute_run_queue_full_uses_safe_backpressure_error(
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
        api_key=None,
        messages=[{"role": "user", "content": "Translate this"}],
        params={},
        request_id="arena-req-queue-full",
    )

    class _QueueFullClient:
        async def stream_chat_completion(self, **kwargs: object):
            _ = kwargs
            if False:
                yield LLMStreamChunk(text_delta="never")
            raise LLMQueueFullError(capacity=1)

    persist_calls: list[dict[str, object]] = []
    emitted: list[tuple[str, object]] = []

    orchestrator._llm_client = _QueueFullClient()  # type: ignore[assignment]
    monkeypatch.setattr(
        orchestrator,
        "_persist_run_result",
        lambda **kwargs: persist_calls.append(dict(kwargs)),
    )

    async def emit(event: str, data: object) -> None:
        emitted.append((event, data))

    ok = asyncio.run(orchestrator._execute_run(prepared=prepared, emit=emit))

    assert ok is False
    assert persist_calls[0]["error_text"] == "LLM queue backpressure: queue_full"
    assert emitted == [
        (
            "run.error",
            {
                "battle_id": str(prepared.battle_id),
                "run_id": str(prepared.run_id),
                "side": "A",
                "error": "LLM queue backpressure: queue_full",
            },
        )
    ]


def test_execute_run_buffered_queue_wait_timeout_preserves_taxonomy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    prepared = PreparedRun(
        battle_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        side="B",
        model_id=uuid.uuid4(),
        base_url="https://gateway.example/v1",
        model_name="gpt-test",
        api_key=None,
        messages=[{"role": "user", "content": "Translate this"}],
        params={},
        request_id="arena-req-queue-timeout",
    )
    state = orchestrator_module._RunStreamState(
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
    )

    class _QueueTimeoutClient:
        async def stream_chat_completion(self, **kwargs: object):
            _ = kwargs
            if False:
                yield LLMStreamChunk(text_delta="never")
            raise LLMQueueWaitTimeoutError(timeout_seconds=0.01)

    persist_calls: list[dict[str, object]] = []
    emitted: list[tuple[str, object]] = []

    orchestrator._llm_client = _QueueTimeoutClient()  # type: ignore[assignment]
    monkeypatch.setattr(
        orchestrator,
        "_persist_run_result",
        lambda **kwargs: persist_calls.append(dict(kwargs)),
    )

    async def emit(event: str, data: object) -> None:
        emitted.append((event, data))

    ok = asyncio.run(orchestrator._execute_run_buffered(state=state, emit=emit))

    assert ok is False
    assert state.error_text == "LLM queue backpressure: timeout_layer=llm_queue_wait"
    assert persist_calls[0]["response_full"] is None
    assert persist_calls[0]["error_text"] == state.error_text
    assert emitted[0][0] == "run.error"
    assert cast(dict[str, object], emitted[0][1])["error"] == state.error_text


def test_execute_runs_synced_emits_deltas_in_lockstep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    run_a_id = uuid.uuid4()
    run_b_id = uuid.uuid4()
    monkeypatch.setattr(orchestrator_module, "SYNC_DISPLAY_DELTA_CHARS", 2)

    prepared_a = PreparedRun(
        battle_id=battle_id,
        run_id=run_a_id,
        side="A",
        model_id=uuid.uuid4(),
        base_url="https://gateway.example/v1",
        model_name="model-a",
        api_key=None,
        messages=[{"role": "user", "content": "Translate this"}],
        params={},
        request_id="arena-req-sync",
    )
    prepared_b = PreparedRun(
        battle_id=battle_id,
        run_id=run_b_id,
        side="B",
        model_id=uuid.uuid4(),
        base_url="https://gateway.example/v1",
        model_name="model-b",
        api_key=None,
        messages=[{"role": "user", "content": "Translate this"}],
        params={},
        request_id="arena-req-sync",
    )

    class _StreamingClient:
        async def stream_chat_completion(self, *, model: str, **kwargs: object):
            _ = kwargs
            if model == "model-a":
                yield LLMStreamChunk(
                    text_delta="A1A2",
                    finish_reason="stop",
                    provider_payload={"id": "chunk-a", "model": "model-a"},
                )
                return

            # Slow side: ensure the fast side has time to run ahead.
            await asyncio.sleep(0.02)
            yield LLMStreamChunk(text_delta="B1")
            await asyncio.sleep(0.02)
            yield LLMStreamChunk(
                text_delta="B2",
                finish_reason="stop",
                provider_payload={"id": "chunk-b", "model": "model-b"},
            )

    orchestrator._llm_client = _StreamingClient()  # type: ignore[assignment]

    persisted: list[dict[str, object]] = []

    def fake_persist(**kwargs: object) -> None:
        persisted.append(dict(kwargs))

    monkeypatch.setattr(orchestrator, "_persist_run_result", fake_persist)

    emitted: list[tuple[str, object]] = []

    async def emit(event: str, data: object) -> None:
        emitted.append((event, data))

    results = asyncio.run(
        orchestrator._execute_runs_synced(
            prepared_runs=[prepared_a, prepared_b],
            emit=emit,
        )
    )

    assert results == [True, True]

    # Deltas are emitted in pairs (A then B), paced by the slow side.
    deltas = [
        (event, cast(dict, payload))
        for event, payload in emitted
        if event == "run.delta"
    ]
    assert [payload["text_delta"] for _, payload in deltas] == ["A1", "B1", "A2", "B2"]

    # Both outputs are persisted from the streamed deltas.
    outputs_by_run = {call["run_id"]: call["output_text"] for call in persisted}
    raw_outputs_by_run = {call["run_id"]: call["output_text_raw"] for call in persisted}
    responses_by_run = {call["run_id"]: call["response_full"] for call in persisted}
    assert outputs_by_run[run_a_id] == "A1A2"
    assert outputs_by_run[run_b_id] == "B1B2"
    assert raw_outputs_by_run[run_a_id] == "A1A2"
    assert raw_outputs_by_run[run_b_id] == "B1B2"
    assert responses_by_run[run_a_id] == {
        "provider": "openai_compatible",
        "response_type": "chat.completion.chunk_stream",
        "stream": True,
        "chunks": [{"id": "chunk-a", "model": "model-a"}],
        "truncated_chunk_count": 0,
    }
    assert responses_by_run[run_b_id] == {
        "provider": "openai_compatible",
        "response_type": "chat.completion.chunk_stream",
        "stream": True,
        "chunks": [{"id": "chunk-b", "model": "model-b"}],
        "truncated_chunk_count": 0,
    }


def test_execute_runs_synced_paces_active_display_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    run_a_id = uuid.uuid4()
    run_b_id = uuid.uuid4()
    min_interval = 0.01
    current_time = 100.0
    real_sleep = asyncio.sleep

    def monotonic() -> float:
        return current_time

    async def fake_sleep(delay: float) -> None:
        nonlocal current_time
        current_time += delay
        await real_sleep(0)

    monkeypatch.setattr(orchestrator_module, "SYNC_DISPLAY_DELTA_CHARS", 2)
    monkeypatch.setattr(
        orchestrator_module,
        "SYNC_REMAINING_DELTA_MIN_INTERVAL_SECONDS",
        min_interval,
    )
    monkeypatch.setattr(orchestrator_module.time, "monotonic", monotonic)
    monkeypatch.setattr(orchestrator_module.asyncio, "sleep", fake_sleep)

    prepared_a = PreparedRun(
        battle_id=battle_id,
        run_id=run_a_id,
        side="A",
        model_id=uuid.uuid4(),
        base_url="https://gateway.example/v1",
        model_name="model-a",
        api_key=None,
        messages=[{"role": "user", "content": "Translate this"}],
        params={},
        request_id="arena-req-sync",
    )
    prepared_b = PreparedRun(
        battle_id=battle_id,
        run_id=run_b_id,
        side="B",
        model_id=uuid.uuid4(),
        base_url="https://gateway.example/v1",
        model_name="model-b",
        api_key=None,
        messages=[{"role": "user", "content": "Translate this"}],
        params={},
        request_id="arena-req-sync",
    )

    class _StreamingClient:
        async def stream_chat_completion(self, *, model: str, **kwargs: object):
            _ = kwargs
            if model == "model-a":
                yield LLMStreamChunk(text_delta="A1A2A3", finish_reason="stop")
                return
            yield LLMStreamChunk(text_delta="B1B2B3", finish_reason="stop")

    monkeypatch.setattr(orchestrator, "_llm_client", _StreamingClient())
    monkeypatch.setattr(orchestrator, "_persist_run_result", lambda **_: None)

    emitted: list[tuple[str, dict[str, object], float]] = []

    async def emit(event: str, data: object) -> None:
        emitted.append((event, cast(dict[str, object], data), current_time))

    results = asyncio.run(
        orchestrator._execute_runs_synced(
            prepared_runs=[prepared_a, prepared_b],
            emit=emit,
        )
    )

    assert results == [True, True]
    deltas = [
        (cast(str, payload["text_delta"]), emitted_at)
        for event, payload, emitted_at in emitted
        if event == "run.delta"
    ]
    assert [text_delta for text_delta, _ in deltas] == [
        "A1",
        "B1",
        "A2",
        "B2",
        "A3",
        "B3",
    ]

    round_times = [deltas[index][1] for index in range(0, len(deltas), 2)]
    assert [deltas[index][1] for index in range(0, len(deltas), 2)] == [
        deltas[index + 1][1] for index in range(0, len(deltas), 2)
    ]
    round_spacings = [
        later - earlier for earlier, later in zip(round_times, round_times[1:])
    ]
    assert len(round_spacings) == 2
    assert all(spacing >= min_interval - 1e-9 for spacing in round_spacings)


def test_execute_runs_synced_paces_remaining_side_after_peer_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    run_a_id = uuid.uuid4()
    run_b_id = uuid.uuid4()
    min_interval = 0.01
    current_time = 100.0
    real_sleep = asyncio.sleep
    monkeypatch.setattr(orchestrator_module, "SYNC_DISPLAY_DELTA_CHARS", 2)

    def monotonic() -> float:
        return current_time

    async def fake_sleep(delay: float) -> None:
        nonlocal current_time
        current_time += delay
        await real_sleep(0)

    monkeypatch.setattr(orchestrator_module.time, "monotonic", monotonic)
    monkeypatch.setattr(orchestrator_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        orchestrator_module,
        "SYNC_REMAINING_DELTA_MIN_INTERVAL_SECONDS",
        min_interval,
    )

    prepared_a = PreparedRun(
        battle_id=battle_id,
        run_id=run_a_id,
        side="A",
        model_id=uuid.uuid4(),
        base_url="https://gateway.example/v1",
        model_name="model-a",
        api_key=None,
        messages=[{"role": "user", "content": "Translate this"}],
        params={},
        request_id="arena-req-sync",
    )
    prepared_b = PreparedRun(
        battle_id=battle_id,
        run_id=run_b_id,
        side="B",
        model_id=uuid.uuid4(),
        base_url="https://gateway.example/v1",
        model_name="model-b",
        api_key=None,
        messages=[{"role": "user", "content": "Translate this"}],
        params={},
        request_id="arena-req-sync",
    )

    class _StreamingClient:
        async def stream_chat_completion(self, *, model: str, **kwargs: object):
            _ = kwargs
            if model == "model-a":
                yield LLMStreamChunk(text_delta="A1", finish_reason="stop")
                return

            yield LLMStreamChunk(text_delta="B1B2B3B4", finish_reason="stop")

    monkeypatch.setattr(orchestrator, "_llm_client", _StreamingClient())

    persisted: list[dict[str, object]] = []

    def fake_persist(**kwargs: object) -> None:
        persisted.append(dict(kwargs))

    monkeypatch.setattr(
        orchestrator,
        "_persist_run_result",
        fake_persist,
    )

    emitted: list[tuple[str, dict[str, object], float]] = []

    async def emit(event: str, data: object) -> None:
        emitted.append((event, cast(dict[str, object], data), current_time))

    results = asyncio.run(
        orchestrator._execute_runs_synced(
            prepared_runs=[prepared_a, prepared_b],
            emit=emit,
        )
    )

    assert results == [True, True]
    deltas = [
        (cast(str, payload["text_delta"]), emitted_at)
        for event, payload, emitted_at in emitted
        if event == "run.delta"
    ]
    assert [text_delta for text_delta, _ in deltas] == ["A1", "B1", "B2", "B3", "B4"]

    b_delta_times = [
        emitted_at for text_delta, emitted_at in deltas if text_delta.startswith("B")
    ]
    b_delta_spacings = [
        later - earlier for earlier, later in zip(b_delta_times, b_delta_times[1:])
    ]
    assert len(b_delta_spacings) == 3
    assert all(spacing >= min_interval - 1e-9 for spacing in b_delta_spacings)

    outputs_by_run = {call["run_id"]: call["output_text"] for call in persisted}
    assert outputs_by_run[run_a_id] == "A1"
    assert outputs_by_run[run_b_id] == "B1B2B3B4"


def test_execute_runs_synced_preserves_source_leading_newline_count_in_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    run_a_id = uuid.uuid4()
    run_b_id = uuid.uuid4()
    source_a = "JP text"
    source_b = "\n\nJP text"

    prepared_a = PreparedRun(
        battle_id=battle_id,
        run_id=run_a_id,
        side="A",
        model_id=uuid.uuid4(),
        base_url="https://gateway.example/v1",
        model_name="model-a",
        api_key=None,
        messages=[{"role": "user", "content": source_a}],
        params={},
        request_id="arena-req-sync-newlines",
        source_leading_newline_count=len(source_a) - len(source_a.lstrip("\n")),
    )
    prepared_b = PreparedRun(
        battle_id=battle_id,
        run_id=run_b_id,
        side="B",
        model_id=uuid.uuid4(),
        base_url="https://gateway.example/v1",
        model_name="model-b",
        api_key=None,
        messages=[{"role": "user", "content": source_b}],
        params={},
        request_id="arena-req-sync-newlines",
        source_leading_newline_count=len(source_b) - len(source_b.lstrip("\n")),
    )

    upstream_by_model = {
        "model-a": ["\nA", "\n\nbody\n"],
        "model-b": ["\nB", "\n\nbody\n"],
    }

    class _StreamingClient:
        async def stream_chat_completion(self, *, model: str, **kwargs: object):
            _ = kwargs
            for part in upstream_by_model[model]:
                yield LLMStreamChunk(text_delta=part)
            yield LLMStreamChunk(finish_reason="stop")

    orchestrator._llm_client = _StreamingClient()  # type: ignore[assignment]

    persisted: list[dict[str, object]] = []

    def fake_persist(**kwargs: object) -> None:
        persisted.append(dict(kwargs))

    monkeypatch.setattr(orchestrator, "_persist_run_result", fake_persist)

    emitted: list[tuple[str, object]] = []

    async def emit(event: str, data: object) -> None:
        emitted.append((event, data))

    results = asyncio.run(
        orchestrator._execute_runs_synced(
            prepared_runs=[prepared_a, prepared_b],
            emit=emit,
        )
    )

    assert results == [True, True]

    expected_by_run = {
        run_a_id: "A\n\nbody\n",
        run_b_id: "\n\nB\n\nbody\n",
    }
    upstream_by_run = {
        run_a_id: "".join(upstream_by_model["model-a"]),
        run_b_id: "".join(upstream_by_model["model-b"]),
    }
    persisted_by_run = {call["run_id"]: call for call in persisted}

    assert persisted_by_run[run_a_id]["output_text"] == expected_by_run[run_a_id]
    assert persisted_by_run[run_b_id]["output_text"] == expected_by_run[run_b_id]
    assert persisted_by_run[run_a_id]["output_text_raw"] == upstream_by_run[run_a_id]
    assert persisted_by_run[run_b_id]["output_text_raw"] == upstream_by_run[run_b_id]

    deltas_by_run = {run_a_id: [], run_b_id: []}
    for event, payload in emitted:
        if event != "run.delta":
            continue
        delta = cast(dict, payload)
        deltas_by_run[uuid.UUID(cast(str, delta["run_id"]))].append(
            cast(str, delta["text_delta"])
        )

    assert "".join(deltas_by_run[run_a_id]) == expected_by_run[run_a_id]
    assert "".join(deltas_by_run[run_b_id]) == expected_by_run[run_b_id]
    assert expected_by_run[run_a_id].lstrip("\n") == upstream_by_run[run_a_id].lstrip(
        "\n"
    )
    assert expected_by_run[run_b_id].lstrip("\n") == upstream_by_run[run_b_id].lstrip(
        "\n"
    )


def test_execute_runs_synced_cancelled_error_propagates_without_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    prepared_runs = [
        PreparedRun(
            battle_id=battle_id,
            run_id=uuid.uuid4(),
            side="A",
            model_id=uuid.uuid4(),
            base_url="https://gateway.example/v1",
            model_name="model-a",
            api_key=None,
            messages=[{"role": "user", "content": "Translate this"}],
            params={},
            request_id="arena-req-cancel",
        ),
        PreparedRun(
            battle_id=battle_id,
            run_id=uuid.uuid4(),
            side="B",
            model_id=uuid.uuid4(),
            base_url="https://gateway.example/v1",
            model_name="model-b",
            api_key=None,
            messages=[{"role": "user", "content": "Translate this"}],
            params={},
            request_id="arena-req-cancel",
        ),
    ]

    class _CancellingClient:
        async def stream_chat_completion(self, **kwargs: object):
            _ = kwargs
            if False:
                yield LLMStreamChunk(text_delta="never")
            raise asyncio.CancelledError

    persist_calls: list[dict[str, object]] = []
    mark_calls: list[dict[str, object]] = []
    emitted: list[tuple[str, object]] = []

    orchestrator._llm_client = _CancellingClient()  # type: ignore[assignment]
    monkeypatch.setattr(
        orchestrator,
        "_persist_run_result",
        lambda **kwargs: persist_calls.append(dict(kwargs)),
    )
    monkeypatch.setattr(
        orchestrator,
        "_mark_battle_status",
        lambda **kwargs: mark_calls.append(dict(kwargs)),
    )

    async def emit(event: str, data: object) -> None:
        emitted.append((event, data))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            orchestrator._execute_runs_synced(
                prepared_runs=prepared_runs,
                emit=emit,
            )
        )

    assert persist_calls == []
    assert mark_calls == []
    assert [event for event, _ in emitted if event in {"run.error", "battle.failed"}] == []


def test_execute_runs_synced_fallback_cancelled_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    prepared = PreparedRun(
        battle_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        side="solo",
        model_id=uuid.uuid4(),
        base_url="https://gateway.example/v1",
        model_name="model-solo",
        api_key=None,
        messages=[{"role": "user", "content": "Translate this"}],
        params={},
        request_id="arena-req-fallback-cancel",
    )

    class _CancellingClient:
        async def stream_chat_completion(self, **kwargs: object):
            _ = kwargs
            if False:
                yield LLMStreamChunk(text_delta="never")
            raise asyncio.CancelledError

    persist_calls: list[dict[str, object]] = []
    emitted: list[tuple[str, object]] = []

    orchestrator._llm_client = _CancellingClient()  # type: ignore[assignment]
    monkeypatch.setattr(
        orchestrator,
        "_persist_run_result",
        lambda **kwargs: persist_calls.append(dict(kwargs)),
    )

    async def emit(event: str, data: object) -> None:
        emitted.append((event, data))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            orchestrator._execute_runs_synced(
                prepared_runs=[prepared],
                emit=emit,
            )
        )

    assert persist_calls == []
    assert emitted == []


def test_execute_owned_battle_synced_cancelled_error_skips_generic_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    prepared_runs = [
        PreparedRun(
            battle_id=battle_id,
            run_id=uuid.uuid4(),
            side="A",
            model_id=uuid.uuid4(),
            base_url="https://gateway.example/v1",
            model_name="model-a",
            api_key=None,
            messages=[],
            params={},
            request_id="arena-req-owned-cancel",
        ),
        PreparedRun(
            battle_id=battle_id,
            run_id=uuid.uuid4(),
            side="B",
            model_id=uuid.uuid4(),
            base_url="https://gateway.example/v1",
            model_name="model-b",
            api_key=None,
            messages=[],
            params={},
            request_id="arena-req-owned-cancel",
        ),
    ]

    class _CancellingClient:
        async def stream_chat_completion(self, **kwargs: object):
            _ = kwargs
            if False:
                yield LLMStreamChunk(text_delta="never")
            raise asyncio.CancelledError

    mark_calls: list[dict[str, object]] = []
    persist_run_calls: list[dict[str, object]] = []
    persist_battle_error_calls: list[dict[str, object]] = []
    retry_calls: list[uuid.UUID] = []
    emitted: list[tuple[str, object]] = []

    monkeypatch.setattr(
        orchestrator,
        "_load_battle_and_runs",
        lambda _battle_id: (
            SimpleNamespace(
                status="pending",
                id=battle_id,
                task_id=uuid.uuid4(),
                metadata_json=None,
            ),
            [SimpleNamespace(side="A"), SimpleNamespace(side="B")],
        ),
    )
    monkeypatch.setattr(
        orchestrator,
        "_prepare_runs_for_execution",
        lambda **_: prepared_runs,
    )
    monkeypatch.setattr(
        orchestrator,
        "_mark_battle_status",
        lambda **kwargs: mark_calls.append(dict(kwargs)),
    )
    monkeypatch.setattr(
        orchestrator,
        "_persist_run_result",
        lambda **kwargs: persist_run_calls.append(dict(kwargs)),
    )
    monkeypatch.setattr(
        orchestrator,
        "_persist_battle_run_errors",
        lambda **kwargs: persist_battle_error_calls.append(dict(kwargs)),
    )
    monkeypatch.setattr(
        orchestrator,
        "_schedule_automatic_retry_if_available",
        lambda *, battle_id: retry_calls.append(battle_id) or False,
    )
    orchestrator._llm_client = _CancellingClient()  # type: ignore[assignment]

    async def emit(event: str, data: object) -> None:
        emitted.append((event, data))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            orchestrator._execute_owned_battle(
                battle_id=battle_id,
                emit=emit,
                request_id="arena-req-owned-cancel",
            )
        )

    assert mark_calls == [{"battle_id": battle_id, "status": "running"}]
    assert persist_run_calls == []
    assert persist_battle_error_calls == []
    assert retry_calls == []
    assert [event for event, _ in emitted] == ["battle.started"]


def test_build_system_prompt_uses_default_prompt() -> None:
    orchestrator = BattleOrchestrator()
    model = SimpleNamespace(system_prompt=None)

    prompt = orchestrator._build_system_prompt(
        model=model,  # type: ignore[arg-type]
        source_text="JP text",
        source_lang="ja",
        target_lang="zh",
    )

    assert prompt == DEFAULT_SYSTEM_PROMPT
    assert "{{ source_lang }}" not in prompt
    assert "{{ target_lang }}" not in prompt


def test_build_system_prompt_uses_model_prompt_when_present() -> None:
    orchestrator = BattleOrchestrator()
    model = SimpleNamespace(
        system_prompt="Translate {{ source_lang }} to {{ target_lang }} for {{ source_text }}"
    )

    prompt = orchestrator._build_system_prompt(
        model=model,  # type: ignore[arg-type]
        source_text="JP text",
        source_lang="ja",
        target_lang="zh",
    )

    assert prompt == "Translate ja to zh for JP text"


def test_build_user_prompt_falls_back_to_source_text_when_blank() -> None:
    orchestrator = BattleOrchestrator()
    model = SimpleNamespace(user_prompt="   ")

    prompt = orchestrator._build_user_prompt(
        model=model,  # type: ignore[arg-type]
        source_text="JP text",
        source_lang="ja",
        target_lang="zh",
    )

    assert prompt == "JP text"


def test_build_user_prompt_uses_model_prompt_when_present() -> None:
    orchestrator = BattleOrchestrator()
    model = SimpleNamespace(user_prompt="Source ({{ source_lang }}): {{ source_text }}")

    prompt = orchestrator._build_user_prompt(
        model=model,  # type: ignore[arg-type]
        source_text="JP text",
        source_lang="ja",
        target_lang="zh",
    )

    assert prompt == "Source (ja): JP text"


def test_prepare_runs_for_execution_applies_independent_prompt_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    task_id = uuid.uuid4()
    model_a_id = uuid.uuid4()
    model_b_id = uuid.uuid4()
    run_a_id = uuid.uuid4()
    run_b_id = uuid.uuid4()

    battle = BattleSnapshot(
        id=battle_id,
        task_id=task_id,
        status="running",
        metadata_json={
            "task_snapshot": {
                "source_text": "JP text",
                "source_lang": "ja",
                "target_lang": "zh",
            }
        },
    )
    runs = [
        RunSnapshot(id=run_a_id, battle_id=battle_id, side="A", model_id=model_a_id),
        RunSnapshot(id=run_b_id, battle_id=battle_id, side="B", model_id=model_b_id),
    ]

    model_a = SimpleNamespace(
        id=model_a_id,
        base_url="https://gateway.example/v1",
        model_name="model-a",
        encrypted_api_key=None,
        params=None,
        temperature=None,
        frequency_penalty=None,
        presence_penalty=None,
        system_prompt="System {{ source_lang }} -> {{ target_lang }}",
        user_prompt=None,
    )
    model_b = SimpleNamespace(
        id=model_b_id,
        base_url="https://gateway.example/v1",
        model_name="model-b",
        encrypted_api_key=None,
        params=None,
        temperature=None,
        frequency_penalty=None,
        presence_penalty=None,
        system_prompt=None,
        user_prompt="User prompt: {{ source_text }}",
    )

    class _FakeDB:
        def get(self, model_type: type[object], key: uuid.UUID) -> object | None:
            if model_type is orchestrator_module.Model:
                if key == model_a_id:
                    return model_a
                if key == model_b_id:
                    return model_b
            return None

        def rollback(self) -> None:
            return None

        def close(self) -> None:
            return None

    orchestrator._SessionLocal = lambda: _FakeDB()  # type: ignore[assignment]
    monkeypatch.setattr(
        orchestrator,
        "_build_model_params",
        lambda _model: {},
    )

    prepared = orchestrator._prepare_runs_for_execution(
        battle=battle,
        runs=runs,
        request_id="req-123",
    )

    prepared_by_side = {item.side: item for item in prepared}
    assert prepared_by_side["A"].messages == [
        {"role": "system", "content": "System ja -> zh"},
        {"role": "user", "content": "JP text"},
    ]
    assert prepared_by_side["A"].prompt_rendered == {
        "system_prompt": "System ja -> zh",
        "user_prompt": "JP text",
        "source_lang": "ja",
        "target_lang": "zh",
    }

    assert prepared_by_side["B"].messages == [
        {
            "role": "system",
            "content": DEFAULT_SYSTEM_PROMPT,
        },
        {"role": "user", "content": "User prompt: JP text"},
    ]
    assert prepared_by_side["B"].prompt_rendered == {
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "user_prompt": "User prompt: JP text",
        "source_lang": "ja",
        "target_lang": "zh",
    }


def test_replay_finished_runs_emits_terminal_event_for_completed_battle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    run_a_id = uuid.uuid4()
    run_b_id = uuid.uuid4()

    runs = [
        SimpleNamespace(
            id=run_a_id,
            side="A",
            output_text="output A",
            error_text=None,
        ),
        SimpleNamespace(
            id=run_b_id,
            side="B",
            output_text="output B",
            error_text=None,
        ),
    ]
    emitted: list[tuple[str, object]] = []

    async def emit(event: str, data: object) -> None:
        emitted.append((event, data))

    asyncio.run(
        orchestrator._replay_finished_runs(
            battle_id=battle_id,
            runs=cast(list[Run], runs),
            emit=emit,
            final_event="battle.completed",
        )
    )

    events = [e for e, _ in emitted]
    assert events == ["run.delta", "run.delta", "battle.completed"]
    assert emitted[-1][1] == {"battle_id": str(battle_id), "replay": True}


def test_replay_finished_runs_emits_error_events_for_failed_battle() -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    run_id = uuid.uuid4()

    runs = [
        SimpleNamespace(
            id=run_id,
            side="A",
            output_text=None,
            error_text="LLM HTTP error: gateway timeout",
        ),
    ]
    emitted: list[tuple[str, object]] = []

    async def emit(event: str, data: object) -> None:
        emitted.append((event, data))

    asyncio.run(
        orchestrator._replay_finished_runs(
            battle_id=battle_id,
            runs=cast(list[Run], runs),
            emit=emit,
            final_event="battle.failed",
        )
    )

    events = [e for e, _ in emitted]
    assert events == ["run.error", "battle.failed"]
    assert (
        cast(dict[str, object], emitted[0][1])["error"]
        == "LLM HTTP error: gateway timeout"
    )


def test_replay_finished_runs_handles_cleared_runs_after_retry() -> None:
    """After retry clears all run artifacts, replay of the reset (pending)
    battle should emit no run deltas/errors — only the terminal event."""
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    run_id = uuid.uuid4()

    runs = [
        SimpleNamespace(
            id=run_id,
            side="A",
            output_text=None,
            error_text=None,
        ),
    ]
    emitted: list[tuple[str, object]] = []

    async def emit(event: str, data: object) -> None:
        emitted.append((event, data))

    asyncio.run(
        orchestrator._replay_finished_runs(
            battle_id=battle_id,
            runs=cast(list[Run], runs),
            emit=emit,
            final_event="battle.failed",
        )
    )

    assert emitted == [
        (
            "battle.failed",
            {"battle_id": str(battle_id), "replay": True, "detail": "replay_failed"},
        ),
    ]


def test_battle_failed_detail_emitted_on_run_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()

    def fake_prepare(*args, **kwargs) -> list[PreparedRun]:
        return [
            PreparedRun(
                battle_id=battle_id,
                run_id=uuid.uuid4(),
                side="A",
                model_id=uuid.uuid4(),
                base_url="https://gateway.example/v1",
                model_name="model-a",
                api_key=None,
                messages=[],
                params={},
                request_id="test",
            ),
            PreparedRun(
                battle_id=battle_id,
                run_id=uuid.uuid4(),
                side="B",
                model_id=uuid.uuid4(),
                base_url="https://gateway.example/v1",
                model_name="model-b",
                api_key=None,
                messages=[],
                params={},
                request_id="test",
            ),
        ]

    async def fake_execute_runs(*args, **kwargs) -> list[bool | BaseException]:
        return [False, False]

    monkeypatch.setattr(
        orchestrator,
        "_load_battle_and_runs",
        lambda _battle_id: (
            SimpleNamespace(
                status="pending", id=battle_id, task_id=uuid.uuid4(), metadata_json=None
            ),
            [SimpleNamespace(side="A"), SimpleNamespace(side="B")],
        ),
    )
    monkeypatch.setattr(orchestrator, "_prepare_runs_for_execution", fake_prepare)
    monkeypatch.setattr(orchestrator, "_execute_runs_synced", fake_execute_runs)
    mark_calls: list[dict[str, object]] = []
    retry_calls: list[uuid.UUID] = []

    monkeypatch.setattr(
        orchestrator,
        "_mark_battle_status",
        lambda **kwargs: mark_calls.append(dict(kwargs)),
    )
    monkeypatch.setattr(
        orchestrator,
        "_schedule_automatic_retry_if_available",
        lambda *, battle_id: retry_calls.append(battle_id) or False,
    )

    emitted: list[tuple[str, object]] = []

    async def emit(event: str, data: object) -> None:
        emitted.append((event, data))

    asyncio.run(
        orchestrator._execute_owned_battle(
            battle_id=battle_id,
            emit=emit,
            request_id="req-123",
        )
    )

    assert (
        "battle.failed",
        {"battle_id": str(battle_id), "detail": "run_failed"},
    ) in emitted
    assert retry_calls == [battle_id]
    assert mark_calls == [
        {"battle_id": battle_id, "status": "running"},
        {"battle_id": battle_id, "status": "failed"},
    ]


def test_execute_owned_battle_performs_single_automatic_retry_before_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    load_calls = 0
    execute_calls = 0
    retry_attempts: list[uuid.UUID] = []
    mark_calls: list[dict[str, object]] = []

    def fake_prepare(*args, **kwargs) -> list[PreparedRun]:
        return [
            PreparedRun(
                battle_id=battle_id,
                run_id=uuid.uuid4(),
                side="A",
                model_id=uuid.uuid4(),
                base_url="https://gateway.example/v1",
                model_name="model-a",
                api_key=None,
                messages=[],
                params={},
                request_id="test",
            ),
            PreparedRun(
                battle_id=battle_id,
                run_id=uuid.uuid4(),
                side="B",
                model_id=uuid.uuid4(),
                base_url="https://gateway.example/v1",
                model_name="model-b",
                api_key=None,
                messages=[],
                params={},
                request_id="test",
            ),
        ]

    def fake_load(_battle_id: uuid.UUID):
        nonlocal load_calls
        load_calls += 1
        return (
            SimpleNamespace(
                status="pending",
                id=battle_id,
                task_id=uuid.uuid4(),
                metadata_json={"automatic_retry_count": 0},
            ),
            [SimpleNamespace(side="A"), SimpleNamespace(side="B")],
        )

    async def fake_execute_runs(*args, **kwargs) -> list[bool | BaseException]:
        nonlocal execute_calls
        execute_calls += 1
        return [False, False]

    def fake_retry(*, battle_id: uuid.UUID) -> bool:
        retry_attempts.append(battle_id)
        return len(retry_attempts) == 1

    monkeypatch.setattr(orchestrator, "_load_battle_and_runs", fake_load)
    monkeypatch.setattr(orchestrator, "_prepare_runs_for_execution", fake_prepare)
    monkeypatch.setattr(orchestrator, "_execute_runs_synced", fake_execute_runs)
    monkeypatch.setattr(
        orchestrator,
        "_schedule_automatic_retry_if_available",
        fake_retry,
    )
    monkeypatch.setattr(
        orchestrator,
        "_mark_battle_status",
        lambda **kwargs: mark_calls.append(dict(kwargs)),
    )

    emitted: list[tuple[str, object]] = []

    async def emit(event: str, data: object) -> None:
        emitted.append((event, data))

    asyncio.run(
        orchestrator._execute_owned_battle(
            battle_id=battle_id,
            emit=emit,
            request_id="req-123",
        )
    )

    assert execute_calls == 2
    assert retry_attempts == [battle_id, battle_id]
    assert [event for event, _ in emitted] == [
        "battle.started",
        "battle.started",
        "battle.failed",
    ]
    assert mark_calls == [
        {"battle_id": battle_id, "status": "running"},
        {"battle_id": battle_id, "status": "running"},
        {"battle_id": battle_id, "status": "failed"},
    ]


def test_run_owned_battle_retries_owner_timeout_once_before_succeeding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    wait_for_calls = 0
    execute_calls = 0
    retry_attempts: list[uuid.UUID] = []
    fail_calls: list[dict[str, object]] = []
    closed_battle_ids: list[uuid.UUID] = []

    async def fake_execute_owned_battle(
        *,
        battle_id: uuid.UUID,
        emit,
        request_id: str | None,
    ) -> None:
        nonlocal execute_calls
        _ = (battle_id, emit, request_id)
        execute_calls += 1

    async def fake_wait_for(awaitable, *, timeout: float):
        nonlocal wait_for_calls
        _ = timeout
        wait_for_calls += 1
        if wait_for_calls == 1:
            awaitable.close()
            raise asyncio.TimeoutError
        return await awaitable

    async def fake_fail_battle_for_timeout(**kwargs: object) -> None:
        fail_calls.append(dict(kwargs))

    async def fake_close_live_battle(*, battle_id: uuid.UUID) -> None:
        closed_battle_ids.append(battle_id)

    monkeypatch.setattr(
        orchestrator, "_execute_owned_battle", fake_execute_owned_battle
    )
    monkeypatch.setattr(orchestrator_module.asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(
        orchestrator,
        "_schedule_automatic_retry_if_available",
        lambda *, battle_id: retry_attempts.append(battle_id) or True,
    )
    monkeypatch.setattr(
        orchestrator,
        "_fail_battle_for_timeout",
        fake_fail_battle_for_timeout,
    )
    monkeypatch.setattr(
        orchestrator,
        "_close_live_battle",
        fake_close_live_battle,
    )

    asyncio.run(
        orchestrator._run_owned_battle(
            battle_id=battle_id,
            request_id="req-123",
        )
    )

    assert wait_for_calls == 2
    assert execute_calls == 1
    assert retry_attempts == [battle_id]
    assert fail_calls == []
    assert closed_battle_ids == [battle_id]


def test_run_owned_battle_cancelled_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    retry_attempts: list[uuid.UUID] = []
    fail_calls: list[dict[str, object]] = []

    async def fake_execute_owned_battle(**_kwargs: object) -> None:
        raise asyncio.CancelledError

    async def fake_close_live_battle(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        orchestrator, "_execute_owned_battle", fake_execute_owned_battle
    )
    monkeypatch.setattr(
        orchestrator,
        "_schedule_automatic_retry_if_available",
        lambda *, battle_id: retry_attempts.append(battle_id) or False,
    )
    monkeypatch.setattr(
        orchestrator,
        "_fail_battle_for_timeout",
        lambda **kwargs: fail_calls.append(dict(kwargs)) or None,
    )
    monkeypatch.setattr(orchestrator, "_close_live_battle", fake_close_live_battle)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            orchestrator._run_owned_battle(
                battle_id=battle_id,
                request_id="req-cancel",
            )
        )

    assert retry_attempts == []
    assert fail_calls == []


def test_schedule_automatic_retry_clears_persisted_run_artifacts() -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    run_a = SimpleNamespace(
        battle_id=battle_id,
        side="A",
        output_text="stale",
        output_text_raw="stale raw",
        response_full={"provider": "openai_compatible", "chunks": [{"id": "old-a"}]},
        error_text="boom",
        stats={"latency_ms": 1},
        request_json={"stream": True},
        prompt_rendered={"system_prompt": "old"},
    )
    run_b = SimpleNamespace(
        battle_id=battle_id,
        side="B",
        output_text="stale",
        output_text_raw="stale raw",
        response_full={"provider": "openai_compatible", "chunks": [{"id": "old-b"}]},
        error_text="boom",
        stats={"latency_ms": 2},
        request_json={"stream": True},
        prompt_rendered={"system_prompt": "old"},
    )
    battle = SimpleNamespace(
        id=battle_id,
        status="running",
        metadata_json={"automatic_retry_count": 0},
    )

    class _FakeResult:
        def __init__(self, rows: list[object]) -> None:
            self._rows = rows

        def scalars(self) -> "_FakeResult":
            return self

        def all(self) -> list[object]:
            return list(self._rows)

    class _FakeDB:
        def __init__(self) -> None:
            self.committed = False
            self.added: list[object] = []

        def get(self, model: type[object], key: uuid.UUID) -> object | None:
            if model is orchestrator_module.Battle and key == battle_id:
                return battle
            return None

        def execute(self, _stmt: object) -> _FakeResult:
            return _FakeResult([run_a, run_b])

        def add(self, obj: object) -> None:
            self.added.append(obj)

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            return None

        def close(self) -> None:
            return None

    orchestrator._SessionLocal = lambda: _FakeDB()  # type: ignore[assignment]

    scheduled = orchestrator._schedule_automatic_retry_if_available(battle_id=battle_id)

    assert scheduled is True
    assert battle.status == "pending"
    assert battle.metadata_json == {"automatic_retry_count": 1}
    for run in (run_a, run_b):
        assert run.output_text is None
        assert run.output_text_raw is None
        assert run.response_full is None
        assert run.error_text is None
        assert run.stats is None
        assert run.request_json is None
        assert run.prompt_rendered is None


# ── Task 4/5 regression: retry-reset battle gets fresh execution ──


def test_retry_reset_battle_gets_fresh_owned_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a retry resets a battle to pending (clearing all run artifacts),
    a new stream_battle call must start a fresh owned execution — not replay
    stale state.  This protects the single-owner invariant from Task 4/5:
    the orchestrator must detect a pending battle with two runs and launch
    _execute_owned_battle, producing live deltas instead of a silent replay."""
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    run_a_id = uuid.uuid4()
    run_b_id = uuid.uuid4()
    owner_calls = 0

    # Simulate a retry-reset battle: status=pending, runs exist but all
    # artifact fields are None (cleared by the retry endpoint).
    def load_battle(_battle_id: uuid.UUID):
        return (
            SimpleNamespace(status="pending"),
            [
                SimpleNamespace(
                    id=run_a_id,
                    side="A",
                    output_text=None,
                    error_text=None,
                ),
                SimpleNamespace(
                    id=run_b_id,
                    side="B",
                    output_text=None,
                    error_text=None,
                ),
            ],
        )

    monkeypatch.setattr(orchestrator, "_load_battle_and_runs", load_battle)

    async def fake_execute_owned_battle(
        *,
        battle_id: uuid.UUID,
        emit,
        request_id: str | None,
    ) -> None:
        nonlocal owner_calls
        _ = request_id
        owner_calls += 1
        await emit(
            "run.delta",
            {
                "battle_id": str(battle_id),
                "run_id": str(run_a_id),
                "side": "A",
                "text_delta": "fresh output",
            },
        )
        await emit("battle.completed", {"battle_id": str(battle_id)})

    monkeypatch.setattr(
        orchestrator,
        "_execute_owned_battle",
        fake_execute_owned_battle,
    )

    async def exercise() -> None:
        stream = cast(
            AsyncGenerator[bytes, None], orchestrator.stream_battle(battle_id)
        )
        events: list[bytes] = []
        async for chunk in stream:
            events.append(chunk)

        assert owner_calls == 1, "Retry-reset battle must trigger fresh owned execution"
        event_text = b"".join(events).decode()
        assert "run.delta" in event_text
        assert "fresh output" in event_text
        assert "battle.completed" in event_text

    asyncio.run(exercise())


def test_stream_queue_full_after_owner_start_persists_failure_and_closes_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    run_a_id = uuid.uuid4()
    run_b_id = uuid.uuid4()
    mark_calls: list[dict[str, object]] = []
    persist_calls: list[dict[str, object]] = []
    retry_calls: list[uuid.UUID] = []

    prepared_runs = [
        PreparedRun(
            battle_id=battle_id,
            run_id=run_a_id,
            side="A",
            model_id=uuid.uuid4(),
            base_url="https://gateway.example/v1",
            model_name="model-a",
            api_key=None,
            messages=[],
            params={},
            request_id="queue-full-stream",
        ),
        PreparedRun(
            battle_id=battle_id,
            run_id=run_b_id,
            side="B",
            model_id=uuid.uuid4(),
            base_url="https://gateway.example/v1",
            model_name="model-b",
            api_key=None,
            messages=[],
            params={},
            request_id="queue-full-stream",
        ),
    ]

    monkeypatch.setattr(
        orchestrator,
        "_load_battle_and_runs",
        lambda _battle_id: (
            SimpleNamespace(
                status="pending",
                id=battle_id,
                task_id=uuid.uuid4(),
                metadata_json={"automatic_retry_count": 1},
            ),
            [SimpleNamespace(side="A"), SimpleNamespace(side="B")],
        ),
    )
    monkeypatch.setattr(orchestrator, "_prepare_runs_for_execution", lambda **_: prepared_runs)
    monkeypatch.setattr(
        orchestrator,
        "_mark_battle_status",
        lambda **kwargs: mark_calls.append(dict(kwargs)),
    )
    monkeypatch.setattr(
        orchestrator,
        "_persist_run_result",
        lambda **kwargs: persist_calls.append(dict(kwargs)),
    )
    monkeypatch.setattr(
        orchestrator,
        "_schedule_automatic_retry_if_available",
        lambda *, battle_id: retry_calls.append(battle_id) or False,
    )

    class _QueueFullClient:
        async def stream_chat_completion(self, **kwargs: object):
            _ = kwargs
            if False:
                yield LLMStreamChunk(text_delta="never")
            raise LLMQueueFullError(capacity=1)

    orchestrator._llm_client = _QueueFullClient()  # type: ignore[assignment]

    async def exercise() -> str:
        chunks: list[bytes] = []
        async for payload in orchestrator.stream_battle(battle_id):
            chunks.append(payload)
        return b"".join(chunks).decode()

    event_text = asyncio.run(exercise())

    assert "event: battle.started" in event_text
    assert "event: run.error" in event_text
    assert "LLM queue backpressure: queue_full" in event_text
    assert "event: battle.failed" in event_text
    assert "run_failed" in event_text
    assert mark_calls == [
        {"battle_id": battle_id, "status": "running"},
        {"battle_id": battle_id, "status": "failed"},
    ]
    assert retry_calls == [battle_id]
    assert [call["error_text"] for call in persist_calls] == [
        "LLM queue backpressure: queue_full",
        "LLM queue backpressure: queue_full",
    ]
    assert [call["response_full"] for call in persist_calls] == [None, None]
    assert orchestrator._live_battles == {}
