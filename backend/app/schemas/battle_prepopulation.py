"""app.schemas.battle_prepopulation

Schemas for battle prepopulation admin jobs and pool stats.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas._types import UuidStr

BattlePrepopulationJobStatus = Literal[
    "pending", "running", "completed", "failed", "cancelled"
]


class BattlePrepopulationJobCreate(BaseModel):
    amount: int = Field(..., ge=1)
    model_ids: list[UuidStr] = Field(default_factory=list, max_length=2)


class BattlePrepopulationLatestJobPublic(BaseModel):
    id: str
    requested_count: int
    completed_count: int
    failed_count: int
    status: BattlePrepopulationJobStatus
    requested_by_user_id: str
    model_ids: list[str] = Field(default_factory=list)
    last_error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str
    updated_at: str


class BattlePrepopulationJobPublic(BattlePrepopulationLatestJobPublic):
    pass


class BattlePrepopulationStatsPublic(BaseModel):
    available_admin_count: int
    available_recycled_count: int
    available_total_count: int
    generating_count: int
    failed_count: int
    voted_consumed_count: int
    total_count: int
    oldest_available_at: str | None = None
    newest_available_at: str | None = None
    latest_job: BattlePrepopulationLatestJobPublic | None = None
    max_job_size: int


class BattlePrepopulationModelOptionPublic(BaseModel):
    id: str
    display_name: str
    model_name: str
