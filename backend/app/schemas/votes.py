"""app.schemas.votes

Schemas for votes.

Notes:
- Capture rubric tags for richer analytics.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class VoteCreate(BaseModel):
    winner: Literal["A", "B", "tie"] = Field(..., description="A | B | tie")
    rubric: dict[str, Any] | None = None
    comment: str | None = Field(default=None, max_length=4096)
    turnstile_token: str | None = Field(default=None, max_length=4096)

    @field_validator("rubric")
    @classmethod
    def _validate_rubric_size(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if value is None:
            return value
        serialized = json.dumps(value, ensure_ascii=False)
        if len(serialized.encode("utf-8")) > 65536:
            raise ValueError("rubric JSON must not exceed 64 KB")
        return value


class VoteSubmitResponse(BaseModel):
    vote_id: str
    battle_id: str
    winner: Literal["A", "B", "tie"]
    reveal: dict[str, dict[str, str]]
