from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from typing import Literal, cast
import uuid

from fastapi import HTTPException, Response
import pytest
from sqlalchemy.exc import IntegrityError

from app.api.routes import votes
from app.core.security import Principal
from app.models.vote import Vote
from app.schemas.votes import VoteCreate, VoteSubmitResponse
from app.utils.requester_identity import RequesterIdentity


def _authenticated_principal() -> Principal:
    return Principal(is_authenticated=True, user_id=str(uuid.uuid4()))


def _settings(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "auth_vote_submit_rate_limit_window_seconds": 60,
        "anon_rate_limit_bucket_seconds": 10,
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


class _VoteDB:
    def __init__(
        self,
        *,
        battle: object | None,
        runs: Sequence[object],
        flush_error: Exception | None = None,
        commit_error: Exception | None = None,
        model_lookup: dict[uuid.UUID, object] | None = None,
    ) -> None:
        self._battle = battle
        self._runs = list(runs)
        self._flush_error = flush_error
        self._commit_error = commit_error
        self._model_lookup = model_lookup or {}
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
        if model_type is votes.Model:
            return self._model_lookup.get(key)
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
    *, status: str = "completed", side_b_output: str | None = "Translation B"
) -> tuple[SimpleNamespace, list[SimpleNamespace], uuid.UUID, uuid.UUID]:
    battle_id = uuid.uuid4()
    model_a_id = uuid.uuid4()
    model_b_id = uuid.uuid4()
    battle = SimpleNamespace(id=battle_id, status=status)
    runs = [
        SimpleNamespace(side="A", model_id=model_a_id, output_text="Translation A"),
        SimpleNamespace(side="B", model_id=model_b_id, output_text=side_b_output),
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


def test_build_vote_submit_response_includes_reveal_metadata() -> None:
    vote_id = uuid.uuid4()
    battle_id = uuid.uuid4()
    model_a_id = uuid.uuid4()
    model_b_id = uuid.uuid4()
    db = _VoteDB(
        battle=None,
        runs=[],
        model_lookup={
            model_a_id: SimpleNamespace(id=model_a_id, display_name="Alpha"),
            model_b_id: SimpleNamespace(id=model_b_id, display_name="Beta"),
        },
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
    assert response.reveal == {
        "A": {"model_id": str(model_a_id), "display_name": "Alpha"},
        "B": {"model_id": str(model_b_id), "display_name": "Beta"},
    }


def test_build_vote_submit_response_raises_when_model_lookup_fails() -> None:
    with pytest.raises(HTTPException) as exc_info:
        votes._build_vote_submit_response(
            db=_VoteDB(battle=None, runs=[]),  # type: ignore[arg-type]
            vote_id=uuid.uuid4(),
            battle_id=uuid.uuid4(),
            winner="A",
            model_a_id=uuid.uuid4(),
            model_b_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Model not found"


def test_resolve_duplicate_vote_conflict_raises_500_when_vote_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(votes, "find_existing_battle_vote", lambda *_a, **_kw: None)

    with pytest.raises(HTTPException) as exc_info:
        votes._resolve_duplicate_vote_conflict(
            db=object(),  # type: ignore[arg-type]
            response=Response(),
            battle_id=uuid.uuid4(),
            winner="A",
            requester_identity=RequesterIdentity(voter_user_id=uuid.uuid4()),
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
        lambda *_a, **_kw: SimpleNamespace(id=uuid.uuid4(), winner="B", revealed=True),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes._resolve_duplicate_vote_conflict(
            db=object(),  # type: ignore[arg-type]
            response=Response(),
            battle_id=uuid.uuid4(),
            winner="A",
            requester_identity=RequesterIdentity(voter_user_id=uuid.uuid4()),
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
    )
    monkeypatch.setattr(
        votes,
        "find_existing_battle_vote",
        lambda *_a, **_kw: existing_vote,
    )

    commit_calls: list[None] = []
    db = SimpleNamespace(commit=lambda: commit_calls.append(None))
    expected_reveal = {
        "A": {"model_id": str(model_a_id), "display_name": "Model A"},
        "B": {"model_id": str(model_b_id), "display_name": "Model B"},
    }
    monkeypatch.setattr(
        votes,
        "_build_vote_submit_response",
        lambda *_a, **_kw: VoteSubmitResponse(
            vote_id=str(existing_vote.id),
            battle_id=str(battle_id),
            winner="A",
            reveal=expected_reveal,
        ),
    )

    response = votes._resolve_duplicate_vote_conflict(
        db=db,  # type: ignore[arg-type]
        response=Response(),
        battle_id=battle_id,
        winner="A",
        requester_identity=RequesterIdentity(voter_user_id=uuid.uuid4()),
        model_a_id=model_a_id,
        model_b_id=model_b_id,
    )

    assert response.vote_id == str(existing_vote.id)
    assert response.battle_id == str(battle_id)
    assert response.winner == "A"
    assert response.reveal == expected_reveal
    assert existing_vote.revealed is True
    assert len(commit_calls) == 1


def test_resolve_duplicate_vote_conflict_updates_payload_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle_id = uuid.uuid4()
    model_a_id = uuid.uuid4()
    model_b_id = uuid.uuid4()
    existing_vote = SimpleNamespace(
        id=uuid.uuid4(),
        winner="A",
        revealed=False,
        rubric={"tags": ["accuracy"]},
        comment="old",
    )
    monkeypatch.setattr(
        votes,
        "find_existing_battle_vote",
        lambda *_a, **_kw: existing_vote,
    )

    commit_calls: list[None] = []
    db = SimpleNamespace(commit=lambda: commit_calls.append(None))
    expected_reveal = {
        "A": {"model_id": str(model_a_id), "display_name": "Model A"},
        "B": {"model_id": str(model_b_id), "display_name": "Model B"},
    }
    monkeypatch.setattr(
        votes,
        "_build_vote_submit_response",
        lambda *_a, **_kw: VoteSubmitResponse(
            vote_id=str(existing_vote.id),
            battle_id=str(battle_id),
            winner="B",
            reveal=expected_reveal,
        ),
    )

    response = votes._resolve_duplicate_vote_conflict(
        db=db,  # type: ignore[arg-type]
        response=Response(),
        battle_id=battle_id,
        winner="B",
        rubric={"tags": ["fluency"]},
        comment="new",
        requester_identity=RequesterIdentity(voter_user_id=uuid.uuid4()),
        model_a_id=model_a_id,
        model_b_id=model_b_id,
    )

    assert response.winner == "B"
    assert response.reveal == expected_reveal
    assert existing_vote.winner == "B"
    assert existing_vote.rubric == {"tags": ["fluency"]}
    assert existing_vote.comment == "new"
    assert existing_vote.revealed is True
    assert len(commit_calls) == 1


def test_submit_vote_rejects_invalid_battle_id() -> None:
    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id="bad-id",
            payload=VoteCreate(winner="A"),
            response=Response(),
            db=_VoteDB(battle=None, runs=[]),  # type: ignore[arg-type]
            principal=_authenticated_principal(),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Invalid battle_id"


def test_submit_vote_returns_404_when_battle_is_missing() -> None:
    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(uuid.uuid4()),
            payload=VoteCreate(winner="A"),
            response=Response(),
            db=_VoteDB(battle=None, runs=[]),  # type: ignore[arg-type]
            principal=_authenticated_principal(),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Battle not found"


def test_submit_vote_rejects_battle_not_ready_for_voting() -> None:
    battle, runs, _, _ = _battle_and_runs(status="pending")

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A"),
            response=Response(),
            db=_VoteDB(battle=battle, runs=runs),  # type: ignore[arg-type]
            principal=_authenticated_principal(),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Battle is not ready for voting"


def test_submit_vote_rejects_battle_without_both_runs() -> None:
    battle, runs, _, _ = _battle_and_runs()

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A"),
            response=Response(),
            db=_VoteDB(battle=battle, runs=runs[:1]),  # type: ignore[arg-type]
            principal=_authenticated_principal(),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Battle runs not ready"


def test_submit_vote_rejects_vote_when_any_run_failed() -> None:
    battle, runs, _, _ = _battle_and_runs(side_b_output=None)

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A"),
            response=Response(),
            db=_VoteDB(battle=battle, runs=runs),  # type: ignore[arg-type]
            principal=_authenticated_principal(),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409
    assert "failed" in exc_info.value.detail


def test_submit_vote_rejects_vote_when_selected_side_has_no_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle_id = uuid.uuid4()
    model_a_id = uuid.uuid4()
    model_b_id = uuid.uuid4()
    db = _VoteDB(
        battle=SimpleNamespace(id=battle_id, status="completed"),
        runs=[
            SimpleNamespace(side="A", model_id=model_a_id, output_text="A"),
            SimpleNamespace(side="B", model_id=model_b_id, output_text=None),
        ],
    )
    monkeypatch.setattr(votes, "find_existing_battle_vote", lambda *_a, **_kw: None)

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle_id),
            payload=VoteCreate(winner="B"),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=_authenticated_principal(),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code in (409, 422)


def test_submit_vote_returns_existing_vote_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, model_a_id, model_b_id = _battle_and_runs()
    principal = _authenticated_principal()
    existing_vote = SimpleNamespace(
        id=uuid.uuid4(),
        winner="A",
        revealed=False,
        rubric=None,
        comment=None,
    )
    captured: dict[str, object] = {}

    def fake_find(
        _db: object, *, battle_id: uuid.UUID, requester_identity: RequesterIdentity
    ):
        captured["battle_id"] = battle_id
        captured["requester_identity"] = requester_identity
        return existing_vote

    monkeypatch.setattr(votes, "find_existing_battle_vote", fake_find)
    monkeypatch.setattr(
        votes,
        "_enforce_auth_vote_rate_limit",
        lambda **_kw: pytest.fail("Rate-limit checks should not run"),
    )

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="A"),
        response=Response(),
        db=_VoteDB(
            battle=battle,
            runs=runs,
            model_lookup={
                model_a_id: SimpleNamespace(id=model_a_id, display_name="Model A"),
                model_b_id: SimpleNamespace(id=model_b_id, display_name="Model B"),
            },
        ),  # type: ignore[arg-type]
        principal=principal,
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response.reveal is not None
    assert response.winner == "A"
    assert captured["battle_id"] == battle.id
    requester_identity = captured["requester_identity"]
    assert isinstance(requester_identity, RequesterIdentity)
    assert requester_identity.voter_user_id == uuid.UUID(principal.user_id)


def test_submit_vote_rejects_conflicting_existing_revealed_vote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, _, _ = _battle_and_runs()
    monkeypatch.setattr(
        votes,
        "find_existing_battle_vote",
        lambda *_a, **_kw: SimpleNamespace(id=uuid.uuid4(), winner="B", revealed=True),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A"),
            response=Response(),
            db=_VoteDB(battle=battle, runs=runs),  # type: ignore[arg-type]
            principal=_authenticated_principal(),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Vote already revealed and cannot be changed"


def test_submit_vote_records_vote_and_uses_auth_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, model_a_id, model_b_id = _battle_and_runs()
    db = _VoteDB(
        battle=battle,
        runs=runs,
        model_lookup={
            model_a_id: SimpleNamespace(id=model_a_id, display_name="Model A"),
            model_b_id: SimpleNamespace(id=model_b_id, display_name="Model B"),
        },
    )
    principal = _authenticated_principal()
    calls: dict[str, object] = {}

    monkeypatch.setattr(votes, "find_existing_battle_vote", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        votes,
        "_enforce_auth_vote_rate_limit",
        lambda **_kw: calls.__setitem__("rate_limit", True),
    )

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="A", comment="great"),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=principal,
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response.reveal == {
        "A": {"model_id": str(model_a_id), "display_name": "Model A"},
        "B": {"model_id": str(model_b_id), "display_name": "Model B"},
    }
    assert response.winner == "A"
    assert calls["rate_limit"] is True
    assert db.flush_calls == 1
    assert db.commit_calls == 1
    vote_row = next(row for row in db.added if isinstance(row, Vote))
    assert vote_row.voter_user_id == uuid.UUID(principal.user_id)
    assert vote_row.revealed is True


def test_submit_vote_resolves_duplicate_conflict_after_flush_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, model_a_id, model_b_id = _battle_and_runs()
    db = _VoteDB(
        battle=battle,
        runs=runs,
        flush_error=_integrity_error("duplicate key"),
    )
    expected = _vote_submit_response(
        vote_id=uuid.uuid4(),
        battle_id=battle.id,
        winner="A",
        model_a_id=model_a_id,
        model_b_id=model_b_id,
    )

    monkeypatch.setattr(votes, "find_existing_battle_vote", lambda *_a, **_kw: None)
    monkeypatch.setattr(votes, "_enforce_auth_vote_rate_limit", lambda **_kw: None)
    monkeypatch.setattr(
        votes, "_resolve_duplicate_vote_conflict", lambda *_a, **_kw: expected
    )

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="A"),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=_authenticated_principal(),
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
        commit_error=_integrity_error("duplicate key"),
    )
    expected = _vote_submit_response(
        vote_id=uuid.uuid4(),
        battle_id=battle.id,
        winner="tie",
        model_a_id=model_a_id,
        model_b_id=model_b_id,
    )

    monkeypatch.setattr(votes, "find_existing_battle_vote", lambda *_a, **_kw: None)
    monkeypatch.setattr(votes, "_enforce_auth_vote_rate_limit", lambda **_kw: None)
    monkeypatch.setattr(
        votes, "_resolve_duplicate_vote_conflict", lambda *_a, **_kw: expected
    )

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="tie"),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=_authenticated_principal(),
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response is expected
    assert db.flush_calls == 1
    assert db.commit_calls == 1
    assert db.rollback_calls == 1


def test_submit_vote_reveals_existing_unrevealed_vote_and_updates_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, model_a_id, model_b_id = _battle_and_runs()
    principal = _authenticated_principal()
    existing_vote = SimpleNamespace(
        id=uuid.uuid4(),
        winner="A",
        revealed=False,
        rubric=None,
        comment=None,
    )
    db = _VoteDB(
        battle=battle,
        runs=runs,
        model_lookup={
            model_a_id: SimpleNamespace(id=model_a_id, display_name="Alpha"),
            model_b_id: SimpleNamespace(id=model_b_id, display_name="Beta"),
        },
    )

    monkeypatch.setattr(
        votes,
        "find_existing_battle_vote",
        lambda *_a, **_kw: existing_vote,
    )
    monkeypatch.setattr(
        votes,
        "_enforce_auth_vote_rate_limit",
        lambda **_kw: pytest.fail("Rate-limit checks should not run"),
    )

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="B", comment="new"),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=principal,
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert existing_vote.winner == "B"
    assert existing_vote.comment == "new"
    assert existing_vote.revealed is True
    assert response.vote_id == str(existing_vote.id)
    assert response.winner == "B"
    assert response.reveal == {
        "A": {"model_id": str(model_a_id), "display_name": "Alpha"},
        "B": {"model_id": str(model_b_id), "display_name": "Beta"},
    }
