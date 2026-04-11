from __future__ import annotations

import uuid
from typing import Any, cast
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.routes import admin_prompts, admin_tasks
from app.schemas.prompts import PromptTemplateCreate


def _integrity_error(message: str) -> IntegrityError:
    return IntegrityError("stmt", {}, Exception(message))


class _FakeCommitDB:
    def __init__(self, commit_error: Exception | None = None) -> None:
        self._commit_error = commit_error
        self.commit_calls = 0
        self.rollback_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1
        if self._commit_error is not None:
            raise self._commit_error

    def rollback(self) -> None:
        self.rollback_calls += 1


class _ScalarOneOrNoneResult:
    def __init__(self, value: object | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object | None:
        return self._value


class _TaskSetDeleteRaceDB:
    def __init__(self, *, task_set_id: uuid.UUID, commit_error: Exception) -> None:
        self.task_set = SimpleNamespace(id=task_set_id)
        self.commit_error = commit_error
        self.deleted: list[object] = []
        self.rollback_calls = 0

    def get(self, model: type[object], key: uuid.UUID) -> object | None:
        if model is admin_tasks.TaskSet and key == self.task_set.id:
            return self.task_set
        return None

    def execute(self, _stmt: object) -> _ScalarOneOrNoneResult:
        # Pre-check says "no tasks", then a concurrent insert races delete.
        return _ScalarOneOrNoneResult(None)

    def delete(self, item: object) -> None:
        self.deleted.append(item)

    def commit(self) -> None:
        raise self.commit_error

    def rollback(self) -> None:
        self.rollback_calls += 1


def test_task_set_commit_returns_conflict_for_duplicate_name() -> None:
    db = _FakeCommitDB(
        _integrity_error(
            'duplicate key value violates unique constraint "uq_task_sets_name"'
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_tasks._commit_task_set_or_raise_conflict(db)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Task set name already exists"
    assert db.rollback_calls == 1


def test_task_set_commit_reraises_unrelated_integrity_errors() -> None:
    db = _FakeCommitDB(
        _integrity_error("insert or update on table violates foreign key constraint")
    )

    with pytest.raises(IntegrityError):
        admin_tasks._commit_task_set_or_raise_conflict(db)  # type: ignore[arg-type]

    assert db.rollback_calls == 1


def test_delete_task_set_returns_conflict_on_racy_fk_violation() -> None:
    task_set_id = uuid.uuid4()
    db = _TaskSetDeleteRaceDB(
        task_set_id=task_set_id,
        commit_error=_integrity_error(
            'insert or update on table "tasks" violates foreign key constraint'
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_tasks.delete_task_set(str(task_set_id), db=cast(Any, db))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Task set contains tasks; delete tasks first"
    assert db.deleted == [db.task_set]
    assert db.rollback_calls == 1


class _FakePromptDB:
    def __init__(self, commit_side_effects: list[Exception | None]) -> None:
        self._effects = list(commit_side_effects)
        self.commit_calls = 0
        self.rollback_calls = 0

    def add(self, _: object) -> None:
        return None

    def commit(self) -> None:
        self.commit_calls += 1
        if self._effects:
            effect = self._effects.pop(0)
            if effect is not None:
                raise effect

    def rollback(self) -> None:
        self.rollback_calls += 1

    def refresh(self, _: object) -> None:
        return None


def test_create_prompt_template_retries_on_version_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    versions = iter([1, 2])
    monkeypatch.setattr(
        admin_prompts, "_next_version", lambda _db, _name: next(versions)
    )

    db = _FakePromptDB(
        [
            _integrity_error(
                "duplicate key value violates unique constraint "
                '"uq_prompt_templates_name_version"'
            ),
            None,
        ]
    )
    payload = PromptTemplateCreate(name="jp2zh_v1", template_text="Translate")

    template = admin_prompts._create_prompt_template_with_retry(
        cast(Any, db),
        payload=payload,
        content_hash="abc",
        max_attempts=3,
    )

    assert template.version == 2
    assert db.commit_calls == 2
    assert db.rollback_calls == 1


def test_create_prompt_template_stops_after_max_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admin_prompts, "_next_version", lambda _db, _name: 1)

    conflict = _integrity_error(
        'duplicate key value violates unique constraint "uq_prompt_templates_name_version"'
    )
    db = _FakePromptDB([conflict, conflict, conflict])
    payload = PromptTemplateCreate(name="jp2zh_v1", template_text="Translate")

    with pytest.raises(IntegrityError):
        admin_prompts._create_prompt_template_with_retry(
            cast(Any, db),
            payload=payload,
            content_hash="abc",
            max_attempts=3,
        )

    assert db.commit_calls == 3
    assert db.rollback_calls == 3
