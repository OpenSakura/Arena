from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import cast
import uuid

import httpx
import pytest

from app.services import battle_orchestrator as orchestrator_module
from app.services.battle_orchestrator import (
    BattleOrchestrator,
    PreparedRun,
    TRANSLATION_ONLY_POLICY,
    _iter_text_chunks,
    _task_payload_from_battle_metadata,
)
from app.services.llm_client import LLMStreamChunk


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


def test_finalize_running_battle_status_keeps_existing_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    mark_calls: list[tuple[uuid.UUID, str]] = []

    monkeypatch.setattr(
        orchestrator,
        "_try_transition_battle_status",
        lambda **_: False,
    )
    monkeypatch.setattr(
        orchestrator,
        "_load_battle_and_runs",
        lambda _battle_id: (SimpleNamespace(status="failed"), []),
    )
    monkeypatch.setattr(
        orchestrator,
        "_mark_battle_status",
        lambda *, battle_id, status: mark_calls.append((battle_id, status)),
    )

    resolved = orchestrator._finalize_running_battle_status(
        battle_id=battle_id,
        desired_status="completed",
    )

    assert resolved == "failed"
    assert mark_calls == []


def test_finalize_running_battle_status_forces_terminal_status_on_unexpected_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    mark_calls: list[tuple[uuid.UUID, str]] = []

    monkeypatch.setattr(
        orchestrator,
        "_try_transition_battle_status",
        lambda **_: False,
    )
    monkeypatch.setattr(
        orchestrator,
        "_load_battle_and_runs",
        lambda _battle_id: (SimpleNamespace(status="running"), []),
    )
    monkeypatch.setattr(
        orchestrator,
        "_mark_battle_status",
        lambda *, battle_id, status: mark_calls.append((battle_id, status)),
    )

    resolved = orchestrator._finalize_running_battle_status(
        battle_id=battle_id,
        desired_status="failed",
    )

    assert resolved == "failed"
    assert mark_calls == [(battle_id, "failed")]


def test_wait_for_running_battle_timeout_marks_stuck_battle_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    emitted: list[tuple[str, object]] = []
    transition_calls: list[dict[str, object]] = []
    persist_error_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        orchestrator_module,
        "get_settings",
        lambda: SimpleNamespace(battle_running_wait_timeout_seconds=1),
    )

    async def immediate_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(orchestrator_module.asyncio, "sleep", immediate_sleep)
    monkeypatch.setattr(
        orchestrator,
        "_load_battle_and_runs",
        lambda _battle_id: (SimpleNamespace(status="running"), []),
    )
    monkeypatch.setattr(
        orchestrator,
        "_try_transition_battle_status",
        lambda **kwargs: transition_calls.append(kwargs) or True,
    )
    monkeypatch.setattr(
        orchestrator,
        "_persist_battle_run_errors",
        lambda **kwargs: persist_error_calls.append(kwargs),
    )

    async def emit(event: str, data: object) -> None:
        emitted.append((event, data))

    asyncio.run(
        orchestrator._wait_for_running_battle(
            battle_id=battle_id,
            emit=emit,
        )
    )

    assert transition_calls == [
        {
            "battle_id": battle_id,
            "from_status": "running",
            "to_status": "failed",
        }
    ]
    assert persist_error_calls == [
        {
            "battle_id": battle_id,
            "error_text": "timed_out_waiting_for_running_battle",
        }
    ]
    assert emitted == [
        (
            "battle.failed",
            {
                "battle_id": str(battle_id),
                "detail": "timed_out_waiting_for_running_battle",
            },
        )
    ]


def test_wait_for_running_battle_timeout_replays_terminal_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()
    run_id = uuid.uuid4()
    emitted: list[tuple[str, object]] = []
    load_calls = 0

    monkeypatch.setattr(
        orchestrator_module,
        "get_settings",
        lambda: SimpleNamespace(battle_running_wait_timeout_seconds=1),
    )

    async def immediate_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(orchestrator_module.asyncio, "sleep", immediate_sleep)

    def load_battle(_battle_id: uuid.UUID):
        nonlocal load_calls
        load_calls += 1
        if load_calls <= 3:
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
        "_try_transition_battle_status",
        lambda **_: False,
    )

    async def emit(event: str, data: object) -> None:
        emitted.append((event, data))

    asyncio.run(
        orchestrator._wait_for_running_battle(
            battle_id=battle_id,
            emit=emit,
        )
    )

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


def test_stream_battle_disconnect_detaches_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = BattleOrchestrator()
    battle_id = uuid.uuid4()

    allow_finish = asyncio.Event()
    runner_finished = asyncio.Event()

    async def fake_stream_impl(
        *,
        battle_id: uuid.UUID,
        emit,
        request_id: str | None,
    ) -> None:
        _ = request_id
        await emit("battle.started", {"battle_id": str(battle_id)})
        await allow_finish.wait()
        runner_finished.set()

    monkeypatch.setattr(orchestrator, "_stream_battle_impl", fake_stream_impl)

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
    assert prompt.endswith(TRANSLATION_ONLY_POLICY)


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
    assert prompt == f"Rendered prompt\n\n{TRANSLATION_ONLY_POLICY}"


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
