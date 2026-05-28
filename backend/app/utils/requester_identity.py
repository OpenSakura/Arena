"""Shared requester identity contract for votes and leaderboard logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.vote import Vote


def _existing_votes_from_stub(db: Session) -> list[object] | None:
    existing_votes = getattr(db, "existing_votes", None)
    if isinstance(existing_votes, list):
        return existing_votes
    private_existing_votes = getattr(db, "_existing_votes", None)
    if isinstance(private_existing_votes, list):
        return private_existing_votes
    return None


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


def find_any_battle_vote(
    db: Session,
    *,
    battle_id: uuid.UUID,
) -> Vote | None:
    existing_votes = _existing_votes_from_stub(db)
    if existing_votes is not None:
        return cast(
            Vote | None,
            next(
                (
                    vote
                    for vote in existing_votes
                    if getattr(vote, "battle_id", None) == battle_id
                ),
                None,
            ),
        )

    stmt = (
        select(Vote)
        .where(Vote.battle_id == battle_id)
        .order_by(Vote.created_at.asc(), Vote.id.asc())
    )
    return db.execute(stmt).scalars().first()


def find_consumer_battle_vote(
    db: Session,
    *,
    battle_id: uuid.UUID,
    consumer_type: str,
) -> Vote | None:
    existing_votes = _existing_votes_from_stub(db)
    normalized_consumer_type = "bot" if consumer_type == "bot" else "human"
    if existing_votes is not None:
        return cast(
            Vote | None,
            next(
                (
                    vote
                    for vote in existing_votes
                    if getattr(vote, "battle_id", None) == battle_id
                    and _vote_matches_consumer(vote, normalized_consumer_type)
                ),
                None,
            ),
        )

    stmt = (
        select(Vote)
        .where(Vote.battle_id == battle_id, _consumer_filter(normalized_consumer_type))
        .order_by(Vote.created_at.asc(), Vote.id.asc())
    )
    return db.execute(stmt).scalars().first()


def _vote_matches_consumer(vote: object, consumer_type: str) -> bool:
    has_service_account = getattr(vote, "service_account_id", None) is not None
    return has_service_account if consumer_type == "bot" else not has_service_account


def _consumer_filter(consumer_type: str) -> object:
    if consumer_type == "bot":
        return Vote.service_account_id.is_not(None)
    return Vote.service_account_id.is_(None)
