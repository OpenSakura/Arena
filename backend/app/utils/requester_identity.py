"""Shared requester identity contract for votes and leaderboard logic."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Literal
import uuid

from fastapi import Request
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.vote import Vote
from app.utils.anon import normalize_anon_id
from app.utils.client_ip import get_client_ip

RequesterIdentityKind = Literal["user", "fingerprint", "ip", "anon", "unknown"]

REQUESTER_IDENTITY_PRECEDENCE: tuple[RequesterIdentityKind, ...] = (
    "user",
    "fingerprint",
    "ip",
    "anon",
    "unknown",
)


def _salted_sha256(value: str | None, *, salt: str) -> str | None:
    if not value:
        return None
    return hashlib.sha256(f"{salt}|{value}".encode("utf-8")).hexdigest()


def hash_request_ip(
    request: Request,
    salt: str,
    *,
    trust_x_forwarded_for: bool = False,
) -> str | None:
    return _salted_sha256(
        get_client_ip(request, trust_x_forwarded_for=trust_x_forwarded_for),
        salt=salt,
    )


def hash_request_user_agent(request: Request, salt: str) -> str | None:
    return _salted_sha256(request.headers.get("user-agent"), salt=salt)


@dataclass(frozen=True, slots=True)
class RequesterIdentity:
    voter_user_id: uuid.UUID | None
    ip_hash: str | None
    user_agent_hash: str | None
    voter_anon_id: str | None

    @classmethod
    def from_request(
        cls,
        request: Request,
        *,
        voter_user_id: uuid.UUID | None,
        voter_anon_id: str | None,
        ip_hash_salt: str,
        user_agent_hash_salt: str,
        trust_x_forwarded_for: bool = False,
    ) -> "RequesterIdentity":
        return cls(
            voter_user_id=voter_user_id,
            ip_hash=hash_request_ip(
                request,
                ip_hash_salt,
                trust_x_forwarded_for=trust_x_forwarded_for,
            ),
            user_agent_hash=hash_request_user_agent(request, user_agent_hash_salt),
            voter_anon_id=normalize_anon_id(voter_anon_id),
        )

    @property
    def primary_kind(self) -> RequesterIdentityKind:
        if self.voter_user_id is not None:
            return "user"
        if self.ip_hash and self.user_agent_hash:
            return "fingerprint"
        if self.ip_hash:
            return "ip"
        if self.voter_anon_id:
            return "anon"
        return "unknown"

    def judge_key(self, *, fallback_vote_id: uuid.UUID | None = None) -> str:
        if self.voter_user_id is not None:
            return f"user:{self.voter_user_id}"
        if self.ip_hash and self.user_agent_hash:
            return f"fp:{self.ip_hash}:{self.user_agent_hash}"
        if self.ip_hash:
            return f"ip:{self.ip_hash}"
        if self.voter_anon_id:
            return f"anon:{self.voter_anon_id}"
        if fallback_vote_id is None:
            raise ValueError("fallback_vote_id is required for unknown requester keys")
        return f"unknown:{fallback_vote_id}"

    def battle_lookup_filters(self) -> list[tuple[RequesterIdentityKind, object]]:
        filters: list[tuple[RequesterIdentityKind, object]] = []

        if self.voter_user_id is not None:
            filters.append(("user", Vote.voter_user_id == self.voter_user_id))

        if self.ip_hash and self.user_agent_hash:
            filters.append(
                (
                    "fingerprint",
                    and_(
                        Vote.ip_hash == self.ip_hash,
                        Vote.user_agent_hash == self.user_agent_hash,
                    ),
                )
            )

        if self.ip_hash:
            filters.append(
                (
                    "ip",
                    and_(
                        Vote.ip_hash == self.ip_hash,
                        Vote.user_agent_hash.is_(None),
                    ),
                )
            )

        if self.voter_anon_id:
            filters.append(("anon", Vote.voter_anon_id == self.voter_anon_id))

        return filters

    def primary_vote_filter(self) -> object | None:
        if self.voter_user_id is not None:
            return Vote.voter_user_id == self.voter_user_id
        if self.ip_hash and self.user_agent_hash:
            return and_(
                Vote.voter_user_id.is_(None),
                Vote.ip_hash == self.ip_hash,
                Vote.user_agent_hash == self.user_agent_hash,
            )
        if self.ip_hash:
            return and_(
                Vote.voter_user_id.is_(None),
                Vote.ip_hash == self.ip_hash,
                Vote.user_agent_hash.is_(None),
            )
        if self.voter_anon_id:
            return and_(
                Vote.voter_user_id.is_(None),
                Vote.ip_hash.is_(None),
                Vote.voter_anon_id == self.voter_anon_id,
            )
        return None


def find_existing_battle_vote(
    db: Session,
    *,
    battle_id: uuid.UUID,
    requester_identity: RequesterIdentity,
) -> Vote | None:
    for _kind, where_clause in requester_identity.battle_lookup_filters():
        vote = _find_battle_vote(db, battle_id=battle_id, where_clause=where_clause)
        if vote is not None:
            return vote
    return None


def _find_battle_vote(
    db: Session,
    *,
    battle_id: uuid.UUID,
    where_clause: object,
) -> Vote | None:
    stmt = (
        select(Vote)
        .where(Vote.battle_id == battle_id, where_clause)
        .order_by(Vote.created_at.asc(), Vote.id.asc())
    )
    return db.execute(stmt).scalars().first()
