from __future__ import annotations

import uuid

from app.utils.requester_identity import (
    RequesterIdentity,
    find_consumer_battle_vote,
    find_existing_battle_vote,
)


class _ScalarResult:
    def __init__(self, value=None) -> None:
        self._value = value

    def scalars(self) -> "_ScalarResult":
        return self

    def first(self):
        return self._value


class _CaptureDB:
    def __init__(self, result: object | None = None) -> None:
        self.stmts: list[object] = []
        self.execute_calls = 0
        self._result = result

    def execute(self, stmt):
        self.stmts.append(stmt)
        self.execute_calls += 1
        return _ScalarResult(self._result)


def test_find_existing_vote_uses_authenticated_user_lookup() -> None:
    db = _CaptureDB(result=object())
    user_id = uuid.uuid4()

    vote = find_existing_battle_vote(
        db,  # type: ignore[arg-type]
        battle_id=uuid.uuid4(),
        requester_identity=RequesterIdentity(voter_user_id=user_id),
    )

    assert vote is not None
    assert db.execute_calls == 1
    sql = str(db.stmts[0])
    assert "votes.voter_user_id =" in sql


def test_find_consumer_battle_vote_uses_human_slot_lookup() -> None:
    db = _CaptureDB(result=object())

    vote = find_consumer_battle_vote(
        db,  # type: ignore[arg-type]
        battle_id=uuid.uuid4(),
        consumer_type="human",
    )

    assert vote is not None
    assert db.execute_calls == 1
    sql = str(db.stmts[0])
    assert "votes.battle_id =" in sql
    assert "votes.service_account_id IS NULL" in sql
    assert "votes.voter_user_id =" not in sql


def test_find_consumer_battle_vote_uses_bot_slot_lookup() -> None:
    db = _CaptureDB(result=object())

    vote = find_consumer_battle_vote(
        db,  # type: ignore[arg-type]
        battle_id=uuid.uuid4(),
        consumer_type="bot",
    )

    assert vote is not None
    assert db.execute_calls == 1
    sql = str(db.stmts[0])
    assert "votes.battle_id =" in sql
    assert "votes.service_account_id IS NOT NULL" in sql


def test_judge_key_is_user_scoped() -> None:
    user_id = uuid.uuid4()

    assert RequesterIdentity(voter_user_id=user_id).judge_key() == f"user:{user_id}"


def test_battle_lookup_filter_targets_user_id() -> None:
    user_id = uuid.uuid4()

    where_clause = RequesterIdentity(voter_user_id=user_id).battle_lookup_filter()

    assert "votes.voter_user_id =" in str(where_clause)
