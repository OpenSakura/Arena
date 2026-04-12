from __future__ import annotations

import hashlib
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Literal, cast
import uuid

from fastapi import HTTPException, Request, Response
import pytest
from sqlalchemy.exc import IntegrityError

from app.api.routes import votes
from app.core.security import Principal
from app.models.vote import Vote
from app.schemas.votes import VoteCreate, VoteSubmitResponse
from app.utils.requester_identity import (
    RequesterIdentity,
    hash_request_ip,
    hash_request_user_agent,
)


def _request(
    *,
    ip: str = "127.0.0.1",
    user_agent: str = "arena-test-agent",
    cookie: str | None = None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = [(b"user-agent", user_agent.encode("utf-8"))]
    if cookie is not None:
        headers.append((b"cookie", cookie.encode("ascii")))

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers,
        "client": (ip, 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


def _settings(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "anon_id_cookie_secure": True,
        "anon_ip_hash_salt": "ip-salt",
        "anon_user_agent_hash_salt": "ua-salt",
        "leaderboard_refresh_elo_k": 32.0,
        "turnstile_secret_key": None,
        "turnstile_verify_url": "https://turnstile.example/siteverify",
        "anon_vote_submit_rate_limit_window_seconds": 60,
        "trust_x_forwarded_for": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _integrity_error(message: str) -> IntegrityError:
    return IntegrityError("stmt", {}, Exception(message))


class _RowsResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = list(rows)

    def scalars(self) -> "_RowsResult":
        return self

    def all(self) -> list[object]:
        return list(self._rows)


class _SingleResult:
    def __init__(self, value: object | None) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _VoteDB:
    def __init__(
        self,
        *,
        battle: object | None,
        runs: Sequence[object],
        flush_error: Exception | None = None,
        commit_error: Exception | None = None,
    ) -> None:
        self._battle = battle
        self._runs = list(runs)
        self._flush_error = flush_error
        self._commit_error = commit_error
        self.added: list[object] = []
        self.vote_row: Vote | None = None
        self.flush_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.execute_calls = 0

    def get(self, model_type: type[object], key: uuid.UUID) -> object | None:
        if model_type is votes.Battle:
            if self._battle is None:
                return None
            if getattr(self._battle, "id", None) != key:
                return None
            return self._battle
        return None

    def execute(self, _stmt: object) -> _RowsResult:
        self.execute_calls += 1
        return _RowsResult(self._runs)

    def add(self, row: object) -> None:
        self.added.append(row)
        if isinstance(row, Vote):
            self.vote_row = row

    def flush(self) -> None:
        self.flush_calls += 1
        if self._flush_error is not None:
            raise self._flush_error
        if self.vote_row is not None and self.vote_row.id is None:
            self.vote_row.id = uuid.uuid4()

    def commit(self) -> None:
        self.commit_calls += 1
        if self._commit_error is not None:
            raise self._commit_error

    def rollback(self) -> None:
        self.rollback_calls += 1


def _battle_and_runs(
    *, status: str = "completed"
) -> tuple[SimpleNamespace, list[SimpleNamespace], uuid.UUID, uuid.UUID]:
    battle_id = uuid.uuid4()
    model_a_id = uuid.uuid4()
    model_b_id = uuid.uuid4()
    battle = SimpleNamespace(id=battle_id, status=status)
    runs = [
        SimpleNamespace(side="A", model_id=model_a_id, output_text="Translation A"),
        SimpleNamespace(side="B", model_id=model_b_id, output_text="Translation B"),
    ]
    return battle, runs, model_a_id, model_b_id


def _vote_submit_response(
    *,
    vote_id: uuid.UUID,
    battle_id: uuid.UUID,
    winner: Literal["A", "B", "tie"],
    model_a_id: uuid.UUID,
    model_b_id: uuid.UUID,
) -> VoteSubmitResponse:
    return VoteSubmitResponse(
        vote_id=str(vote_id),
        battle_id=str(battle_id),
        winner=cast(Literal["A", "B", "tie"], winner),
        reveal={
            "A": {"model_id": str(model_a_id), "display_name": "Model A"},
            "B": {"model_id": str(model_b_id), "display_name": "Model B"},
        },
    )


def test_parse_uuid_or_422_rejects_invalid_values() -> None:
    from app.utils.id import parse_uuid_or_422

    with pytest.raises(HTTPException) as exc_info:
        parse_uuid_or_422("bad-uuid", "battle_id")

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Invalid battle_id"


def test_hash_helpers_use_salted_sha256() -> None:
    request = _request(ip="203.0.113.7", user_agent="arena-agent")

    assert (
        hash_request_ip(request, "ip-salt")
        == hashlib.sha256(b"ip-salt|203.0.113.7").hexdigest()
    )
    assert (
        hash_request_user_agent(request, "ua-salt")
        == hashlib.sha256(b"ua-salt|arena-agent").hexdigest()
    )


def test_hash_helpers_return_none_when_source_data_missing() -> None:
    request_without_ua = _request(user_agent="")
    assert hash_request_user_agent(request_without_ua, "ua-salt") is None

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [],
        "server": ("testserver", 80),
    }
    request_without_client = Request(scope)
    assert hash_request_ip(request_without_client, "ip-salt") is None


def test_enforce_anon_vote_rate_limit_allows_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Limiter:
        def __init__(self) -> None:
            self.seen_keys: list[str] = []

        def is_limited(self, key: str) -> bool:
            self.seen_keys.append(key)
            return False

    limiter = _Limiter()
    monkeypatch.setattr(votes, "_get_vote_submit_rate_limiter", lambda: limiter)

    def fake_key_builder(**kwargs: object) -> str:
        captured.update(kwargs)
        return "anon-vote-key"

    monkeypatch.setattr(votes, "build_anon_rate_limit_key", fake_key_builder)

    votes._enforce_anon_vote_rate_limit(
        request=_request(),
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert captured["scope"] == "anon_vote_submit"
    assert limiter.seen_keys == ["anon-vote-key"]


def test_enforce_anon_vote_rate_limit_raises_429_when_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Limiter:
        def is_limited(self, _key: str) -> bool:
            return True

    monkeypatch.setattr(votes, "_get_vote_submit_rate_limiter", lambda: _Limiter())
    monkeypatch.setattr(votes, "build_anon_rate_limit_key", lambda **_kwargs: "anon")

    settings = _settings(anon_vote_submit_rate_limit_window_seconds=45)

    with pytest.raises(HTTPException) as exc_info:
        votes._enforce_anon_vote_rate_limit(
            request=_request(),
            settings=settings,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "Too many anonymous vote submissions"
    assert exc_info.value.headers == {"Retry-After": "45"}


class _ModelLookupDB:
    def __init__(self, lookup: dict[uuid.UUID, object]) -> None:
        self._lookup = lookup

    def get(self, model_type: type[object], key: uuid.UUID) -> object | None:
        if model_type is not votes.Model:
            return None
        return self._lookup.get(key)


def test_build_vote_submit_response_includes_reveal_metadata() -> None:
    vote_id = uuid.uuid4()
    battle_id = uuid.uuid4()
    model_a_id = uuid.uuid4()
    model_b_id = uuid.uuid4()
    db = _ModelLookupDB(
        {
            model_a_id: SimpleNamespace(id=model_a_id, display_name="Alpha"),
            model_b_id: SimpleNamespace(id=model_b_id, display_name="Beta"),
        }
    )

    response = votes._build_vote_submit_response(
        db=db,  # type: ignore[arg-type]
        vote_id=vote_id,
        battle_id=battle_id,
        winner="A",
        model_a_id=model_a_id,
        model_b_id=model_b_id,
    )

    assert response.vote_id == str(vote_id)
    assert response.battle_id == str(battle_id)
    assert response.reveal is not None
    assert response.reveal["A"] == {
        "model_id": str(model_a_id),
        "display_name": "Alpha",
    }
    assert response.reveal["B"] == {
        "model_id": str(model_b_id),
        "display_name": "Beta",
    }


def test_build_vote_submit_response_raises_when_model_lookup_fails() -> None:
    model_a_id = uuid.uuid4()
    model_b_id = uuid.uuid4()
    db = _ModelLookupDB({model_a_id: SimpleNamespace(id=model_a_id, display_name="A")})

    with pytest.raises(HTTPException) as exc_info:
        votes._build_vote_submit_response(
            db=db,  # type: ignore[arg-type]
            vote_id=uuid.uuid4(),
            battle_id=uuid.uuid4(),
            winner="A",
            model_a_id=model_a_id,
            model_b_id=model_b_id,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Model not found"


def test_resolve_duplicate_vote_conflict_raises_500_when_vote_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        votes,
        "find_existing_battle_vote",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(HTTPException) as exc_info:
        votes._resolve_duplicate_vote_conflict(
            db=object(),  # type: ignore[arg-type]
            response=Response(),
            battle_id=uuid.uuid4(),
            winner="A",
            voter_user_id=None,
            requester_identity=RequesterIdentity(
                voter_user_id=None,
                voter_anon_id="anon-1",
                ip_hash=None,
                user_agent_hash=None,
            ),
            model_a_id=uuid.uuid4(),
            model_b_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to persist vote"


def test_resolve_duplicate_vote_conflict_raises_409_for_mismatched_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        votes,
        "find_existing_battle_vote",
        lambda *_args, **_kwargs: SimpleNamespace(
            id=uuid.uuid4(),
            winner="B",
            revealed=True,
            voter_user_id=None,
            voter_anon_id="anon-1",
            ip_hash=None,
            user_agent_hash=None,
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes._resolve_duplicate_vote_conflict(
            db=object(),  # type: ignore[arg-type]
            response=Response(),
            battle_id=uuid.uuid4(),
            winner="A",
            voter_user_id=None,
            requester_identity=RequesterIdentity(
                voter_user_id=None,
                voter_anon_id="anon-1",
                ip_hash=None,
                user_agent_hash=None,
            ),
            model_a_id=uuid.uuid4(),
            model_b_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Vote already revealed and cannot be changed"


def test_resolve_duplicate_vote_conflict_returns_existing_vote_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle_id = uuid.uuid4()
    model_a_id = uuid.uuid4()
    model_b_id = uuid.uuid4()
    existing_vote = SimpleNamespace(
        id=uuid.uuid4(),
        winner="A",
        revealed=False,
        rubric=None,
        comment=None,
        voter_user_id=None,
        voter_anon_id="anon-1",
        ip_hash="ip-hash",
        user_agent_hash="ua-hash",
    )

    monkeypatch.setattr(
        votes,
        "find_existing_battle_vote",
        lambda *_args, **_kwargs: existing_vote,
    )

    db = SimpleNamespace(commit=lambda: None)
    response = votes._resolve_duplicate_vote_conflict(
        db=db,  # type: ignore[arg-type]
        response=Response(),
        battle_id=battle_id,
        winner="A",
        voter_user_id=None,
        requester_identity=RequesterIdentity(
            voter_user_id=None,
            voter_anon_id="anon-1",
            ip_hash="ip-hash",
            user_agent_hash="ua-hash",
        ),
        model_a_id=model_a_id,
        model_b_id=model_b_id,
    )

    assert isinstance(response, VoteSubmitResponse)
    assert response.vote_id == str(existing_vote.id)
    assert response.battle_id == str(battle_id)
    assert response.winner == "A"
    assert response.reveal is None


def test_submit_vote_rejects_invalid_battle_id() -> None:
    db = _VoteDB(battle=None, runs=[])

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id="bad-id",
            payload=VoteCreate(winner="A"),
            request=_request(),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=Principal(is_authenticated=False),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Invalid battle_id"


def test_submit_vote_returns_404_when_battle_is_missing() -> None:
    db = _VoteDB(battle=None, runs=[])

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(uuid.uuid4()),
            payload=VoteCreate(winner="A"),
            request=_request(),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=Principal(is_authenticated=False),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Battle not found"


def test_submit_vote_rejects_battle_not_ready_for_voting() -> None:
    battle, runs, _, _ = _battle_and_runs(status="pending")
    db = _VoteDB(battle=battle, runs=runs)

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A"),
            request=_request(),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=Principal(is_authenticated=False),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Battle is not ready for voting"


def test_submit_vote_rejects_battle_without_both_runs() -> None:
    battle, runs, _, _ = _battle_and_runs()
    db = _VoteDB(battle=battle, runs=runs[:1])

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A"),
            request=_request(),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=Principal(is_authenticated=False),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Battle runs not ready"


def test_submit_vote_returns_existing_vote_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, model_a_id, model_b_id = _battle_and_runs()
    db = _VoteDB(battle=battle, runs=runs)
    existing_vote = SimpleNamespace(
        id=uuid.uuid4(),
        winner="A",
        revealed=False,
        rubric=None,
        comment=None,
        voter_user_id=None,
        voter_anon_id="anon-1",
        ip_hash="ip-hash",
        user_agent_hash="ua-hash",
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(votes, "get_or_set_anon_id", lambda **_kwargs: "anon-1")

    def fake_find(
        _db: object,
        *,
        battle_id: uuid.UUID,
        requester_identity: RequesterIdentity,
    ) -> object:
        captured["battle_id"] = battle_id
        captured["requester_identity"] = requester_identity
        return existing_vote

    monkeypatch.setattr(votes, "find_existing_battle_vote", fake_find)
    monkeypatch.setattr(
        votes,
        "_enforce_anon_vote_rate_limit",
        lambda **_kwargs: pytest.fail("Rate-limit checks should not run"),
    )

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="A"),
        request=_request(),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=Principal(is_authenticated=False),
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert isinstance(response, VoteSubmitResponse)
    assert response.reveal is None
    assert response.winner == "A"
    assert captured["battle_id"] == battle.id
    requester_identity = captured["requester_identity"]
    assert isinstance(requester_identity, RequesterIdentity)
    assert requester_identity.voter_user_id is None
    assert requester_identity.voter_anon_id == "anon-1"
    assert (
        requester_identity.ip_hash == hashlib.sha256(b"ip-salt|127.0.0.1").hexdigest()
    )
    assert (
        requester_identity.user_agent_hash
        == hashlib.sha256(b"ua-salt|arena-test-agent").hexdigest()
    )
    assert db.added == []
    assert db.flush_calls == 0
    assert db.commit_calls == 0


def test_submit_vote_rejects_conflicting_existing_vote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, _, _ = _battle_and_runs()
    db = _VoteDB(battle=battle, runs=runs)

    monkeypatch.setattr(votes, "get_or_set_anon_id", lambda **_kwargs: "anon-1")
    monkeypatch.setattr(
        votes,
        "find_existing_battle_vote",
        lambda *_args, **_kwargs: SimpleNamespace(
            id=uuid.uuid4(),
            winner="B",
            revealed=True,
            voter_user_id=None,
            voter_anon_id="anon-1",
            ip_hash=None,
            user_agent_hash=None,
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A"),
            request=_request(),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=Principal(is_authenticated=False),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Vote already revealed and cannot be changed"


def test_submit_vote_anonymous_path_updates_ratings_and_records_vote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, model_a_id, model_b_id = _battle_and_runs()
    db = _VoteDB(battle=battle, runs=runs)
    settings = _settings(
        anon_ip_hash_salt="ip-salt", anon_user_agent_hash_salt="ua-salt"
    )

    calls: dict[str, object] = {}
    monkeypatch.setattr(votes, "get_or_set_anon_id", lambda **_kwargs: "anon-1")
    monkeypatch.setattr(
        votes,
        "find_existing_battle_vote",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        votes,
        "_enforce_anon_vote_rate_limit",
        lambda **_kwargs: calls.__setitem__("rate_limit", True),
    )

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="A", comment="great"),
        request=_request(ip="10.0.0.8", user_agent="arena-agent"),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=Principal(is_authenticated=False),
        settings=settings,  # type: ignore[arg-type]
    )

    assert isinstance(response, VoteSubmitResponse)
    assert response.reveal is None
    assert response.winner == "A"
    assert calls["rate_limit"] is True

    assert db.flush_calls == 1
    assert db.commit_calls == 1
    assert db.rollback_calls == 0

    vote_row = next(row for row in db.added if isinstance(row, Vote))
    assert vote_row.voter_user_id is None
    assert vote_row.voter_anon_id == "anon-1"
    assert vote_row.ip_hash == hashlib.sha256(b"ip-salt|10.0.0.8").hexdigest()
    assert (
        vote_row.user_agent_hash == hashlib.sha256(b"ua-salt|arena-agent").hexdigest()
    )


def test_submit_vote_authenticated_path_skips_anonymous_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, model_a_id, model_b_id = _battle_and_runs()
    db = _VoteDB(battle=battle, runs=runs)
    principal = Principal(is_authenticated=True, user_id=str(uuid.uuid4()))

    monkeypatch.setattr(votes, "get_or_set_anon_id", lambda **_kwargs: "anon-1")
    monkeypatch.setattr(
        votes,
        "find_existing_battle_vote",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        votes,
        "_enforce_anon_vote_rate_limit",
        lambda **_kwargs: pytest.fail("Rate-limit checks should be skipped"),
    )

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="B"),
        request=_request(),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=principal,
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert isinstance(response, VoteSubmitResponse)
    assert response.reveal is None
    assert response.winner == "B"
    vote_row = next(row for row in db.added if isinstance(row, Vote))
    assert vote_row.voter_user_id == uuid.UUID(principal.user_id)
    assert vote_row.voter_anon_id == "anon-1"


def test_submit_vote_upgrades_existing_anonymous_vote_when_user_logs_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, _, _ = _battle_and_runs()
    db = _VoteDB(battle=battle, runs=runs)
    principal = Principal(is_authenticated=True, user_id=str(uuid.uuid4()))
    existing_vote = Vote(
        battle_id=battle.id,
        winner="A",
        voter_user_id=None,
        voter_anon_id="anon-1",
        ip_hash="ip-hash",
        user_agent_hash="ua-hash",
    )
    existing_vote.id = uuid.uuid4()

    monkeypatch.setattr(votes, "get_or_set_anon_id", lambda **_kwargs: "anon-1")
    monkeypatch.setattr(
        votes,
        "find_existing_battle_vote",
        lambda *_args, **_kwargs: existing_vote,
    )
    monkeypatch.setattr(
        votes,
        "_enforce_anon_vote_rate_limit",
        lambda **_kwargs: pytest.fail("Rate-limit checks should be skipped"),
    )

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="A"),
        request=_request(),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=principal,
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response.vote_id == str(existing_vote.id)
    assert existing_vote.voter_user_id == uuid.UUID(principal.user_id)
    assert db.commit_calls == 1


def test_reveal_vote_upgrades_existing_anonymous_vote_when_user_logs_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, model_a_id, model_b_id = _battle_and_runs()
    db = _VoteDB(battle=battle, runs=runs)
    principal = Principal(is_authenticated=True, user_id=str(uuid.uuid4()))
    existing_vote = Vote(
        battle_id=battle.id,
        winner="B",
        voter_user_id=None,
        voter_anon_id="anon-1",
        ip_hash="ip-hash",
        user_agent_hash="ua-hash",
    )
    existing_vote.id = uuid.uuid4()

    monkeypatch.setattr(votes, "get_or_set_anon_id", lambda **_kwargs: "anon-1")
    monkeypatch.setattr(
        votes,
        "find_existing_battle_vote",
        lambda *_args, **_kwargs: existing_vote,
    )
    monkeypatch.setattr(
        votes,
        "_build_vote_submit_response",
        lambda *_args, **_kwargs: _vote_submit_response(
            vote_id=existing_vote.id,
            battle_id=battle.id,
            winner="B",
            model_a_id=model_a_id,
            model_b_id=model_b_id,
        ),
    )

    response = votes.reveal_vote(
        battle_id=str(battle.id),
        request=_request(),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=principal,
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response.vote_id == str(existing_vote.id)
    assert existing_vote.revealed is True
    assert existing_vote.voter_user_id == uuid.UUID(principal.user_id)
    assert db.commit_calls == 1


def test_submit_vote_resolves_duplicate_conflict_after_flush_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, model_a_id, model_b_id = _battle_and_runs()
    db = _VoteDB(
        battle=battle,
        runs=runs,
        flush_error=_integrity_error(
            'duplicate key value violates unique constraint "uq_votes_battle_voter_anon"'
        ),
    )

    monkeypatch.setattr(votes, "get_or_set_anon_id", lambda **_kwargs: "anon-1")
    monkeypatch.setattr(
        votes,
        "find_existing_battle_vote",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(votes, "_enforce_anon_vote_rate_limit", lambda **_kwargs: None)

    expected = _vote_submit_response(
        vote_id=uuid.uuid4(),
        battle_id=battle.id,
        winner="A",
        model_a_id=model_a_id,
        model_b_id=model_b_id,
    )
    monkeypatch.setattr(
        votes,
        "_resolve_duplicate_vote_conflict",
        lambda *_args, **_kwargs: expected,
    )

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="A"),
        request=_request(),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=Principal(is_authenticated=False),
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response is expected
    assert db.flush_calls == 1
    assert db.rollback_calls == 1
    assert db.commit_calls == 0


def test_submit_vote_resolves_duplicate_conflict_after_commit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, model_a_id, model_b_id = _battle_and_runs()
    db = _VoteDB(
        battle=battle,
        runs=runs,
        commit_error=_integrity_error(
            'duplicate key value violates unique constraint "uq_votes_battle_identity"'
        ),
    )

    monkeypatch.setattr(votes, "get_or_set_anon_id", lambda **_kwargs: "anon-1")
    monkeypatch.setattr(
        votes,
        "find_existing_battle_vote",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(votes, "_enforce_anon_vote_rate_limit", lambda **_kwargs: None)

    expected = _vote_submit_response(
        vote_id=uuid.uuid4(),
        battle_id=battle.id,
        winner="tie",
        model_a_id=model_a_id,
        model_b_id=model_b_id,
    )
    monkeypatch.setattr(
        votes,
        "_resolve_duplicate_vote_conflict",
        lambda *_args, **_kwargs: expected,
    )

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="tie"),
        request=_request(),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=Principal(is_authenticated=False),
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response is expected
    assert db.flush_calls == 1
    assert db.commit_calls == 1
    assert db.rollback_calls == 1


class TestUpgradeVoteIdentity:
    def test_promotes_anonymous_to_authenticated(self) -> None:
        user_id = uuid.uuid4()
        vote = Vote(
            battle_id=uuid.uuid4(),
            winner="A",
            voter_user_id=None,
            voter_anon_id="anon-1",
            ip_hash="ip-hash",
            user_agent_hash="ua-hash",
        )
        identity = RequesterIdentity(
            voter_user_id=user_id,
            voter_anon_id="anon-1",
            ip_hash="ip-hash",
            user_agent_hash="ua-hash",
        )
        changed = votes._upgrade_vote_identity(
            vote,
            voter_user_id=user_id,
            requester_identity=identity,
        )
        assert changed is True
        assert vote.voter_user_id == user_id

    def test_backfills_null_anon_id(self) -> None:
        vote = Vote(
            battle_id=uuid.uuid4(),
            winner="A",
            voter_user_id=None,
            voter_anon_id=None,
            ip_hash="ip-hash",
            user_agent_hash="ua-hash",
        )
        identity = RequesterIdentity(
            voter_user_id=None,
            voter_anon_id="anon-new",
            ip_hash="ip-hash",
            user_agent_hash="ua-hash",
        )
        changed = votes._upgrade_vote_identity(
            vote,
            voter_user_id=None,
            requester_identity=identity,
        )
        assert changed is True
        assert vote.voter_anon_id == "anon-new"

    def test_does_not_overwrite_existing_anon_id(self) -> None:
        vote = Vote(
            battle_id=uuid.uuid4(),
            winner="A",
            voter_user_id=None,
            voter_anon_id="original",
            ip_hash=None,
            user_agent_hash=None,
        )
        identity = RequesterIdentity(
            voter_user_id=None,
            voter_anon_id="different",
            ip_hash=None,
            user_agent_hash=None,
        )
        changed = votes._upgrade_vote_identity(
            vote,
            voter_user_id=None,
            requester_identity=identity,
        )
        assert changed is False
        assert vote.voter_anon_id == "original"

    def test_backfills_null_fingerprint_hashes(self) -> None:
        vote = Vote(
            battle_id=uuid.uuid4(),
            winner="A",
            voter_user_id=None,
            voter_anon_id="anon-1",
            ip_hash=None,
            user_agent_hash=None,
        )
        identity = RequesterIdentity(
            voter_user_id=None,
            voter_anon_id="anon-1",
            ip_hash="new-ip",
            user_agent_hash="new-ua",
        )
        changed = votes._upgrade_vote_identity(
            vote,
            voter_user_id=None,
            requester_identity=identity,
        )
        assert changed is True
        assert vote.ip_hash == "new-ip"
        assert vote.user_agent_hash == "new-ua"

    def test_does_not_overwrite_existing_fingerprint(self) -> None:
        vote = Vote(
            battle_id=uuid.uuid4(),
            winner="A",
            voter_user_id=None,
            voter_anon_id="anon-1",
            ip_hash="old-ip",
            user_agent_hash="old-ua",
        )
        identity = RequesterIdentity(
            voter_user_id=None,
            voter_anon_id="anon-1",
            ip_hash="new-ip",
            user_agent_hash="new-ua",
        )
        changed = votes._upgrade_vote_identity(
            vote,
            voter_user_id=None,
            requester_identity=identity,
        )
        assert changed is False
        assert vote.ip_hash == "old-ip"
        assert vote.user_agent_hash == "old-ua"

    def test_noop_when_nothing_to_upgrade(self) -> None:
        user_id = uuid.uuid4()
        vote = Vote(
            battle_id=uuid.uuid4(),
            winner="A",
            voter_user_id=user_id,
            voter_anon_id="anon-1",
            ip_hash="ip",
            user_agent_hash="ua",
        )
        identity = RequesterIdentity(
            voter_user_id=user_id,
            voter_anon_id="anon-1",
            ip_hash="ip",
            user_agent_hash="ua",
        )
        changed = votes._upgrade_vote_identity(
            vote,
            voter_user_id=user_id,
            requester_identity=identity,
        )
        assert changed is False


def test_submit_vote_anon_first_then_auth_backfills_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anonymous vote found via shared lookup; authenticated resubmit upgrades
    voter_user_id AND back-fills any missing identity fields."""
    battle, runs, _, _ = _battle_and_runs()
    db = _VoteDB(battle=battle, runs=runs)
    user_id = uuid.uuid4()
    principal = Principal(is_authenticated=True, user_id=str(user_id))

    existing_vote = Vote(
        battle_id=battle.id,
        winner="A",
        voter_user_id=None,
        voter_anon_id="anon-1",
        ip_hash=None,
        user_agent_hash=None,
    )
    existing_vote.id = uuid.uuid4()

    monkeypatch.setattr(votes, "get_or_set_anon_id", lambda **_kwargs: "anon-1")
    monkeypatch.setattr(
        votes,
        "find_existing_battle_vote",
        lambda *_a, **_kw: existing_vote,
    )

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="A"),
        request=_request(),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=principal,
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response.vote_id == str(existing_vote.id)
    assert existing_vote.voter_user_id == user_id
    assert existing_vote.ip_hash is not None
    assert existing_vote.user_agent_hash is not None
    assert db.commit_calls == 1


def test_submit_vote_auth_first_found_via_shared_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authenticated vote exists; anonymous resubmit from same fingerprint
    should find it via the shared precedence chain (fingerprint tier)."""
    battle, runs, _, _ = _battle_and_runs()
    db = _VoteDB(battle=battle, runs=runs)
    user_id = uuid.uuid4()

    existing_vote = Vote(
        battle_id=battle.id,
        winner="B",
        voter_user_id=user_id,
        voter_anon_id="anon-1",
        ip_hash="ip-hash",
        user_agent_hash="ua-hash",
    )
    existing_vote.id = uuid.uuid4()

    captured_identity: list[RequesterIdentity] = []

    def fake_find(
        _db: object, *, battle_id: uuid.UUID, requester_identity: RequesterIdentity
    ) -> Vote:
        captured_identity.append(requester_identity)
        return existing_vote

    monkeypatch.setattr(votes, "get_or_set_anon_id", lambda **_kwargs: "anon-1")
    monkeypatch.setattr(votes, "find_existing_battle_vote", fake_find)

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="B"),
        request=_request(),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=Principal(is_authenticated=False),
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response.vote_id == str(existing_vote.id)
    assert captured_identity[0].voter_user_id is None
    assert existing_vote.voter_user_id == user_id


def test_reveal_after_identity_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vote created anonymously, then upgraded to authenticated on resubmit,
    then revealed — should lock vote and reveal models."""
    battle, runs, model_a_id, model_b_id = _battle_and_runs()
    db = _VoteDB(battle=battle, runs=runs)
    user_id = uuid.uuid4()

    existing_vote = Vote(
        battle_id=battle.id,
        winner="A",
        voter_user_id=user_id,
        voter_anon_id="anon-1",
        ip_hash="ip-hash",
        user_agent_hash="ua-hash",
    )
    existing_vote.id = uuid.uuid4()

    monkeypatch.setattr(votes, "get_or_set_anon_id", lambda **_kwargs: "anon-1")
    monkeypatch.setattr(
        votes,
        "find_existing_battle_vote",
        lambda *_a, **_kw: existing_vote,
    )
    monkeypatch.setattr(
        votes,
        "_build_vote_submit_response",
        lambda *_a, **_kw: _vote_submit_response(
            vote_id=existing_vote.id,
            battle_id=battle.id,
            winner="A",
            model_a_id=model_a_id,
            model_b_id=model_b_id,
        ),
    )

    response = votes.reveal_vote(
        battle_id=str(battle.id),
        request=_request(),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=Principal(is_authenticated=True, user_id=str(user_id)),
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert existing_vote.revealed is True
    assert existing_vote.voter_user_id == user_id
    assert response.reveal is not None
    assert db.commit_calls == 1


def test_reveal_upgrades_anonymous_vote_identity_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anonymous vote with missing fingerprint fields gets them back-filled
    during reveal when the requester has them."""
    battle, runs, model_a_id, model_b_id = _battle_and_runs()
    db = _VoteDB(battle=battle, runs=runs)

    existing_vote = Vote(
        battle_id=battle.id,
        winner="B",
        voter_user_id=None,
        voter_anon_id="anon-1",
        ip_hash=None,
        user_agent_hash=None,
    )
    existing_vote.id = uuid.uuid4()

    monkeypatch.setattr(votes, "get_or_set_anon_id", lambda **_kwargs: "anon-1")
    monkeypatch.setattr(
        votes,
        "find_existing_battle_vote",
        lambda *_a, **_kw: existing_vote,
    )
    monkeypatch.setattr(
        votes,
        "_build_vote_submit_response",
        lambda *_a, **_kw: _vote_submit_response(
            vote_id=existing_vote.id,
            battle_id=battle.id,
            winner="B",
            model_a_id=model_a_id,
            model_b_id=model_b_id,
        ),
    )

    votes.reveal_vote(
        battle_id=str(battle.id),
        request=_request(),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=Principal(is_authenticated=False),
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert existing_vote.revealed is True
    assert existing_vote.ip_hash is not None
    assert existing_vote.user_agent_hash is not None
    assert db.commit_calls == 1


def test_duplicate_conflict_recovery_applies_identity_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IntegrityError during flush should recover via shared lookup AND apply
    identity upgrade (e.g. anonymous conflict found, authenticated user upgrading)."""
    battle, runs, model_a_id, model_b_id = _battle_and_runs()
    user_id = uuid.uuid4()
    db = _VoteDB(
        battle=battle,
        runs=runs,
        flush_error=_integrity_error("duplicate key"),
    )

    existing_vote = Vote(
        battle_id=battle.id,
        winner="A",
        voter_user_id=None,
        voter_anon_id="anon-1",
        ip_hash="ip-hash",
        user_agent_hash=None,
    )
    existing_vote.id = uuid.uuid4()

    find_call_count = [0]

    def fake_find(
        _db: object, *, battle_id: uuid.UUID, requester_identity: RequesterIdentity
    ) -> Vote | None:
        find_call_count[0] += 1
        if find_call_count[0] == 1:
            return None
        return existing_vote

    monkeypatch.setattr(votes, "get_or_set_anon_id", lambda **_kwargs: "anon-1")
    monkeypatch.setattr(votes, "find_existing_battle_vote", fake_find)
    monkeypatch.setattr(votes, "_enforce_anon_vote_rate_limit", lambda **_kwargs: None)

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="A"),
        request=_request(),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=Principal(is_authenticated=True, user_id=str(user_id)),
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response.vote_id == str(existing_vote.id)
    assert existing_vote.voter_user_id == user_id
    assert existing_vote.user_agent_hash is not None


def test_mid_flow_identity_switch_finds_existing_vote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same person votes anonymously then re-submits while authenticated.
    The shared identity lookup should find the anonymous vote via the
    fingerprint/anon-cookie fallback tiers, preventing a second row."""
    battle, runs, _, _ = _battle_and_runs()
    db = _VoteDB(battle=battle, runs=runs)
    user_id = uuid.uuid4()

    anon_vote = Vote(
        battle_id=battle.id,
        winner="A",
        voter_user_id=None,
        voter_anon_id="anon-1",
        ip_hash="ip-hash",
        user_agent_hash="ua-hash",
    )
    anon_vote.id = uuid.uuid4()

    monkeypatch.setattr(votes, "get_or_set_anon_id", lambda **_kwargs: "anon-1")
    monkeypatch.setattr(
        votes,
        "find_existing_battle_vote",
        lambda *_a, **_kw: anon_vote,
    )

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="B"),
        request=_request(),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=Principal(is_authenticated=True, user_id=str(user_id)),
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response.vote_id == str(anon_vote.id)
    assert anon_vote.voter_user_id == user_id
    assert anon_vote.winner == "B"
    assert db.added == []
    assert db.flush_calls == 0
    assert db.commit_calls == 1


# ── Cross-cutting regression: anonymous→authenticated vote upgrade (Task 2) ──


class TestAnonymousToAuthenticatedUpgradeRegression:
    """Regression tests protecting the repaired identity contract where an
    anonymous vote is discovered and upgraded when the same person re-submits
    or reveals while authenticated."""

    def test_submit_upgrade_then_reveal_full_lifecycle(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """End-to-end lifecycle: anonymous submit → auth resubmit (upgrades
        user_id + back-fills fingerprint) → reveal (locks vote and exposes
        models).  Proves all three surfaces share the same identity contract."""
        battle, runs, model_a_id, model_b_id = _battle_and_runs()
        db = _VoteDB(battle=battle, runs=runs)
        user_id = uuid.uuid4()

        # Vote was created anonymously with minimal identity
        existing_vote = Vote(
            battle_id=battle.id,
            winner="A",
            voter_user_id=None,
            voter_anon_id="anon-lifecycle",
            ip_hash=None,
            user_agent_hash=None,
        )
        existing_vote.id = uuid.uuid4()

        monkeypatch.setattr(
            votes, "get_or_set_anon_id", lambda **_kwargs: "anon-lifecycle"
        )
        monkeypatch.setattr(
            votes,
            "find_existing_battle_vote",
            lambda *_a, **_kw: existing_vote,
        )

        # Step 1: auth user resubmits → upgrades user_id AND back-fills hashes
        resp = votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A"),
            request=_request(ip="10.10.10.10", user_agent="lifecycle-agent"),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=Principal(is_authenticated=True, user_id=str(user_id)),
            settings=_settings(),  # type: ignore[arg-type]
        )

        assert resp.vote_id == str(existing_vote.id)
        assert existing_vote.voter_user_id == user_id
        assert existing_vote.ip_hash is not None
        assert existing_vote.user_agent_hash is not None
        ip_after_submit = existing_vote.ip_hash
        ua_after_submit = existing_vote.user_agent_hash

        # Step 2: reveal — should lock vote, not re-upgrade fingerprints
        monkeypatch.setattr(
            votes,
            "_build_vote_submit_response",
            lambda *_a, **_kw: _vote_submit_response(
                vote_id=existing_vote.id,
                battle_id=battle.id,
                winner="A",
                model_a_id=model_a_id,
                model_b_id=model_b_id,
            ),
        )

        reveal_resp = votes.reveal_vote(
            battle_id=str(battle.id),
            request=_request(ip="10.10.10.10", user_agent="lifecycle-agent"),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=Principal(is_authenticated=True, user_id=str(user_id)),
            settings=_settings(),  # type: ignore[arg-type]
        )

        assert existing_vote.revealed is True
        assert reveal_resp.reveal is not None
        # Fingerprints must not have been overwritten
        assert existing_vote.ip_hash == ip_after_submit
        assert existing_vote.user_agent_hash == ua_after_submit

    def test_conflict_recovery_upgrades_all_null_identity_fields(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """IntegrityError conflict path must upgrade ALL null identity fields,
        not just voter_user_id — protecting against partial-identity votes
        becoming undiscoverable by richer identity tiers later."""
        battle, runs, _, _ = _battle_and_runs()
        user_id = uuid.uuid4()

        existing_vote = Vote(
            battle_id=battle.id,
            winner="A",
            voter_user_id=None,
            voter_anon_id=None,
            ip_hash=None,
            user_agent_hash=None,
        )
        existing_vote.id = uuid.uuid4()

        monkeypatch.setattr(
            votes, "get_or_set_anon_id", lambda **_kwargs: "anon-conflict"
        )
        monkeypatch.setattr(
            votes,
            "find_existing_battle_vote",
            lambda *_a, **_kw: existing_vote,
        )

        # Call _resolve_duplicate_vote_conflict directly
        db = SimpleNamespace(commit=lambda: None)
        votes._resolve_duplicate_vote_conflict(
            db=db,  # type: ignore[arg-type]
            response=Response(),
            battle_id=battle.id,
            winner="A",
            voter_user_id=user_id,
            requester_identity=RequesterIdentity(
                voter_user_id=user_id,
                voter_anon_id="anon-conflict",
                ip_hash="new-ip",
                user_agent_hash="new-ua",
            ),
            model_a_id=uuid.uuid4(),
            model_b_id=uuid.uuid4(),
        )

        assert existing_vote.voter_user_id == user_id
        assert existing_vote.voter_anon_id == "anon-conflict"
        assert existing_vote.ip_hash == "new-ip"
        assert existing_vote.user_agent_hash == "new-ua"


# ── Task 2 regressions: duplicate conflict payload + missing-side validation ──


def test_duplicate_conflict_updates_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unrevealed duplicate-conflict recovery must persist the latest winner,
    rubric, and comment — not the stale values from the first insert attempt."""
    battle, runs, model_a_id, model_b_id = _battle_and_runs()

    existing_vote = Vote(
        battle_id=battle.id,
        winner="A",
        voter_user_id=None,
        voter_anon_id="anon-dup",
        ip_hash="ip",
        user_agent_hash="ua",
    )
    existing_vote.id = uuid.uuid4()
    existing_vote.rubric = {"tags": ["accuracy"]}
    existing_vote.comment = "first comment"

    monkeypatch.setattr(
        votes,
        "find_existing_battle_vote",
        lambda *_a, **_kw: existing_vote,
    )

    db = SimpleNamespace(commit=lambda: None)
    resp = votes._resolve_duplicate_vote_conflict(
        db=db,  # type: ignore[arg-type]
        response=Response(),
        battle_id=battle.id,
        winner="B",
        rubric={"tags": ["fluency", "style"]},
        comment="updated comment",
        voter_user_id=None,
        requester_identity=RequesterIdentity(
            voter_user_id=None,
            voter_anon_id="anon-dup",
            ip_hash="ip",
            user_agent_hash="ua",
        ),
        model_a_id=model_a_id,
        model_b_id=model_b_id,
    )

    assert resp.winner == "B"
    assert existing_vote.winner == "B"
    assert existing_vote.rubric == {"tags": ["fluency", "style"]}
    assert existing_vote.comment == "updated comment"


def test_rejects_vote_for_missing_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Voting for a side whose run has no rendered output must return 422."""
    battle_id = uuid.uuid4()
    model_a_id = uuid.uuid4()
    model_b_id = uuid.uuid4()
    battle = SimpleNamespace(id=battle_id, status="completed")
    runs = [
        SimpleNamespace(side="A", model_id=model_a_id, output_text="Translation A"),
        SimpleNamespace(side="B", model_id=model_b_id, output_text=None),
    ]
    db = _VoteDB(battle=battle, runs=runs)

    monkeypatch.setattr(votes, "get_or_set_anon_id", lambda **_kwargs: "anon-1")
    monkeypatch.setattr(
        votes,
        "find_existing_battle_vote",
        lambda *_a, **_kw: None,
    )

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle_id),
            payload=VoteCreate(winner="B"),
            request=_request(),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=Principal(is_authenticated=False),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code in (409, 422)
    assert (
        "Side B" in exc_info.value.detail
        or "translation runs failed" in exc_info.value.detail
    )


def test_submit_vote_ip_fallback_does_not_overmatch_fingerprinted_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same-IP vote with a non-null user_agent_hash must NOT be found by the
    IP-only fallback tier — the narrowed filter requires user_agent_hash IS NULL."""
    battle, runs, model_a_id, model_b_id = _battle_and_runs()
    db = _VoteDB(battle=battle, runs=runs)

    captured_identity: list[RequesterIdentity] = []

    def fake_find(
        _db: object, *, battle_id: uuid.UUID, requester_identity: RequesterIdentity
    ) -> None:
        captured_identity.append(requester_identity)
        return None

    monkeypatch.setattr(votes, "get_or_set_anon_id", lambda **_kwargs: "anon-1")
    monkeypatch.setattr(votes, "find_existing_battle_vote", fake_find)
    monkeypatch.setattr(votes, "_enforce_anon_vote_rate_limit", lambda **_kwargs: None)

    votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="A"),
        request=_request(ip="10.0.0.1", user_agent="browser-X"),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=Principal(is_authenticated=False),
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert len(captured_identity) == 1
    identity = captured_identity[0]

    ip_filters = [
        (kind, clause)
        for kind, clause in identity.battle_lookup_filters()
        if kind == "ip"
    ]
    assert len(ip_filters) == 1
    ip_sql = str(ip_filters[0][1])
    assert "user_agent_hash IS NULL" in ip_sql
