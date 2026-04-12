"""app.schemas.me

Schemas for /me and user profile onboarding.

Notes:
- Profile fields are used for downstream filtering, not as an access gate.
- Keep the raw answers (JSON) so you can evolve the schema without data loss.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, model_validator


class UserPublic(BaseModel):
    id: str
    oidc_issuer: str
    oidc_sub: str
    created_at: str


class ProfilePublic(BaseModel):
    display_name: str | None = None
    ui_language: str | None = None
    zh_variant: str | None = None
    jp_proficiency: dict[str, Any] | None = None
    translation_experience: dict[str, Any] | None = None
    consents: dict[str, Any] | None = None
    completed_at: str | None = None


class MeResponse(BaseModel):
    authenticated: bool
    is_admin: bool = False
    user: UserPublic | None = None
    profile: ProfilePublic | None = None


class ProfileUpsert(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    ui_language: str | None = Field(default=None, max_length=32)
    zh_variant: str | None = Field(default=None, max_length=32)

    jp_proficiency: dict[str, Any] | None = None
    translation_experience: dict[str, Any] | None = None
    consents: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_json_field_sizes(self) -> "ProfileUpsert":
        _max_json_bytes = 16 * 1024  # 16 KB
        for field_name in ("jp_proficiency", "translation_experience", "consents"):
            value = getattr(self, field_name)
            if value is not None:
                serialized = json.dumps(
                    value, separators=(",", ":"), ensure_ascii=False
                )
                if len(serialized.encode("utf-8")) > _max_json_bytes:
                    raise ValueError(
                        f"{field_name} exceeds maximum allowed size of {_max_json_bytes} bytes"
                    )
        return self
