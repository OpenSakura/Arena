"""app.api.routes.admin_models

Admin endpoints for model registry CRUD.

Notes:
- Restrict to admins via OIDC roles/claims (Authentik groups).
- Do NOT allow arbitrary external `base_url` from untrusted users (SSRF risk).
- Store provider tokens encrypted at rest in Postgres.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.security import require_admin
from app.db.session import get_db
from app.models.model_registry import Model
from app.models.prompt_template import PromptTemplate
from app.schemas.models import ModelAdmin, ModelCreate, ModelUpdate
from app.services.battle_orchestrator import get_battle_orchestrator
from app.utils.id import parse_optional_uuid_or_422, parse_uuid_or_422

router = APIRouter(
    prefix="/admin/models",
    tags=["admin", "models"],
    dependencies=[Depends(require_admin)],
)


@router.get("")
def list_models(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, list[ModelAdmin]]:
    stmt = select(Model).order_by(Model.created_at.desc()).limit(limit).offset(offset)
    models = db.execute(stmt).scalars().all()
    return {"models": [_to_admin_model(item) for item in models]}


@router.get("/{model_id}", response_model=ModelAdmin)
def get_model(model_id: str, db: Session = Depends(get_db)) -> ModelAdmin:
    model = db.get(Model, parse_uuid_or_422(model_id, "model_id"))
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return _to_admin_model(model)


@router.post("", response_model=ModelAdmin, status_code=status.HTTP_201_CREATED)
def create_model(payload: ModelCreate, db: Session = Depends(get_db)) -> ModelAdmin:
    prompt_template_uuid = parse_optional_uuid_or_422(
        payload.prompt_template_id, "prompt_template_id"
    )
    if prompt_template_uuid is not None:
        template = db.get(PromptTemplate, prompt_template_uuid)
        if template is None:
            raise HTTPException(status_code=404, detail="Prompt template not found")

    encrypted_api_key: str | None = None
    if payload.api_key is not None:
        encrypted_api_key = _encrypt_api_key(payload.api_key)

    model = Model(
        display_name=payload.display_name,
        provider_type=payload.provider_type,
        model_name=payload.model_name,
        base_url=payload.base_url,
        enabled=payload.enabled,
        visibility=payload.visibility,
        tags=payload.tags,
        temperature=payload.temperature,
        frequency_penalty=payload.frequency_penalty,
        presence_penalty=payload.presence_penalty,
        params=payload.params,
        prompt_template_id=prompt_template_uuid,
        encrypted_api_key=encrypted_api_key,
    )
    db.add(model)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Model with the same name already exists",
        ) from exc
    db.refresh(model)

    return _to_admin_model(model)


@router.put("/{model_id}", response_model=ModelAdmin)
def update_model(
    model_id: str,
    payload: ModelUpdate,
    db: Session = Depends(get_db),
) -> ModelAdmin:
    model = db.get(Model, parse_uuid_or_422(model_id, "model_id"))
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found")

    patch = payload.model_dump(exclude_unset=True)

    if "prompt_template_id" in patch:
        prompt_template_id = parse_optional_uuid_or_422(
            patch.pop("prompt_template_id"), "prompt_template_id"
        )
        if prompt_template_id is not None:
            template = db.get(PromptTemplate, prompt_template_id)
            if template is None:
                raise HTTPException(status_code=404, detail="Prompt template not found")
        model.prompt_template_id = prompt_template_id

    if "api_key" in patch:
        api_key = patch.pop("api_key")
        if api_key is None:
            model.encrypted_api_key = None
        else:
            model.encrypted_api_key = _encrypt_api_key(api_key)

    _ALLOWED_MODEL_FIELDS = {
        "display_name",
        "provider_type",
        "model_name",
        "base_url",
        "enabled",
        "visibility",
        "tags",
        "temperature",
        "frequency_penalty",
        "presence_penalty",
        "params",
    }
    # Fields that must never be set to NULL (NOT NULL in the DB).
    _NON_NULLABLE_FIELDS = {
        "display_name",
        "provider_type",
        "model_name",
        "base_url",
        "enabled",
        "visibility",
    }
    for field, value in patch.items():
        if field not in _ALLOWED_MODEL_FIELDS:
            continue
        if value is None and field in _NON_NULLABLE_FIELDS:
            raise HTTPException(
                status_code=422,
                detail=f"{field} cannot be null",
            )
        setattr(model, field, value)

    db.add(model)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Update conflicts with an existing model",
        ) from exc
    db.refresh(model)

    return _to_admin_model(model)


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model(model_id: str, db: Session = Depends(get_db)) -> Response:
    model = db.get(Model, parse_uuid_or_422(model_id, "model_id"))
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found")

    db.delete(model)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Model is referenced by existing battles/ratings",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{model_id}/test")
async def test_model(model_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    # db.get() is synchronous but acceptable here: admin-only PK lookup (~us).
    model = db.get(Model, parse_uuid_or_422(model_id, "model_id"))
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found")

    has_api_key = model.encrypted_api_key is not None

    api_key: str | None = None
    if has_api_key:
        try:
            encrypted = model.encrypted_api_key
            if encrypted is None:
                raise ValueError("encrypted_api_key is None despite has_api_key check")
            api_key = decrypt_secret(encrypted)
        except Exception:  # noqa: BLE001
            return {
                "ok": False,
                "note": "Failed to decrypt API key",
                "model_id": str(model.id),
                "has_api_key": has_api_key,
            }

    # Build a very small request. Include model params so routing is exercised.
    params: dict[str, object] = {}
    if model.params:
        params.update(model.params)
    if model.temperature is not None:
        params["temperature"] = model.temperature
    if model.frequency_penalty is not None:
        params["frequency_penalty"] = model.frequency_penalty
    if model.presence_penalty is not None:
        params["presence_penalty"] = model.presence_penalty

    # Keep the test cheap even if the model params are expensive.
    # Hard override to enforce the cap regardless of model params.
    params["max_tokens"] = 12
    params["temperature"] = 0

    client = get_battle_orchestrator().llm_client
    try:
        import time

        started = time.monotonic()
        body = await client.chat_completion(
            base_url=model.base_url,
            model=model.model_name,
            api_key=api_key,
            messages=[
                {
                    "role": "system",
                    "content": "Health check: respond with exactly the text 'ok'.",
                },
                {"role": "user", "content": "ping"},
            ],
            params=params,
            timeout_seconds=20.0,
        )
        latency_ms = int((time.monotonic() - started) * 1000)

        # Best-effort: extract a short preview of the output.
        preview: str | None = None
        choices = body.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str):
                        preview = content.strip()[:200]

        request_id = body.get("request_id")
        request_id_str = request_id if isinstance(request_id, str) else None

        return {
            "ok": True,
            "note": "Connectivity test succeeded",
            "model_id": str(model.id),
            "has_api_key": has_api_key,
            "latency_ms": latency_ms,
            "request_id": request_id_str,
            "output_preview": preview,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "note": f"Connectivity test failed: {type(exc).__name__}: {exc}",
            "model_id": str(model.id),
            "has_api_key": has_api_key,
        }


def _encrypt_api_key(api_key: str) -> str:
    try:
        return encrypt_secret(api_key)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=500, detail="Failed to encrypt API key"
        ) from exc


def _to_admin_model(model: Model) -> ModelAdmin:
    return ModelAdmin(
        id=str(model.id),
        display_name=model.display_name,
        provider_type=model.provider_type,
        model_name=model.model_name,
        base_url=model.base_url,
        enabled=model.enabled,
        visibility=model.visibility,
        tags=model.tags,
        temperature=model.temperature,
        frequency_penalty=model.frequency_penalty,
        presence_penalty=model.presence_penalty,
        params=model.params,
        prompt_template_id=(
            str(model.prompt_template_id)
            if model.prompt_template_id is not None
            else None
        ),
        has_api_key=model.encrypted_api_key is not None,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )
