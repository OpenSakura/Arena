from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import contextvars
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core import logging as app_logging
import app.core.config as config_module
import app.main as main
import app.utils.llm_queue as llm_queue_module
from app.utils.llm_queue import (
    LLMQueueFullError,
    LLMQueueShutdownError,
    LLMQueueWaitTimeoutError,
    LLMRequestQueue,
)


def _queue(
    *,
    max_concurrent: int = 1,
    capacity: int = 10,
    wait_timeout_seconds: float = 0.5,
    shutdown_timeout_seconds: float = 0.5,
) -> LLMRequestQueue:
    return LLMRequestQueue(
        name="test_llm_requests",
        max_concurrent=max_concurrent,
        capacity=capacity,
        wait_timeout_seconds=wait_timeout_seconds,
        shutdown_timeout_seconds=shutdown_timeout_seconds,
    )


def test_success_returns_result_and_cleans_up_in_flight() -> None:
    async def _run() -> LLMRequestQueue:
        queue = _queue()

        async def provider(value: str) -> str:
            return f"ok:{value}"

        assert await queue.submit(provider, "a") == "ok:a"
        assert queue.stats()["queued"] == 0
        assert queue.stats()["in_flight"] == 0
        await queue.stop()
        return queue

    queue = asyncio.run(_run())
    assert queue.pending_task_count() == 0


def test_queue_full_is_deterministic_and_redacted() -> None:
    async def _run() -> str:
        queue = _queue(capacity=1)
        blocker = asyncio.Event()

        async def provider() -> None:
            await blocker.wait()

        first = asyncio.create_task(queue.submit(provider))
        await asyncio.sleep(0)
        second = asyncio.create_task(queue.submit(provider))
        await asyncio.sleep(0)
        with pytest.raises(LLMQueueFullError) as exc_info:
            await queue.submit(provider)
        blocker.set()
        await asyncio.gather(first, second)
        await queue.stop()
        return str(exc_info.value)

    message = asyncio.run(_run())
    assert "capacity=1" in message
    assert "secret prompt" not in message
    assert "sk-secret" not in message


def test_queue_full_uses_configured_singleton_capacity() -> None:
    async def _run() -> dict[str, int | bool]:
        config_module.get_settings.cache_clear()
        await llm_queue_module.reset_llm_request_queue_for_tests()
        queue = llm_queue_module.get_llm_request_queue()
        blocker = asyncio.Event()
        tasks: list[asyncio.Task[None]] = []

        async def provider() -> None:
            await blocker.wait()

        try:
            for _ in range(queue.max_concurrent):
                tasks.append(
                    asyncio.create_task(
                        queue.submit(provider, wait_timeout_seconds=5.0)
                    )
                )
            for _ in range(100):
                if queue.stats()["in_flight"] == queue.max_concurrent:
                    break
                await asyncio.sleep(0.01)

            for _ in range(queue.capacity):
                tasks.append(
                    asyncio.create_task(
                        queue.submit(provider, wait_timeout_seconds=5.0)
                    )
                )

            for _ in range(100):
                stats = queue.stats()
                if (
                    stats["in_flight"] == queue.max_concurrent
                    and stats["queued"] == queue.capacity
                ):
                    break
                await asyncio.sleep(0.01)

            stats = queue.stats()
            assert stats["in_flight"] == queue.max_concurrent
            assert stats["queued"] == queue.capacity
            with pytest.raises(LLMQueueFullError):
                await queue.submit(provider, wait_timeout_seconds=5.0)
            return queue.stats()
        finally:
            blocker.set()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await llm_queue_module.reset_llm_request_queue_for_tests()

    stats = asyncio.run(_run())
    assert stats["capacity"] >= 1
    assert stats["max_concurrent"] >= 1
    assert stats["rejected"] == 1


def test_wait_timeout_cancels_queued_work_before_execution() -> None:
    async def _run() -> tuple[list[str], dict[str, int | bool]]:
        queue = _queue(capacity=2, wait_timeout_seconds=0.01)
        blocker = asyncio.Event()
        executed: list[str] = []

        async def first_provider() -> None:
            await blocker.wait()

        async def timed_out_provider() -> None:
            executed.append("timed-out")

        first = asyncio.create_task(queue.submit(first_provider))
        await asyncio.sleep(0)
        with pytest.raises(LLMQueueWaitTimeoutError):
            await queue.submit(timed_out_provider)
        blocker.set()
        await first
        await asyncio.sleep(0)
        stats = queue.stats()
        await queue.stop()
        return executed, stats

    executed, stats = asyncio.run(_run())
    assert executed == []
    assert stats["queued"] == 0
    assert stats["in_flight"] == 0


def test_cancelled_queued_job_releases_capacity_for_new_work() -> None:
    async def _run() -> tuple[str, dict[str, int | bool]]:
        queue = _queue(capacity=1, wait_timeout_seconds=1.0)
        blocker = asyncio.Event()

        async def first_provider() -> None:
            await blocker.wait()

        async def queued_provider() -> None:
            raise AssertionError("cancelled queued provider should not execute")

        async def replacement_provider() -> str:
            return "replacement"

        first = asyncio.create_task(queue.submit(first_provider))
        await asyncio.sleep(0)
        queued = asyncio.create_task(queue.submit(queued_provider))
        await asyncio.sleep(0)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued

        replacement = asyncio.create_task(queue.submit(replacement_provider))
        blocker.set()
        await first
        result = await replacement
        stats = queue.stats()
        await queue.stop()
        return result, stats

    result, stats = asyncio.run(_run())
    assert result == "replacement"
    assert stats["queued"] == 0
    assert stats["in_flight"] == 0


def test_wait_timeout_releases_capacity_for_new_work() -> None:
    async def _run() -> tuple[str, dict[str, int | bool]]:
        queue = _queue(capacity=1, wait_timeout_seconds=0.01)
        blocker = asyncio.Event()

        async def first_provider() -> None:
            await blocker.wait()

        async def timed_out_provider() -> None:
            raise AssertionError("timed-out provider should not execute")

        async def replacement_provider() -> str:
            return "replacement"

        first = asyncio.create_task(queue.submit(first_provider))
        await asyncio.sleep(0)
        timed_out = asyncio.create_task(queue.submit(timed_out_provider))
        await asyncio.sleep(0)
        with pytest.raises(LLMQueueWaitTimeoutError):
            await timed_out

        replacement = asyncio.create_task(queue.submit(replacement_provider))
        blocker.set()
        await first
        result = await replacement
        stats = queue.stats()
        await queue.stop()
        return result, stats

    result, stats = asyncio.run(_run())
    assert result == "replacement"
    assert stats["queued"] == 0
    assert stats["in_flight"] == 0


def test_queued_cancellation_skips_provider_call() -> None:
    async def _run() -> list[str]:
        queue = _queue(capacity=2, wait_timeout_seconds=1.0)
        blocker = asyncio.Event()
        calls: list[str] = []

        async def first_provider() -> None:
            await blocker.wait()

        async def queued_provider() -> None:
            calls.append("queued")

        first = asyncio.create_task(queue.submit(first_provider))
        await asyncio.sleep(0)
        queued = asyncio.create_task(queue.submit(queued_provider))
        await asyncio.sleep(0)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        blocker.set()
        await first
        await asyncio.sleep(0)
        await queue.stop()
        return calls

    assert asyncio.run(_run()) == []


def test_in_flight_cancellation_cancels_provider_task() -> None:
    async def _run() -> bool:
        queue = _queue(wait_timeout_seconds=1.0)
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def provider() -> None:
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(queue.submit(provider))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(cancelled.wait(), timeout=1.0)
        await queue.stop()
        return cancelled.is_set()

    assert asyncio.run(_run()) is True


def test_contextvars_propagate_to_provider_execution() -> None:
    trace_var: contextvars.ContextVar[str] = contextvars.ContextVar(
        "test_trace_id",
        default="-",
    )

    async def _run() -> tuple[str, str]:
        queue = _queue()

        async def provider() -> tuple[str, str]:
            return app_logging.get_request_id(), trace_var.get()

        request_token = app_logging.set_request_id("req-queue-context")
        trace_token = trace_var.set("trace-queue-context")
        try:
            return await queue.submit(provider)
        finally:
            app_logging.clear_request_id(request_token)
            trace_var.reset(trace_token)
            await queue.stop()

    assert asyncio.run(_run()) == ("req-queue-context", "trace-queue-context")


def test_priority_ordering_prefers_lower_priority_value() -> None:
    async def _run() -> list[str]:
        queue = _queue(capacity=3, wait_timeout_seconds=1.0)
        blocker = asyncio.Event()
        order: list[str] = []

        async def first_provider() -> None:
            await blocker.wait()

        async def provider(name: str) -> str:
            order.append(name)
            return name

        first = asyncio.create_task(queue.submit(first_provider))
        await asyncio.sleep(0)
        low = asyncio.create_task(queue.submit(provider, "low", queue_priority=10))
        high = asyncio.create_task(queue.submit(provider, "high", queue_priority=-10))
        await asyncio.sleep(0)
        blocker.set()
        await asyncio.gather(first, low, high)
        await queue.stop()
        return order

    assert asyncio.run(_run()) == ["high", "low"]


def test_stats_are_safe_counts_only_and_track_rejections() -> None:
    async def _run() -> dict[str, int | bool]:
        queue = _queue(capacity=1, max_concurrent=1)
        blocker = asyncio.Event()

        async def provider() -> None:
            await blocker.wait()

        first = asyncio.create_task(queue.submit(provider))
        await asyncio.sleep(0)
        second = asyncio.create_task(queue.submit(provider))
        await asyncio.sleep(0)
        with pytest.raises(LLMQueueFullError):
            await queue.submit(provider)
        stats = queue.stats()
        blocker.set()
        await asyncio.gather(first, second)
        await queue.stop()
        return stats

    stats = asyncio.run(_run())
    assert stats == {
        "queued": 1,
        "in_flight": 1,
        "capacity": 1,
        "max_concurrent": 1,
        "rejected": 1,
        "stopped": False,
    }


def test_shutdown_cancels_queued_and_in_flight_tasks() -> None:
    async def _run() -> tuple[bool, int]:
        queue = _queue(capacity=2, wait_timeout_seconds=1.0)
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def in_flight_provider() -> None:
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        async def queued_provider() -> None:
            raise AssertionError("queued provider should not execute")

        running = asyncio.create_task(queue.submit(in_flight_provider))
        await started.wait()
        queued = asyncio.create_task(queue.submit(queued_provider))
        await asyncio.sleep(0)
        await queue.stop()

        for task in (running, queued):
            with pytest.raises(asyncio.CancelledError):
                await task
        return cancelled.is_set(), queue.pending_task_count()

    assert asyncio.run(_run()) == (True, 0)


def test_submit_after_shutdown_raises_shutdown_error() -> None:
    async def _run() -> None:
        queue = _queue()
        await queue.stop()

        async def provider() -> None:
            return None

        with pytest.raises(LLMQueueShutdownError):
            await queue.submit(provider)

    asyncio.run(_run())


def test_stop_without_in_flight_cancel_allows_provider_to_finish() -> None:
    async def _run() -> tuple[bool, str, int]:
        queue = _queue(wait_timeout_seconds=1.0, shutdown_timeout_seconds=0.01)
        started = asyncio.Event()
        release = asyncio.Event()
        completed = asyncio.Event()
        cancelled = False

        async def provider() -> str:
            nonlocal cancelled
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled = True
                raise
            completed.set()
            return "finished"

        task = asyncio.create_task(queue.submit(provider))
        await started.wait()
        await queue.stop(cancel_in_flight=False, timeout_seconds=0.01)
        assert not task.done()
        release.set()
        result = await asyncio.wait_for(task, timeout=1.0)
        await asyncio.wait_for(completed.wait(), timeout=1.0)
        return cancelled, result, queue.pending_task_count()

    assert asyncio.run(_run()) == (False, "finished", 0)


def test_fastapi_shutdown_invokes_llm_queue_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[float | None] = []
    settings = SimpleNamespace(
        app_name="OpenSakura Arena API (queue test)",
        app_env="test",
        leaderboard_refresh_enabled=False,
        access_log_enabled=False,
        cors_allow_origins=["http://localhost:3000"],
        api_v1_prefix="/api/v1",
        rate_limit_redis_url="",
        web_concurrency=1,
        llm_queue_shutdown_timeout_seconds=0.25,
        trust_x_forwarded_for=False,
    )

    async def fake_stop_llm_request_queue(*, timeout_seconds: float | None) -> None:
        calls.append(timeout_seconds)

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "configure_logging", lambda _settings: None)
    monkeypatch.setattr(main, "bootstrap_schema", lambda: None)
    monkeypatch.setattr(main, "acquire_battle_process_lock", lambda: None)
    monkeypatch.setattr(main, "release_battle_process_lock", lambda: None)
    monkeypatch.setattr(main, "stop_llm_request_queue", fake_stop_llm_request_queue)
    monkeypatch.setattr(main, "close_all_redis_clients", lambda: None)
    monkeypatch.setattr(main, "init_tracing", lambda settings: None)
    monkeypatch.setattr(main, "shutdown_tracing", lambda: None)

    app = main.create_app()
    with TestClient(app):
        pass

    assert calls == [0.25]


def test_stream_success_and_cancel() -> None:
    async def _run() -> tuple[list[str], bool]:
        queue = _queue(wait_timeout_seconds=1.0)
        cancelled = asyncio.Event()
        chunks: list[str] = []

        async def stream_provider() -> AsyncIterator[str]:
            try:
                yield "a"
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        async def consumer() -> None:
            async for chunk in queue.stream(stream_provider):
                chunks.append(chunk)
                break

        task = asyncio.create_task(consumer())
        await task
        await asyncio.wait_for(cancelled.wait(), timeout=1.0)
        await queue.stop()
        return chunks, cancelled.is_set()

    assert asyncio.run(_run()) == (["a"], True)


def test_stream_cancellation_while_waiting_leaves_no_orphan_get_task() -> None:
    async def _run() -> tuple[bool, int]:
        queue = _queue(wait_timeout_seconds=1.0)
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def stream_provider() -> AsyncIterator[str]:
            started.set()
            try:
                await asyncio.sleep(60)
                yield "never"
            except asyncio.CancelledError:
                cancelled.set()
                raise

        async def consumer() -> None:
            async for _chunk in queue.stream(stream_provider):
                pass

        task = asyncio.create_task(consumer())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(cancelled.wait(), timeout=1.0)
        await queue.stop()
        current = asyncio.current_task()
        pending_get_tasks = [
            task
            for task in asyncio.all_tasks()
            if task is not current
            and not task.done()
            and "Queue.get" in repr(task.get_coro())
        ]
        return cancelled.is_set(), len(pending_get_tasks)

    assert asyncio.run(_run()) == (True, 0)
