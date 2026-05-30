from __future__ import annotations

import asyncio
from io import BytesIO
import uuid

from fastapi import HTTPException, UploadFile
import pytest

from app.api.routes import admin_tasks
from app.models.task import Task, TaskSet


class _ImportDB:
    def __init__(self, *, task_set_exists: bool = True) -> None:
        self.task_set_exists = task_set_exists
        self.added: list[Task] = []
        self.commit_calls = 0

    def get(self, model: type[TaskSet], key: uuid.UUID) -> object | None:
        if model is TaskSet and self.task_set_exists:
            return TaskSet(id=key, name="Set A")
        return None

    def add(self, task: Task) -> None:
        self.added.append(task)

    def commit(self) -> None:
        self.commit_calls += 1


def _run_import(
    *,
    db: _ImportDB,
    content: bytes,
    filename: str = "tasks.jsonl",
    task_set_id: str | None = None,
    source_lang: str = "ja",
    target_lang: str = "zh",
) -> dict[str, object]:
    upload = UploadFile(filename=filename, file=BytesIO(content))
    return asyncio.run(
        admin_tasks.import_tasks_jsonl(
            file=upload,
            task_set_id=task_set_id,
            source_lang=source_lang,
            target_lang=target_lang,
            db=db,  # type: ignore[arg-type]
        )
    )


def test_import_tasks_jsonl_requires_jsonl_extension() -> None:
    db = _ImportDB()

    with pytest.raises(HTTPException) as exc_info:
        _run_import(db=db, content=b'{"source_text":"JP"}\n', filename="tasks.txt")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Expected a .jsonl file"


def test_import_tasks_jsonl_rejects_invalid_utf8() -> None:
    db = _ImportDB()

    with pytest.raises(HTTPException) as exc_info:
        _run_import(db=db, content=b"\xff\xfe")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "File must be UTF-8"


def test_import_tasks_jsonl_rejects_invalid_json_line() -> None:
    db = _ImportDB()

    with pytest.raises(HTTPException) as exc_info:
        _run_import(db=db, content=b'{"source_text":"ok"}\n{bad json}\n')

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid JSON on line 2"


def test_import_tasks_jsonl_rejects_non_object_rows() -> None:
    db = _ImportDB()

    with pytest.raises(HTTPException) as exc_info:
        _run_import(db=db, content=b"[]\n")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Line 1 must be a JSON object"


def test_import_tasks_jsonl_requires_non_empty_source_text() -> None:
    db = _ImportDB()

    with pytest.raises(HTTPException) as exc_info:
        _run_import(db=db, content=b'{"source_text":"   "}\n')

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Line 1 missing non-empty source_text"


def test_import_tasks_jsonl_rejects_non_object_metadata() -> None:
    db = _ImportDB()

    with pytest.raises(HTTPException) as exc_info:
        _run_import(db=db, content=b'{"source_text":"ok","metadata":[]}\n')

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Line 1 has non-object metadata"


def test_import_tasks_jsonl_rejects_metadata_over_64kb() -> None:
    db = _ImportDB()
    content = ('{"source_text":"ok","metadata":{"blob":"' + ("x" * 65_536) + '"}}\n').encode(
        "utf-8"
    )

    with pytest.raises(HTTPException) as exc_info:
        _run_import(db=db, content=content)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Line 1 metadata JSON must not exceed 64 KB"


def test_import_tasks_jsonl_rejects_overlong_default_source_lang() -> None:
    db = _ImportDB()

    with pytest.raises(HTTPException) as exc_info:
        _run_import(
            db=db,
            content=b'{"source_text":"ok"}\n',
            source_lang="a" * 17,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "source_lang must be at most 16 characters"


def test_import_tasks_jsonl_rejects_overlong_row_source_lang() -> None:
    db = _ImportDB()
    row = '{"source_text":"ok","source_lang":"' + ("a" * 17) + '"}\n'

    with pytest.raises(HTTPException) as exc_info:
        _run_import(db=db, content=row.encode("utf-8"))

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "source_lang must be at most 16 characters"


def test_import_tasks_jsonl_requires_existing_task_set_when_specified() -> None:
    task_set_id = uuid.uuid4()
    db = _ImportDB(task_set_exists=False)

    with pytest.raises(HTTPException) as exc_info:
        _run_import(
            db=db,
            content=b'{"source_text":"ok"}\n',
            task_set_id=str(task_set_id),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Task set not found"


def test_import_tasks_jsonl_imports_valid_rows_and_applies_defaults() -> None:
    task_set_id = uuid.uuid4()
    db = _ImportDB(task_set_exists=True)

    response = _run_import(
        db=db,
        content=(
            b'{"source_text":"JP one","metadata":{"chapter":1}}\n'
            b"\n"
            b'{"source_text":"JP two","source_lang":"en","target_lang":"fr"}\n'
        ),
        task_set_id=str(task_set_id),
        source_lang="ja",
        target_lang="zh",
    )

    assert response == {
        "ok": True,
        "imported": 2,
        "task_set_id": str(task_set_id),
        "filename": "tasks.jsonl",
    }
    assert db.commit_calls == 1
    assert len(db.added) == 2

    first, second = db.added
    assert first.task_set_id == task_set_id
    assert first.source_lang == "ja"
    assert first.target_lang == "zh"
    assert first.source_text == "JP one"
    assert first.metadata_json == {"chapter": 1}

    assert second.task_set_id == task_set_id
    assert second.source_lang == "en"
    assert second.target_lang == "fr"
    assert second.source_text == "JP two"
    assert second.metadata_json is None


def test_import_tasks_jsonl_trims_lang_values() -> None:
    db = _ImportDB(task_set_exists=True)

    response = _run_import(
        db=db,
        content=(
            b'{"source_text":"JP one","source_lang":" en ","target_lang":" fr "}\n'
            b'{"source_text":"JP two","source_lang":"   ","target_lang":"   "}\n'
        ),
        source_lang=" ja ",
        target_lang=" zh ",
    )

    assert response["ok"] is True
    assert response["imported"] == 2

    first, second = db.added
    assert first.source_lang == "en"
    assert first.target_lang == "fr"
    assert second.source_lang == "ja"
    assert second.target_lang == "zh"
