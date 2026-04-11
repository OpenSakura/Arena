"""app.schemas.battles

Schemas for battles and runs.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas._types import UuidStr

# Allowed battle modes.  Add new entries here — no migration needed.
ALLOWED_MODES = ("jp2zh_ab",)


class BattleCreate(BaseModel):
    # Optional overrides; defaults chosen server-side.
    task_set_id: UuidStr | None = None
    task_id: UuidStr | None = None
    mode: str = "jp2zh_ab"
    turnstile_token: str | None = Field(default=None, max_length=4096)

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        if v not in ALLOWED_MODES:
            raise ValueError(f"Unsupported mode: {v!r}. Allowed: {ALLOWED_MODES}")
        return v


class RunPublic(BaseModel):
    id: str
    side: Literal["A", "B"]
    output_text: str | None = None
    stats: dict[str, Any] | None = None
    error_text: str | None = None


class BattlePublic(BaseModel):
    id: str
    task_id: str
    source_text: str
    source_lang: str
    target_lang: str
    mode: str
    status: Literal["pending", "running", "completed", "failed"]
    # Keep model identities hidden until after vote.
    run_a: RunPublic | None = None
    run_b: RunPublic | None = None
