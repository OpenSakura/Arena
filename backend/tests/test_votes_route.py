from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Literal, cast
import uuid

from fastapi import HTTPException, Response
import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.api.routes import battles, votes
from app.core.security import Principal
from app.models.vote import Vote
from app.schemas.votes import VoteCreate, VoteSubmitResponse


_CREATOR_USER_ID = uuid.uuid4()


def _authenticated_principal(
    *,
    user_id: str | None = None,
    claims: dict[str, object] | None = None,
) -> Principal:
    return Principal(
        is_authenticated=True,
        user_id=user_id or str(_CREATOR_USER_ID),
        claims=claims or {},
    )


def _bot_principal(
    *,
    user_id: str | None = None,
    service_account_id: uuid.UUID | None = None,
    service_account_token_id: uuid.UUID | None = None,
    scopes: tuple[str, ...] = ("vote:create",),
    service_account_name: str = "Auto Judge",
) -> Principal:
    return Principal(
        is_authenticated=True,
        actor_type="bot",
        user_id=user_id or str(uuid.uuid4()),
        service_account_id=str(service_account_id or uuid.uuid4()),
        service_account_name=service_account_name,
        token_id=str(service_account_token_id or uuid.uuid4()),
        scopes=scopes,
        claims={},
    )


def _settings(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "auth_vote_submit_rate_limit_window_seconds": 60,
        "anon_rate_limit_bucket_seconds": 10,
        "battle_pool_user_recycle_after_hours": 9999,
        "battle_pool_assignment_ttl_seconds": 900,
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

    def scalar_one_or_none(self) -> object | None:
        return self._rows[0] if self._rows else None


class _VoteDB:
    def __init__(
        self,
        *,
        battle: object | None,
        runs: Sequence[object],
        flush_error: Exception | None = None,
        commit_error: Exception | None = None,
        model_lookup: dict[uuid.UUID, object] | None = None,
        existing_votes: Sequence[object] | None = None,
    ) -> None:
        self._battle = battle
        self._runs = list(runs)
        self._flush_error = flush_error
        self._commit_error = commit_error
        self._model_lookup = model_lookup or {}
        self._existing_votes = list(existing_votes or [])
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
        if "votes" in str(_stmt).lower():
            return _RowsResult(list(self._existing_votes))
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


class _CaptureRateLimiter:
    def __init__(self, *, limited: bool = False) -> None:
        self.limited = limited
        self.keys: list[str] = []

    def is_limited(self, key: str) -> bool:
        self.keys.append(key)
        return self.limited


def _battle_and_runs(
    *,
    status: str = "completed",
    side_b_output: str | None = "Translation B",
    requester_user_id: str | None = None,
) -> tuple[SimpleNamespace, list[SimpleNamespace], uuid.UUID, uuid.UUID]:
    battle_id = uuid.uuid4()
    model_a_id = uuid.uuid4()
    model_b_id = uuid.uuid4()
    battle = SimpleNamespace(
        id=battle_id,
        status=status,
        metadata_json={"requester_user_id": requester_user_id or str(_CREATOR_USER_ID)},
        requester_service_account_id=None,
        created_at=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
    )
    runs = [
        SimpleNamespace(side="A", model_id=model_a_id, output_text="Translation A"),
        SimpleNamespace(side="B", model_id=model_b_id, output_text=side_b_output),
    ]
    return battle, runs, model_a_id, model_b_id


def _pool_battle_and_runs() -> tuple[SimpleNamespace, list[SimpleNamespace], uuid.UUID, uuid.UUID]:
    creator_id = str(uuid.uuid4())
    battle, runs, model_a_id, model_b_id = _battle_and_runs(
        requester_user_id=creator_id,
    )
    battle.metadata_json.update(
        {
            "pre_generated": True,
            "prepopulation_job_id": str(uuid.uuid4()),
        }
    )
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


def test_vote_create_accepts_bounded_bot_metadata() -> None:
    metadata = {"external_run_id": "run-001", "score": 0.87}

    payload = VoteCreate(winner="A", bot_metadata=metadata)

    assert payload.winner == "A"
    assert payload.bot_metadata == metadata


def test_vote_create_accepts_new_rubric_tags() -> None:
    payload = VoteCreate.model_validate(
        {
            "winner": "A",
            "rubric": {
                "tags": ["knowledge", "cultural", "voice", "terminology", "refusal"]
            },
        }
    )

    assert payload.rubric is not None
    assert payload.rubric.tags == [
        "knowledge",
        "cultural",
        "voice",
        "terminology",
        "refusal",
    ]


def test_vote_create_rejects_invalid_bot_metadata_before_route_use() -> None:
    db = _VoteDB(battle=None, runs=[])

    with pytest.raises(ValidationError) as exc_info:
        VoteCreate.model_validate({"winner": "A", "bot_metadata": "not an object"})

    assert exc_info.value.errors()[0]["loc"] == ("bot_metadata",)
    assert db.added == []
    assert db.flush_calls == 0
    assert db.commit_calls == 0


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


def test_find_consumer_battle_vote_uses_human_battle_lookup() -> None:
    battle_id = uuid.uuid4()
    matching_vote = SimpleNamespace(battle_id=battle_id)
    db = _VoteDB(
        battle=None,
        runs=[],
        existing_votes=[SimpleNamespace(battle_id=uuid.uuid4()), matching_vote],
    )

    assert votes.find_consumer_battle_vote(db, battle_id=battle_id, consumer_type="human") is matching_vote  # type: ignore[arg-type]


def test_resolve_duplicate_vote_conflict_raises_500_when_vote_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(votes, "find_consumer_battle_vote", lambda *_a, **_kw: None)

    with pytest.raises(HTTPException) as exc_info:
        votes._resolve_duplicate_vote_conflict(
            db=object(),  # type: ignore[arg-type]
            battle_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to persist vote"


def test_resolve_duplicate_vote_conflict_raises_409_for_existing_vote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        votes,
        "find_consumer_battle_vote",
        lambda *_a, **_kw: SimpleNamespace(id=uuid.uuid4(), winner="B", revealed=True),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes._resolve_duplicate_vote_conflict(
            db=object(),  # type: ignore[arg-type]
            battle_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Battle already has a vote"


def test_resolve_duplicate_vote_conflict_rejects_same_winner_existing_vote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing_vote = SimpleNamespace(
        id=uuid.uuid4(),
        winner="A",
        revealed=True,
        rubric=None,
        comment=None,
    )
    monkeypatch.setattr(
        votes,
        "find_consumer_battle_vote",
        lambda *_a, **_kw: existing_vote,
    )

    with pytest.raises(HTTPException) as exc_info:
        votes._resolve_duplicate_vote_conflict(
            db=object(),  # type: ignore[arg-type]
            battle_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Battle already has a vote"


def test_resolve_duplicate_vote_conflict_rejects_unrevealed_existing_vote_without_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing_vote = SimpleNamespace(
        id=uuid.uuid4(),
        winner="A",
        revealed=False,
        rubric={"tags": ["accuracy"]},
        comment="old",
    )
    monkeypatch.setattr(
        votes,
        "find_consumer_battle_vote",
        lambda *_a, **_kw: existing_vote,
    )

    with pytest.raises(HTTPException) as exc_info:
        votes._resolve_duplicate_vote_conflict(
            db=object(),  # type: ignore[arg-type]
            battle_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Battle already has a vote"
    assert existing_vote.winner == "A"
    assert existing_vote.rubric == {"tags": ["accuracy"]}
    assert existing_vote.comment == "old"
    assert existing_vote.revealed is False


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


def test_submit_vote_rejects_failed_battle() -> None:
    battle, runs, _, _ = _battle_and_runs(status="failed")

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


def test_submit_vote_rejects_too_new_running_battle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, _, _ = _battle_and_runs(status="running")
    battle.created_at = datetime.now(timezone.utc) - timedelta(seconds=9)
    db = _VoteDB(battle=battle, runs=runs)

    monkeypatch.setattr(
        votes,
        "find_consumer_battle_vote",
        lambda *_a, **_kw: pytest.fail("vote lookup must not run during cooldown"),
    )
    monkeypatch.setattr(
        votes,
        "_enforce_auth_vote_rate_limit",
        lambda **_kw: pytest.fail("rate limit must not run during cooldown"),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A"),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=_authenticated_principal(),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Battle is not ready for voting"
    assert db.added == []
    assert db.commit_calls == 0


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


def test_submit_vote_allows_running_battle_after_delay_without_persisted_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, model_a_id, model_b_id = _battle_and_runs(status="running")
    battle.created_at = datetime.now(timezone.utc) - timedelta(seconds=11)
    for run in runs:
        run.output_text = None
    db = _VoteDB(
        battle=battle,
        runs=runs,
        model_lookup={
            model_a_id: SimpleNamespace(id=model_a_id, display_name="Model A"),
            model_b_id: SimpleNamespace(id=model_b_id, display_name="Model B"),
        },
    )
    monkeypatch.setattr(votes, "find_consumer_battle_vote", lambda *_a, **_kw: None)
    monkeypatch.setattr(votes, "_enforce_auth_vote_rate_limit", lambda **_kw: None)

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="A"),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=_authenticated_principal(),
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response.winner == "A"
    assert response.reveal == {
        "A": {"model_id": str(model_a_id), "display_name": "Model A"},
        "B": {"model_id": str(model_b_id), "display_name": "Model B"},
    }
    assert db.flush_calls == 1
    assert db.commit_calls == 1


def test_submit_vote_rejects_vote_when_selected_side_has_no_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle_id = uuid.uuid4()
    model_a_id = uuid.uuid4()
    model_b_id = uuid.uuid4()
    db = _VoteDB(
        battle=SimpleNamespace(
            id=battle_id,
            status="completed",
            metadata_json={"requester_user_id": str(_CREATOR_USER_ID)},
        ),
        runs=[
            SimpleNamespace(side="A", model_id=model_a_id, output_text="A"),
            SimpleNamespace(side="B", model_id=model_b_id, output_text=None),
        ],
    )
    monkeypatch.setattr(votes, "find_consumer_battle_vote", lambda *_a, **_kw: None)

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


def test_submit_vote_rejects_authenticated_non_creator_before_reveal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    creator_id = str(uuid.uuid4())
    non_creator_id = str(uuid.uuid4())
    battle, runs, _, _ = _battle_and_runs(requester_user_id=creator_id)
    db = _VoteDB(battle=battle, runs=runs)

    monkeypatch.setattr(
        votes,
        "find_consumer_battle_vote",
        lambda *_a, **_kw: pytest.fail("vote lookup must not run before auth"),
    )
    monkeypatch.setattr(
        votes,
        "_enforce_auth_vote_rate_limit",
        lambda **_kw: pytest.fail("rate limit must not run before auth"),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A"),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=_authenticated_principal(user_id=non_creator_id),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Only the battle creator or an admin may vote on this battle"
    assert db.added == []
    assert db.commit_calls == 0


def test_submit_vote_allows_non_creator_for_pool_eligible_unvoted_battle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voter_id = str(uuid.uuid4())
    battle, runs, model_a_id, model_b_id = _pool_battle_and_runs()
    battle.metadata_json["pooled_replay"] = {
        "backend_gated": True,
        "display_delay_ms": 12_345,
        "assigned_user_id": voter_id,
        "source": "admin_pre_generated",
        "unlocked": True,
    }
    db = _VoteDB(
        battle=battle,
        runs=runs,
        model_lookup={
            model_a_id: SimpleNamespace(id=model_a_id, display_name="Model A"),
            model_b_id: SimpleNamespace(id=model_b_id, display_name="Model B"),
        },
    )

    monkeypatch.setattr(votes, "find_consumer_battle_vote", lambda *_a, **_kw: None)
    monkeypatch.setattr(votes, "_enforce_auth_vote_rate_limit", lambda **_kw: None)

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="A"),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=_authenticated_principal(user_id=voter_id),
        settings=_settings(battle_pool_user_recycle_after_hours=24),  # type: ignore[arg-type]
    )

    assert response.winner == "A"
    assert db.vote_row is not None
    assert db.vote_row.battle_id == battle.id
    assert db.commit_calls == 1


def test_submit_vote_rejects_unassigned_pool_battle_before_reveal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, _, _ = _pool_battle_and_runs()
    db = _VoteDB(battle=battle, runs=runs)

    monkeypatch.setattr(
        votes,
        "find_consumer_battle_vote",
        lambda *_a, **_kw: pytest.fail("unassigned pool replay must fail before vote lookup"),
    )
    monkeypatch.setattr(
        votes,
        "_enforce_auth_vote_rate_limit",
        lambda **_kw: pytest.fail("unassigned pool replay must fail before rate limit"),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A"),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=_authenticated_principal(user_id=str(uuid.uuid4())),
            settings=_settings(battle_pool_user_recycle_after_hours=24),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409
    assert db.added == []
    assert db.flush_calls == 0
    assert db.commit_calls == 0


def test_submit_vote_rejects_expired_locked_pool_replay_before_reveal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voter_id = str(uuid.uuid4())
    now = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
    battle, runs, _, _ = _pool_battle_and_runs()
    battle.metadata_json["pooled_replay"] = {
        "backend_gated": True,
        "display_delay_ms": 12_345,
        "assigned_user_id": voter_id,
        "assigned_at": (now - timedelta(hours=1)).isoformat(),
        "expires_at": (now - timedelta(seconds=1)).isoformat(),
        "source": "admin_pre_generated",
        "unlocked": False,
    }
    db = _VoteDB(battle=battle, runs=runs)

    monkeypatch.setattr(
        votes,
        "find_consumer_battle_vote",
        lambda *_a, **_kw: pytest.fail("expired locked replay must fail before vote lookup"),
    )
    monkeypatch.setattr(
        votes,
        "_enforce_auth_vote_rate_limit",
        lambda **_kw: pytest.fail("expired locked replay must fail before rate limit"),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A"),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=_authenticated_principal(user_id=voter_id),
            settings=_settings(battle_pool_user_recycle_after_hours=24),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409
    assert db.added == []
    assert db.flush_calls == 0
    assert db.commit_calls == 0


def test_submit_vote_rejects_locked_backend_gated_pooled_replay_before_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voter_id = str(uuid.uuid4())
    battle, runs, _, _ = _pool_battle_and_runs()
    now = datetime.now(timezone.utc)
    battle.created_at = now - timedelta(days=1)
    battle.metadata_json["pooled_replay"] = {
        "backend_gated": True,
        "display_delay_ms": 0,
        "assigned_user_id": voter_id,
        "assigned_at": (now - timedelta(seconds=9)).isoformat(),
    }
    db = _VoteDB(battle=battle, runs=runs)

    monkeypatch.setattr(
        votes,
        "find_consumer_battle_vote",
        lambda *_a, **_kw: pytest.fail("locked replay must fail before vote lookup"),
    )
    monkeypatch.setattr(
        votes,
        "_enforce_auth_vote_rate_limit",
        lambda **_kw: pytest.fail("locked replay must fail before rate limit"),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A"),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=_authenticated_principal(user_id=voter_id),
            settings=_settings(battle_pool_user_recycle_after_hours=24),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == (
        "Battle replay is still streaming; wait for replay to complete before voting"
    )
    assert db.added == []
    assert db.flush_calls == 0
    assert db.commit_calls == 0


def test_submit_vote_allows_unlocked_backend_gated_pooled_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voter_id = str(uuid.uuid4())
    battle, runs, model_a_id, model_b_id = _pool_battle_and_runs()
    battle.metadata_json["pooled_replay"] = {
        "backend_gated": True,
        "display_delay_ms": 12_345,
        "assigned_user_id": voter_id,
        "unlocked": True,
    }
    db = _VoteDB(
        battle=battle,
        runs=runs,
        model_lookup={
            model_a_id: SimpleNamespace(id=model_a_id, display_name="Model A"),
            model_b_id: SimpleNamespace(id=model_b_id, display_name="Model B"),
        },
    )

    monkeypatch.setattr(votes, "find_consumer_battle_vote", lambda *_a, **_kw: None)
    monkeypatch.setattr(votes, "_enforce_auth_vote_rate_limit", lambda **_kw: None)

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="B"),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=_authenticated_principal(user_id=voter_id),
        settings=_settings(battle_pool_user_recycle_after_hours=24),  # type: ignore[arg-type]
    )

    assert response.winner == "B"
    assert response.reveal == {
        "A": {"model_id": str(model_a_id), "display_name": "Model A"},
        "B": {"model_id": str(model_b_id), "display_name": "Model B"},
    }
    assert db.vote_row is not None
    assert db.vote_row.battle_id == battle.id
    assert db.commit_calls == 1


def test_submit_vote_allows_assigned_locked_backend_gated_pooled_replay_after_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voter_id = str(uuid.uuid4())
    battle, runs, model_a_id, model_b_id = _pool_battle_and_runs()
    now = datetime.now(timezone.utc)
    battle.created_at = now - timedelta(days=1)
    battle.metadata_json["pooled_replay"] = {
        "backend_gated": True,
        "display_delay_ms": 0,
        "assigned_user_id": voter_id,
        "assigned_at": (
            now - timedelta(seconds=votes.RUNNING_BATTLE_VOTE_DELAY_SECONDS + 1)
        ).isoformat(),
        "unlocked": False,
    }
    db = _VoteDB(
        battle=battle,
        runs=runs,
        model_lookup={
            model_a_id: SimpleNamespace(id=model_a_id, display_name="Model A"),
            model_b_id: SimpleNamespace(id=model_b_id, display_name="Model B"),
        },
    )

    monkeypatch.setattr(votes, "find_consumer_battle_vote", lambda *_a, **_kw: None)
    monkeypatch.setattr(votes, "_enforce_auth_vote_rate_limit", lambda **_kw: None)

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="B"),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=_authenticated_principal(user_id=voter_id),
        settings=_settings(battle_pool_user_recycle_after_hours=24),  # type: ignore[arg-type]
    )

    assert response.winner == "B"
    assert response.reveal == {
        "A": {"model_id": str(model_a_id), "display_name": "Model A"},
        "B": {"model_id": str(model_b_id), "display_name": "Model B"},
    }
    assert db.vote_row is not None
    assert db.vote_row.battle_id == battle.id
    assert db.commit_calls == 1


def test_submit_vote_rejects_unlocked_pool_replay_assigned_to_other_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, _, _ = _pool_battle_and_runs()
    battle.metadata_json["pooled_replay"] = {
        "backend_gated": True,
        "display_delay_ms": 12_345,
        "assigned_user_id": str(uuid.uuid4()),
        "source": "admin_pre_generated",
        "unlocked": True,
    }
    db = _VoteDB(battle=battle, runs=runs)

    monkeypatch.setattr(
        votes,
        "find_consumer_battle_vote",
        lambda *_a, **_kw: pytest.fail("other user's replay must fail before vote lookup"),
    )
    monkeypatch.setattr(
        votes,
        "_enforce_auth_vote_rate_limit",
        lambda **_kw: pytest.fail("other user's replay must fail before rate limit"),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A"),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=_authenticated_principal(user_id=str(uuid.uuid4())),
            settings=_settings(battle_pool_user_recycle_after_hours=24),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 403
    assert db.added == []
    assert db.commit_calls == 0


def test_submit_vote_rejects_locked_pool_replay_assigned_to_other_user_after_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, _, _ = _pool_battle_and_runs()
    now = datetime.now(timezone.utc)
    battle.created_at = now - timedelta(days=1)
    battle.metadata_json["pooled_replay"] = {
        "backend_gated": True,
        "display_delay_ms": 0,
        "assigned_user_id": str(uuid.uuid4()),
        "assigned_at": (
            now - timedelta(seconds=votes.RUNNING_BATTLE_VOTE_DELAY_SECONDS + 1)
        ).isoformat(),
        "source": "admin_pre_generated",
        "unlocked": False,
    }
    db = _VoteDB(battle=battle, runs=runs)

    monkeypatch.setattr(
        votes,
        "find_consumer_battle_vote",
        lambda *_a, **_kw: pytest.fail("other user's replay must fail before vote lookup"),
    )
    monkeypatch.setattr(
        votes,
        "_enforce_auth_vote_rate_limit",
        lambda **_kw: pytest.fail("other user's replay must fail before rate limit"),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A"),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=_authenticated_principal(user_id=str(uuid.uuid4())),
            settings=_settings(battle_pool_user_recycle_after_hours=24),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 403
    assert db.added == []
    assert db.commit_calls == 0


def test_submit_vote_rejects_non_creator_for_pool_battle_after_any_vote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, _, _ = _pool_battle_and_runs()
    db = _VoteDB(
        battle=battle,
        runs=runs,
        existing_votes=[SimpleNamespace(battle_id=battle.id)],
    )

    monkeypatch.setattr(
        votes,
        "find_consumer_battle_vote",
        lambda *_a, **_kw: pytest.fail("vote lookup must not run after pool auth denial"),
    )
    monkeypatch.setattr(
        votes,
        "_enforce_auth_vote_rate_limit",
        lambda **_kw: pytest.fail("rate limit must not run after pool auth denial"),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A"),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=_authenticated_principal(user_id=str(uuid.uuid4())),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Only the battle creator or an admin may vote on this battle"
    assert db.added == []
    assert db.commit_calls == 0


def test_submit_vote_allows_admin_non_creator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, model_a_id, model_b_id = _battle_and_runs(
        requester_user_id=str(uuid.uuid4())
    )
    db = _VoteDB(
        battle=battle,
        runs=runs,
        model_lookup={
            model_a_id: SimpleNamespace(id=model_a_id, display_name="Model A"),
            model_b_id: SimpleNamespace(id=model_b_id, display_name="Model B"),
        },
    )
    admin_group_name = "arena-admins"
    monkeypatch.setattr(votes, "find_consumer_battle_vote", lambda *_a, **_kw: None)
    monkeypatch.setattr(votes, "_enforce_auth_vote_rate_limit", lambda **_kw: None)
    monkeypatch.setattr(
        battles,
        "get_settings",
        lambda: SimpleNamespace(
            oidc_admin_group_claim="groups",
            oidc_admin_group_name=admin_group_name,
        ),
    )

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="B"),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=_authenticated_principal(
            user_id=str(uuid.uuid4()),
            claims={"groups": [admin_group_name]},
        ),
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response.winner == "B"
    assert response.reveal == {
        "A": {"model_id": str(model_a_id), "display_name": "Model A"},
        "B": {"model_id": str(model_b_id), "display_name": "Model B"},
    }
    assert db.commit_calls == 1


def test_submit_vote_returns_existing_vote_for_same_human_same_winner_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, model_a_id, model_b_id = _battle_and_runs()
    principal = _authenticated_principal()
    existing_vote_id = uuid.uuid4()
    existing_vote = SimpleNamespace(
        id=existing_vote_id,
        battle_id=battle.id,
        winner="A",
        revealed=True,
        rubric=None,
        comment=None,
        voter_user_id=uuid.UUID(principal.user_id),
    )
    db = _VoteDB(
        battle=battle,
        runs=runs,
        existing_votes=[existing_vote],
        model_lookup={
            model_a_id: SimpleNamespace(id=model_a_id, display_name="Model A"),
            model_b_id: SimpleNamespace(id=model_b_id, display_name="Model B"),
        },
    )
    monkeypatch.setattr(
        votes,
        "_enforce_auth_vote_rate_limit",
        lambda **_kw: pytest.fail("Rate-limit checks should not run"),
    )

    route_response = Response()
    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="A"),
        response=route_response,
        db=db,  # type: ignore[arg-type]
        principal=principal,
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert route_response.status_code == 200
    assert response.vote_id == str(existing_vote_id)
    assert response.winner == "A"
    assert db.added == []
    assert db.commit_calls == 0


def test_submit_vote_rejects_same_human_different_winner_after_reveal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, _, _ = _battle_and_runs()
    principal = _authenticated_principal()
    existing_vote = SimpleNamespace(
        id=uuid.uuid4(),
        battle_id=battle.id,
        winner="A",
        revealed=True,
        rubric=None,
        comment=None,
        voter_user_id=uuid.UUID(principal.user_id),
    )
    db = _VoteDB(battle=battle, runs=runs, existing_votes=[existing_vote])
    monkeypatch.setattr(
        votes,
        "_enforce_auth_vote_rate_limit",
        lambda **_kw: pytest.fail("Rate-limit checks should not run"),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="B"),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=principal,
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Vote already revealed and cannot be changed"
    assert db.added == []
    assert db.commit_calls == 0


def test_submit_vote_rejects_different_human_second_vote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    second_user_id = str(uuid.uuid4())
    battle, runs, _, _ = _battle_and_runs(requester_user_id=second_user_id)
    existing_vote = SimpleNamespace(
        id=uuid.uuid4(),
        battle_id=battle.id,
        winner="B",
        revealed=True,
        voter_user_id=uuid.uuid4(),
    )
    db = _VoteDB(battle=battle, runs=runs, existing_votes=[existing_vote])
    monkeypatch.setattr(
        votes,
        "_enforce_auth_vote_rate_limit",
        lambda **_kw: pytest.fail("duplicate vote must not rate limit"),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A"),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=_authenticated_principal(user_id=second_user_id),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Battle already has a vote"


def test_submit_vote_allows_human_after_bot_vote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    human_user_id = str(uuid.uuid4())
    battle, runs, _, _ = _battle_and_runs(requester_user_id=human_user_id)
    existing_vote = SimpleNamespace(
        id=uuid.uuid4(),
        battle_id=battle.id,
        winner="A",
        revealed=True,
        service_account_id=uuid.uuid4(),
    )
    db = _VoteDB(battle=battle, runs=runs, existing_votes=[existing_vote])
    monkeypatch.setattr(votes, "_enforce_auth_vote_rate_limit", lambda **_kw: None)
    db._model_lookup.update(
        {
            runs[0].model_id: SimpleNamespace(id=runs[0].model_id, display_name="Model A"),
            runs[1].model_id: SimpleNamespace(id=runs[1].model_id, display_name="Model B"),
        }
    )

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="B"),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=_authenticated_principal(user_id=human_user_id),
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response.winner == "B"
    assert db.commit_calls == 1


def test_submit_vote_records_vote_and_uses_human_auth_rate_limit_key(
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
    limiter = _CaptureRateLimiter()

    monkeypatch.setattr(votes, "find_consumer_battle_vote", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        votes, "_get_auth_vote_submit_rate_limiter", lambda: limiter
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
    assert limiter.keys == [f"auth_vote_submit:user:{principal.user_id}"]
    assert db.flush_calls == 1
    assert db.commit_calls == 1
    vote_row = next(row for row in db.added if isinstance(row, Vote))
    assert vote_row.voter_user_id == uuid.UUID(principal.user_id)
    assert vote_row.revealed is True


def test_submit_vote_persists_bot_metadata_and_safe_attribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot_user_id = str(uuid.uuid4())
    service_account_id = uuid.uuid4()
    service_account_token_id = uuid.uuid4()
    principal = _bot_principal(
        user_id=bot_user_id,
        service_account_id=service_account_id,
        service_account_token_id=service_account_token_id,
    )
    battle, runs, model_a_id, model_b_id = _battle_and_runs(
        requester_user_id=bot_user_id
    )
    metadata = {"external_run_id": "judge-001", "score": 0.91}
    db = _VoteDB(
        battle=battle,
        runs=runs,
        model_lookup={
            model_a_id: SimpleNamespace(id=model_a_id, display_name="Model A"),
            model_b_id: SimpleNamespace(id=model_b_id, display_name="Model B"),
        },
    )
    limiter = _CaptureRateLimiter()

    monkeypatch.setattr(votes, "find_consumer_battle_vote", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        votes, "_get_auth_vote_submit_rate_limiter", lambda: limiter
    )

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="B", comment="auto", bot_metadata=metadata),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=principal,
        settings=_settings(),  # type: ignore[arg-type]
    )

    vote_row = next(row for row in db.added if isinstance(row, Vote))
    assert vote_row.voter_user_id == uuid.UUID(bot_user_id)
    assert vote_row.service_account_id == service_account_id
    assert vote_row.service_account_token_id == service_account_token_id
    assert vote_row.bot_metadata == metadata
    assert vote_row.winner == "B"
    assert vote_row.comment == "auto"
    assert vote_row.revealed is True
    assert limiter.keys == [f"auth_vote_submit:service_account:{service_account_id}"]
    assert f"auth_vote_submit:user:{bot_user_id}" not in limiter.keys
    assert response.voter_actor_type == "bot"
    assert response.service_account_id == str(service_account_id)
    assert response.service_account_name == "Auto Judge"
    assert response.service_account_token_id == str(service_account_token_id)
    assert response.bot_metadata == metadata
    assert not hasattr(response, "token_hash")
    assert not hasattr(response, "plaintext_token")


def test_submit_vote_allows_assigned_unlocked_bot_pool_battle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot_user_id = str(uuid.uuid4())
    service_account_id = uuid.uuid4()
    service_account_token_id = uuid.uuid4()
    principal = _bot_principal(
        user_id=bot_user_id,
        service_account_id=service_account_id,
        service_account_token_id=service_account_token_id,
    )
    battle, runs, model_a_id, model_b_id = _pool_battle_and_runs()
    battle.metadata_json["pooled_replays"] = {
        "human": None,
        "bot": {
            "backend_gated": True,
            "display_delay_ms": 12_345,
            "assigned_user_id": bot_user_id,
            "assigned_service_account_id": str(service_account_id),
            "source": "admin_pre_generated",
            "unlocked": True,
        },
    }
    db = _VoteDB(
        battle=battle,
        runs=runs,
        model_lookup={
            model_a_id: SimpleNamespace(id=model_a_id, display_name="Model A"),
            model_b_id: SimpleNamespace(id=model_b_id, display_name="Model B"),
        },
    )
    monkeypatch.setattr(votes, "find_consumer_battle_vote", lambda *_a, **_kw: None)
    monkeypatch.setattr(votes, "_enforce_auth_vote_rate_limit", lambda **_kw: None)

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="A", bot_metadata={"run": "pooled"}),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=principal,
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response.winner == "A"
    assert response.voter_actor_type == "bot"
    assert db.vote_row is not None
    assert db.vote_row.service_account_id == service_account_id
    assert db.commit_calls == 1


def test_submit_vote_allows_assigned_locked_bot_pool_battle_after_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot_user_id = str(uuid.uuid4())
    service_account_id = uuid.uuid4()
    service_account_token_id = uuid.uuid4()
    principal = _bot_principal(
        user_id=bot_user_id,
        service_account_id=service_account_id,
        service_account_token_id=service_account_token_id,
    )
    battle, runs, model_a_id, model_b_id = _pool_battle_and_runs()
    now = datetime.now(timezone.utc)
    battle.created_at = now - timedelta(days=1)
    battle.metadata_json["pooled_replays"] = {
        "human": None,
        "bot": {
            "backend_gated": True,
            "display_delay_ms": 0,
            "assigned_user_id": bot_user_id,
            "assigned_service_account_id": str(service_account_id),
            "assigned_at": (
                now - timedelta(seconds=votes.RUNNING_BATTLE_VOTE_DELAY_SECONDS + 1)
            ).isoformat(),
            "source": "admin_pre_generated",
            "unlocked": False,
        },
    }
    db = _VoteDB(
        battle=battle,
        runs=runs,
        model_lookup={
            model_a_id: SimpleNamespace(id=model_a_id, display_name="Model A"),
            model_b_id: SimpleNamespace(id=model_b_id, display_name="Model B"),
        },
    )
    monkeypatch.setattr(votes, "find_consumer_battle_vote", lambda *_a, **_kw: None)
    monkeypatch.setattr(votes, "_enforce_auth_vote_rate_limit", lambda **_kw: None)

    response = votes.submit_vote(
        battle_id=str(battle.id),
        payload=VoteCreate(winner="A", bot_metadata={"run": "pooled"}),
        response=Response(),
        db=db,  # type: ignore[arg-type]
        principal=principal,
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response.winner == "A"
    assert response.voter_actor_type == "bot"
    assert db.vote_row is not None
    assert db.vote_row.service_account_id == service_account_id
    assert db.commit_calls == 1


def test_submit_vote_rejects_human_bot_metadata_without_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, _, _ = _battle_and_runs()
    db = _VoteDB(battle=battle, runs=runs)
    monkeypatch.setattr(
        votes,
        "find_consumer_battle_vote",
        lambda *_a, **_kw: pytest.fail("human bot_metadata must fail before lookup"),
    )
    monkeypatch.setattr(
        votes,
        "_enforce_auth_vote_rate_limit",
        lambda **_kw: pytest.fail("human bot_metadata must fail before rate limit"),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A", bot_metadata={"spoof": True}),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=_authenticated_principal(),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Human principals cannot submit bot_metadata"
    assert db.added == []
    assert db.flush_calls == 0
    assert db.commit_calls == 0


def test_submit_vote_rejects_under_scoped_bot_without_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot_user_id = str(uuid.uuid4())
    battle, runs, _, _ = _battle_and_runs(requester_user_id=bot_user_id)
    db = _VoteDB(battle=battle, runs=runs)
    principal = _bot_principal(user_id=bot_user_id, scopes=("battle:read",))
    monkeypatch.setattr(
        votes,
        "find_consumer_battle_vote",
        lambda *_a, **_kw: pytest.fail("under-scoped bot must fail before lookup"),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A", bot_metadata={"run": "judge-001"}),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=principal,
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Required service token scope missing"
    assert db.added == []
    assert db.flush_calls == 0
    assert db.commit_calls == 0


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("service_account_id", None),
        ("token_id", None),
        ("service_account_id", "not-a-uuid"),
        ("token_id", "not-a-uuid"),
    ],
)
def test_submit_vote_rejects_malformed_bot_principal_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    field_value: str | None,
) -> None:
    bot_user_id = str(uuid.uuid4())
    battle, runs, _, _ = _battle_and_runs(requester_user_id=bot_user_id)
    db = _VoteDB(battle=battle, runs=runs)
    principal = _bot_principal(user_id=bot_user_id).model_copy(
        update={field_name: field_value}
    )
    monkeypatch.setattr(
        votes,
        "find_consumer_battle_vote",
        lambda *_a, **_kw: pytest.fail("malformed bot must fail before lookup"),
    )
    monkeypatch.setattr(
        votes,
        "_enforce_auth_vote_rate_limit",
        lambda **_kw: pytest.fail("malformed bot must fail before rate limit"),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A", bot_metadata={"run": "judge-001"}),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=principal,
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Bot principal service account context required"
    assert db.added == []
    assert db.flush_calls == 0
    assert db.commit_calls == 0


def test_enforce_auth_vote_rate_limit_uses_bot_token_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot_user_id = uuid.uuid4()
    token_id = uuid.uuid4()
    principal = _bot_principal(
        user_id=str(bot_user_id),
        service_account_token_id=token_id,
    ).model_copy(update={"service_account_id": None})
    limiter = _CaptureRateLimiter()
    monkeypatch.setattr(
        votes, "_get_auth_vote_submit_rate_limiter", lambda: limiter
    )

    votes._enforce_auth_vote_rate_limit(
        voter_user_id=bot_user_id,
        principal=principal,
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert limiter.keys == [f"auth_vote_submit:token:{token_id}"]
    assert f"auth_vote_submit:user:{bot_user_id}" not in limiter.keys


def test_submit_vote_rejects_bot_second_vote_without_metadata_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot_user_id = str(uuid.uuid4())
    service_account_id = uuid.uuid4()
    service_account_token_id = uuid.uuid4()
    principal = _bot_principal(
        user_id=bot_user_id,
        service_account_id=service_account_id,
        service_account_token_id=service_account_token_id,
    )
    battle, runs, model_a_id, model_b_id = _battle_and_runs(
        requester_user_id=bot_user_id
    )
    existing_vote = SimpleNamespace(
        id=uuid.uuid4(),
        battle_id=battle.id,
        winner="A",
        revealed=True,
        rubric={"tags": ["accuracy"]},
        comment="existing",
        service_account_id=service_account_id,
        service_account_token_id=service_account_token_id,
        bot_metadata={"external_run_id": "original"},
    )
    db = _VoteDB(
        battle=battle,
        runs=runs,
        existing_votes=[existing_vote],
        model_lookup={
            model_a_id: SimpleNamespace(id=model_a_id, display_name="Model A"),
            model_b_id: SimpleNamespace(id=model_b_id, display_name="Model B"),
        },
    )
    route_response = Response()

    monkeypatch.setattr(
        votes,
        "_enforce_auth_vote_rate_limit",
        lambda **_kw: pytest.fail("duplicate vote must not rate limit"),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(
                winner="A",
                comment="new",
                bot_metadata={"external_run_id": "replacement"},
            ),
            response=route_response,
            db=db,  # type: ignore[arg-type]
            principal=principal,
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Battle already has a vote"
    assert existing_vote.comment == "existing"
    assert existing_vote.bot_metadata == {"external_run_id": "original"}
    assert db.commit_calls == 0


def test_submit_vote_rejects_duplicate_conflict_after_flush_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, _, _ = _battle_and_runs()
    existing_vote = SimpleNamespace(id=uuid.uuid4(), battle_id=battle.id, winner="A")
    db = _VoteDB(
        battle=battle,
        runs=runs,
        flush_error=_integrity_error("duplicate key"),
        existing_votes=[existing_vote],
    )

    lookup_calls = 0

    def fake_find_any(
        _db: object,
        *,
        battle_id: uuid.UUID,
        consumer_type: str,
    ) -> object | None:
        _ = consumer_type
        nonlocal lookup_calls
        lookup_calls += 1
        if lookup_calls == 1:
            return None
        return existing_vote

    monkeypatch.setattr(votes, "find_consumer_battle_vote", fake_find_any)
    monkeypatch.setattr(votes, "_enforce_auth_vote_rate_limit", lambda **_kw: None)

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="A"),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=_authenticated_principal(),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Battle already has a vote"
    assert db.flush_calls == 1
    assert db.rollback_calls == 1
    assert db.commit_calls == 0


def test_submit_vote_rejects_duplicate_conflict_after_commit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, _, _ = _battle_and_runs()
    existing_vote = SimpleNamespace(id=uuid.uuid4(), battle_id=battle.id, winner="tie")
    db = _VoteDB(
        battle=battle,
        runs=runs,
        commit_error=_integrity_error("duplicate key"),
        existing_votes=[existing_vote],
    )

    lookup_calls = 0

    def fake_find_any(
        _db: object,
        *,
        battle_id: uuid.UUID,
        consumer_type: str,
    ) -> object | None:
        _ = consumer_type
        nonlocal lookup_calls
        lookup_calls += 1
        if lookup_calls == 1:
            return None
        return existing_vote

    monkeypatch.setattr(votes, "find_consumer_battle_vote", fake_find_any)
    monkeypatch.setattr(votes, "_enforce_auth_vote_rate_limit", lambda **_kw: None)

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="tie"),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=_authenticated_principal(),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Battle already has a vote"
    assert db.flush_calls == 1
    assert db.commit_calls == 1
    assert db.rollback_calls == 1


def test_submit_vote_rejects_unrevealed_existing_vote_without_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle, runs, _, _ = _battle_and_runs()
    principal = _authenticated_principal()
    existing_vote = SimpleNamespace(
        id=uuid.uuid4(),
        battle_id=battle.id,
        winner="A",
        revealed=False,
        rubric=None,
        comment=None,
    )
    db = _VoteDB(battle=battle, runs=runs, existing_votes=[existing_vote])
    monkeypatch.setattr(
        votes,
        "_enforce_auth_vote_rate_limit",
        lambda **_kw: pytest.fail("Rate-limit checks should not run"),
    )

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="B", comment="new"),
            response=Response(),
            db=db,  # type: ignore[arg-type]
            principal=principal,
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Battle already has a vote"
    assert existing_vote.winner == "A"
    assert existing_vote.comment is None
    assert existing_vote.revealed is False
