from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import contextvars
from contextlib import suppress
from dataclasses import dataclass
import logging
import time
from typing import Any, Generic, TypeVar, cast

from app.core.config import get_settings
from app.core.logging import get_request_id
from app.utils.tracing import add_span_event, set_span_attributes, traced_span


logger = logging.getLogger(__name__)

T = TypeVar("T")
QueueCallable = Callable[..., Awaitable[T]]
QueueStreamCallable = Callable[..., AsyncIterator[T]]


class LLMQueueError(RuntimeError):
    pass


class LLMQueueFullError(LLMQueueError):
    def __init__(self, *, capacity: int) -> None:
        super().__init__(f"LLM request queue is full (capacity={capacity})")
        self.capacity = capacity


class LLMQueueWaitTimeoutError(LLMQueueError):
    def __init__(self, *, timeout_seconds: float) -> None:
        super().__init__(
            "LLM timeout layer=llm_queue_wait exceeded "
            f"after {timeout_seconds:.3g}s"
        )
        self.timeout_layer = "llm_queue_wait"
        self.timeout_seconds = timeout_seconds


class LLMQueueShutdownError(LLMQueueError):
    def __init__(self) -> None:
        super().__init__("LLM request queue is shutting down")


@dataclass(slots=True)
class _StreamValue(Generic[T]):
    value: T


@dataclass(slots=True)
class _StreamError:
    exc: Exception


_STREAM_END = object()
_NO_STREAM_ITEM = object()
_StreamItem = _StreamValue[Any] | _StreamError | object


@dataclass(slots=True)
class _QueueJob(Generic[T]):
    ctx: contextvars.Context
    func: QueueCallable[T] | QueueStreamCallable[T]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    result_future: asyncio.Future[T | None]
    started_future: asyncio.Future[float]
    queued_at: float
    stream_queue: asyncio.Queue[_StreamItem] | None = None


QueueItem = tuple[int, int, _QueueJob[Any]]


class LLMRequestQueue:
    def __init__(
        self,
        *,
        name: str,
        max_concurrent: int,
        capacity: int,
        wait_timeout_seconds: float,
        shutdown_timeout_seconds: float,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        if capacity < 1:
            raise ValueError("capacity must be >= 1")

        self.name = name
        self.max_concurrent = max_concurrent
        self.capacity = capacity
        self.wait_timeout_seconds = max(float(wait_timeout_seconds), 0.0)
        self.shutdown_timeout_seconds = max(float(shutdown_timeout_seconds), 0.0)

        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.PriorityQueue[QueueItem] | None = None
        self._workers: set[asyncio.Task[None]] = set()
        self._in_flight: set[asyncio.Task[None]] = set()
        self._sequence = 0
        self._rejected = 0
        self._stopped = False

    @property
    def stopped(self) -> bool:
        return self._stopped

    def stats(self) -> dict[str, int | bool]:
        self._drop_done_queued_jobs()
        return {
            "queued": self._queue.qsize() if self._queue is not None else 0,
            "in_flight": len(self._in_flight),
            "capacity": self.capacity,
            "max_concurrent": self.max_concurrent,
            "rejected": self._rejected,
            "stopped": self._stopped,
        }

    def pending_task_count(self) -> int:
        return sum(not task.done() for task in self._workers | self._in_flight)

    async def submit(
        self,
        func: QueueCallable[T],
        *args: Any,
        queue_priority: int = 0,
        wait_timeout_seconds: float | None = None,
        **kwargs: Any,
    ) -> T:
        self._ensure_loop_resources()
        job = self._enqueue(
            func,
            args,
            kwargs,
            queue_priority=queue_priority,
            stream_queue=None,
        )
        await self._ensure_workers()

        try:
            await self._wait_for_start(job, wait_timeout_seconds)
            return cast(T, await job.result_future)
        except asyncio.CancelledError:
            self._cancel_job(job)
            raise

    async def stream(
        self,
        func: QueueStreamCallable[T],
        *args: Any,
        queue_priority: int = 0,
        wait_timeout_seconds: float | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[T]:
        self._ensure_loop_resources()
        stream_queue: asyncio.Queue[_StreamItem] = asyncio.Queue(maxsize=1)
        job = self._enqueue(
            func,
            args,
            kwargs,
            queue_priority=queue_priority,
            stream_queue=stream_queue,
        )
        await self._ensure_workers()

        try:
            await self._wait_for_start(job, wait_timeout_seconds)
            while True:
                try:
                    item = stream_queue.get_nowait()
                except asyncio.QueueEmpty:
                    item = _NO_STREAM_ITEM
                if item is not _NO_STREAM_ITEM:
                    if item is _STREAM_END:
                        await job.result_future
                        return
                    if isinstance(item, _StreamError):
                        if job.result_future.done() and not job.result_future.cancelled():
                            with suppress(Exception):
                                job.result_future.exception()
                        raise item.exc
                    yield cast(_StreamValue[T], item).value
                    continue

                if job.result_future.done():
                    await job.result_future
                    return

                get_task = asyncio.create_task(stream_queue.get())
                try:
                    done, _pending = await asyncio.wait(
                        {get_task, job.result_future},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except asyncio.CancelledError:
                    get_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await get_task
                    raise

                if get_task in done:
                    item = get_task.result()
                    if item is _STREAM_END:
                        await job.result_future
                        return
                    if isinstance(item, _StreamError):
                        if job.result_future.done() and not job.result_future.cancelled():
                            with suppress(Exception):
                                job.result_future.exception()
                        raise item.exc
                    yield cast(_StreamValue[T], item).value
                    continue

                get_task.cancel()
                with suppress(asyncio.CancelledError):
                    await get_task
                await job.result_future
                return
        except asyncio.CancelledError:
            self._cancel_job(job)
            raise
        finally:
            if not job.result_future.done():
                self._cancel_job(job)

    async def stop(
        self,
        *,
        cancel_in_flight: bool = True,
        timeout_seconds: float | None = None,
    ) -> None:
        if self._stopped:
            return
        self._stopped = True
        timeout = self.shutdown_timeout_seconds
        if timeout_seconds is not None:
            timeout = max(float(timeout_seconds), 0.0)

        self._cancel_queued_jobs()

        if cancel_in_flight:
            in_flight = [task for task in self._in_flight if not task.done()]
            for task in in_flight:
                task.cancel()
            await self._wait_for_tasks(in_flight, timeout=timeout)
        else:
            in_flight = [task for task in self._in_flight if not task.done()]
            await self._wait_for_tasks(in_flight, timeout=timeout)

        pending_in_flight = [task for task in self._in_flight if not task.done()]
        workers = [task for task in self._workers if not task.done()]
        if cancel_in_flight or not pending_in_flight:
            for task in workers:
                task.cancel()
            await self._wait_for_tasks(workers, timeout=timeout)

        self._workers = {task for task in self._workers if not task.done()}
        self._in_flight = {task for task in self._in_flight if not task.done()}

    def _ensure_loop_resources(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is loop and self._queue is not None:
            return

        for task in self._workers | self._in_flight:
            if not task.done():
                task.cancel()

        self._loop = loop
        self._queue = asyncio.PriorityQueue(maxsize=self.capacity)
        self._workers = set()
        self._in_flight = set()
        self._sequence = 0

    def _enqueue(
        self,
        func: QueueCallable[T] | QueueStreamCallable[T],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        *,
        queue_priority: int,
        stream_queue: asyncio.Queue[_StreamItem] | None,
    ) -> _QueueJob[T]:
        if self._stopped:
            self._rejected += 1
            raise LLMQueueShutdownError()
        if self._queue is None:
            raise RuntimeError("LLM request queue is not initialized")
        self._drop_done_queued_jobs()

        loop = asyncio.get_running_loop()
        stats = self.stats()
        attributes = _queue_attributes(
            queue_name=self.name,
            stats=stats,
            queue_priority=queue_priority,
        )
        job: _QueueJob[T] = _QueueJob(
            ctx=contextvars.copy_context(),
            func=func,
            args=args,
            kwargs=kwargs,
            result_future=loop.create_future(),
            started_future=loop.create_future(),
            queued_at=time.monotonic(),
            stream_queue=stream_queue,
        )
        item: QueueItem = (int(queue_priority), self._sequence, cast(_QueueJob[Any], job))

        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull as exc:
            self._rejected += 1
            _queue_status_span(
                "llm.queue.full",
                {
                    **attributes,
                    "queue.capacity": self.capacity,
                    "status": "queue_full",
                    "error.type": "LLMQueueFullError",
                },
            )
            raise LLMQueueFullError(capacity=self.capacity) from exc

        self._sequence += 1
        add_span_event("llm.queue.enqueued", attributes)
        return job

    async def _ensure_workers(self) -> None:
        self._workers = {task for task in self._workers if not task.done()}
        while len(self._workers) < self.max_concurrent:
            worker_index = len(self._workers) + 1
            runner_ctx = contextvars.Context()
            try:
                task = asyncio.create_task(
                    self._worker(),
                    name=f"{self.name}-worker-{worker_index}",
                    context=runner_ctx,
                )
            except TypeError:
                task = runner_ctx.run(
                    asyncio.create_task,
                    self._worker(),
                    name=f"{self.name}-worker-{worker_index}",
                )
            self._workers.add(task)

    async def _wait_for_start(
        self,
        job: _QueueJob[Any],
        wait_timeout_seconds: float | None,
    ) -> None:
        timeout = self.wait_timeout_seconds
        if wait_timeout_seconds is not None:
            timeout = max(float(wait_timeout_seconds), 0.0)
        try:
            await asyncio.wait_for(asyncio.shield(job.started_future), timeout=timeout)
        except TimeoutError as exc:
            self._cancel_job(job)
            _queue_status_span(
                "llm.queue.wait_timeout",
                {
                    "queue.name": self.name,
                    "timeout_layer": "llm_queue_wait",
                    "timeout_seconds": timeout,
                    "status": "timeout",
                    "error.type": "LLMQueueWaitTimeoutError",
                }
            )
            raise LLMQueueWaitTimeoutError(timeout_seconds=timeout) from exc

    async def _worker(self) -> None:
        try:
            while not self._stopped:
                if self._queue is None:
                    return
                _priority, _sequence, job = await self._queue.get()
                try:
                    if job.result_future.done():
                        self._cancel_started_future(job)
                        continue

                    if not job.started_future.done():
                        waited_seconds = time.monotonic() - job.queued_at
                        job.started_future.set_result(waited_seconds)

                    try:
                        task = asyncio.create_task(
                            self._execute_job(job),
                            context=job.ctx,
                        )
                    except TypeError:
                        task = job.ctx.run(
                            asyncio.create_task,
                            self._execute_job(job),
                        )

                    def _cancel_if_result_cancelled(
                        future: asyncio.Future[Any],
                    ) -> None:
                        if future.cancelled() and not task.done():
                            task.cancel()

                    job.result_future.add_done_callback(_cancel_if_result_cancelled)
                    self._in_flight.add(task)
                    try:
                        result = await task
                    except asyncio.CancelledError:
                        if not job.result_future.done():
                            job.result_future.cancel()
                        if self._stopped:
                            raise
                    except Exception as exc:
                        if job.stream_queue is not None:
                            await job.stream_queue.put(_StreamError(exc))
                        if not job.result_future.done():
                            job.result_future.set_exception(exc)
                    else:
                        if not job.result_future.done():
                            job.result_future.set_result(result)
                    finally:
                        self._in_flight.discard(task)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            return

    async def _execute_job(self, job: _QueueJob[Any]) -> Any:
        started_at = time.monotonic()
        wait_seconds = started_at - job.queued_at
        attributes = _queue_attributes(
            queue_name=self.name,
            stats=self.stats(),
            operation="stream" if job.stream_queue is not None else "submit",
        )
        attributes["queue.wait_seconds"] = wait_seconds
        attributes["request_id"] = get_request_id()

        with traced_span("llm.queue.wait", attributes):
            add_span_event("llm.queue.started", attributes)

        with traced_span("llm.queue.execute", attributes):
            try:
                if job.stream_queue is not None:
                    result = await self._execute_stream_job(job)
                else:
                    result = await self._execute_value_job(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                set_span_attributes(
                    {
                        "status": "error",
                        "error.type": type(exc).__name__,
                    }
                )
                raise
            else:
                set_span_attributes({"status": "ok"})
                return result

    async def _execute_value_job(self, job: _QueueJob[Any]) -> Any:
        if job.result_future.done():
            return None
        func = cast(QueueCallable[Any], job.func)
        return await func(*job.args, **job.kwargs)

    async def _execute_stream_job(self, job: _QueueJob[Any]) -> None:
        if job.stream_queue is None:
            raise RuntimeError("stream job missing stream queue")
        if job.result_future.done():
            return None
        func = cast(QueueStreamCallable[Any], job.func)
        async for item in func(*job.args, **job.kwargs):
            if job.result_future.done():
                return None
            await job.stream_queue.put(_StreamValue(item))
        await job.stream_queue.put(_STREAM_END)
        return None

    def _cancel_queued_jobs(self) -> None:
        if self._queue is None:
            return
        while True:
            try:
                _priority, _sequence, job = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            self._cancel_job(job)
            self._queue.task_done()

    def _drop_done_queued_jobs(self) -> None:
        if self._queue is None:
            return

        retained: list[QueueItem] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            _priority, _sequence, job = item
            if not job.result_future.done():
                retained.append(item)
            else:
                self._cancel_started_future(job)
            self._queue.task_done()

        for item in retained:
            self._queue.put_nowait(item)

    def _cancel_job(self, job: _QueueJob[Any]) -> None:
        if not job.result_future.done():
            job.result_future.cancel()
        self._cancel_started_future(job)

    @staticmethod
    def _cancel_started_future(job: _QueueJob[Any]) -> None:
        if not job.started_future.done():
            job.started_future.cancel()

    async def _wait_for_tasks(
        self,
        tasks: list[asyncio.Task[None]],
        *,
        timeout: float,
    ) -> None:
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in done:
            with suppress(asyncio.CancelledError, Exception):
                task.result()
        if pending:
            logger.warning(
                "LLM timeout layer=llm_queue_shutdown exceeded while stopping tasks",
                extra={
                    "pending_tasks": len(pending),
                    "timeout_layer": "llm_queue_shutdown",
                },
            )


_llm_request_queue: LLMRequestQueue | None = None


def _queue_config() -> tuple[int, int, float, float]:
    settings = get_settings()
    max_concurrent = max(int(settings.max_concurrent_llm_requests), 1)
    capacity = max(int(settings.max_llm_requests), 1)
    wait_timeout = max(float(settings.llm_queue_wait_timeout_seconds), 0.0)
    shutdown_timeout = max(float(settings.llm_queue_shutdown_timeout_seconds), 0.0)
    return max_concurrent, capacity, wait_timeout, shutdown_timeout


def get_llm_request_queue() -> LLMRequestQueue:
    global _llm_request_queue

    max_concurrent, capacity, wait_timeout, shutdown_timeout = _queue_config()
    if (
        _llm_request_queue is None
        or _llm_request_queue.stopped
        or _llm_request_queue.max_concurrent != max_concurrent
        or _llm_request_queue.capacity != capacity
        or _llm_request_queue.wait_timeout_seconds != wait_timeout
        or _llm_request_queue.shutdown_timeout_seconds != shutdown_timeout
    ):
        _llm_request_queue = LLMRequestQueue(
            name="llm_requests",
            max_concurrent=max_concurrent,
            capacity=capacity,
            wait_timeout_seconds=wait_timeout,
            shutdown_timeout_seconds=shutdown_timeout,
        )
    return _llm_request_queue


async def stop_llm_request_queue(*, timeout_seconds: float | None = None) -> None:
    if _llm_request_queue is None:
        return
    await _llm_request_queue.stop(
        cancel_in_flight=True,
        timeout_seconds=timeout_seconds,
    )


async def reset_llm_request_queue_for_tests() -> None:
    global _llm_request_queue

    if _llm_request_queue is not None:
        await _llm_request_queue.stop(cancel_in_flight=True)
    _llm_request_queue = None


def _queue_attributes(
    *,
    queue_name: str,
    stats: dict[str, int | bool],
    queue_priority: int | None = None,
    operation: str | None = None,
) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "queue.name": queue_name,
        "queue.queued": int(stats["queued"]),
        "queue.in_flight": int(stats["in_flight"]),
        "queue.capacity": int(stats["capacity"]),
        "queue.max_concurrent": int(stats["max_concurrent"]),
        "queue.rejected": int(stats["rejected"]),
    }
    if queue_priority is not None:
        attributes["queue.priority"] = int(queue_priority)
    if operation is not None:
        attributes["queue.operation"] = operation
    return attributes


def _queue_status_span(name: str, attributes: dict[str, Any]) -> None:
    with traced_span(name, attributes):
        set_span_attributes({"status": attributes.get("status", "ok")})
