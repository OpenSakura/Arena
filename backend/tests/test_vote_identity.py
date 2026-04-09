from __future__ import annotations

import uuid

from app.api.routes.votes import _find_existing_vote_for_identity


class _ScalarResult:
    def scalars(self) -> "_ScalarResult":
        return self

    def first(self):
        return None


class _CaptureDB:
    def __init__(self) -> None:
        self.stmt = None
        self.execute_calls = 0

    def execute(self, stmt):
        self.stmt = stmt
        self.execute_calls += 1
        return _ScalarResult()


def test_find_existing_vote_checks_user_or_anon_for_authenticated_votes() -> None:
    db = _CaptureDB()

    _find_existing_vote_for_identity(
        db,  # type: ignore[arg-type]
        battle_id=uuid.uuid4(),
        voter_user_id=uuid.uuid4(),
        voter_anon_id="anon-1",
        ip_hash="ip-hash",
        user_agent_hash="ua-hash",
    )

    sql = str(db.stmt)
    assert "votes.voter_user_id =" in sql
    assert "OR votes.voter_anon_id =" in sql


def test_find_existing_vote_uses_anon_identity_for_anonymous_votes() -> None:
    db = _CaptureDB()

    _find_existing_vote_for_identity(
        db,  # type: ignore[arg-type]
        battle_id=uuid.uuid4(),
        voter_user_id=None,
        voter_anon_id="anon-1",
        ip_hash=None,
        user_agent_hash=None,
    )

    sql = str(db.stmt)
    assert "votes.voter_anon_id =" in sql
    assert "votes.voter_user_id IS NULL" not in sql


def test_find_existing_vote_uses_fingerprint_for_anonymous_votes() -> None:
    db = _CaptureDB()

    _find_existing_vote_for_identity(
        db,  # type: ignore[arg-type]
        battle_id=uuid.uuid4(),
        voter_user_id=None,
        voter_anon_id=None,
        ip_hash="ip-hash",
        user_agent_hash="ua-hash",
    )

    sql = str(db.stmt)
    assert "votes.ip_hash =" in sql
    assert "votes.user_agent_hash =" in sql


def test_find_existing_vote_skips_query_without_identity() -> None:
    db = _CaptureDB()

    vote = _find_existing_vote_for_identity(
        db,  # type: ignore[arg-type]
        battle_id=uuid.uuid4(),
        voter_user_id=None,
        voter_anon_id=None,
        ip_hash=None,
        user_agent_hash=None,
    )

    assert vote is None
    assert db.execute_calls == 0
