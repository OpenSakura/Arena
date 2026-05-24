# pyright: reportMissingImports=false

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
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
        commit_error: Exception | None = None,
    ) -> None:
        self._model = model
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
        return None

    def add(self, item: object) -> None:
        self.added.append(item)

    def delete(self, item: object) -> None:
        self.deleted.append(item)

    def commit(self) -> None:
        self.commit_calls += 1
        if self._commit_error is not None:
            raise self._commit_error

    def refresh(self, _item: Any) -> None:
        self.refresh_calls += 1
        if getattr(_item, "created_at", None) is None:
            now = datetime(2026, 2, 18, 10, 0, tzinfo=timezone.utc)
            _item.created_at = now
            _item.updated_at = now

    def rollback(self) -> None:
        self.rollback_calls += 1


def _model_stub(**overrides: object) -> SimpleNamespace:
    now = datetime(2026, 2, 18, 10, 0, tzinfo=timezone.utc)
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "display_name": "Model Alpha",
        "model_name": "gpt-alpha",
        "base_url": "https://gateway.example/v1",
        "enabled": True,
        "visibility": "public",
        "tags": {"tier": "prod"},
        "temperature": None,
        "frequency_penalty": None,
        "presence_penalty": None,
        "system_prompt": None,
        "user_prompt": None,
        "params": None,
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


def test_model_schema_visibility_allows_only_public_or_private() -> None:
    payload_public = ModelCreate(
        display_name="Model Alpha",
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
                "model_name": "gpt-alpha",
                "base_url": "https://gateway.example",
                "visibility": "PUBLIC",
            }
        )

    with pytest.raises(ValidationError):
        ModelUpdate.model_validate({"visibility": "internal"})


def test_model_schema_rejects_removed_provider_type_field() -> None:
    with pytest.raises(ValidationError):
        ModelCreate.model_validate(
            {
                "display_name": "Model Alpha",
                "provider_type": "openai_compat",
                "model_name": "gpt-alpha",
                "base_url": "https://gateway.example",
            }
        )

    with pytest.raises(ValidationError):
        ModelUpdate.model_validate({"provider_type": "openai_compat"})


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


def test_to_admin_model_maps_secret_presence() -> None:
    model = _model_stub(
        encrypted_api_key="encrypted-token",
        system_prompt="System prompt",
        user_prompt="User prompt",
    )

    response = admin_models._to_admin_model(model)  # type: ignore[arg-type]

    assert response.id == str(model.id)
    assert response.has_api_key is True
    assert response.base_url == "https://gateway.example/v1"
    assert response.system_prompt == "System prompt"
    assert response.user_prompt == "User prompt"
    assert set(response.model_dump()) == {
        "id",
        "display_name",
        "model_name",
        "base_url",
        "enabled",
        "visibility",
        "tags",
        "temperature",
        "frequency_penalty",
        "presence_penalty",
        "system_prompt",
        "user_prompt",
        "params",
        "has_api_key",
        "created_at",
        "updated_at",
    }


def test_model_prompt_fields_normalize_blank_input_to_none() -> None:
    create_payload = ModelCreate(
        display_name="Model Alpha",
        model_name="gpt-alpha",
        base_url="https://gateway.example",
        system_prompt="   ",
        user_prompt="\n\t",
    )
    update_payload = ModelUpdate(system_prompt="", user_prompt="  ")

    assert create_payload.system_prompt is None
    assert create_payload.user_prompt is None
    assert update_payload.system_prompt is None
    assert update_payload.user_prompt is None


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

    class _FakeOrchestrator:
        @property
        def llm_client(self):
            return _FakeClient()

    monkeypatch.setattr(
        admin_models, "get_battle_orchestrator", lambda: _FakeOrchestrator()
    )

    response = asyncio.run(admin_models.test_model(str(model.id), db=db))  # type: ignore[arg-type]

    assert response["ok"] is True
    assert response["request_id"] == "req-42"
    assert response["output_preview"] == "connectivity ok"
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


def test_create_model_encrypts_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _ModelMutationDB()

    monkeypatch.setattr(
        admin_models, "_encrypt_api_key", lambda api_key: f"enc:{api_key}"
    )

    payload = ModelCreate(
        display_name="Model Alpha",
        model_name="gpt-alpha",
        base_url="https://gateway.example/v1",
        api_key="secret-token",
        system_prompt="Translate carefully",
        user_prompt="Source: {{ source_text }}",
        params={"max_tokens": 64},
    )

    response = admin_models.create_model(payload, db=db)  # type: ignore[arg-type]

    assert db.commit_calls == 1
    assert db.refresh_calls == 1
    created_model = db.added[0]
    assert isinstance(created_model, Model)
    assert created_model.model_name == "gpt-alpha"
    assert created_model.encrypted_api_key == "enc:secret-token"
    assert created_model.system_prompt == "Translate carefully"
    assert created_model.user_prompt == "Source: {{ source_text }}"
    assert response.has_api_key is True


def test_create_model_normalizes_blank_prompt_fields_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _ModelMutationDB()

    payload = ModelCreate(
        display_name="Model Alpha",
        model_name="gpt-alpha",
        base_url="https://gateway.example/v1",
        system_prompt="   ",
        user_prompt="\n",
    )

    response = admin_models.create_model(payload, db=db)  # type: ignore[arg-type]

    created_model = db.added[0]
    assert isinstance(created_model, Model)
    assert created_model.system_prompt is None
    assert created_model.user_prompt is None
    assert response.system_prompt is None
    assert response.user_prompt is None


def test_update_model_clears_api_key_when_requested() -> None:
    model = _model_stub(encrypted_api_key="cipher", display_name="Old Name")
    db = _ModelMutationDB(model=model)

    response = admin_models.update_model(
        str(model.id),
        ModelUpdate(display_name="New Name", api_key=None),
        db=db,  # type: ignore[arg-type]
    )

    assert db.commit_calls == 1
    assert db.refresh_calls == 1
    assert model.display_name == "New Name"
    assert model.encrypted_api_key is None
    assert response.display_name == "New Name"
    assert response.has_api_key is False


def test_update_model_normalizes_blank_prompt_fields_to_none() -> None:
    model = _model_stub(
        system_prompt="Existing system prompt",
        user_prompt="Existing user prompt",
    )
    db = _ModelMutationDB(model=model)

    response = admin_models.update_model(
        str(model.id),
        ModelUpdate(system_prompt="  ", user_prompt=""),
        db=db,  # type: ignore[arg-type]
    )

    assert db.commit_calls == 1
    assert model.system_prompt is None
    assert model.user_prompt is None
    assert response.system_prompt is None
    assert response.user_prompt is None


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


def test_test_model_uses_shared_client(monkeypatch: pytest.MonkeyPatch) -> None:
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
        admin_models, "get_battle_orchestrator", lambda: _FakeOrchestrator()
    )

    response = asyncio.run(admin_models.test_model(str(model.id), db=db))  # type: ignore[arg-type]

    assert response["ok"] is True
    assert shared.call_count == 1


def test_validate_base_url_accepts_private_internal_targets() -> None:
    admin_models._validate_base_url("http://10.0.0.7:8000/v1")
    admin_models._validate_base_url("http://localhost:3000")
    admin_models._validate_base_url("https://gateway.internal.example/v1")


@pytest.mark.parametrize(
    ("base_url", "detail_fragment"),
    [
        ("not a url", "must use http:// or https:// scheme"),
        ("ftp://example.com", "must use http:// or https:// scheme"),
        ("https:///v1", "base_url has no hostname"),
    ],
)
def test_validate_base_url_rejects_invalid_urls(
    base_url: str,
    detail_fragment: str,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        admin_models._validate_base_url(base_url)

    assert exc_info.value.status_code == 422
    assert detail_fragment in str(exc_info.value.detail)
