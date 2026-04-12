from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from types import SimpleNamespace
import uuid

from fastapi import HTTPException
import pytest
from sqlalchemy.exc import IntegrityError

from app.api.routes import admin_prompts
from app.models.prompt_template import PromptTemplate
from app.schemas.prompts import PromptTemplateCreate


class _Result:
    def __init__(
        self,
        *,
        rows: list[object] | None = None,
        scalar_one: object = None,
        scalar_one_or_none: object = None,
    ) -> None:
        self._rows = list(rows or [])
        self._scalar_one = scalar_one
        self._scalar_one_or_none = scalar_one_or_none

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[object]:
        return list(self._rows)

    def scalar_one(self) -> object:
        return self._scalar_one

    def scalar_one_or_none(self) -> object:
        return self._scalar_one_or_none


class _QueueDB:
    def __init__(
        self,
        *,
        result_queue: list[_Result] | None = None,
        get_map: dict[tuple[type[object], uuid.UUID], object] | None = None,
        commit_errors: list[Exception | None] | None = None,
    ) -> None:
        self._result_queue = list(result_queue or [])
        self._get_map = get_map or {}
        self._commit_errors = list(commit_errors or [])
        self.statements: list[object] = []
        self.added: list[object] = []
        self.deleted: list[object] = []
        self.commits = 0
        self.rollbacks = 0
        self.refreshed: list[object] = []

    def execute(self, stmt: object) -> _Result:
        self.statements.append(stmt)
        assert self._result_queue, "Unexpected execute() call"
        return self._result_queue.pop(0)

    def get(self, model: type[object], key: uuid.UUID) -> object | None:
        return self._get_map.get((model, key))

    def add(self, item: object) -> None:
        self.added.append(item)

    def delete(self, item: object) -> None:
        self.deleted.append(item)

    def commit(self) -> None:
        self.commits += 1
        if not self._commit_errors:
            return
        error = self._commit_errors.pop(0)
        if error is not None:
            raise error

    def rollback(self) -> None:
        self.rollbacks += 1

    def refresh(self, item: object) -> None:
        self.refreshed.append(item)


def _integrity_error(detail: str) -> IntegrityError:
    return IntegrityError("INSERT", {}, Exception(detail))


def _template_stub(**overrides: object) -> SimpleNamespace:
    now = datetime(2026, 2, 19, 10, 0, tzinfo=timezone.utc)
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "name": "translation/base",
        "version": 2,
        "template_text": "Translate exactly.",
        "input_schema": {"source_text": {"type": "string"}},
        "content_hash": "abc123",
        "created_at": now,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_list_prompt_templates_returns_serialized_admin_rows() -> None:
    first = _template_stub(name="alpha", version=3)
    second = _template_stub(name="alpha", version=2)
    db = _QueueDB(result_queue=[_Result(rows=[first, second])])

    response = admin_prompts.list_prompt_templates(db=db)  # type: ignore[arg-type]

    templates = response["prompt_templates"]
    assert len(templates) == 2
    assert templates[0].id == str(first.id)
    assert templates[1].id == str(second.id)


def test_get_prompt_template_rejects_invalid_uuid() -> None:
    with pytest.raises(HTTPException) as exc_info:
        admin_prompts.get_prompt_template("bad-uuid", db=_QueueDB())  # type: ignore[arg-type]

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Invalid template_id"


def test_get_prompt_template_returns_404_when_missing() -> None:
    template_id = uuid.uuid4()

    with pytest.raises(HTTPException) as exc_info:
        admin_prompts.get_prompt_template(  # type: ignore[arg-type]
            str(template_id),
            db=_QueueDB(),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Prompt template not found"


def test_get_prompt_template_returns_serialized_template() -> None:
    template = _template_stub()
    db = _QueueDB(get_map={(PromptTemplate, template.id): template})

    response = admin_prompts.get_prompt_template(  # type: ignore[arg-type]
        str(template.id),
        db=db,
    )

    assert response.id == str(template.id)
    assert response.name == template.name
    assert response.version == template.version
    assert response.template_text == template.template_text


def test_create_prompt_template_hashes_content_and_uses_retry_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = PromptTemplateCreate(
        name="translation/base",
        template_text="Please translate {source_text}",
        input_schema={"source_text": {"type": "string"}},
    )
    template = _template_stub(name=payload.name, version=1)
    captured: dict[str, object] = {}

    def _fake_create_with_retry(
        db: object,
        *,
        payload: PromptTemplateCreate,
        content_hash: str,
        max_attempts: int,
    ) -> object:
        captured["db"] = db
        captured["payload"] = payload
        captured["content_hash"] = content_hash
        captured["max_attempts"] = max_attempts
        return template

    monkeypatch.setattr(
        admin_prompts,
        "_create_prompt_template_with_retry",
        _fake_create_with_retry,
    )

    db = _QueueDB()
    response = admin_prompts.create_prompt_template(payload, db=db)  # type: ignore[arg-type]

    assert response.id == str(template.id)
    assert captured["db"] is db
    assert captured["payload"] == payload
    assert captured["max_attempts"] == admin_prompts._MAX_CREATE_VERSION_RETRIES
    assert (
        captured["content_hash"]
        == hashlib.sha256(payload.template_text.encode("utf-8")).hexdigest()
    )


def test_create_prompt_template_translates_version_conflicts_to_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = PromptTemplateCreate(name="translation/base", template_text="x")

    def _raise_conflict(*_args: object, **_kwargs: object) -> object:
        raise _integrity_error('duplicate key "uq_prompt_templates_name_version"')

    monkeypatch.setattr(
        admin_prompts,
        "_create_prompt_template_with_retry",
        _raise_conflict,
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_prompts.create_prompt_template(payload, db=_QueueDB())  # type: ignore[arg-type]

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Prompt template version conflict; retry request"


def test_create_prompt_template_reraises_non_conflict_integrity_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = PromptTemplateCreate(name="translation/base", template_text="x")

    def _raise_integrity_error(*_args: object, **_kwargs: object) -> object:
        raise _integrity_error("different unique constraint")

    monkeypatch.setattr(
        admin_prompts,
        "_create_prompt_template_with_retry",
        _raise_integrity_error,
    )

    with pytest.raises(IntegrityError):
        admin_prompts.create_prompt_template(payload, db=_QueueDB())  # type: ignore[arg-type]


def test_delete_prompt_template_rejects_invalid_uuid() -> None:
    with pytest.raises(HTTPException) as exc_info:
        admin_prompts.delete_prompt_template("bad-uuid", db=_QueueDB())  # type: ignore[arg-type]

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Invalid template_id"


def test_delete_prompt_template_returns_404_when_template_missing() -> None:
    with pytest.raises(HTTPException) as exc_info:
        admin_prompts.delete_prompt_template(str(uuid.uuid4()), db=_QueueDB())  # type: ignore[arg-type]

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Prompt template not found"


def test_delete_prompt_template_rejects_bound_templates() -> None:
    template = _template_stub()
    db = _QueueDB(
        get_map={(PromptTemplate, template.id): template},
        result_queue=[_Result(scalar_one_or_none=uuid.uuid4())],
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_prompts.delete_prompt_template(str(template.id), db=db)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Prompt template is currently bound to a model"
    assert db.deleted == []
    assert db.commits == 0


def test_delete_prompt_template_deletes_when_unbound() -> None:
    template = _template_stub()
    db = _QueueDB(
        get_map={(PromptTemplate, template.id): template},
        result_queue=[_Result(scalar_one_or_none=None)],
    )

    response = admin_prompts.delete_prompt_template(str(template.id), db=db)  # type: ignore[arg-type]

    assert response.status_code == 204
    assert db.deleted == [template]
    assert db.commits == 1


def test_delete_prompt_template_returns_conflict_when_commit_races() -> None:
    template = _template_stub()
    db = _QueueDB(
        get_map={(PromptTemplate, template.id): template},
        result_queue=[_Result(scalar_one_or_none=None)],
        commit_errors=[
            _integrity_error('update on table "models" violates foreign key')
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_prompts.delete_prompt_template(str(template.id), db=db)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Prompt template is currently bound to a model"
    assert db.deleted == [template]
    assert db.rollbacks == 1


def test_next_version_starts_at_one_when_template_name_has_no_rows() -> None:
    db = _QueueDB(result_queue=[_Result(scalar_one=None)])

    version = admin_prompts._next_version(db=db, name="translation/base")  # type: ignore[arg-type]

    assert version == 1


def test_next_version_increments_highest_existing_version() -> None:
    db = _QueueDB(result_queue=[_Result(scalar_one=7)])

    version = admin_prompts._next_version(db=db, name="translation/base")  # type: ignore[arg-type]

    assert version == 8


def test_create_prompt_template_with_retry_retries_version_conflicts() -> None:
    payload = PromptTemplateCreate(name="translation/base", template_text="prompt")
    db = _QueueDB(
        result_queue=[_Result(scalar_one=1), _Result(scalar_one=2)],
        commit_errors=[
            _integrity_error("prompt_templates_name_version_key"),
            None,
        ],
    )

    template = admin_prompts._create_prompt_template_with_retry(
        db=db,  # type: ignore[arg-type]
        payload=payload,
        content_hash="hash-value",
        max_attempts=3,
    )

    assert template.name == payload.name
    assert template.version == 3
    assert template.content_hash == "hash-value"
    assert db.commits == 2
    assert db.rollbacks == 1
    assert len(db.added) == 2
    assert db.refreshed == [template]


def test_create_prompt_template_with_retry_raises_after_conflict_retries() -> None:
    payload = PromptTemplateCreate(name="translation/base", template_text="prompt")
    conflict = _integrity_error("uq_prompt_templates_name_version")
    db = _QueueDB(
        result_queue=[_Result(scalar_one=1), _Result(scalar_one=2)],
        commit_errors=[conflict, conflict],
    )

    with pytest.raises(IntegrityError):
        admin_prompts._create_prompt_template_with_retry(
            db=db,  # type: ignore[arg-type]
            payload=payload,
            content_hash="hash-value",
            max_attempts=2,
        )

    assert db.commits == 2
    assert db.rollbacks == 2


def test_create_prompt_template_with_retry_does_not_retry_non_conflicts() -> None:
    payload = PromptTemplateCreate(name="translation/base", template_text="prompt")
    db = _QueueDB(
        result_queue=[_Result(scalar_one=1)],
        commit_errors=[_integrity_error("not-a-version-conflict")],
    )

    with pytest.raises(IntegrityError):
        admin_prompts._create_prompt_template_with_retry(
            db=db,  # type: ignore[arg-type]
            payload=payload,
            content_hash="hash-value",
            max_attempts=5,
        )

    assert db.commits == 1
    assert db.rollbacks == 1
    assert len(db.added) == 1


def test_create_prompt_template_with_retry_attempts_once_when_max_attempts_is_zero() -> (
    None
):
    payload = PromptTemplateCreate(name="translation/base", template_text="prompt")
    db = _QueueDB(
        result_queue=[_Result(scalar_one=0)],
        commit_errors=[None],
    )

    admin_prompts._create_prompt_template_with_retry(
        db=db,  # type: ignore[arg-type]
        payload=payload,
        content_hash="hash-value",
        max_attempts=0,
    )

    assert db.commits == 1


@pytest.mark.parametrize(
    "detail, expected",
    [
        ("duplicate key uq_prompt_templates_name_version", True),
        ("duplicate key prompt_templates_name_version_key", True),
        ("different constraint", False),
    ],
)
def test_is_prompt_template_version_conflict_matches_expected_signatures(
    detail: str,
    expected: bool,
) -> None:
    exc = _integrity_error(detail)
    assert admin_prompts._is_prompt_template_version_conflict(exc) is expected


def test_parse_uuid_or_422_returns_uuid_and_rejects_invalid_value() -> None:
    from app.utils.id import parse_uuid_or_422

    value = str(uuid.uuid4())
    parsed = parse_uuid_or_422(value, "template_id")
    assert str(parsed) == value

    with pytest.raises(HTTPException) as exc_info:
        parse_uuid_or_422("not-uuid", "template_id")

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Invalid template_id"


def test_to_admin_prompt_maps_all_schema_fields() -> None:
    template = _template_stub()

    response = admin_prompts._to_admin_prompt(template)  # type: ignore[arg-type]

    assert response.id == str(template.id)
    assert response.name == template.name
    assert response.version == template.version
    assert response.template_text == template.template_text
    assert response.input_schema == template.input_schema
    assert response.content_hash == template.content_hash
    assert response.created_at == template.created_at
