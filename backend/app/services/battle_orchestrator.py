"""app.services.battle_orchestrator

Battle orchestration (select pair, call models, persist, stream).

Notes:
- In the MVP (no worker queue), the request thread initiates both model calls.
- Use asyncio concurrency; persist incremental state sparingly.
- Enforce idempotency: a battle/run should not be generated twice.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
import logging
import time
import uuid

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.db.session import get_sessionmaker
from app.models.battle import Battle, Run
from app.models.model_registry import Model
from app.models.prompt_template import PromptTemplate
from app.models.task import Task
from app.services.llm_client import LLMClient
from app.services.prompting import build_chat_messages, render_prompt_template
from app.utils.sse import sse_event


DEFAULT_SYSTEM_PROMPT = (
    "You are a professional literary translator. "
    "Translate the user input from {source_lang} to {target_lang} while preserving tone, nuance, "
    "style, and character voice."
)

TRANSLATION_ONLY_POLICY = (
    "Output policy: return only the translated text. "
    "Do not include analysis, explanations, headings, XML/JSON wrappers, or code fences."
)

logger = logging.getLogger(__name__)

EmitFn = Callable[[str, object], Awaitable[None]]
MAX_REPLAY_DELTA_CHARS = 32_000


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


@dataclass(slots=True)
class BattleLockEntry:
    lock: asyncio.Lock
    users: int = 0


class BattleOrchestrator:
    """Coordinates battle lifecycle and streaming execution."""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm_client = llm_client or LLMClient()
        self._SessionLocal = get_sessionmaker()
        # Instance-level mutable state (not class-level) so each instance
        # (and especially tests) gets its own lock registry.
        self._lock_by_battle_id: dict[uuid.UUID, BattleLockEntry] = {}
        self._lock_registry_guard = asyncio.Lock()

    async def stream_battle(
        self,
        battle_id: uuid.UUID,
        *,
        request_id: str | None = None,
    ) -> AsyncIterator[bytes]:
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        stream_detached = False
        stream_completed = False

        async def emit(event: str, data: object) -> None:
            if stream_detached:
                return
            await queue.put(sse_event(event=event, data=data))

        async def runner() -> None:
            try:
                await self._stream_battle_impl(
                    battle_id=battle_id,
                    emit=emit,
                    request_id=request_id,
                )
            except Exception:  # noqa: BLE001
                logger.exception("Battle stream failed for battle_id=%s", battle_id)
                if not stream_detached:
                    await emit(
                        "battle.error",
                        {"battle_id": str(battle_id), "detail": "stream_failed"},
                    )
            finally:
                if not stream_detached:
                    await queue.put(None)

        task = asyncio.create_task(runner())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    stream_completed = True
                    break
                yield item
        finally:
            stream_detached = True
            if stream_completed:
                with suppress(asyncio.CancelledError):
                    await task
                return

            if task.done():
                self._log_background_runner_failure(task=task, battle_id=battle_id)
                return

            task.add_done_callback(
                lambda done_task: self._log_background_runner_failure(
                    task=done_task,
                    battle_id=battle_id,
                )
            )

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

    async def _stream_battle_impl(
        self,
        *,
        battle_id: uuid.UUID,
        emit: EmitFn,
        request_id: str | None,
    ) -> None:
        lock = await self._get_lock(battle_id)
        try:
            async with lock:
                await self._stream_locked(
                    battle_id=battle_id,
                    emit=emit,
                    request_id=request_id,
                )
        finally:
            await self._release_lock_ref(battle_id=battle_id, lock=lock)

    async def _stream_locked(
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
            await self._wait_for_running_battle(battle_id=battle_id, emit=emit)
            return

        # Claim the battle for execution (works across multiple API workers).
        if not await asyncio.to_thread(
            lambda: self._try_transition_battle_status(
                battle_id=battle_id,
                from_status="pending",
                to_status="running",
            )
        ):
            await self._wait_for_running_battle(battle_id=battle_id, emit=emit)
            return

        await emit("battle.started", {"battle_id": str(battle_id)})

        try:
            prepared_runs = await asyncio.to_thread(
                lambda: self._prepare_runs_for_execution(
                    battle=battle,
                    runs=runs,
                    request_id=request_id,
                )
            )
            results = await asyncio.gather(
                *[
                    self._execute_run(prepared=prepared, emit=emit)
                    for prepared in prepared_runs
                ],
                return_exceptions=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Battle execution failed for battle_id=%s", battle_id)
            final_status = await asyncio.to_thread(
                lambda: self._finalize_running_battle_status(
                    battle_id=battle_id,
                    desired_status="failed",
                )
            )
            await asyncio.to_thread(
                lambda: self._persist_battle_run_errors(
                    battle_id=battle_id, error_text=str(exc)
                )
            )
            await emit(
                "battle.completed" if final_status == "completed" else "battle.failed",
                {"battle_id": str(battle_id)},
            )
            return

        # With return_exceptions=True, exceptions appear as values in the
        # results list.  Treat them as run failures so one crashing run
        # does not cancel the other.
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

        all_success = all(run_ok)
        desired_status = "completed" if all_success else "failed"
        final_status = await asyncio.to_thread(
            lambda: self._finalize_running_battle_status(
                battle_id=battle_id,
                desired_status=desired_status,
            )
        )

        await emit(
            "battle.completed" if final_status == "completed" else "battle.failed",
            {"battle_id": str(battle_id)},
        )

    def _load_battle_and_runs(
        self, battle_id: uuid.UUID
    ) -> tuple[Battle | None, list[Run]]:
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
            return battle, list(runs)
        finally:
            db.close()

    async def _replay_finished_runs(
        self,
        *,
        battle_id: uuid.UUID,
        runs: list[Run],
        emit: EmitFn,
        final_event: str,
    ) -> None:
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

        await emit(
            final_event,
            {"battle_id": str(battle_id), "replay": True},
        )

    async def _wait_for_running_battle(
        self,
        *,
        battle_id: uuid.UUID,
        emit: EmitFn,
    ) -> None:
        settings = get_settings()
        poll_interval_seconds = 0.5
        max_wait_seconds = max(int(settings.battle_running_wait_timeout_seconds), 1)
        max_polls = max(1, int(max_wait_seconds / poll_interval_seconds))

        # Best-effort wait for another worker's execution.
        for _ in range(max_polls):
            await asyncio.sleep(poll_interval_seconds)
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

        timeout_detail = "timed_out_waiting_for_running_battle"
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
            # Avoid leaving battles permanently stuck in running state when
            # observers time out waiting for another worker to finish.
            transitioned = await asyncio.to_thread(
                lambda: self._try_transition_battle_status(
                    battle_id=battle_id,
                    from_status="running",
                    to_status="failed",
                )
            )
            if transitioned:
                await asyncio.to_thread(
                    lambda: self._persist_battle_run_errors(
                        battle_id=battle_id,
                        error_text=timeout_detail,
                    )
                )
                await emit(
                    "battle.failed",
                    {
                        "battle_id": str(battle_id),
                        "detail": timeout_detail,
                    },
                )
                return

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

        await emit(
            "battle.error",
            {
                "battle_id": str(battle_id),
                "detail": timeout_detail,
            },
        )

    def _prepare_runs_for_execution(
        self,
        *,
        battle: Battle,
        runs: list[Run],
        request_id: str | None,
    ) -> list[PreparedRun]:
        db: Session = self._SessionLocal()
        try:
            source_text, source_lang, target_lang = self._resolve_task_payload(
                db=db,
                battle=battle,
            )

            prepared: list[PreparedRun] = []
            for run in runs:
                model = db.get(Model, run.model_id)
                if model is None:
                    raise RuntimeError(f"Model not found for run: {run.id}")

                system_prompt = self._build_system_prompt(
                    db=db,
                    model=model,
                    source_text=source_text,
                    source_lang=source_lang,
                    target_lang=target_lang,
                )
                user_prompt = source_text
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
                prompt_rendered = {
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "source_lang": source_lang,
                    "target_lang": target_lang,
                    "prompt_template_id": (
                        str(model.prompt_template_id)
                        if model.prompt_template_id is not None
                        else None
                    ),
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
        db: Session,
        model: Model,
        source_text: str,
        source_lang: str,
        target_lang: str,
    ) -> str:
        if model.prompt_template_id is not None:
            template = db.get(PromptTemplate, model.prompt_template_id)
            if template is None:
                raise RuntimeError(
                    f"Prompt template {model.prompt_template_id} not found for model {model.id}"
                )
            rendered = render_prompt_template(
                template.template_text,
                {
                    "source_text": source_text,
                    "source_lang": source_lang,
                    "target_lang": target_lang,
                },
            )
            return f"{rendered}\n\n{TRANSLATION_ONLY_POLICY}"

        # Use Template.safe_substitute to avoid crashes if language names
        # happen to contain Python format specifiers like {0} or {__class__}.
        from string import Template as _StrTemplate

        default_prompt = _StrTemplate(
            DEFAULT_SYSTEM_PROMPT.replace("{", "${")
        ).safe_substitute(
            source_lang=source_lang,
            target_lang=target_lang,
        )
        return f"{default_prompt}\n\n{TRANSLATION_ONLY_POLICY}"

    @staticmethod
    def _build_model_params(model: Model) -> dict[str, object]:
        params: dict[str, object] = {}
        if model.default_params:
            params.update(model.default_params)

        if model.temperature is not None:
            params["temperature"] = model.temperature
        if model.frequency_penalty is not None:
            params["frequency_penalty"] = model.frequency_penalty
        if model.presence_penalty is not None:
            params["presence_penalty"] = model.presence_penalty

        if model.extra_body:
            params.update(model.extra_body)

        return params

    async def _execute_run(
        self,
        *,
        prepared: PreparedRun,
        emit: EmitFn,
    ) -> bool:
        started = time.monotonic()

        text_parts: list[str] = []
        usage: dict[str, object] | None = None
        request_id: str | None = None
        finish_reason: str | None = None
        error_text: str | None = None

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
                if chunk.request_id is not None and request_id is None:
                    request_id = chunk.request_id
                if chunk.usage is not None:
                    usage = chunk.usage
                if chunk.finish_reason is not None:
                    finish_reason = chunk.finish_reason
                if chunk.text_delta:
                    text_parts.append(chunk.text_delta)
                    await emit(
                        "run.delta",
                        {
                            "battle_id": str(prepared.battle_id),
                            "run_id": str(prepared.run_id),
                            "side": prepared.side,
                            "text_delta": chunk.text_delta,
                        },
                    )
        except httpx.HTTPError as exc:
            error_text = f"LLM HTTP error: {exc}"
        except Exception as exc:  # noqa: BLE001
            error_text = f"LLM stream error: {exc}"

        latency_ms = int((time.monotonic() - started) * 1000)
        output_text = "".join(text_parts) if text_parts else None

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
                stats=stats,
                error_text=error_text,
                request_json=prepared.request_json,
                prompt_rendered=prepared.prompt_rendered,
            )
        )

        if error_text is not None:
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

    def _persist_run_result(
        self,
        *,
        run_id: uuid.UUID,
        output_text: str | None,
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

            # Only update error_text if the run doesn't already have one set
            # by a timeout observer. This prevents overwriting a timeout error
            # with None when the executor finishes after the observer timed out.
            if run.error_text is None:
                run.error_text = error_text
            elif error_text is not None:
                # Executor also errored — keep the first (observer's) error
                # but log the executor's error for diagnostics.
                logger.warning(
                    "Run %s already has error_text from observer, "
                    "executor error discarded: %s",
                    run_id,
                    error_text,
                )

            run.output_text = output_text
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

    def _finalize_running_battle_status(
        self,
        *,
        battle_id: uuid.UUID,
        desired_status: str,
    ) -> str:
        """Finalize a running battle without clobbering terminal races.

        Another stream worker can time out and mark a stuck ``running`` battle as
        ``failed``. When that happens, the original executor must not overwrite
        the terminal status back to ``completed``.
        """

        if self._try_transition_battle_status(
            battle_id=battle_id,
            from_status="running",
            to_status=desired_status,
        ):
            return desired_status

        battle, _ = self._load_battle_and_runs(battle_id)
        if battle is None:
            return desired_status

        if battle.status in {"completed", "failed"}:
            return battle.status

        # Unexpected non-terminal status (for example, manual DB mutation).
        # Keep this path deterministic and force the desired terminal status.
        self._mark_battle_status(battle_id=battle_id, status=desired_status)
        return desired_status

    def _try_transition_battle_status(
        self,
        *,
        battle_id: uuid.UUID,
        from_status: str,
        to_status: str,
    ) -> bool:
        db: Session = self._SessionLocal()
        try:
            result = db.execute(
                update(Battle)
                .where(Battle.id == battle_id, Battle.status == from_status)
                .values(status=to_status, updated_at=func.now())
            )
            db.commit()
            # SQLAlchemy's rowcount typing varies by backend/stubs.
            rowcount_attr = "rowcount"
            return bool(getattr(result, rowcount_attr, 0))
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
    def _resolve_task_payload(*, db: Session, battle: Battle) -> tuple[str, str, str]:
        from_metadata = _task_payload_from_battle_metadata(battle.metadata_json)
        if from_metadata is not None:
            return from_metadata

        task = db.get(Task, battle.task_id)
        if task is None:
            raise RuntimeError("Battle task not found")
        return task.source_text, task.source_lang, task.target_lang

    async def _get_lock(self, battle_id: uuid.UUID) -> asyncio.Lock:
        async with self._lock_registry_guard:
            entry = self._lock_by_battle_id.get(battle_id)
            if entry is None:
                entry = BattleLockEntry(lock=asyncio.Lock())
                self._lock_by_battle_id[battle_id] = entry
            entry.users += 1
            return entry.lock

    async def _release_lock_ref(
        self, *, battle_id: uuid.UUID, lock: asyncio.Lock
    ) -> None:
        async with self._lock_registry_guard:
            entry = self._lock_by_battle_id.get(battle_id)
            if entry is None or entry.lock is not lock:
                return

            if entry.users > 0:
                entry.users -= 1

            if entry.users == 0 and not entry.lock.locked():
                self._lock_by_battle_id.pop(battle_id, None)


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
                loop.create_task(orchestrator._llm_client.aclose())
            else:
                loop.run_until_complete(orchestrator._llm_client.aclose())
        except Exception:  # noqa: BLE001
            pass
    get_battle_orchestrator.cache_clear()
