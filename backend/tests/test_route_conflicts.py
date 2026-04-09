from __future__ import annotations

import uuid
from typing import Any, cast
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.routes import admin_prompts, admin_tasks, votes
from app.api.routes.votes import _get_or_create_rating
from app.models.rating import ModelRating
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


class _DummyNestedTx:
    def __enter__(self) -> "_DummyNestedTx":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        _ = (exc_type, exc, tb)
        return False


class _RatingCreateSession:
    def __init__(self) -> None:
        self.added: ModelRating | None = None
        self.flush_calls = 0

    def get(
        self, _model: type[ModelRating], _model_id: uuid.UUID
    ) -> ModelRating | None:
        return None

    def add(self, rating: ModelRating) -> None:
        self.added = rating

    def flush(self) -> None:
        self.flush_calls += 1

    def begin_nested(self) -> _DummyNestedTx:
        return _DummyNestedTx()


def test_get_or_create_rating_creates_row_when_missing() -> None:
    model_id = uuid.uuid4()
    session = _RatingCreateSession()

    rating = _get_or_create_rating(session, model_id)  # type: ignore[arg-type]

    assert rating.model_id == model_id
    assert session.flush_calls == 1
    assert session.added is rating


class _RatingRaceSession:
    def __init__(self, model_id: uuid.UUID) -> None:
        self._existing = ModelRating(model_id=model_id, rating=1012.0, games_played=7)
        self._get_calls = 0
        self.flush_calls = 0

    def get(
        self, _model: type[ModelRating], _model_id: uuid.UUID
    ) -> ModelRating | None:
        self._get_calls += 1
        if self._get_calls == 1:
            return None
        return self._existing

    def add(self, _: ModelRating) -> None:
        return None

    def flush(self) -> None:
        self.flush_calls += 1
        raise _integrity_error(
            'duplicate key value violates unique constraint "model_ratings_pkey"'
        )

    def begin_nested(self) -> _DummyNestedTx:
        return _DummyNestedTx()


def test_get_or_create_rating_recovers_from_insert_race() -> None:
    model_id = uuid.uuid4()
    session = _RatingRaceSession(model_id)

    rating = _get_or_create_rating(session, model_id)  # type: ignore[arg-type]

    assert rating.model_id == model_id
    assert rating.games_played == 7
    assert session.flush_calls == 1


def test_lock_ratings_for_vote_uses_stable_lock_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    locked_order: list[uuid.UUID] = []

    def fake_get_or_create_for_update(_db: object, model_id: uuid.UUID) -> ModelRating:
        locked_order.append(model_id)
        return ModelRating(model_id=model_id)

    monkeypatch.setattr(
        votes, "_get_or_create_rating_for_update", fake_get_or_create_for_update
    )

    rating_a, rating_b = votes._lock_ratings_for_vote(
        object(),  # type: ignore[arg-type]
        model_a_id=model_b,
        model_b_id=model_a,
    )

    assert locked_order == sorted([model_a, model_b], key=str)
    assert rating_a.model_id == model_b
    assert rating_b.model_id == model_a
