"""app.api.routes.admin_tasks

Admin endpoints for tasks and task sets.

Notes:
- Task text is persisted as-is for reproducibility.
- Metadata stores provenance (work title/source URL/license/etc.).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
import codecs
import json
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.session import get_db
from app.models.task import Task, TaskSet
from app.schemas.tasks import (
    TaskCreate,
    TaskPublic,
    TaskSetCreate,
    TaskSetPublic,
    TaskSetUpdate,
    TaskUpdate,
)
from app.utils.id import parse_uuid, parse_optional_uuid

router = APIRouter(
    prefix="/admin",
    tags=["admin", "tasks"],
    dependencies=[Depends(require_admin)],
)

_LANG_CODE_MAX_LENGTH = 16
_MAX_JSONL_ROWS = 10_000
_MAX_JSONL_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


@router.get("/task-sets")
def list_task_sets(db: Session = Depends(get_db)) -> dict[str, list[TaskSetPublic]]:
    stmt = select(TaskSet).order_by(TaskSet.created_at.desc())
    task_sets = db.execute(stmt).scalars().all()
    return {"task_sets": [_to_task_set(item) for item in task_sets]}


@router.post(
    "/task-sets", response_model=TaskSetPublic, status_code=status.HTTP_201_CREATED
)
def create_task_set(
    payload: TaskSetCreate, db: Session = Depends(get_db)
) -> TaskSetPublic:
    task_set = TaskSet(
        name=payload.name,
        description=payload.description,
        metadata_json=payload.metadata,
    )
    db.add(task_set)
    _commit_task_set_or_raise_conflict(db)
    db.refresh(task_set)
    return _to_task_set(task_set)


@router.put("/task-sets/{task_set_id}", response_model=TaskSetPublic)
def update_task_set(
    task_set_id: str,
    payload: TaskSetUpdate,
    db: Session = Depends(get_db),
) -> TaskSetPublic:
    task_set = db.get(TaskSet, parse_uuid(task_set_id, "task_set_id"))
    if task_set is None:
        raise HTTPException(status_code=404, detail="Task set not found")

    patch = payload.model_dump(exclude_unset=True)
    if "metadata" in patch:
        task_set.metadata_json = patch.pop("metadata")

    _ALLOWED_TASK_SET_FIELDS = {"name", "description"}
    _NON_NULLABLE_TASK_SET_FIELDS = {"name"}
    for field, value in patch.items():
        if field not in _ALLOWED_TASK_SET_FIELDS:
            continue
        if value is None and field in _NON_NULLABLE_TASK_SET_FIELDS:
            raise HTTPException(
                status_code=422,
                detail=f"{field} cannot be null",
            )
        setattr(task_set, field, value)

    db.add(task_set)
    _commit_task_set_or_raise_conflict(db)
    db.refresh(task_set)
    return _to_task_set(task_set)


@router.delete("/task-sets/{task_set_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task_set(task_set_id: str, db: Session = Depends(get_db)) -> Response:
    task_set = db.get(TaskSet, parse_uuid(task_set_id, "task_set_id"))
    if task_set is None:
        raise HTTPException(status_code=404, detail="Task set not found")

    has_tasks = db.execute(
        select(Task.id).where(Task.task_set_id == task_set.id).limit(1)
    ).scalar_one_or_none()
    if has_tasks is not None:
        raise HTTPException(
            status_code=409,
            detail="Task set contains tasks; delete tasks first",
        )

    db.delete(task_set)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Task set contains tasks; delete tasks first",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/tasks")
def list_tasks(
    task_set_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, list[TaskPublic]]:
    stmt = select(Task).order_by(Task.created_at.desc())
    if task_set_id is not None:
        stmt = stmt.where(Task.task_set_id == parse_uuid(task_set_id, "task_set_id"))
    tasks = db.execute(stmt).scalars().all()
    return {"tasks": [_to_task(item) for item in tasks]}


@router.get("/tasks/{task_id}", response_model=TaskPublic)
def get_task(task_id: str, db: Session = Depends(get_db)) -> TaskPublic:
    task = db.get(Task, parse_uuid(task_id, "task_id"))
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _to_task(task)


@router.post("/tasks", response_model=TaskPublic, status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreate, db: Session = Depends(get_db)) -> TaskPublic:
    task_set_uuid = parse_optional_uuid(payload.task_set_id, "task_set_id")
    if task_set_uuid is not None:
        _require_task_set(db, task_set_uuid)

    task = Task(
        task_set_id=task_set_uuid,
        source_lang=_normalize_lang_code(payload.source_lang, field_name="source_lang"),
        target_lang=_normalize_lang_code(payload.target_lang, field_name="target_lang"),
        source_text=payload.source_text,
        metadata_json=payload.metadata,
    )
    db.add(task)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Task creation failed due to a constraint conflict",
        ) from exc
    db.refresh(task)
    return _to_task(task)


@router.put("/tasks/{task_id}", response_model=TaskPublic)
def update_task(
    task_id: str,
    payload: TaskUpdate,
    db: Session = Depends(get_db),
) -> TaskPublic:
    task = db.get(Task, parse_uuid(task_id, "task_id"))
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    patch = payload.model_dump(exclude_unset=True)
    if "task_set_id" in patch:
        task_set_uuid = parse_optional_uuid(patch.pop("task_set_id"), "task_set_id")
        if task_set_uuid is not None:
            _require_task_set(db, task_set_uuid)
        task.task_set_id = task_set_uuid

    if "metadata" in patch:
        task.metadata_json = patch.pop("metadata")

    if "source_lang" in patch:
        patch["source_lang"] = _normalize_lang_code(
            patch["source_lang"],
            field_name="source_lang",
        )
    if "target_lang" in patch:
        patch["target_lang"] = _normalize_lang_code(
            patch["target_lang"],
            field_name="target_lang",
        )

    _ALLOWED_TASK_FIELDS = {"source_lang", "target_lang", "source_text"}
    _NON_NULLABLE_TASK_FIELDS = {"source_lang", "target_lang", "source_text"}
    for field, value in patch.items():
        if field not in _ALLOWED_TASK_FIELDS:
            continue
        if value is None and field in _NON_NULLABLE_TASK_FIELDS:
            raise HTTPException(
                status_code=422,
                detail=f"{field} cannot be null",
            )
        setattr(task, field, value)

    db.add(task)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Task update failed due to a constraint conflict",
        ) from exc
    db.refresh(task)
    return _to_task(task)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: str, db: Session = Depends(get_db)) -> Response:
    task = db.get(Task, parse_uuid(task_id, "task_id"))
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    db.delete(task)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Task is referenced by existing battles",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/tasks/import-jsonl")
async def import_tasks_jsonl(
    file: UploadFile = File(...),
    task_set_id: str | None = Query(default=None),
    source_lang: str = Query(default="ja"),
    target_lang: str = Query(default="zh"),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if not file.filename or not file.filename.endswith(".jsonl"):
        raise HTTPException(status_code=400, detail="Expected a .jsonl file")

    if file.size is not None and file.size > _MAX_JSONL_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"JSONL file exceeds maximum allowed size of {_MAX_JSONL_FILE_SIZE} bytes",
        )

    task_set_uuid = parse_optional_uuid(task_set_id, "task_set_id")
    if task_set_uuid is not None:
        _require_task_set(db, task_set_uuid)

    default_source_lang = _normalize_lang_code(source_lang, field_name="source_lang")
    default_target_lang = _normalize_lang_code(target_lang, field_name="target_lang")

    created = 0
    async for line_number, line in _iter_utf8_lines(
        file, max_bytes=_MAX_JSONL_FILE_SIZE
    ):
        stripped = line.strip()
        if not stripped:
            continue

        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid JSON on line {line_number}",
            ) from exc

        if not isinstance(row, dict):
            raise HTTPException(
                status_code=400,
                detail=f"Line {line_number} must be a JSON object",
            )

        source_text = row.get("source_text")
        if not isinstance(source_text, str) or not source_text.strip():
            raise HTTPException(
                status_code=400,
                detail=f"Line {line_number} missing non-empty source_text",
            )

        _MAX_SOURCE_TEXT_LENGTH = 131_072
        if len(source_text) > _MAX_SOURCE_TEXT_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=f"Line {line_number} source_text exceeds maximum length of {_MAX_SOURCE_TEXT_LENGTH}",
            )

        metadata = row.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise HTTPException(
                status_code=400,
                detail=f"Line {line_number} has non-object metadata",
            )

        task = Task(
            task_set_id=task_set_uuid,
            source_lang=_coerce_lang(
                row.get("source_lang"),
                default_source_lang,
                field_name="source_lang",
            ),
            target_lang=_coerce_lang(
                row.get("target_lang"),
                default_target_lang,
                field_name="target_lang",
            ),
            source_text=source_text,
            metadata_json=metadata,
        )
        if created >= _MAX_JSONL_ROWS:
            raise HTTPException(
                status_code=400,
                detail=f"JSONL import exceeds maximum of {_MAX_JSONL_ROWS} rows",
            )

        db.add(task)
        created += 1

    try:
        # Commit on the event loop thread to keep all Session usage on the
        # same thread.  Session is not thread-safe, so the previous
        # ``asyncio.to_thread(db.commit)`` was unsafe.
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Import failed due to a constraint conflict",
        ) from exc

    return {
        "ok": True,
        "imported": created,
        "task_set_id": str(task_set_uuid) if task_set_uuid is not None else None,
        "filename": file.filename,
    }


async def _iter_utf8_lines(
    file: UploadFile,
    *,
    chunk_bytes: int = 64 * 1024,
    max_bytes: int = 0,
) -> AsyncIterator[tuple[int, str]]:
    """Iterate over UTF-8 lines from an UploadFile.

    When *max_bytes* > 0, raises 400 if the cumulative bytes read from the
    file exceed the limit.  This prevents a client from bypassing the
    Content-Length–based ``file.size`` check by sending a small header but a
    large body.
    """
    await file.seek(0)

    decoder = codecs.getincrementaldecoder("utf-8")()
    pending = ""
    line_number = 0
    total_bytes_read = 0

    while True:
        chunk = await file.read(chunk_bytes)
        if not chunk:
            break

        total_bytes_read += len(chunk)
        if max_bytes > 0 and total_bytes_read > max_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"JSONL file exceeds maximum allowed size of {max_bytes} bytes",
            )

        try:
            pending += decoder.decode(chunk)
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="File must be UTF-8") from exc

        while True:
            newline_idx = pending.find("\n")
            if newline_idx < 0:
                break

            line = pending[:newline_idx]
            pending = pending[newline_idx + 1 :]
            if line.endswith("\r"):
                line = line[:-1]

            line_number += 1
            yield line_number, line

    try:
        pending += decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="File must be UTF-8") from exc

    if pending:
        if pending.endswith("\r"):
            pending = pending[:-1]
        line_number += 1
        yield line_number, pending


def _coerce_lang(value: object, default: str, *, field_name: str) -> str:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            return _normalize_lang_code(normalized, field_name=field_name)
    return default


def _normalize_lang_code(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must be a non-empty string",
        )

    normalized = value.strip()
    if not normalized:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must be a non-empty string",
        )

    if len(normalized) > _LANG_CODE_MAX_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must be at most {_LANG_CODE_MAX_LENGTH} characters",
        )

    return normalized


def _require_task_set(db: Session, task_set_id: uuid.UUID) -> None:
    task_set = db.get(TaskSet, task_set_id)
    if task_set is None:
        raise HTTPException(status_code=404, detail="Task set not found")


def _commit_task_set_or_raise_conflict(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if _is_task_set_name_conflict(exc):
            raise HTTPException(
                status_code=409,
                detail="Task set name already exists",
            ) from exc
        raise


def _is_task_set_name_conflict(exc: IntegrityError) -> bool:
    detail = str(exc.orig) if exc.orig is not None else str(exc)
    detail_lower = detail.lower()
    return "uq_task_sets_name" in detail_lower or "task_sets_name_key" in detail_lower


def _to_task_set(task_set: TaskSet) -> TaskSetPublic:
    return TaskSetPublic(
        id=str(task_set.id),
        name=task_set.name,
        description=task_set.description,
        metadata=task_set.metadata_json,
    )


def _to_task(task: Task) -> TaskPublic:
    return TaskPublic(
        id=str(task.id),
        task_set_id=str(task.task_set_id) if task.task_set_id is not None else None,
        source_lang=task.source_lang,
        target_lang=task.target_lang,
        source_text=task.source_text,
        metadata=task.metadata_json,
    )
