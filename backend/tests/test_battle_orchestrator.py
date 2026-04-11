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
    PreparedRun,
    RunSnapshot,
    _iter_text_chunks,
    _task_payload_from_battle_metadata,
)
from app.models.battle import Run
from app.services.llm_client import LLMStreamChunk
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


def test_execute_runs_synced_emits_deltas_in_lockstep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    run_a_id = uuid.uuid4()
    run_b_id = uuid.uuid4()

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
                yield LLMStreamChunk(text_delta="A1")
                yield LLMStreamChunk(text_delta="A2", finish_reason="stop")
                return

            # Slow side: ensure the fast side has time to run ahead.
            await asyncio.sleep(0.02)
            yield LLMStreamChunk(text_delta="B1")
            await asyncio.sleep(0.02)
            yield LLMStreamChunk(text_delta="B2", finish_reason="stop")

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
    assert outputs_by_run[run_a_id] == "A1A2"
    assert outputs_by_run[run_b_id] == "B1B2"


def test_build_system_prompt_uses_default_prompt_when_model_has_no_template() -> None:
    orchestrator = BattleOrchestrator()
    model = SimpleNamespace(prompt_template_id=None)

    prompt = orchestrator._build_system_prompt(
        db=object(),  # type: ignore[arg-type]
        model=model,  # type: ignore[arg-type]
        source_text="JP text",
        source_lang="ja",
        target_lang="zh",
    )

    assert "Translate the user input from ja to zh" in prompt
    assert "Output policy" not in prompt


def test_build_system_prompt_renders_bound_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    template_id = uuid.uuid4()
    model_id = uuid.uuid4()
    model = SimpleNamespace(id=model_id, prompt_template_id=template_id)
    captured: dict[str, object] = {}

    class _PromptDB:
        def __init__(self) -> None:
            self.calls: list[tuple[type[object], uuid.UUID]] = []

        def get(self, model_type: type[object], key: uuid.UUID) -> object | None:
            self.calls.append((model_type, key))
            return SimpleNamespace(template_text="Template: {source_text}")

    def fake_render(template_text: str, inputs: dict[str, object]) -> str:
        captured["template_text"] = template_text
        captured["inputs"] = inputs
        return "Rendered prompt"

    db = _PromptDB()
    monkeypatch.setattr(orchestrator_module, "render_prompt_template", fake_render)

    prompt = orchestrator._build_system_prompt(
        db=db,  # type: ignore[arg-type]
        model=model,  # type: ignore[arg-type]
        source_text="JP text",
        source_lang="ja",
        target_lang="zh",
    )

    assert db.calls == [(orchestrator_module.PromptTemplate, template_id)]
    assert captured == {
        "template_text": "Template: {source_text}",
        "inputs": {
            "source_text": "JP text",
            "source_lang": "ja",
            "target_lang": "zh",
        },
    }
    assert prompt == "Rendered prompt"


def test_build_system_prompt_raises_when_bound_template_is_missing() -> None:
    orchestrator = BattleOrchestrator()
    template_id = uuid.uuid4()
    model_id = uuid.uuid4()
    model = SimpleNamespace(id=model_id, prompt_template_id=template_id)
    db = SimpleNamespace(get=lambda _model, _key: None)

    with pytest.raises(RuntimeError) as exc_info:
        orchestrator._build_system_prompt(
            db=db,  # type: ignore[arg-type]
            model=model,  # type: ignore[arg-type]
            source_text="JP text",
            source_lang="ja",
            target_lang="zh",
        )

    detail = str(exc_info.value)
    assert str(template_id) in detail
    assert str(model_id) in detail


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
    monkeypatch.setattr(orchestrator, "_mark_battle_status", lambda **kwargs: None)

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
