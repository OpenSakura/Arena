# pyright: reportMissingImports=false

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

from fastapi import HTTPException
from pydantic import ValidationError
import pytest
from sqlalchemy.exc import IntegrityError

from app.api.routes import admin_models
from app.models.model_registry import Model
from app.schemas.models import ModelCreate, ModelUpdate


class _LookupDB:
    def __init__(self, model: object | None) -> None:
        self._model = model

    def get(self, model_type: type[Model], model_id: uuid.UUID) -> object | None:
        if self._model is None:
            return None
        if model_type is not Model:
            return None
        if getattr(self._model, "id", None) != model_id:
            return None
        return self._model


def _integrity_error(message: str) -> IntegrityError:
    return IntegrityError("stmt", {}, Exception(message))


class _ModelMutationDB:
    def __init__(
        self,
        *,
        model: object | None = None,
        prompt_templates: set[uuid.UUID] | None = None,
        commit_error: Exception | None = None,
    ) -> None:
        self._model = model
        self._prompt_templates = prompt_templates or set()
        self._commit_error = commit_error
        self.added: list[object] = []
        self.deleted: list[object] = []
        self.commit_calls = 0
        self.refresh_calls = 0
        self.rollback_calls = 0

    def get(self, model_type: type[object], key: uuid.UUID) -> object | None:
        if model_type is Model:
            if self._model is not None and getattr(self._model, "id", None) == key:
                return self._model
            return None

        if model_type is admin_models.PromptTemplate and key in self._prompt_templates:
            return SimpleNamespace(id=key)

        return None

    def add(self, item: object) -> None:
        self.added.append(item)

    def delete(self, item: object) -> None:
        self.deleted.append(item)

    def commit(self) -> None:
        self.commit_calls += 1
        if self._commit_error is not None:
            raise self._commit_error

    def refresh(self, _item: object) -> None:
        self.refresh_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


def _model_stub(**overrides: object) -> SimpleNamespace:
    now = datetime(2026, 2, 18, 10, 0, tzinfo=timezone.utc)
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "display_name": "Model Alpha",
        "provider_type": "openai",
        "model_name": "gpt-alpha",
        "base_url": "https://gateway.example/v1",
        "enabled": True,
        "visibility": "public",
        "tags": {"tier": "prod"},
        "temperature": None,
        "frequency_penalty": None,
        "presence_penalty": None,
        "params": None,
        "prompt_template_id": None,
        "encrypted_api_key": None,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_parse_uuid_rejects_invalid_values() -> None:
    from app.utils.id import parse_uuid_or_422

    with pytest.raises(HTTPException) as exc_info:
        parse_uuid_or_422("not-a-uuid", "model_id")

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Invalid model_id"


def test_parse_optional_uuid_returns_none_when_unset() -> None:
    from app.utils.id import parse_optional_uuid_or_422

    assert parse_optional_uuid_or_422(None, "prompt_template_id") is None


def test_model_schema_visibility_allows_only_public_or_private() -> None:
    payload_public = ModelCreate(
        display_name="Model Alpha",
        provider_type="openai_compat",
        model_name="gpt-alpha",
        base_url="https://gateway.example",
        visibility="public",
    )
    payload_private = ModelUpdate(visibility="private")

    assert payload_public.visibility == "public"
    assert payload_private.visibility == "private"


def test_model_schema_visibility_rejects_invalid_values() -> None:
    with pytest.raises(ValidationError):
        ModelCreate.model_validate(
            {
                "display_name": "Model Alpha",
                "provider_type": "openai_compat",
                "model_name": "gpt-alpha",
                "base_url": "https://gateway.example",
                "visibility": "PUBLIC",
            }
        )

    with pytest.raises(ValidationError):
        ModelUpdate.model_validate({"visibility": "internal"})


def test_encrypt_api_key_wraps_runtime_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        admin_models,
        "encrypt_secret",
        lambda _api_key: (_ for _ in ()).throw(RuntimeError("missing key")),
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_models._encrypt_api_key("api-key")

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to encrypt API key"


def test_to_admin_model_maps_prompt_binding_and_secret_presence() -> None:
    prompt_template_id = uuid.uuid4()
    model = _model_stub(
        prompt_template_id=prompt_template_id,
        encrypted_api_key="encrypted-token",
    )

    response = admin_models._to_admin_model(model)  # type: ignore[arg-type]

    assert response.id == str(model.id)
    assert response.prompt_template_id == str(prompt_template_id)
    assert response.has_api_key is True
    assert response.base_url == "https://gateway.example/v1"


def test_test_model_raises_404_when_model_is_missing() -> None:
    db = _LookupDB(None)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(admin_models.test_model(str(uuid.uuid4()), db=db))  # type: ignore[arg-type]

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Model not found"


def test_test_model_returns_error_when_api_key_decryption_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _model_stub(encrypted_api_key="ciphertext")
    db = _LookupDB(model)

    monkeypatch.setattr(
        admin_models,
        "decrypt_secret",
        lambda _value: (_ for _ in ()).throw(ValueError("bad ciphertext")),
    )

    class _UnexpectedClient:
        async def chat_completion(self, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("chat_completion should not be called")

    class _FakeOrchestrator:
        @property
        def llm_client(self):
            return _UnexpectedClient()

    monkeypatch.setattr(
        admin_models, "get_battle_orchestrator", lambda: _FakeOrchestrator()
    )

    response = asyncio.run(admin_models.test_model(str(model.id), db=db))  # type: ignore[arg-type]

    assert response["ok"] is False
    assert response["model_id"] == str(model.id)
    assert response["has_api_key"] is True
    assert "Failed to decrypt API key" in str(response["note"])


def test_test_model_merges_parameters_and_returns_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _model_stub(
        encrypted_api_key="ciphertext",
        params={"max_tokens": 64, "temperature": 0.8, "foo": "extra", "top_p": 0.9},
        temperature=0.2,
        frequency_penalty=0.5,
    )
    db = _LookupDB(model)

    monkeypatch.setattr(admin_models, "decrypt_secret", lambda _value: "secret-key")

    captured: dict[str, object] = {}

    class _FakeClient:
        async def chat_completion(self, **kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {
                "choices": [{"message": {"content": "  connectivity ok  "}}],
                "request_id": "req-42",
            }

    fake_client = _FakeClient()

    class _FakeOrchestrator:
        @property
        def llm_client(self):
            return fake_client

    monkeypatch.setattr(
        admin_models, "get_battle_orchestrator", lambda: _FakeOrchestrator()
    )

    response = asyncio.run(admin_models.test_model(str(model.id), db=db))  # type: ignore[arg-type]

    assert response["ok"] is True
    assert response["model_id"] == str(model.id)
    assert response["has_api_key"] is True
    assert response["request_id"] == "req-42"
    assert response["output_preview"] == "connectivity ok"
    assert isinstance(response["latency_ms"], int)
    assert response["latency_ms"] >= 0

    assert captured["base_url"] == model.base_url
    assert captured["model"] == model.model_name
    assert captured["api_key"] == "secret-key"
    assert captured["timeout_seconds"] == 20.0
    assert captured["params"] == {
        "max_tokens": 12,
        "temperature": 0,
        "foo": "extra",
        "frequency_penalty": 0.5,
        "top_p": 0.9,
    }


def test_test_model_returns_failure_payload_on_client_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _model_stub(encrypted_api_key=None)
    db = _LookupDB(model)

    class _FailingClient:
        async def chat_completion(self, **_kwargs: object) -> dict[str, object]:
            raise RuntimeError("gateway timeout")

    class _FakeOrchestrator:
        @property
        def llm_client(self):
            return _FailingClient()

    monkeypatch.setattr(
        admin_models, "get_battle_orchestrator", lambda: _FakeOrchestrator()
    )

    response = asyncio.run(admin_models.test_model(str(model.id), db=db))  # type: ignore[arg-type]

    assert response == {
        "ok": False,
        "note": "Connectivity test failed: RuntimeError: gateway timeout",
        "model_id": str(model.id),
        "has_api_key": False,
    }


def test_create_model_encrypts_api_key_and_resolves_prompt_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template_id = uuid.uuid4()
    db = _ModelMutationDB(prompt_templates={template_id})

    monkeypatch.setattr(
        admin_models, "_encrypt_api_key", lambda api_key: f"enc:{api_key}"
    )
    monkeypatch.setattr(
        admin_models,
        "_to_admin_model",
        lambda model: {
            "model_name": model.model_name,
            "prompt_template_id": (
                str(model.prompt_template_id)
                if model.prompt_template_id is not None
                else None
            ),
            "has_api_key": model.encrypted_api_key is not None,
        },
    )

    payload = ModelCreate(
        display_name="Model Alpha",
        provider_type="openai",
        model_name="gpt-alpha",
        base_url="https://gateway.example/v1",
        prompt_template_id=str(template_id),
        api_key="secret-token",
        params={"max_tokens": 64},
    )

    response = admin_models.create_model(payload, db=db)  # type: ignore[arg-type]

    assert db.commit_calls == 1
    assert db.refresh_calls == 1
    assert len(db.added) == 1

    created_model = db.added[0]
    assert isinstance(created_model, Model)
    assert created_model.model_name == "gpt-alpha"
    assert created_model.prompt_template_id == template_id
    assert created_model.encrypted_api_key == "enc:secret-token"
    assert response == {
        "model_name": "gpt-alpha",
        "prompt_template_id": str(template_id),
        "has_api_key": True,
    }


def test_update_model_clears_api_key_and_prompt_template_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _model_stub(
        encrypted_api_key="cipher",
        prompt_template_id=uuid.uuid4(),
        display_name="Old Name",
    )
    db = _ModelMutationDB(model=model)

    monkeypatch.setattr(
        admin_models,
        "_to_admin_model",
        lambda item: {
            "display_name": item.display_name,
            "prompt_template_id": (
                str(item.prompt_template_id)
                if item.prompt_template_id is not None
                else None
            ),
            "has_api_key": item.encrypted_api_key is not None,
        },
    )

    response = admin_models.update_model(
        str(model.id),
        ModelUpdate(
            display_name="New Name",
            prompt_template_id=None,
            api_key=None,
        ),
        db=db,  # type: ignore[arg-type]
    )

    assert db.commit_calls == 1
    assert db.refresh_calls == 1
    assert db.added == [model]
    assert model.display_name == "New Name"
    assert model.prompt_template_id is None
    assert model.encrypted_api_key is None
    assert response == {
        "display_name": "New Name",
        "prompt_template_id": None,
        "has_api_key": False,
    }


def test_update_model_rejects_missing_prompt_template() -> None:
    model = _model_stub()
    db = _ModelMutationDB(model=model, prompt_templates=set())

    with pytest.raises(HTTPException) as exc_info:
        admin_models.update_model(
            str(model.id),
            ModelUpdate(prompt_template_id=str(uuid.uuid4())),
            db=db,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Prompt template not found"
    assert db.commit_calls == 0
    assert db.refresh_calls == 0


def test_delete_model_rolls_back_and_returns_conflict_on_integrity_error() -> None:
    model = _model_stub()
    db = _ModelMutationDB(
        model=model,
        commit_error=_integrity_error(
            'update or delete on table "models" violates foreign key constraint'
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_models.delete_model(str(model.id), db=db)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Model is referenced by existing battles/ratings"
    assert db.deleted == [model]
    assert db.commit_calls == 1
    assert db.rollback_calls == 1


def test_test_model_uses_shared_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _model_stub(encrypted_api_key=None)
    db = _LookupDB(model)

    class _SharedClient:
        def __init__(self) -> None:
            self.call_count = 0

        async def chat_completion(self, **_kwargs: object) -> dict[str, object]:
            self.call_count += 1
            return {
                "choices": [{"message": {"content": "ok"}}],
            }

    shared = _SharedClient()

    class _FakeOrchestrator:
        @property
        def llm_client(self):
            return shared

    monkeypatch.setattr(
        admin_models,
        "get_battle_orchestrator",
        lambda: _FakeOrchestrator(),
    )

    response = asyncio.run(admin_models.test_model(str(model.id), db=db))  # type: ignore[arg-type]

    assert response["ok"] is True
    assert shared.call_count == 1
