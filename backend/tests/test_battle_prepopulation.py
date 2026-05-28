from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import importlib
from types import SimpleNamespace
from typing import Any
import uuid

from fastapi import HTTPException
from fastapi.routing import APIRoute
import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.api.routes import battles, votes
from app.core.security import Principal
from app.models.battle import Battle
from app.models.battle_prepopulation import BattlePrepopulationJob
from app.models.model_registry import Model
from app.models.vote import Vote
from app.schemas.battles import BattleCreate
from app.schemas.votes import VoteCreate


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type: JSONB, _compiler: object, **_kw: object) -> str:
    return "JSON"


def _prepopulation_service() -> Any:
    return importlib.import_module("app.services.battle_prepopulation")


def _admin_route() -> Any:
    return importlib.import_module("app.api.routes.admin_battle_prepopulation")


def _authenticated_principal(
    *,
    user_id: str | None = None,
    admin: bool = False,
) -> Principal:
    claims = {"groups": ["arena_admin", "arena-admins"]} if admin else {}
    return Principal(
        is_authenticated=True,
        actor_type="human",
        user_id=user_id or str(uuid.uuid4()),
        claims=claims,
    )


def _settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "battle_prepopulation_max_job_size": 50,
        "battle_pool_user_recycle_after_hours": 24,
        "battle_pool_assignment_ttl_seconds": 900,
        "battle_prepopulation_enabled": True,
        "oidc_admin_group_claim": "groups",
        "oidc_admin_group_name": "arena_admin",
        "battle_sampling_weights": {},
        "battle_targets": {},
        "battle_strict_targets": {},
        "battle_outage_models": [],
        "battle_sampling_boost_models": [],
        "leaderboard_refresh_daily_vote_cap": 0,
        "auth_battle_create_rate_limit": 0,
        "auth_battle_create_rate_limit_window_seconds": 60,
        "anon_rate_limit_bucket_seconds": 10,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _model(
    *,
    model_id: uuid.UUID | None = None,
    enabled: bool = True,
    visibility: str = "public",
    model_name: str = "model-a",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=model_id or uuid.uuid4(),
        display_name=model_name.title(),
        model_name=model_name,
        enabled=enabled,
        visibility=visibility,
    )


def _task() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        source_text="JP text",
        source_lang="ja",
        target_lang="zh",
    )


def _battle(
    *,
    status: str = "completed",
    created_at: datetime | None = None,
    metadata_json: dict[str, object] | None = None,
    requester_user_id: str | None = None,
) -> SimpleNamespace:
    user_id = requester_user_id or str(uuid.uuid4())
    metadata = {
        "task_snapshot": {
            "source_text": "JP text",
            "source_lang": "ja",
            "target_lang": "zh",
        },
        "requester_user_id": user_id,
    }
    if metadata_json is not None:
        metadata.update(metadata_json)
    return SimpleNamespace(
        id=uuid.uuid4(),
        task_id=uuid.uuid4(),
        mode="jp2zh_ab",
        status=status,
        metadata_json=metadata,
        created_at=created_at or datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
        requester_service_account_id=None,
    )


def _run(side: str, *, model_id: uuid.UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        side=side,
        model_id=model_id or uuid.uuid4(),
        output_text=f"translation {side}",
        error_text=None,
        stats={"tokens": 12},
    )


class _RowsResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = list(rows)

    def scalars(self) -> "_RowsResult":
        return self

    def all(self) -> list[object]:
        return list(self._rows)

    def first(self) -> object | None:
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self) -> object | None:
        return self._rows[0] if self._rows else None


class _BattleDB:
    def __init__(
        self,
        *,
        battle: object | None = None,
        runs: list[object] | None = None,
        execute_rows: list[list[object]] | None = None,
        models: list[object] | None = None,
        task: object | None = None,
        existing_votes: list[object] | None = None,
    ) -> None:
        self.battle = battle
        self.runs = runs or []
        self.execute_rows = [list(rows) for rows in (execute_rows or [])]
        self.models = {getattr(model, "id"): model for model in (models or [])}
        self.task = task
        self.existing_votes = list(existing_votes or [])
        self.added: list[object] = []
        self.commit_calls = 0
        self.flush_calls = 0
        self.refreshed: list[object] = []

    def get(self, model_type: type[object], key: uuid.UUID) -> object | None:
        if model_type is Battle and getattr(self.battle, "id", None) == key:
            return self.battle
        if model_type is Model:
            return self.models.get(key)
        if getattr(model_type, "__name__", "") == "Task" and getattr(self.task, "id", None) == key:
            return self.task
        return None

    def execute(self, _stmt: object) -> _RowsResult:
        if self.execute_rows:
            return _RowsResult(self.execute_rows.pop(0))
        for description in getattr(_stmt, "column_descriptions", []):
            if description.get("entity") is Model:
                return _RowsResult(list(self.models.values()))
        return _RowsResult(self.runs)

    def add(self, item: object) -> None:
        self.added.append(item)

    def add_all(self, items: list[object]) -> None:
        self.added.extend(items)

    def flush(self) -> None:
        self.flush_calls += 1
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = uuid.uuid4()

    def commit(self) -> None:
        self.commit_calls += 1
        now = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = uuid.uuid4()
            if hasattr(item, "created_at") and getattr(item, "created_at", None) is None:
                item.created_at = now
            if hasattr(item, "updated_at") and getattr(item, "updated_at", None) is None:
                item.updated_at = now

    def refresh(self, item: object) -> None:
        self.refreshed.append(item)


class _VoteDB(_BattleDB):
    def __init__(self, *, battle: object, runs: list[object], existing_votes: list[object]) -> None:
        super().__init__(battle=battle, runs=runs)
        self.existing_votes = existing_votes
        self.vote_row: Vote | None = None

    def execute(self, _stmt: object) -> _RowsResult:
        if "votes" in str(_stmt).lower():
            return _RowsResult(self.existing_votes)
        return _RowsResult(self.runs)

    def add(self, item: object) -> None:
        super().add(item)
        if isinstance(item, Vote):
            self.vote_row = item


def test_pool_prefers_completed_unvoted_admin_pregenerated_battle() -> None:
    service = _prepopulation_service()
    pooled = _battle(
        metadata_json={
            "pre_generated": True,
            "prepopulation_job_id": str(uuid.uuid4()),
        }
    )
    db = _BattleDB(battle=pooled, runs=[_run("A"), _run("B")])

    selected = service.select_eligible_pool_battle(
        db,
        now=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
        settings=_settings(),
    )

    assert selected.battle.id == pooled.id
    assert selected.source == "admin_pre_generated"
    assert 10_000 <= selected.display_delay_ms <= 30_000


def test_pool_selection_skips_already_assigned_backend_gated_battle() -> None:
    service = _prepopulation_service()
    pooled = _battle(
        metadata_json={
            "pre_generated": True,
            "prepopulation_job_id": str(uuid.uuid4()),
            "pooled_replay": {
                "backend_gated": True,
                "display_delay_ms": 12_345,
                "assigned_user_id": str(uuid.uuid4()),
            },
        }
    )
    db = _BattleDB(battle=pooled, runs=[_run("A"), _run("B")])

    selected = service.select_eligible_pool_battle(
        db,
        now=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
        settings=_settings(),
    )

    assert selected is None


def test_claim_pool_battle_skips_active_assignment_for_other_user() -> None:
    service = _prepopulation_service()
    now = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
    pooled = _battle(
        metadata_json={
            "pre_generated": True,
            "prepopulation_job_id": str(uuid.uuid4()),
            "pooled_replay": {
                "backend_gated": True,
                "display_delay_ms": 12_345,
                "assigned_user_id": str(uuid.uuid4()),
                "assigned_at": (now - timedelta(minutes=1)).isoformat(),
                "expires_at": (now + timedelta(minutes=10)).isoformat(),
                "source": "admin_pre_generated",
                "unlocked": False,
            },
        }
    )
    db = _BattleDB(battle=pooled, runs=[_run("A"), _run("B")])

    selection = service.claim_eligible_pool_battle(
        db,
        _authenticated_principal(),
        settings=_settings(),
        now=now,
    )

    assert selection is None
    assert db.commit_calls == 0


def test_claim_pool_battle_reclaims_expired_assignment() -> None:
    service = _prepopulation_service()
    now = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
    principal = _authenticated_principal()
    previous_user_id = str(uuid.uuid4())
    pooled = _battle(
        metadata_json={
            "pre_generated": True,
            "prepopulation_job_id": str(uuid.uuid4()),
            "pooled_replay": {
                "backend_gated": True,
                "display_delay_ms": 12_345,
                "assigned_user_id": previous_user_id,
                "assigned_at": (now - timedelta(hours=1)).isoformat(),
                "expires_at": (now - timedelta(seconds=1)).isoformat(),
                "source": "admin_pre_generated",
                "unlocked": False,
            },
        }
    )
    db = _BattleDB(battle=pooled, runs=[_run("A"), _run("B")])

    selection = service.claim_eligible_pool_battle(
        db,
        principal,
        settings=_settings(),
        now=now,
    )

    assert selection is not None
    assert selection.battle.id == pooled.id
    replay = pooled.metadata_json["pooled_replay"]
    assert replay["assigned_user_id"] == principal.user_id
    assert replay["assigned_at"] == now.isoformat()
    assert replay["expires_at"] == (now + timedelta(seconds=900)).isoformat()
    assert replay["backend_gated"] is True
    assert replay["unlocked"] is False
    assert db.commit_calls == 1


def test_unlocked_pool_replay_is_consumed_not_reclaimed() -> None:
    service = _prepopulation_service()
    now = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
    assigned_user_id = str(uuid.uuid4())
    pooled = _battle(
        metadata_json={
            "pre_generated": True,
            "prepopulation_job_id": str(uuid.uuid4()),
            "pooled_replay": {
                "backend_gated": True,
                "display_delay_ms": 12_345,
                "assigned_user_id": assigned_user_id,
                "assigned_at": (now - timedelta(hours=1)).isoformat(),
                "expires_at": (now - timedelta(seconds=1)).isoformat(),
                "source": "admin_pre_generated",
                "unlocked": True,
            },
        }
    )
    db = _BattleDB(battle=pooled, runs=[_run("A"), _run("B")])

    selection = service.claim_eligible_pool_battle(
        db,
        _authenticated_principal(),
        settings=_settings(),
        now=now,
    )

    assert selection is None
    assert pooled.metadata_json["pooled_replay"]["assigned_user_id"] == assigned_user_id
    assert db.commit_calls == 0


def test_direct_claim_reuses_active_assignment_for_same_user_without_overwrite() -> None:
    service = _prepopulation_service()
    now = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
    principal = _authenticated_principal()
    assigned_at = now - timedelta(minutes=1)
    expires_at = now + timedelta(minutes=10)
    pooled = _battle(
        metadata_json={
            "pre_generated": True,
            "prepopulation_job_id": str(uuid.uuid4()),
            "pooled_replay": {
                "backend_gated": True,
                "display_delay_ms": 12_345,
                "assigned_user_id": principal.user_id,
                "assigned_at": assigned_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "source": "admin_pre_generated",
                "unlocked": False,
            },
        }
    )
    db = _BattleDB(battle=pooled, runs=[_run("A"), _run("B")])

    selection = service.claim_pool_battle_for_principal(
        db,
        pooled.id,
        principal,
        False,
        settings=_settings(),
        now=now,
    )

    assert selection is not None
    assert selection.display_delay_ms == 12_345
    assert pooled.metadata_json["pooled_replay"]["assigned_at"] == assigned_at.isoformat()
    assert db.commit_calls == 0


def test_direct_claim_lock_query_refreshes_existing_identity() -> None:
    service = _prepopulation_service()
    battle_id = uuid.uuid4()

    class _RealSessionLike(Session):
        def __init__(self) -> None:
            self.statement: object | None = None

        def execute(self, statement: object) -> _RowsResult:  # type: ignore[override]
            self.statement = statement
            return _RowsResult([])

    db = _RealSessionLike()

    assert service._get_pool_battle_for_update(db, battle_id) is None
    assert db.statement is not None
    assert db.statement.get_execution_options().get("populate_existing") is True


def test_user_created_completed_unvoted_battle_enters_pool_after_24_hours() -> None:
    service = _prepopulation_service()
    now = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
    recycled = _battle(created_at=now - timedelta(hours=24, seconds=1))
    too_new = _battle(created_at=now - timedelta(hours=23, minutes=59))

    assert service.is_battle_pool_eligible(
        recycled,
        has_vote=False,
        now=now,
        settings=_settings(),
    ) == "user_recycled"
    assert service.is_battle_pool_eligible(
        too_new,
        has_vote=False,
        now=now,
        settings=_settings(),
    ) is None


def test_bot_created_completed_unvoted_battle_enters_human_pool_after_recycle_delay() -> None:
    service = _prepopulation_service()
    now = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
    recycled = _battle(created_at=now - timedelta(hours=24, seconds=1))
    recycled.requester_service_account_id = uuid.uuid4()

    assert service.is_battle_pool_eligible(
        recycled,
        has_vote=False,
        consumer_type="human",
        now=now,
        settings=_settings(),
    ) == "user_recycled"


@pytest.mark.parametrize("status", ["pending", "running", "failed"])
def test_pool_selection_requires_completed_unvoted_battles(status: str) -> None:
    service = _prepopulation_service()
    candidate = _battle(
        status=status,
        metadata_json={"pre_generated": True, "prepopulation_job_id": str(uuid.uuid4())},
    )

    assert service.is_battle_pool_eligible(
        candidate,
        has_vote=False,
        now=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
        settings=_settings(),
    ) is None


def test_voted_battle_is_not_selected_from_pool() -> None:
    service = _prepopulation_service()
    candidate = _battle(
        metadata_json={"pre_generated": True, "prepopulation_job_id": str(uuid.uuid4())}
    )

    assert service.is_battle_pool_eligible(
        candidate,
        has_vote=True,
        now=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
        settings=_settings(),
    ) is None

    selection = service.select_eligible_pool_battle(
        _BattleDB(
            battle=candidate,
            existing_votes=[SimpleNamespace(battle_id=candidate.id)],
        ),
        now=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
        settings=_settings(),
    )

    assert selection is None


def test_voted_battle_is_counted_consumed_not_available_in_pool_stats() -> None:
    service = _prepopulation_service()
    unvoted = Battle(
        id=uuid.uuid4(),
        task_id=uuid.uuid4(),
        mode="jp2zh_ab",
        status="completed",
        metadata_json={
            "pre_generated": True,
            "prepopulation_job_id": str(uuid.uuid4()),
        },
        created_at=datetime(2026, 5, 26, 11, 0, tzinfo=timezone.utc),
    )
    voted = Battle(
        id=uuid.uuid4(),
        task_id=uuid.uuid4(),
        mode="jp2zh_ab",
        status="completed",
        metadata_json={
            "pre_generated": True,
            "prepopulation_job_id": str(uuid.uuid4()),
        },
        created_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
    )
    db = _BattleDB(
        battle=unvoted,
        execute_rows=[[unvoted, voted], [], [0], [0]],
        existing_votes=[SimpleNamespace(battle_id=voted.id)],
    )

    stats = service.get_pool_stats(db, settings=_settings())  # type: ignore[arg-type]

    assert stats.available_admin_count == 1
    assert stats.available_total_count == 1
    assert stats.voted_consumed_count == 1
    assert stats.total_count == 2


def test_live_fallback_creates_battle_when_no_prepopulation_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _prepopulation_service()
    monkeypatch.setattr(service, "claim_eligible_pool_battle", lambda *_a, **_kw: None)
    monkeypatch.setattr(battles, "_enforce_auth_battle_rate_limit", lambda **_kw: None)
    monkeypatch.setattr(battles, "_enforce_daily_vote_cap", lambda **_kw: None)
    monkeypatch.setattr(battles, "_select_task", lambda **_kw: _task())
    monkeypatch.setattr(battles, "_select_model_pair", lambda *_a, **_kw: (uuid.uuid4(), uuid.uuid4()))

    response = battles.create_battle(
        payload=BattleCreate(),
        request=SimpleNamespace(),
        db=_BattleDB(),  # type: ignore[arg-type]
        principal=_authenticated_principal(),
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response.status == "pending"
    assert response.prepopulation is None


def test_create_battle_prefers_completed_unvoted_pool_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _prepopulation_service()
    principal = _authenticated_principal()
    creator_id = str(uuid.uuid4())
    pooled = _battle(
        requester_user_id=creator_id,
        metadata_json={"pre_generated": True, "prepopulation_job_id": str(uuid.uuid4())},
    )
    runs = [_run("A"), _run("B")]
    db = _BattleDB(battle=pooled, runs=runs)

    def _claim_pool(_db: object, claim_principal: object, **_kw: object) -> object:
        metadata = dict(pooled.metadata_json)
        metadata["pooled_replay"] = service.build_pooled_replay_metadata(
            principal=claim_principal,
            source="admin_pre_generated",
            display_delay_ms=12_345,
            settings=_settings(),
            now=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
        )
        pooled.metadata_json = metadata
        db.commit()
        return service.PoolBattleSelection(
            battle=pooled,
            source="admin_pre_generated",
            display_delay_ms=12_345,
        )

    monkeypatch.setattr(service, "claim_eligible_pool_battle", _claim_pool)
    monkeypatch.setattr(battles, "_enforce_auth_battle_rate_limit", lambda **_kw: None)
    monkeypatch.setattr(battles, "_enforce_daily_vote_cap", lambda **_kw: None)
    monkeypatch.setattr(
        battles,
        "_select_task",
        lambda **_kw: pytest.fail("pooled create must skip live task selection"),
    )
    monkeypatch.setattr(
        battles,
        "_select_model_pair",
        lambda *_a, **_kw: pytest.fail("pooled create must skip live model selection"),
    )

    response = battles.create_battle(
        payload=BattleCreate(),
        request=SimpleNamespace(),
        db=db,  # type: ignore[arg-type]
        principal=principal,
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response.id == str(pooled.id)
    assert response.status == "completed"
    assert response.run_a is not None
    assert response.run_a.output_text is None
    assert response.run_b is not None
    assert response.run_b.output_text is None
    assert response.prepopulation is not None
    assert response.prepopulation.source == "admin_pre_generated"
    assert response.prepopulation.pooled is True
    assert response.prepopulation.display_delay_ms == 12_345
    assert response.prepopulation.backend_gated_replay is True
    assert pooled.metadata_json["pooled_replay"] == {
        "assigned_at": "2026-05-26T12:00:00+00:00",
        "expires_at": "2026-05-26T12:15:00+00:00",
        "assigned_user_id": principal.user_id,
        "assigned_service_account_id": None,
        "source": "admin_pre_generated",
        "display_delay_ms": 12_345,
        "backend_gated": True,
        "unlocked": False,
    }
    assert pooled.metadata_json["requester_user_id"] == creator_id
    assert db.added == []
    assert db.commit_calls == 1


def test_create_battle_explicit_task_payload_skips_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _prepopulation_service()
    monkeypatch.setattr(
        service,
        "claim_eligible_pool_battle",
        lambda *_a, **_kw: pytest.fail("explicit task payload must skip pool selection"),
    )
    monkeypatch.setattr(battles, "_enforce_auth_battle_rate_limit", lambda **_kw: None)
    monkeypatch.setattr(battles, "_enforce_daily_vote_cap", lambda **_kw: None)
    monkeypatch.setattr(battles, "_select_task", lambda **_kw: _task())
    monkeypatch.setattr(battles, "_select_model_pair", lambda *_a, **_kw: (uuid.uuid4(), uuid.uuid4()))

    response = battles.create_battle(
        payload=BattleCreate(task_id=str(uuid.uuid4())),
        request=SimpleNamespace(),
        db=_BattleDB(),  # type: ignore[arg-type]
        principal=_authenticated_principal(),
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response.status == "pending"


def test_pool_eligible_battle_allows_cross_user_access_until_vote() -> None:
    service = _prepopulation_service()
    creator_id = str(uuid.uuid4())
    non_creator = _authenticated_principal()
    pooled = _battle(
        requester_user_id=creator_id,
        metadata_json={"pre_generated": True, "prepopulation_job_id": str(uuid.uuid4())},
    )

    assert service.can_access_pool_battle(
        pooled,
        principal=non_creator,
        has_vote=False,
        now=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
        settings=_settings(),
    ) is True
    assert service.can_access_pool_battle(
        pooled,
        principal=non_creator,
        has_vote=True,
        now=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
        settings=_settings(),
    ) is False


def test_cross_user_access_reads_pool_battle_until_vote() -> None:
    creator_id = str(uuid.uuid4())
    non_creator = _authenticated_principal()
    pooled = _battle(
        requester_user_id=creator_id,
        metadata_json={"pre_generated": True, "prepopulation_job_id": str(uuid.uuid4())},
    )
    runs = [_run("A"), _run("B")]

    response = battles.get_battle(
        str(pooled.id),
        db=_BattleDB(battle=pooled, runs=runs, existing_votes=[]),  # type: ignore[arg-type]
        principal=non_creator,
    )

    assert response.id == str(pooled.id)
    assert response.run_a is not None
    assert response.run_a.output_text is None
    assert response.run_b is not None
    assert response.run_b.output_text is None
    assert response.prepopulation is not None
    assert response.prepopulation.backend_gated_replay is True
    assert pooled.metadata_json["pooled_replay"]["assigned_user_id"] == non_creator.user_id

    with pytest.raises(HTTPException) as exc_info:
        battles.get_battle(
            str(pooled.id),
            db=_BattleDB(
                battle=pooled,
                runs=runs,
                existing_votes=[SimpleNamespace(battle_id=pooled.id)],
            ),  # type: ignore[arg-type]
            principal=non_creator,
        )

    assert exc_info.value.status_code == 403


def test_stats_shape_includes_pool_and_job_fields() -> None:
    service = _prepopulation_service()

    stats = service.get_pool_stats(_BattleDB(), settings=_settings())  # type: ignore[arg-type]

    assert set(stats.model_dump()) >= {
        "available_admin_count",
        "available_recycled_count",
        "available_total_count",
        "generating_count",
        "failed_count",
        "voted_consumed_count",
        "total_count",
        "oldest_available_at",
        "newest_available_at",
        "latest_job",
        "max_job_size",
    }


def test_recent_jobs_shape_includes_progress_fields() -> None:
    service = _prepopulation_service()
    now = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
    job = BattlePrepopulationJob(
        id=uuid.uuid4(),
        requested_count=3,
        completed_count=1,
        failed_count=0,
        status="running",
        requested_by_user_id=uuid.uuid4(),
        model_ids=[],
        created_at=now,
        updated_at=now,
    )

    jobs = service.list_recent_jobs(_BattleDB(execute_rows=[[job]]), limit=20)  # type: ignore[arg-type]

    assert isinstance(jobs, list)
    assert set(jobs[0].model_dump()) >= {
        "id",
        "requested_count",
        "completed_count",
        "failed_count",
        "status",
        "model_ids",
        "created_at",
        "started_at",
        "finished_at",
        "last_error",
    }


def test_admin_prepopulation_endpoints_reject_non_admin() -> None:
    route = _admin_route()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            route.create_prepopulation_job(
                payload=SimpleNamespace(amount=3, model_ids=[]),
                db=_BattleDB(),  # type: ignore[arg-type]
                principal=_authenticated_principal(admin=False),
                settings=_settings(),  # type: ignore[arg-type]
            )
        )

    assert exc_info.value.status_code == 403


class _RecordingPrepopulationService:
    def __init__(self) -> None:
        self.inner = _prepopulation_service().BattlePrepopulationService()
        self.started: list[object] = []

    def create_job(self, *args: object, **kwargs: object) -> BattlePrepopulationJob:
        return self.inner.create_job(*args, **kwargs)

    def start_job(self, job_id: object, **_kwargs: object) -> None:
        self.started.append(job_id)


def _route_models() -> list[SimpleNamespace]:
    return [
        _model(model_id=uuid.uuid4(), model_name="model-a"),
        _model(model_id=uuid.uuid4(), model_name="model-b"),
    ]


def _post_jobs_route(route_module: object) -> APIRoute:
    route = next(
        item
        for item in route_module.router.routes
        if isinstance(item, APIRoute)
        and item.path == "/admin/battle-prepopulation/jobs"
        and "POST" in item.methods
    )
    return route


def test_admin_job_endpoint_creates_db_job_and_schedules_background_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = _admin_route()
    recording_service = _RecordingPrepopulationService()
    monkeypatch.setattr(
        route,
        "get_battle_prepopulation_service",
        lambda: recording_service,
    )
    db = _BattleDB(models=_route_models())

    response = asyncio.run(
        route.create_prepopulation_job(
            payload=route.BattlePrepopulationJobCreate(amount=3, model_ids=[]),
            db=db,  # type: ignore[arg-type]
            principal=_authenticated_principal(admin=True),
            settings=_settings(battle_prepopulation_max_job_size=10),  # type: ignore[arg-type]
        )
    )

    assert _post_jobs_route(route).status_code == 202
    assert response.requested_count == 3
    assert response.status == "pending"
    assert response.model_ids == []
    assert len(recording_service.started) == 1
    assert recording_service.started[0] == db.added[0].id
    assert db.commit_calls == 1


def test_admin_job_endpoint_accepts_one_and_two_model_constraints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = _admin_route()
    model_a, model_b = _route_models()

    for model_ids in ([str(model_a.id)], [str(model_a.id), str(model_b.id)]):
        recording_service = _RecordingPrepopulationService()
        monkeypatch.setattr(
            route,
            "get_battle_prepopulation_service",
            lambda service=recording_service: service,
        )
        db = _BattleDB(models=[model_a, model_b])

        response = asyncio.run(
            route.create_prepopulation_job(
                payload=route.BattlePrepopulationJobCreate(
                    amount=2,
                    model_ids=model_ids,
                ),
                db=db,  # type: ignore[arg-type]
                principal=_authenticated_principal(admin=True),
                settings=_settings(),  # type: ignore[arg-type]
            )
        )

        assert response.model_ids == model_ids
        assert len(recording_service.started) == 1


def test_admin_job_endpoint_rejects_disabled_setting_without_creating_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = _admin_route()
    recording_service = _RecordingPrepopulationService()
    monkeypatch.setattr(route, "get_battle_prepopulation_service", lambda: recording_service)
    db = _BattleDB(models=_route_models())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            route.create_prepopulation_job(
                payload=SimpleNamespace(amount=3, model_ids=[]),
                db=db,  # type: ignore[arg-type]
                principal=_authenticated_principal(admin=True),
                settings=_settings(battle_prepopulation_enabled=False),  # type: ignore[arg-type]
            )
        )

    assert exc_info.value.status_code == 503
    assert "disabled" in str(exc_info.value.detail).lower()
    assert db.added == []
    assert recording_service.started == []


@pytest.mark.parametrize("amount", [0, -1, 51])
def test_admin_job_endpoint_rejects_amount_constraints_without_creating_jobs(
    amount: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = _admin_route()
    recording_service = _RecordingPrepopulationService()
    monkeypatch.setattr(route, "get_battle_prepopulation_service", lambda: recording_service)
    db = _BattleDB(models=_route_models())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            route.create_prepopulation_job(
                payload=SimpleNamespace(amount=amount, model_ids=[]),
                db=db,  # type: ignore[arg-type]
                principal=_authenticated_principal(admin=True),
                settings=_settings(battle_prepopulation_max_job_size=50),  # type: ignore[arg-type]
            )
        )

    assert exc_info.value.status_code in {400, 422}
    assert "amount" in str(exc_info.value.detail).lower()
    assert db.added == []
    assert recording_service.started == []


@pytest.mark.parametrize(
    ("model_ids_case", "models_case", "expected_detail"),
    [
        ("duplicate", "valid", "distinct"),
        ("private", "private", "public"),
        ("disabled", "disabled", "disabled"),
        ("unknown", "unknown", "not found"),
        ("zero_infeasible", "one_public", "at least two"),
        ("one_infeasible", "one_public", "distinct"),
    ],
)
def test_admin_job_endpoint_rejects_model_constraints_without_creating_jobs(
    model_ids_case: str,
    models_case: str,
    expected_detail: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = _admin_route()
    selected = uuid.uuid4()
    other = uuid.uuid4()
    selected_ids = {
        "duplicate": [str(selected), str(selected)],
        "private": [str(selected)],
        "disabled": [str(selected)],
        "unknown": [str(selected)],
        "zero_infeasible": [],
        "one_infeasible": [str(selected)],
    }[model_ids_case]
    models = {
        "valid": [
            _model(model_id=selected, model_name="model-a"),
            _model(model_id=other, model_name="model-b"),
        ],
        "private": [_model(model_id=selected, visibility="private")],
        "disabled": [_model(model_id=selected, enabled=False)],
        "unknown": [_model(model_id=other, model_name="model-b")],
        "one_public": [_model(model_id=selected, model_name="model-a")],
    }[models_case]
    recording_service = _RecordingPrepopulationService()
    monkeypatch.setattr(route, "get_battle_prepopulation_service", lambda: recording_service)
    db = _BattleDB(models=models)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            route.create_prepopulation_job(
                payload=SimpleNamespace(amount=3, model_ids=selected_ids),
                db=db,  # type: ignore[arg-type]
                principal=_authenticated_principal(admin=True),
                settings=_settings(),  # type: ignore[arg-type]
            )
        )

    assert exc_info.value.status_code in {400, 422}
    assert expected_detail in str(exc_info.value.detail).lower()
    assert db.added == []
    assert recording_service.started == []


def test_admin_prepopulation_stats_endpoint_returns_service_stats() -> None:
    route = _admin_route()

    stats = route.get_prepopulation_stats(
        db=_BattleDB(),  # type: ignore[arg-type]
        principal=_authenticated_principal(admin=True),
        settings=_settings(battle_prepopulation_max_job_size=7),  # type: ignore[arg-type]
    )

    assert stats.max_job_size == 7
    assert set(stats.model_dump()) >= {
        "available_admin_count",
        "available_recycled_count",
        "available_total_count",
        "generating_count",
        "failed_count",
        "voted_consumed_count",
        "total_count",
        "latest_job",
    }


def test_admin_prepopulation_jobs_endpoint_returns_jobs_object_and_clamps_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = _admin_route()
    captured_limits: list[int] = []

    def _fake_recent_jobs(_db: object, *, limit: int) -> list[object]:
        captured_limits.append(limit)
        return [SimpleNamespace(id="job-1")]

    monkeypatch.setattr(route, "list_recent_jobs", _fake_recent_jobs)

    response = route.list_prepopulation_jobs(
        limit=250,
        db=_BattleDB(),  # type: ignore[arg-type]
        principal=_authenticated_principal(admin=True),
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert captured_limits == [100]
    assert response == {"jobs": [SimpleNamespace(id="job-1")]}


def test_admin_prepopulation_model_options_return_enabled_public_models_only() -> None:
    route = _admin_route()
    public_model = _model(model_name="public-model")
    private_model = _model(model_name="private-model", visibility="private")
    disabled_model = _model(model_name="disabled-model", enabled=False)

    response = route.list_model_options(
        db=_BattleDB(models=[public_model, private_model, disabled_model]),  # type: ignore[arg-type]
        principal=_authenticated_principal(admin=True),
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response == {
        "models": [
            route.BattlePrepopulationModelOptionPublic(
                id=str(public_model.id),
                display_name=public_model.display_name,
                model_name=public_model.model_name,
            )
        ]
    }


def test_admin_prepopulation_router_is_mounted_once() -> None:
    api_router = importlib.import_module("app.api.router")

    matches = [
        item
        for item in api_router.api_router.routes
        if getattr(item, "path", "").startswith("/admin/battle-prepopulation")
    ]

    assert matches
    assert len([item for item in matches if getattr(item, "path", "").endswith("/jobs")]) == 2


@pytest.mark.parametrize(
    "call_name",
    ["create", "stats", "jobs", "model_options"],
)
def test_admin_prepopulation_endpoints_forbidden_for_non_admin_principals(
    call_name: str,
) -> None:
    route = _admin_route()
    principal = _authenticated_principal(admin=False)

    with pytest.raises(HTTPException) as exc_info:
        if call_name == "create":
            asyncio.run(
                route.create_prepopulation_job(
                    payload=SimpleNamespace(amount=3, model_ids=[]),
                    db=_BattleDB(),  # type: ignore[arg-type]
                    principal=principal,
                    settings=_settings(),  # type: ignore[arg-type]
                )
            )
        elif call_name == "stats":
            route.get_prepopulation_stats(
                db=_BattleDB(),  # type: ignore[arg-type]
                principal=principal,
                settings=_settings(),  # type: ignore[arg-type]
            )
        elif call_name == "jobs":
            route.list_prepopulation_jobs(
                db=_BattleDB(),  # type: ignore[arg-type]
                principal=principal,
                settings=_settings(),  # type: ignore[arg-type]
            )
        else:
            route.list_model_options(
                db=_BattleDB(),  # type: ignore[arg-type]
                principal=principal,
                settings=_settings(),  # type: ignore[arg-type]
            )

    assert exc_info.value.status_code == 403


@pytest.mark.parametrize("selected_count", [0, 1, 2])
def test_model_constraint_accepts_zero_one_or_two_models(selected_count: int) -> None:
    service = _prepopulation_service()
    model_a_id = uuid.uuid4()
    model_b_id = uuid.uuid4()
    model_ids = [str(model_a_id), str(model_b_id)][:selected_count]
    eligible = [
        _model(model_id=model_a_id, model_name="model-a"),
        _model(model_id=model_b_id, model_name="model-b"),
    ]

    request = service.validate_prepopulation_job_request(
        amount=3,
        model_ids=model_ids,
        eligible_models=eligible,
        settings=_settings(),
    )

    assert request.amount == 3
    assert request.model_ids == model_ids


@pytest.mark.parametrize(
    ("randint_result", "expected_order"),
    [
        (0, "selected"),
        (1, "reversed"),
    ],
)
def test_two_selected_prepopulation_models_randomize_side_assignment(
    randint_result: int,
    expected_order: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _prepopulation_service()
    selected_1 = uuid.uuid4()
    selected_2 = uuid.uuid4()
    model_ids = [str(selected_1), str(selected_2)]
    db = _BattleDB(
        models=[
            _model(model_id=selected_1, model_name="selected-1"),
            _model(model_id=selected_2, model_name="selected-2"),
        ]
    )

    monkeypatch.setattr(service.random, "randint", lambda _start, _end: randint_result)

    pair = service._select_prepopulation_model_pair(
        db,  # type: ignore[arg-type]
        model_ids=model_ids,
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert model_ids == [str(selected_1), str(selected_2)]
    assert pair == {
        "selected": (selected_1, selected_2),
        "reversed": (selected_2, selected_1),
    }[expected_order]


@pytest.mark.parametrize(
    ("selected_ids", "eligible_models", "expected_details"),
    [
        ("duplicate", "duplicate", ("distinct", "duplicate")),
        ("private", "private", ("public", "private", "visibility")),
        ("disabled", "disabled", ("enabled", "disabled")),
        ("unknown", "unknown", ("not found", "unknown")),
    ],
)
def test_model_constraint_rejects_invalid_selected_models(
    selected_ids: str,
    eligible_models: str,
    expected_details: tuple[str, ...],
) -> None:
    service = _prepopulation_service()
    selected = uuid.uuid4()
    other = uuid.uuid4()

    selected_model_ids = {
        "duplicate": [str(selected), str(selected)],
        "private": [str(selected)],
        "disabled": [str(selected)],
        "unknown": [str(selected)],
    }[selected_ids]
    eligible = {
        "duplicate": [
            _model(model_id=selected, model_name="model-a"),
            _model(model_id=other, model_name="model-b"),
        ],
        "private": [_model(model_id=selected, visibility="private")],
        "disabled": [_model(model_id=selected, enabled=False)],
        "unknown": [_model(model_id=other, model_name="model-b")],
    }[eligible_models]

    with pytest.raises(HTTPException) as exc_info:
        service.validate_prepopulation_job_request(
            amount=3,
            model_ids=selected_model_ids,
            eligible_models=eligible,
            settings=_settings(),
        )

    assert exc_info.value.status_code in {400, 422}
    detail = str(exc_info.value.detail).lower()
    assert any(expected in detail for expected in expected_details)


def test_zero_model_constraint_rejects_fewer_than_two_eligible_models() -> None:
    service = _prepopulation_service()

    with pytest.raises(HTTPException) as exc_info:
        service.validate_prepopulation_job_request(
            amount=3,
            model_ids=[],
            eligible_models=[_model()],
            settings=_settings(),
        )

    assert exc_info.value.status_code in {400, 422}
    assert "at least two" in str(exc_info.value.detail).lower()


def test_one_model_constraint_rejects_when_no_distinct_opponent_exists() -> None:
    service = _prepopulation_service()
    selected = uuid.uuid4()

    with pytest.raises(HTTPException) as exc_info:
        service.validate_prepopulation_job_request(
            amount=3,
            model_ids=[str(selected)],
            eligible_models=[_model(model_id=selected)],
            settings=_settings(),
        )

    assert exc_info.value.status_code in {400, 422}
    assert "distinct" in str(exc_info.value.detail).lower()


def test_second_human_vote_on_same_battle_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    creator_user_id = str(uuid.uuid4())
    battle = _battle(requester_user_id=creator_user_id)
    runs = [_run("A"), _run("B")]
    existing_vote = SimpleNamespace(
        id=uuid.uuid4(),
        battle_id=battle.id,
        winner="A",
        revealed=True,
    )
    db = _VoteDB(battle=battle, runs=runs, existing_votes=[existing_vote])

    monkeypatch.setattr(votes, "_enforce_auth_vote_rate_limit", lambda **_kw: None)

    with pytest.raises(HTTPException) as exc_info:
        votes.submit_vote(
            battle_id=str(battle.id),
            payload=VoteCreate(winner="B"),
            response=SimpleNamespace(status_code=201),
            db=db,  # type: ignore[arg-type]
            principal=_authenticated_principal(user_id=creator_user_id),
            settings=_settings(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Battle already has a vote"
