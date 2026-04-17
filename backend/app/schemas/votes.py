"""app.schemas.votes

Schemas for votes.

Notes:
- Capture rubric tags for richer analytics.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, Field, field_validator

# Predefined rubric tag categories for translation quality assessment.
# Keep in sync with RUBRIC_TAGS in frontend/src/components/BattleView.tsx.
ALLOWED_RUBRIC_TAGS = frozenset(
    {
        "accuracy",
        "fluency",
        "style",
        "consistency",
        "naturalness",
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


VoteReveal: TypeAlias = dict[str, dict[str, str]]


class VoteSubmitResponse(BaseModel):
    vote_id: str
    battle_id: str
    winner: Literal["A", "B", "tie"]
    reveal: VoteReveal
