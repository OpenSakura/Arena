from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas._types import UuidStr
from app.schemas.battles import ALLOWED_MODES


BotBattleStatus: TypeAlias = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "timeout",
]


class BotBattleCreateAndWaitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_set_id: UuidStr | None = None
    task_id: UuidStr | None = None
    mode: str = "jp2zh_ab"
    timeout_seconds: int = Field(default=60, ge=1, le=120)

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, value: str) -> str:
        if value not in ALLOWED_MODES:
            raise ValueError(f"Unsupported mode: {value!r}. Allowed: {ALLOWED_MODES}")
        return value


class BotRunPublic(BaseModel):
    id: str
    side: Literal["A", "B"]
    model_id: str
    output_text: str | None = None
    error_text: str | None = None


class BotBattleResult(BaseModel):
    battle_id: str
    run_a: BotRunPublic | None = None
    run_b: BotRunPublic | None = None
    error: str | None = None


class BotBattleCreateAndWaitResponse(BaseModel):
    battle_id: str
    status: BotBattleStatus
    status_url: str = Field(..., max_length=2048)
    result: BotBattleResult | None = None


class BotBattleStatusResponse(BaseModel):
    battle_id: str
    status: BotBattleStatus
    status_url: str = Field(..., max_length=2048)
    result_url: str | None = Field(default=None, max_length=2048)
    result: BotBattleResult | None = None
