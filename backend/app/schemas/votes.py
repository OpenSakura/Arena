"""app.schemas.votes

Schemas for votes.

Notes:
- Capture rubric tags for richer analytics.
"""

from __future__ import annotations

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, Field, field_validator

from app.utils.json_limits import validate_bounded_json_object

# Predefined rubric tag categories for translation quality assessment.
# Keep in sync with RUBRIC_TAGS in frontend/src/components/BattleView.tsx.
ALLOWED_RUBRIC_TAGS = frozenset(
    {
        "accuracy",
        "fluency",
        "style",
        "knowledge",
        "cultural",
        "voice",
        "terminology",
        "consistency",
        "naturalness",
        "refusal",
    }
)


class RubricPayload(BaseModel):
    """Structured rubric data submitted with a vote."""

    tags: list[str] = Field(default_factory=list, max_length=len(ALLOWED_RUBRIC_TAGS))

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, v: list[str]) -> list[str]:
        invalid = set(v) - ALLOWED_RUBRIC_TAGS
        if invalid:
            raise ValueError(
                f"Unknown rubric tags: {sorted(invalid)}. "
                f"Allowed: {sorted(ALLOWED_RUBRIC_TAGS)}"
            )
        # Deduplicate while preserving order.
        seen: set[str] = set()
        deduped: list[str] = []
        for tag in v:
            if tag not in seen:
                seen.add(tag)
                deduped.append(tag)
        return deduped


class VoteCreate(BaseModel):
    winner: Literal["A", "B", "tie"] = Field(..., description="A | B | tie")
    rubric: RubricPayload | None = None
    comment: str | None = Field(default=None, max_length=4096)
    bot_metadata: dict[str, Any] | None = None

    @field_validator("bot_metadata", mode="before")
    @classmethod
    def _validate_bot_metadata(cls, value: Any) -> Any:
        if value is None:
            return value
        return validate_bounded_json_object(value, field_name="bot_metadata")


VoteReveal: TypeAlias = dict[str, dict[str, str]]


class VoteSubmitResponse(BaseModel):
    vote_id: str
    battle_id: str
    winner: Literal["A", "B", "tie"]
    reveal: VoteReveal
    voter_actor_type: Literal["human", "bot"] = "human"
    service_account_id: str | None = None
    service_account_name: str | None = None
    service_account_token_id: str | None = None
    bot_metadata: dict[str, Any] | None = None
