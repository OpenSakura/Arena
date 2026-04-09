"""app.api.routes.admin_prompts

Admin endpoints for prompt template CRUD/versioning.

Notes:
- Prompt templates are versioned for reproducibility.
- Deletion is blocked only while a template is bound to a model config.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.session import get_db
from app.models.model_registry import Model
from app.models.prompt_template import PromptTemplate
from app.schemas.prompts import PromptTemplateAdmin, PromptTemplateCreate
from app.utils.id import parse_uuid

router = APIRouter(
    prefix="/admin/prompt-templates",
    tags=["admin", "prompts"],
    dependencies=[Depends(require_admin)],
)

_MAX_CREATE_VERSION_RETRIES = 3


@router.get("")
def list_prompt_templates(
    db: Session = Depends(get_db),
) -> dict[str, list[PromptTemplateAdmin]]:
    stmt = select(PromptTemplate).order_by(
        PromptTemplate.name.asc(), PromptTemplate.version.desc()
    )
    templates = db.execute(stmt).scalars().all()
    return {"prompt_templates": [_to_admin_prompt(item) for item in templates]}


@router.get("/{template_id}", response_model=PromptTemplateAdmin)
def get_prompt_template(
    template_id: str, db: Session = Depends(get_db)
) -> PromptTemplateAdmin:
    template = db.get(PromptTemplate, parse_uuid(template_id, "template_id"))
    if template is None:
        raise HTTPException(status_code=404, detail="Prompt template not found")
    return _to_admin_prompt(template)


@router.post(
    "", response_model=PromptTemplateAdmin, status_code=status.HTTP_201_CREATED
)
def create_prompt_template(
    payload: PromptTemplateCreate,
    db: Session = Depends(get_db),
) -> PromptTemplateAdmin:
    content_hash = hashlib.sha256(payload.template_text.encode("utf-8")).hexdigest()

    try:
        template = _create_prompt_template_with_retry(
            db,
            payload=payload,
            content_hash=content_hash,
            max_attempts=_MAX_CREATE_VERSION_RETRIES,
        )
    except IntegrityError as exc:
        if _is_prompt_template_version_conflict(exc):
            raise HTTPException(
                status_code=409,
                detail="Prompt template version conflict; retry request",
            ) from exc
        raise

    return _to_admin_prompt(template)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_prompt_template(template_id: str, db: Session = Depends(get_db)) -> Response:
    template_uuid = parse_uuid(template_id, "template_id")
    template = db.get(PromptTemplate, template_uuid)
    if template is None:
        raise HTTPException(status_code=404, detail="Prompt template not found")

    # Prevent deleting templates currently bound to model configs.
    bound_model = db.execute(
        select(Model.id).where(Model.prompt_template_id == template_uuid).limit(1)
    ).scalar_one_or_none()
    if bound_model is not None:
        raise HTTPException(
            status_code=409,
            detail="Prompt template is currently bound to a model",
        )

    db.delete(template)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Prompt template is currently bound to a model",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _next_version(db: Session, name: str) -> int:
    current = db.execute(
        select(func.max(PromptTemplate.version)).where(PromptTemplate.name == name)
    ).scalar_one()
    return (current or 0) + 1


def _create_prompt_template_with_retry(
    db: Session,
    *,
    payload: PromptTemplateCreate,
    content_hash: str,
    max_attempts: int,
) -> PromptTemplate:
    attempts = max(int(max_attempts), 1)

    for attempt_idx in range(attempts):
        version = _next_version(db, payload.name)
        template = PromptTemplate(
            name=payload.name,
            version=version,
            template_text=payload.template_text,
            input_schema=payload.input_schema,
            content_hash=content_hash,
        )
        db.add(template)

        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            should_retry = _is_prompt_template_version_conflict(exc)
            if should_retry and attempt_idx + 1 < attempts:
                continue
            raise

        db.refresh(template)
        return template

    raise RuntimeError("unreachable")


def _is_prompt_template_version_conflict(exc: IntegrityError) -> bool:
    detail = str(exc.orig) if exc.orig is not None else str(exc)
    detail_lower = detail.lower()
    return (
        "uq_prompt_templates_name_version" in detail_lower
        or "prompt_templates_name_version_key" in detail_lower
    )


def _to_admin_prompt(template: PromptTemplate) -> PromptTemplateAdmin:
    return PromptTemplateAdmin(
        id=str(template.id),
        name=template.name,
        version=template.version,
        template_text=template.template_text,
        input_schema=template.input_schema,
        content_hash=template.content_hash,
        created_at=template.created_at,
    )
