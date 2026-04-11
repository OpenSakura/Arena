from __future__ import annotations

import hashlib
import uuid

from fastapi import Request

from app.utils.requester_identity import (
    REQUESTER_IDENTITY_PRECEDENCE,
    RequesterIdentity,
    find_existing_battle_vote,
    hash_request_ip,
    hash_request_user_agent,
)


class _ScalarResult:
    def __init__(self, value=None) -> None:
        self._value = value

    def scalars(self) -> "_ScalarResult":
        return self

    def first(self):
        return self._value


class _CaptureDB:
    def __init__(self, results: list[object | None] | None = None) -> None:
        self.stmts: list[object] = []
        self._results = list(results or [])
        self.execute_calls = 0

    def execute(self, stmt):
        self.stmts.append(stmt)
        self.execute_calls += 1
        value = self._results.pop(0) if self._results else None
        return _ScalarResult(value)


def _request(*, ip: str = "127.0.0.1", user_agent: str = "arena-test-agent") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"user-agent", user_agent.encode("utf-8"))],
        "client": (ip, 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


def test_requester_identity_precedence_contract_is_explicit() -> None:
    assert REQUESTER_IDENTITY_PRECEDENCE == (
        "user",
        "fingerprint",
        "ip",
        "anon",
        "unknown",
    )


def test_hash_request_helpers_use_salted_sha256() -> None:
    request = _request(ip="203.0.113.7", user_agent="arena-agent")

    assert (
        hash_request_ip(request, "ip-salt")
        == hashlib.sha256(b"ip-salt|203.0.113.7").hexdigest()
    )
    assert (
        hash_request_user_agent(request, "ua-salt")
        == hashlib.sha256(b"ua-salt|arena-agent").hexdigest()
    )


def test_find_existing_vote_checks_user_identity_first_for_authenticated_votes() -> (
    None
):
    db = _CaptureDB(results=[object()])

    vote = find_existing_battle_vote(
        db,  # type: ignore[arg-type]
        battle_id=uuid.uuid4(),
        requester_identity=RequesterIdentity(
            voter_user_id=uuid.uuid4(),
            voter_anon_id="anon-1",
            ip_hash="ip-hash",
            user_agent_hash="ua-hash",
        ),
    )

    assert vote is not None
    assert db.execute_calls == 1
    sql = str(db.stmts[0])
    assert "votes.voter_user_id =" in sql
    assert "votes.voter_anon_id =" not in sql
    assert "votes.ip_hash =" not in sql


def test_find_existing_vote_uses_fingerprint_before_weaker_anonymous_signals() -> None:
    db = _CaptureDB(results=[object()])

    _ = find_existing_battle_vote(
        db,  # type: ignore[arg-type]
        battle_id=uuid.uuid4(),
        requester_identity=RequesterIdentity(
            voter_user_id=None,
            voter_anon_id="anon-1",
            ip_hash="ip-hash",
            user_agent_hash="ua-hash",
        ),
    )

    sql = str(db.stmts[0])
    assert "votes.ip_hash =" in sql
    assert "votes.user_agent_hash =" in sql
    assert "votes.voter_anon_id =" not in sql


def test_find_existing_vote_falls_back_to_ip_after_missing_fingerprint_match() -> None:
    db = _CaptureDB(results=[None, object()])

    vote = find_existing_battle_vote(
        db,  # type: ignore[arg-type]
        battle_id=uuid.uuid4(),
        requester_identity=RequesterIdentity(
            voter_user_id=None,
            voter_anon_id="anon-1",
            ip_hash="ip-hash",
            user_agent_hash="ua-hash",
        ),
    )

    assert vote is not None
    assert len(db.stmts) == 2
    assert "votes.user_agent_hash =" in str(db.stmts[0])
    assert "votes.user_agent_hash =" not in str(db.stmts[1])
    assert "votes.ip_hash =" in str(db.stmts[1])
    assert "votes.voter_anon_id =" not in str(db.stmts[1])


def test_find_existing_vote_falls_back_to_anon_cookie_after_missing_stronger_matches() -> (
    None
):
    db = _CaptureDB(results=[None, None, object()])

    vote = find_existing_battle_vote(
        db,  # type: ignore[arg-type]
        battle_id=uuid.uuid4(),
        requester_identity=RequesterIdentity(
            voter_user_id=None,
            voter_anon_id="anon-1",
            ip_hash="ip-hash",
            user_agent_hash="ua-hash",
        ),
    )

    assert vote is not None
    assert db.execute_calls == 3
    assert "votes.user_agent_hash =" in str(db.stmts[0])
    assert "votes.user_agent_hash =" not in str(db.stmts[1])
    assert "votes.ip_hash =" in str(db.stmts[1])
    assert "votes.voter_anon_id =" in str(db.stmts[2])
    assert "votes.ip_hash =" not in str(db.stmts[2])


def test_find_existing_vote_skips_query_without_identity() -> None:
    db = _CaptureDB()

    vote = find_existing_battle_vote(
        db,  # type: ignore[arg-type]
        battle_id=uuid.uuid4(),
        requester_identity=RequesterIdentity(
            voter_user_id=None,
            voter_anon_id=None,
            ip_hash=None,
            user_agent_hash=None,
        ),
    )

    assert vote is None
    assert db.execute_calls == 0
