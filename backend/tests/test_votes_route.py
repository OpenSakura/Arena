from __future__ import annotations

import hashlib
from types import SimpleNamespace
import uuid

from fastapi import HTTPException, Request, Response
import httpx
import pytest
from sqlalchemy.exc import IntegrityError

from app.api.routes import votes
from app.core.security import Principal
from app.models.rating import ModelRating, RatingEvent
from app.models.vote import Vote
from app.schemas.votes import VoteCreate, VoteSubmitResponse


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


class _DummyNestedTx:
    def __enter__(self) -> "_DummyNestedTx":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        _ = (exc_type, exc, tb)
        return False


class _RatingForUpdateSession:
    def __init__(
        self,
        *,
        first_lookup: ModelRating | None,
        second_lookup: ModelRating | None,
        flush_error: Exception | None = None,
        fallback_to_added: bool = True,
    ) -> None:
        self._lookups = [first_lookup, second_lookup]
        self._flush_error = flush_error
        self._fallback_to_added = fallback_to_added
        self.added: ModelRating | None = None
        self.execute_calls = 0
        self.flush_calls = 0

    def execute(self, _stmt: object) -> _SingleResult:
        idx = min(self.execute_calls, len(self._lookups) - 1)
        self.execute_calls += 1
        value = self._lookups[idx]
        if value is None and idx == 1 and self._fallback_to_added:
            value = self.added
        return _SingleResult(value)

    def begin_nested(self) -> _DummyNestedTx:
        return _DummyNestedTx()

    def add(self, rating: ModelRating) -> None:
        self.added = rating

    def flush(self) -> None:
        self.flush_calls += 1
        if self._flush_error is not None:
            raise self._flush_error


class _VoteDB:
    def __init__(
        self,
        *,
        battle: object | None,
        runs: list[object],
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
        SimpleNamespace(side="A", model_id=model_a_id),
        SimpleNamespace(side="B", model_id=model_b_id),
    ]
    return battle, runs, model_a_id, model_b_id


def _vote_submit_response(
    *,
    vote_id: uuid.UUID,
    battle_id: uuid.UUID,
    winner: str,
    model_a_id: uuid.UUID,
    model_b_id: uuid.UUID,
) -> VoteSubmitResponse:
    return VoteSubmitResponse(
        vote_id=str(vote_id),
        battle_id=str(battle_id),
        winner=winner,
        reveal={
            "A": {"model_id": str(model_a_id), "display_name": "Model A"},
            "B": {"model_id": str(model_b_id), "display_name": "Model B"},
        },
    )


def test_parse_uuid_rejects_invalid_values() -> None:
    with pytest.raises(HTTPException) as exc_info:
        votes.parse_uuid("bad-uuid", "battle_id")

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Invalid battle_id"


def test_hash_helpers_use_salted_sha256() -> None:
    request = _request(ip="203.0.113.7", user_agent="arena-agent")

    assert (
        votes._hash_ip(request, "ip-salt")
        == hashlib.sha256(b"ip-salt|203.0.113.7").hexdigest()
    )
    assert (
        votes._hash_user_agent(request, "ua-salt")
        == hashlib.sha256(b"ua-salt|arena-agent").hexdigest()
    )


def test_hash_helpers_return_none_when_source_data_missing() -> None:
    request_without_ua = _request(user_agent="")
    assert votes._hash_user_agent(request_without_ua, "ua-salt") is None

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
    assert votes._hash_ip(request_without_client, "ip-salt") is None


def test_verify_turnstile_returns_early_when_secret_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        votes.httpx,
        "post",
        lambda *_args, **_kwargs: pytest.fail("Turnstile should not be called"),
    )

    votes._verify_turnstile_or_raise(
        payload=VoteCreate(winner="A"),
        request=_request(),
        settings=_settings(turnstile_secret_key=None),  # type: ignore[arg-type]
    )


def test_verify_turnstile_requires_token_when_enabled() -> None:
    with pytest.raises(HTTPException) as exc_info:
        votes._verify_turnstile_or_raise(
            payload=VoteCreate(winner="A"),
            request=_request(),
            settings=_settings(turnstile_secret_key="secret"),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Missing Turnstile token"


def test_verify_turnstile_wraps_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        votes.httpx,
        "post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            httpx.HTTPError("network down")
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes._verify_turnstile_or_raise(
            payload=VoteCreate(winner="A", turnstile_token="token"),
            request=_request(),
            settings=_settings(turnstile_secret_key="secret"),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "Turnstile verification failed"


def test_verify_turnstile_wraps_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BadJsonResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            raise ValueError("bad json")

    monkeypatch.setattr(
        votes.httpx,
        "post",
        lambda *_args, **_kwargs: _BadJsonResponse(),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes._verify_turnstile_or_raise(
            payload=VoteCreate(winner="A", turnstile_token="token"),
            request=_request(),
            settings=_settings(turnstile_secret_key="secret"),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "Turnstile verification response was invalid"


def test_verify_turnstile_rejects_unsuccessful_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailureResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"success": False}

    monkeypatch.setattr(
        votes.httpx,
        "post",
        lambda *_args, **_kwargs: _FailureResponse(),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes._verify_turnstile_or_raise(
            payload=VoteCreate(winner="A", turnstile_token="token"),
            request=_request(),
            settings=_settings(turnstile_secret_key="secret"),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid Turnstile token"


def test_verify_turnstile_posts_expected_form_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _SuccessResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"success": True}

    def fake_post(
        url: str, *, data: dict[str, str], timeout: float
    ) -> _SuccessResponse:
        captured["url"] = url
        captured["data"] = data
        captured["timeout"] = timeout
        return _SuccessResponse()

    monkeypatch.setattr(votes.httpx, "post", fake_post)

    request = _request(ip="10.1.2.3")
    settings = _settings(
        turnstile_secret_key="secret",
        turnstile_verify_url="https://verify.example/turnstile",
    )
    votes._verify_turnstile_or_raise(
        payload=VoteCreate(winner="A", turnstile_token="tok-123"),
        request=request,
        settings=settings,  # type: ignore[arg-type]
    )

    assert captured["url"] == "https://verify.example/turnstile"
    assert captured["timeout"] == 5.0
    assert captured["data"] == {
        "secret": "secret",
        "response": "tok-123",
        "remoteip": "10.1.2.3",
    }


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


def test_get_or_create_rating_for_update_returns_existing_rating() -> None:
    model_id = uuid.uuid4()
    existing = ModelRating(model_id=model_id, rating=1101.0, games_played=9)
    session = _RatingForUpdateSession(
        first_lookup=existing,
        second_lookup=None,
    )

    rating = votes._get_or_create_rating_for_update(session, model_id)  # type: ignore[arg-type]

    assert rating is existing
    assert session.flush_calls == 0


def test_get_or_create_rating_for_update_creates_row_when_missing() -> None:
    model_id = uuid.uuid4()
    session = _RatingForUpdateSession(
        first_lookup=None,
        second_lookup=None,
    )

    rating = votes._get_or_create_rating_for_update(session, model_id)  # type: ignore[arg-type]

    assert rating.model_id == model_id
    assert session.added is rating
    assert session.flush_calls == 1


def test_get_or_create_rating_for_update_recovers_from_insert_race() -> None:
    model_id = uuid.uuid4()
    existing = ModelRating(model_id=model_id, rating=1040.0, games_played=3)
    session = _RatingForUpdateSession(
        first_lookup=None,
        second_lookup=existing,
        flush_error=_integrity_error(
            'duplicate key value violates unique constraint "model_ratings_pkey"'
        ),
    )

    rating = votes._get_or_create_rating_for_update(session, model_id)  # type: ignore[arg-type]

    assert rating is existing
    assert session.flush_calls == 1


def test_get_or_create_rating_for_update_raises_when_row_is_still_missing() -> None:
    model_id = uuid.uuid4()
    session = _RatingForUpdateSession(
        first_lookup=None,
        second_lookup=None,
        flush_error=_integrity_error("insert raced"),
        fallback_to_added=False,
    )

    with pytest.raises(RuntimeError) as exc_info:
        votes._get_or_create_rating_for_update(session, model_id)  # type: ignore[arg-type]

    assert str(model_id) in str(exc_info.value)


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
        "_find_existing_vote_for_identity",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(HTTPException) as exc_info:
        votes._resolve_duplicate_vote_conflict(
            db=object(),  # type: ignore[arg-type]
            battle_id=uuid.uuid4(),
            winner="A",
            voter_user_id=None,
            voter_anon_id="anon-1",
            ip_hash=None,
            user_agent_hash=None,
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
        "_find_existing_vote_for_identity",
        lambda *_args, **_kwargs: SimpleNamespace(id=uuid.uuid4(), winner="B"),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes._resolve_duplicate_vote_conflict(
            db=object(),  # type: ignore[arg-type]
            battle_id=uuid.uuid4(),
            winner="A",
            voter_user_id=None,
            voter_anon_id="anon-1",
            ip_hash=None,
            user_agent_hash=None,
            model_a_id=uuid.uuid4(),
            model_b_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Vote already submitted for this battle"


def test_resolve_duplicate_vote_conflict_returns_existing_vote_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle_id = uuid.uuid4()
    model_a_id = uuid.uuid4()
    model_b_id = uuid.uuid4()
    existing_vote = SimpleNamespace(id=uuid.uuid4(), winner="A")
    captured: dict[str, object] = {}
    sentinel = object()

    monkeypatch.setattr(
        votes,
        "_find_existing_vote_for_identity",
        lambda *_args, **_kwargs: existing_vote,
    )

    def fake_build(*args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        _ = args
        return sentinel

    monkeypatch.setattr(votes, "_build_vote_submit_response", fake_build)

    response = votes._resolve_duplicate_vote_conflict(
        db=object(),  # type: ignore[arg-type]
        battle_id=battle_id,
        winner="A",
        voter_user_id=None,
        voter_anon_id="anon-1",
        ip_hash="ip-hash",
        user_agent_hash="ua-hash",
        model_a_id=model_a_id,
        model_b_id=model_b_id,
    )

    assert response is sentinel
    assert captured["vote_id"] == existing_vote.id
    assert captured["battle_id"] == battle_id
    assert captured["winner"] == "A"
    assert captured["model_a_id"] == model_a_id
    assert captured["model_b_id"] == model_b_id


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
    existing_vote = SimpleNamespace(id=uuid.uuid4(), winner="A")
    captured: dict[str, object] = {}
    expected_response = _vote_submit_response(
        vote_id=existing_vote.id,
        battle_id=battle.id,
        winner="A",
        model_a_id=model_a_id,
        model_b_id=model_b_id,
    )

    monkeypatch.setattr(votes, "get_or_set_anon_id", lambda **_kwargs: "anon-1")

    def fake_find(
        _db: object,
        *,
        battle_id: uuid.UUID,
        voter_user_id: uuid.UUID | None,
        voter_anon_id: str | None,
        ip_hash: str | None,
        user_agent_hash: str | None,
    ) -> object:
        captured["battle_id"] = battle_id
        captured["voter_user_id"] = voter_user_id
        captured["voter_anon_id"] = voter_anon_id
        captured["ip_hash"] = ip_hash
        captured["user_agent_hash"] = user_agent_hash
        return existing_vote

    monkeypatch.setattr(votes, "_find_existing_vote_for_identity", fake_find)
    monkeypatch.setattr(
        votes,
        "_build_vote_submit_response",
        lambda *_args, **_kwargs: expected_response,
    )
    monkeypatch.setattr(
        votes,
        "_enforce_anon_vote_rate_limit",
        lambda **_kwargs: pytest.fail("Rate-limit checks should not run"),
    )
    monkeypatch.setattr(
        votes,
        "_verify_turnstile_or_raise",
        lambda **_kwargs: pytest.fail("Turnstile checks should not run"),
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

    assert response is expected_response
    assert captured == {
        "battle_id": battle.id,
        "voter_user_id": None,
        "voter_anon_id": "anon-1",
        "ip_hash": hashlib.sha256(b"ip-salt|127.0.0.1").hexdigest(),
        "user_agent_hash": hashlib.sha256(b"ua-salt|arena-test-agent").hexdigest(),
    }
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
        "_find_existing_vote_for_identity",
        lambda *_args, **_kwargs: SimpleNamespace(id=uuid.uuid4(), winner="B"),
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
    assert exc_info.value.detail == "Vote already submitted for this battle"


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
        "_find_existing_vote_for_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        votes,
        "_enforce_anon_vote_rate_limit",
        lambda **_kwargs: calls.__setitem__("rate_limit", True),
    )
    monkeypatch.setattr(
        votes,
        "_verify_turnstile_or_raise",
        lambda **_kwargs: calls.__setitem__("turnstile", True),
    )

    rating_a = ModelRating(model_id=model_a_id, rating=1000.0, games_played=2)
    rating_b = ModelRating(model_id=model_b_id, rating=1000.0, games_played=5)
    monkeypatch.setattr(
        votes,
        "_lock_ratings_for_vote",
        lambda *_args, **_kwargs: (rating_a, rating_b),
    )

    def fake_elo_update(
        *, rating_a: float, rating_b: float, outcome: str, k: float
    ) -> tuple[float, float]:
        calls["elo_args"] = {
            "rating_a": rating_a,
            "rating_b": rating_b,
            "outcome": outcome,
            "k": k,
        }
        return (12.5, -12.5)

    monkeypatch.setattr(votes, "elo_update", fake_elo_update)

    expected = _vote_submit_response(
        vote_id=uuid.uuid4(),
        battle_id=battle.id,
        winner="A",
        model_a_id=model_a_id,
        model_b_id=model_b_id,
    )
    monkeypatch.setattr(
        votes,
        "_build_vote_submit_response",
        lambda *_args, **_kwargs: expected,
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

    assert response is expected
    assert calls["rate_limit"] is True
    assert calls["turnstile"] is True
    assert calls["elo_args"] == {
        "rating_a": 1000.0,
        "rating_b": 1000.0,
        "outcome": "A",
        "k": 32.0,
    }

    assert rating_a.rating == 1012.5
    assert rating_a.games_played == 3
    assert rating_b.rating == 987.5
    assert rating_b.games_played == 6

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
    assert any(isinstance(row, RatingEvent) for row in db.added)


def test_submit_vote_authenticated_path_skips_anonymous_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, model_a_id, model_b_id = _battle_and_runs()
    db = _VoteDB(battle=battle, runs=runs)
    principal = Principal(is_authenticated=True, user_id=str(uuid.uuid4()))

    monkeypatch.setattr(votes, "get_or_set_anon_id", lambda **_kwargs: "anon-1")
    monkeypatch.setattr(
        votes,
        "_find_existing_vote_for_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        votes,
        "_enforce_anon_vote_rate_limit",
        lambda **_kwargs: pytest.fail("Rate-limit checks should be skipped"),
    )
    monkeypatch.setattr(
        votes,
        "_verify_turnstile_or_raise",
        lambda **_kwargs: pytest.fail("Turnstile checks should be skipped"),
    )

    rating_a = ModelRating(model_id=model_a_id, rating=1000.0, games_played=0)
    rating_b = ModelRating(model_id=model_b_id, rating=1000.0, games_played=0)
    monkeypatch.setattr(
        votes,
        "_lock_ratings_for_vote",
        lambda *_args, **_kwargs: (rating_a, rating_b),
    )
    monkeypatch.setattr(votes, "elo_update", lambda **_kwargs: (1.0, -1.0))
    expected = _vote_submit_response(
        vote_id=uuid.uuid4(),
        battle_id=battle.id,
        winner="B",
        model_a_id=model_a_id,
        model_b_id=model_b_id,
    )
    monkeypatch.setattr(
        votes,
        "_build_vote_submit_response",
        lambda *_args, **_kwargs: expected,
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

    assert response is expected
    vote_row = next(row for row in db.added if isinstance(row, Vote))
    assert vote_row.voter_user_id == uuid.UUID(principal.user_id)
    assert vote_row.voter_anon_id == "anon-1"


def test_submit_vote_translates_rating_lock_failures_to_http_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, _, _ = _battle_and_runs()
    db = _VoteDB(battle=battle, runs=runs)

    monkeypatch.setattr(votes, "get_or_set_anon_id", lambda **_kwargs: "anon-1")
    monkeypatch.setattr(
        votes,
        "_find_existing_vote_for_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(votes, "_enforce_anon_vote_rate_limit", lambda **_kwargs: None)
    monkeypatch.setattr(votes, "_verify_turnstile_or_raise", lambda **_kwargs: None)
    monkeypatch.setattr(
        votes,
        "_lock_ratings_for_vote",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("lock failed")),
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

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to load model ratings"
    assert db.rollback_calls == 1
    assert db.commit_calls == 0


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
        "_find_existing_vote_for_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(votes, "_enforce_anon_vote_rate_limit", lambda **_kwargs: None)
    monkeypatch.setattr(votes, "_verify_turnstile_or_raise", lambda **_kwargs: None)
    monkeypatch.setattr(
        votes,
        "_lock_ratings_for_vote",
        lambda *_args, **_kwargs: pytest.fail(
            "Ratings should not be locked after flush conflict"
        ),
    )

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
            'duplicate key value violates unique constraint "uq_rating_events_vote_id"'
        ),
    )

    monkeypatch.setattr(votes, "get_or_set_anon_id", lambda **_kwargs: "anon-1")
    monkeypatch.setattr(
        votes,
        "_find_existing_vote_for_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(votes, "_enforce_anon_vote_rate_limit", lambda **_kwargs: None)
    monkeypatch.setattr(votes, "_verify_turnstile_or_raise", lambda **_kwargs: None)

    rating_a = ModelRating(model_id=model_a_id, rating=1000.0, games_played=0)
    rating_b = ModelRating(model_id=model_b_id, rating=1000.0, games_played=0)
    monkeypatch.setattr(
        votes,
        "_lock_ratings_for_vote",
        lambda *_args, **_kwargs: (rating_a, rating_b),
    )
    monkeypatch.setattr(votes, "elo_update", lambda **_kwargs: (0.0, 0.0))

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
