"""app.schemas.prompts

Schemas for prompt templates.
"""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from pydantic import BaseModel, Field, field_validator


_MAX_JSON_DICT_BYTES = 65_536  # 64 KB


class PromptTemplateAdmin(BaseModel):
    id: str
    name: str
    version: int
    template_text: str
    input_schema: dict[str, Any] | None = None
    content_hash: str
    created_at: datetime


class PromptTemplateCreate(BaseModel):
    name: str = Field(..., max_length=128)
    template_text: str = Field(..., max_length=65536)
    input_schema: dict[str, Any] | None = None

    @field_validator("input_schema")
    @classmethod
    def _validate_input_schema_size(
        cls, v: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if v is None:
            return v
        serialized = json.dumps(v, ensure_ascii=False)
        if len(serialized.encode("utf-8")) > _MAX_JSON_DICT_BYTES:
            raise ValueError("input_schema JSON must not exceed 64 KB")
        return v
