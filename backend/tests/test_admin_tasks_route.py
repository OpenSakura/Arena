from __future__ import annotations

import uuid

from fastapi import HTTPException
import pytest

from app.api.routes import admin_tasks
from app.models.task import Task
from app.schemas.tasks import TaskCreate, TaskUpdate


class _TaskMutationDB:
    def __init__(self, *, task: Task | None = None) -> None:
        self.task = task
        self.added: list[Task] = []
        self.commit_calls = 0
        self.refresh_calls = 0

    def get(self, model: type[Task], key: uuid.UUID) -> object | None:
        if model is Task and self.task is not None and self.task.id == key:
            return self.task
        return None

    def add(self, task: Task) -> None:
        self.added.append(task)

    def commit(self) -> None:
        self.commit_calls += 1

    def refresh(self, _task: Task) -> None:
        self.refresh_calls += 1


def test_create_task_trims_language_codes() -> None:
    db = _TaskMutationDB()

    payload = TaskCreate(
        source_lang=" ja ",
        target_lang=" zh ",
        source_text="JP source",
    )
    response = admin_tasks.create_task(payload, db=db)  # type: ignore[arg-type]

    assert db.commit_calls == 1
    assert db.refresh_calls == 1
    assert len(db.added) == 1
    assert db.added[0].source_lang == "ja"
    assert db.added[0].target_lang == "zh"
    assert response.source_lang == "ja"
    assert response.target_lang == "zh"


def test_update_task_trims_language_codes() -> None:
    task = Task(
        id=uuid.uuid4(),
        source_lang="ja",
        target_lang="zh",
        source_text="JP source",
    )
    db = _TaskMutationDB(task=task)

    payload = TaskUpdate(source_lang=" en ", target_lang=" fr ")
    response = admin_tasks.update_task(str(task.id), payload, db=db)  # type: ignore[arg-type]

    assert db.commit_calls == 1
    assert db.refresh_calls == 1
    assert task.source_lang == "en"
    assert task.target_lang == "fr"
    assert response.source_lang == "en"
    assert response.target_lang == "fr"


def test_normalize_lang_code_rejects_non_string_or_blank_values() -> None:
    with pytest.raises(HTTPException) as non_string_exc:
        admin_tasks._normalize_lang_code(None, field_name="source_lang")
    assert non_string_exc.value.status_code == 422
    assert non_string_exc.value.detail == "source_lang must be a non-empty string"

    with pytest.raises(HTTPException) as blank_exc:
        admin_tasks._normalize_lang_code("   ", field_name="target_lang")
    assert blank_exc.value.status_code == 422
    assert blank_exc.value.detail == "target_lang must be a non-empty string"


def test_normalize_lang_code_rejects_values_longer_than_db_limit() -> None:
    with pytest.raises(HTTPException) as exc_info:
        admin_tasks._normalize_lang_code("a" * 17, field_name="source_lang")

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "source_lang must be at most 16 characters"
