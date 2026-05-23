"""app.schemas.leaderboard

Schemas for leaderboard responses.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LeaderboardRow(BaseModel):
    model_id: str
    display_name: str
    rank: int | None = None
    confidence_rank: int | None = None
    rating: float
    rating_lower: float | None = None
    rating_upper: float | None = None
    games_played: int


class VoteSourceCounts(BaseModel):
    human: int = 0
    bot: int = 0
    total: int = 0


class LeaderboardResponse(BaseModel):
    models: list[LeaderboardRow]
    method: str = "elo"
    ci: bool = False
    bootstrap_rounds: int | None = None
    vote_source_counts: VoteSourceCounts = Field(default_factory=VoteSourceCounts)
