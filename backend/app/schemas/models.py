"""app.schemas.models

Schemas for model registry.

Notes:
- Never expose gateway secrets in API responses.
"""

from __future__ import annotations

from datetime import datetime
import json
from typing import Literal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.prompting import normalize_optional_prompt_text


_MAX_JSON_DICT_BYTES = 65_536  # 64 KB


def _validate_json_dict_size(
    value: dict[str, Any] | None, field_label: str
) -> dict[str, Any] | None:
    """Reject dicts whose JSON serialization exceeds *_MAX_JSON_DICT_BYTES*."""
    if value is None:
        return value
    serialized = json.dumps(value, ensure_ascii=False)
    if len(serialized.encode("utf-8")) > _MAX_JSON_DICT_BYTES:
        raise ValueError(f"{field_label} JSON must not exceed 64 KB")
    return value


class ModelAdmin(BaseModel):
    id: str
    display_name: str
    model_name: str
    base_url: str
    enabled: bool
    visibility: Literal["public", "private"]
    tags: dict[str, Any] | None = None
    temperature: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    system_prompt: str | None = None
    user_prompt: str | None = None
    params: dict[str, Any] | None = None
    has_api_key: bool
    created_at: datetime
    updated_at: datetime


class ModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(..., max_length=128)
    model_name: str = Field(..., max_length=128)
    base_url: str = Field(
        ...,
        max_length=2048,
        description="Admin-only. Points to the local API gateway by design (no SSRF mitigation needed).",
    )

    enabled: bool = True
    visibility: Literal["public", "private"] = "public"
    tags: dict[str, Any] | None = None

    # Common OpenAI-compatible sampling params.
    # temperature range [0, 2] matches the DB CHECK constraint ck_models_temperature.
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    frequency_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    presence_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    system_prompt: str | None = None
    user_prompt: str | None = None

    params: dict[str, Any] | None = None

    api_key: str | None = None

    @field_validator("tags")
    @classmethod
    def _validate_tags_size(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return _validate_json_dict_size(v, "tags")

    @field_validator("params")
    @classmethod
    def _validate_params_size(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return _validate_json_dict_size(v, "params")

    @field_validator("system_prompt", "user_prompt", mode="before")
    @classmethod
    def _normalize_prompt_text(cls, v: Any) -> Any:
        if isinstance(v, str) or v is None:
            return normalize_optional_prompt_text(v)
        return v


class ModelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=128)
    model_name: str | None = Field(default=None, max_length=128)
    base_url: str | None = Field(default=None, max_length=2048)

    enabled: bool | None = None
    visibility: Literal["public", "private"] | None = None
    tags: dict[str, Any] | None = None

    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    frequency_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    presence_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    system_prompt: str | None = None
    user_prompt: str | None = None

    params: dict[str, Any] | None = None

    api_key: str | None = None

    @field_validator("tags")
    @classmethod
    def _validate_update_tags_size(
        cls, v: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        return _validate_json_dict_size(v, "tags")

    @field_validator("params")
    @classmethod
    def _validate_update_params_size(
        cls, v: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        return _validate_json_dict_size(v, "params")

    @field_validator("system_prompt", "user_prompt", mode="before")
    @classmethod
    def _normalize_update_prompt_text(cls, v: Any) -> Any:
        if isinstance(v, str) or v is None:
            return normalize_optional_prompt_text(v)
        return v
