"""Shared requester identity contract for votes and leaderboard logic."""

from __future__ import annotations

from dataclasses import dataclass
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.vote import Vote


@dataclass(frozen=True, slots=True)
class RequesterIdentity:
    voter_user_id: uuid.UUID

    def judge_key(self) -> str:
        return f"user:{self.voter_user_id}"

    def battle_lookup_filter(self) -> object:
        return Vote.voter_user_id == self.voter_user_id


def find_existing_battle_vote(
    db: Session,
    *,
    battle_id: uuid.UUID,
    requester_identity: RequesterIdentity,
) -> Vote | None:
    stmt = (
        select(Vote)
        .where(Vote.battle_id == battle_id, requester_identity.battle_lookup_filter())
        .order_by(Vote.created_at.asc(), Vote.id.asc())
    )
    return db.execute(stmt).scalars().first()
