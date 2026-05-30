"""app.schemas.tasks

Schemas for tasks and task sets.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.schemas._types import UuidStr


_MAX_JSON_DICT_BYTES = 65_536  # 64 KB


def validate_metadata_size(
    value: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Reject metadata dicts whose JSON serialization exceeds 64 KB."""
    if value is None:
        return value
    serialized = json.dumps(value, ensure_ascii=False)
    if len(serialized.encode("utf-8")) > _MAX_JSON_DICT_BYTES:
        raise ValueError("metadata JSON must not exceed 64 KB")
    return value


class TaskSetPublic(BaseModel):
    id: str
    name: str
    description: str | None = None
    metadata: dict[str, Any] | None = None


class TaskSetCreate(BaseModel):
    name: str = Field(..., max_length=128)
    description: str | None = Field(default=None, max_length=4096)
    metadata: dict[str, Any] | None = None

    @field_validator("metadata")
    @classmethod
    def _validate_metadata_size(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return validate_metadata_size(v)


class TaskSetUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=4096)
    metadata: dict[str, Any] | None = None

    @field_validator("metadata")
    @classmethod
    def _validate_metadata_size(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return validate_metadata_size(v)


class TaskPublic(BaseModel):
    id: str
    task_set_id: str | None = None
    source_lang: str
    target_lang: str
    source_text: str
    metadata: dict[str, Any] | None = None


class TaskCreate(BaseModel):
    task_set_id: UuidStr | None = None
    source_lang: str = Field(default="ja", max_length=16)
    target_lang: str = Field(default="zh", max_length=16)
    source_text: str = Field(..., max_length=131072)
    metadata: dict[str, Any] | None = None

    @field_validator("metadata")
    @classmethod
    def _validate_metadata_size(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return validate_metadata_size(v)


class TaskUpdate(BaseModel):
    task_set_id: UuidStr | None = None
    source_lang: str | None = Field(default=None, max_length=16)
    target_lang: str | None = Field(default=None, max_length=16)
    source_text: str | None = Field(default=None, max_length=131072)
    metadata: dict[str, Any] | None = None

    @field_validator("metadata")
    @classmethod
    def _validate_metadata_size(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return validate_metadata_size(v)
