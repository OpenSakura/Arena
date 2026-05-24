from __future__ import annotations

import asyncio
from collections.abc import Iterator, Sequence
from types import SimpleNamespace
from typing import cast
import uuid

from fastapi import HTTPException, Request, Response
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.api.routes import battles, bot_battles
from app.core.config import Settings
from app.core.security import Principal
from app.db.base import Base
import app.models  # noqa: F401
from app.models.battle import Battle
from app.models.model_registry import Model
from app.models.service_account import ServiceAccount
from app.models.task import Task
from app.models.user import User
from app.schemas.battles import BattleCreate
from app.schemas.bot import BotBattleCreateAndWaitRequest
from app.services.sampling import CandidateModel, SamplingPolicy


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type: JSONB, _compiler: object, **_kw: object) -> str:
    return "JSON"


class _Result:
    def __init__(self, rows: Sequence[object]) -> None:
        self._rows = list(rows)

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[object]:
        return list(self._rows)

    def scalar_one_or_none(self) -> object | None:
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> object:
        assert len(self._rows) == 1
        return self._rows[0]


class _QueueDB:
    def __init__(
        self,
        result_sets: Sequence[Sequence[object]],
        *,
        get_map: dict[tuple[type[object], uuid.UUID], object] | None = None,
    ) -> None:
        self._result_sets = [list(items) for items in result_sets]
        self._get_map = get_map or {}
        self.statements: list[object] = []

    def get(self, model: type[object], key: uuid.UUID) -> object | None:
        return self._get_map.get((model, key))

    def execute(self, stmt: object) -> _Result:
        self.statements.append(stmt)
        assert self._result_sets, "Unexpected execute() call"
        return _Result(self._result_sets.pop(0))


def _task() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        source_text="JP text",
        source_lang="ja",
        target_lang="zh",
    )


def _request() -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


def _settings(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "battle_sampling_weights": {},
        "battle_targets": {},
        "battle_strict_targets": {},
        "battle_outage_models": [],
        "battle_sampling_boost_models": [],
        "trust_x_forwarded_for": False,
        "turnstile_secret_key": None,
        "turnstile_verify_url": "https://turnstile.example/siteverify",
        "leaderboard_refresh_daily_vote_cap": 0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_battle_create_rejects_mode_longer_than_db_limit() -> None:
    with pytest.raises(ValidationError) as exc_info:
        BattleCreate(mode="x" * 65)

    assert "Unsupported mode" in str(exc_info.value) or "value_error" in str(
        exc_info.value
    )


def test_select_task_returns_explicit_task_id_when_present() -> None:
    task = _task()
    db = _QueueDB([], get_map={(Task, task.id): task})

    selected = battles._select_task(  # type: ignore[arg-type]
        db=db,
        payload=BattleCreate(task_id=str(task.id)),
    )

    assert selected is task
    assert db.statements == []


def test_select_task_rejects_invalid_task_id() -> None:
    """Invalid task_id is rejected at the schema level (UuidStr validation)."""
    with pytest.raises(ValidationError):
        BattleCreate(task_id="bad-id")


def test_select_task_returns_404_for_missing_explicit_task() -> None:
    task_id = uuid.uuid4()
    db = _QueueDB([], get_map={(Task, uuid.uuid4()): _task()})

    with pytest.raises(HTTPException) as exc_info:
        battles._select_task(  # type: ignore[arg-type]
            db=db,
            payload=BattleCreate(task_id=str(task_id)),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Task not found"


def test_select_task_raises_when_no_candidates_available() -> None:
    db = _QueueDB([[]])

    with pytest.raises(HTTPException) as exc_info:
        battles._select_task(db=db, payload=BattleCreate())  # type: ignore[arg-type]

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "No tasks available for battle"


def test_select_task_returns_result_from_weighted_query() -> None:
    task = _task()
    db = _QueueDB([[task]])

    selected = battles._select_task(db=db, payload=BattleCreate())  # type: ignore[arg-type]

    assert selected is task
    assert len(db.statements) == 1
    sql = str(db.statements[0]).lower()
    assert "ln(" in sql
    assert "greatest(random()" in sql
    assert "random() /" not in sql
    assert "coalesce" in sql


def test_select_task_applies_task_set_filter() -> None:
    """When task_set_id is provided, _select_task should issue a query
    (the filter is applied at the SQL level)."""
    task = _task()
    task_set_id = uuid.uuid4()
    db = _QueueDB([[task]])

    selected = battles._select_task(  # type: ignore[arg-type]
        db=db,
        payload=BattleCreate(task_set_id=str(task_set_id)),
    )

    assert selected is task
    assert "tasks.task_set_id" in str(db.statements[0]).lower()


def test_select_model_pair_builds_candidates_and_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    db = _QueueDB(
        [
            [
                (model_a, "model-a", 5),
                (model_b, "model-b", 1),
            ]
        ]
    )
    settings = _settings(
        battle_sampling_weights={"model-a": 2.5},
        battle_targets={"model-a": ["model-b"]},
        battle_strict_targets={"model-a": ["model-*"]},
        battle_outage_models=["model-z"],
        battle_sampling_boost_models=["model-b"],
    )

    captured: dict[str, object] = {}

    def fake_select_battle_pair(*, candidates, policy):
        captured["candidates"] = candidates
        captured["policy"] = policy
        return candidates[1].id, candidates[0].id

    monkeypatch.setattr(battles, "select_battle_pair", fake_select_battle_pair)

    pair = battles._select_model_pair(db=db, settings=settings)  # type: ignore[arg-type]

    assert pair == (model_b, model_a)

    candidates = cast(list[CandidateModel], captured["candidates"])
    assert [candidate.model_name for candidate in candidates] == ["model-a", "model-b"]
    assert [candidate.games_played for candidate in candidates] == [5, 1]

    policy = cast(SamplingPolicy, captured["policy"])
    assert policy.weights == settings.battle_sampling_weights
    assert policy.targets == settings.battle_targets
    assert policy.strict_targets == settings.battle_strict_targets
    assert policy.outage_models == {"model-z"}
    assert policy.boost_models == {"model-b"}


def test_select_model_pair_requires_at_least_two_models() -> None:
    db = _QueueDB([[(uuid.uuid4(), "model-a", 0)]])

    with pytest.raises(HTTPException) as exc_info:
        battles._select_model_pair(db=db, settings=_settings())  # type: ignore[arg-type]

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "At least two public models are required"


def test_select_model_pair_translates_sampling_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _QueueDB([[(uuid.uuid4(), "model-a", 0), (uuid.uuid4(), "model-b", 0)]])

    def _raise(*, candidates, policy):
        _ = (candidates, policy)
        raise ValueError("sampling failed")

    monkeypatch.setattr(battles, "select_battle_pair", _raise)

    with pytest.raises(HTTPException) as exc_info:
        battles._select_model_pair(db=db, settings=_settings())  # type: ignore[arg-type]

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "No valid model pair available for sampling"


def test_battle_task_snapshot_reads_valid_metadata() -> None:
    battle = SimpleNamespace(
        metadata_json={
            "task_snapshot": {
                "source_text": "JP text",
                "source_lang": "ja",
                "target_lang": "zh",
            }
        }
    )

    assert battles._battle_task_snapshot(cast(Battle, battle)) == (
        "JP text",
        "ja",
        "zh",
    )


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"task_snapshot": None},
        {"task_snapshot": {"source_text": "JP"}},
        {
            "task_snapshot": {
                "source_text": "JP",
                "source_lang": "ja",
                "target_lang": 123,
            }
        },
    ],
)
def test_battle_task_snapshot_returns_none_for_invalid_payloads(
    metadata: object,
) -> None:
    battle = SimpleNamespace(metadata_json=metadata)
    assert battles._battle_task_snapshot(cast(Battle, battle)) is None


class _FakeScalar:
    """Mimics the result of ``db.execute(select(func.count(...))).scalar_one()``."""

    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _CountDB:
    """DB stub that returns a fixed count for execute()."""

    def __init__(self, count: int) -> None:
        self._count = count

    def execute(self, _stmt: object) -> "_FakeScalar":
        return _FakeScalar(self._count)


def _principal(
    *,
    authenticated: bool = False,
    user_id: str | None = None,
    claims: dict[str, object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        is_authenticated=authenticated,
        user_id=user_id,
        claims=claims or {},
    )


def _creator_principal_and_battle(
    *,
    task_id: uuid.UUID | None = None,
    status: str = "failed",
    metadata_json: dict[str, object] | None = None,
) -> tuple[Principal, SimpleNamespace]:
    user_id = str(uuid.uuid4())
    battle_metadata = metadata_json or {
        "task_snapshot": {
            "source_text": "JP text",
            "source_lang": "ja",
            "target_lang": "zh",
        },
        "requester_user_id": user_id,
    }
    battle = SimpleNamespace(
        id=uuid.uuid4(),
        task_id=task_id or uuid.uuid4(),
        mode="jp2zh_ab",
        status=status,
        metadata_json=battle_metadata,
    )
    principal = cast(
        Principal,
        _principal(authenticated=True, user_id=user_id),
    )
    return principal, battle


def _bot_principal() -> Principal:
    return Principal(
        is_authenticated=True,
        actor_type="bot",
        user_id=str(uuid.uuid4()),
        service_account_id=str(uuid.uuid4()),
        service_account_name="Auto Judge",
        token_id=str(uuid.uuid4()),
        scopes=("battle:create", "battle:read"),
    )


class _NoBattleDB:
    def get(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("bot principal should be rejected before DB lookup")

    def execute(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("bot principal should be rejected before DB lookup")


def test_human_battle_create_read_and_retry_routes_reject_bot_principals() -> None:
    bot_principal = _bot_principal()
    settings = cast(Settings, _settings())

    with pytest.raises(HTTPException) as create_exc:
        battles.create_battle(
            payload=BattleCreate(),
            request=_request(),
            db=_NoBattleDB(),  # type: ignore[arg-type]
            principal=bot_principal,
            settings=settings,
        )
    assert create_exc.value.status_code == 403
    assert create_exc.value.detail == "Bot principals cannot use human battle endpoints"

    with pytest.raises(HTTPException) as read_exc:
        battles.get_battle(
            str(uuid.uuid4()),
            db=_NoBattleDB(),  # type: ignore[arg-type]
            principal=bot_principal,
        )
    assert read_exc.value.status_code == 403
    assert read_exc.value.detail == "Bot principals cannot use human battle endpoints"

    with pytest.raises(HTTPException) as retry_exc:
        battles.retry_battle(
            str(uuid.uuid4()),
            request=_request(),
            db=_NoBattleDB(),  # type: ignore[arg-type]
            principal=bot_principal,
        )
    assert retry_exc.value.status_code == 403
    assert retry_exc.value.detail == "Bot principals cannot use human battle endpoints"


def test_human_battle_stream_route_rejects_bot_principals() -> None:
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            battles.stream_battle(
                str(uuid.uuid4()),
                request=_request(),
                db=_NoBattleDB(),  # type: ignore[arg-type]
                orchestrator=object(),  # type: ignore[arg-type]
                principal=_bot_principal(),
                settings=cast(Settings, _settings()),
            )
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Bot principals cannot use human battle endpoints"


def test_human_battle_stream_route_closes_db_dependencies_before_streaming() -> None:
    route = next(
        route
        for route in battles.router.routes
        if getattr(route, "name", None) == "stream_battle"
    )
    dependencies = route.dependant.dependencies

    db_dependency = next(dep for dep in dependencies if dep.name == "db")
    principal_dependency = next(dep for dep in dependencies if dep.name == "principal")
    principal_db_dependency = next(
        dep for dep in principal_dependency.dependencies if dep.name == "db"
    )

    assert db_dependency.call is battles.get_db
    assert db_dependency.scope == "function"
    assert principal_dependency.call is battles.get_principal_optional
    assert principal_dependency.scope == "function"
    assert principal_db_dependency.call is battles.get_db
    assert principal_db_dependency.scope == "function"


def test_enforce_daily_vote_cap_allows_when_disabled() -> None:
    """Cap <= 0 means disabled — should not raise."""
    battles._enforce_daily_vote_cap(
        db=_CountDB(999),  # type: ignore[arg-type]
        principal=cast(Principal, _principal()),
        settings=cast(Settings, _settings(leaderboard_refresh_daily_vote_cap=0)),
    )


def test_enforce_daily_vote_cap_allows_when_under_limit() -> None:
    battles._enforce_daily_vote_cap(
        db=_CountDB(2),  # type: ignore[arg-type]
        principal=cast(
            Principal, _principal(authenticated=True, user_id=str(uuid.uuid4()))
        ),
        settings=cast(
            Settings,
            _settings(leaderboard_refresh_daily_vote_cap=5),
        ),
    )


def test_enforce_daily_vote_cap_raises_429_at_limit() -> None:
    with pytest.raises(HTTPException) as exc_info:
        battles._enforce_daily_vote_cap(
            db=_CountDB(5),  # type: ignore[arg-type]
            principal=cast(
                Principal, _principal(authenticated=True, user_id=str(uuid.uuid4()))
            ),
            settings=cast(Settings, _settings(leaderboard_refresh_daily_vote_cap=5)),
        )

    assert exc_info.value.status_code == 429
    assert "Daily vote limit reached" in exc_info.value.detail
    assert "(5 votes per day)" in exc_info.value.detail


def test_enforce_daily_vote_cap_skips_unauthenticated_requests() -> None:
    battles._enforce_daily_vote_cap(
        db=_CountDB(999),  # type: ignore[arg-type]
        principal=cast(Principal, _principal(authenticated=False, user_id=None)),
        settings=cast(Settings, _settings(leaderboard_refresh_daily_vote_cap=1)),
    )


# ── _build_sampling_policy tests ──


def test_build_sampling_policy_uses_settings_values() -> None:
    settings = _settings(
        battle_sampling_weights={"env-model": 1.0},
        battle_targets={"env-model": ["rival"]},
        battle_strict_targets={"env-model": ["env-*"]},
        battle_outage_models=["env-outage"],
        battle_sampling_boost_models=["env-boost"],
    )

    policy = battles._build_sampling_policy(settings)  # type: ignore[arg-type]

    assert policy.weights == {"env-model": 1.0}
    assert policy.targets == {"env-model": ["rival"]}
    assert policy.strict_targets == {"env-model": ["env-*"]}
    assert policy.outage_models == {"env-outage"}
    assert policy.boost_models == {"env-boost"}


def test_get_battle_rejects_unauthenticated_completed_battle() -> None:
    battle_id = uuid.uuid4()
    task = _task()
    battle = SimpleNamespace(
        id=battle_id,
        task_id=task.id,
        mode="jp2zh_ab",
        status="completed",
        metadata_json=None,
    )
    db = _QueueDB(
        [],
        get_map={
            (battles.Battle, battle_id): battle,
            (Task, task.id): task,
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        battles.get_battle(
            str(battle_id),
            db=db,  # type: ignore[arg-type]
            principal=cast(Principal, _principal(authenticated=False, user_id=None)),
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Authentication required"


def test_get_battle_rejects_unauthenticated_non_completed_battle() -> None:
    battle_id = uuid.uuid4()
    task = _task()
    battle = SimpleNamespace(
        id=battle_id,
        task_id=task.id,
        mode="jp2zh_ab",
        status="running",
        metadata_json=None,
    )
    db = _QueueDB(
        [],
        get_map={
            (battles.Battle, battle_id): battle,
            (Task, task.id): task,
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        battles.get_battle(
            str(battle_id),
            db=db,  # type: ignore[arg-type]
            principal=cast(Principal, _principal(authenticated=False, user_id=None)),
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Authentication required"


def test_get_battle_rejects_authenticated_non_creator_non_admin() -> None:
    battle_id = uuid.uuid4()
    task = _task()
    battle = SimpleNamespace(
        id=battle_id,
        task_id=task.id,
        mode="jp2zh_ab",
        status="completed",
        metadata_json={"requester_user_id": str(uuid.uuid4())},
    )
    db = _QueueDB(
        [],
        get_map={
            (battles.Battle, battle_id): battle,
            (Task, task.id): task,
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        battles.get_battle(
            str(battle_id),
            db=db,  # type: ignore[arg-type]
            principal=cast(
                Principal,
                _principal(authenticated=True, user_id=str(uuid.uuid4())),
            ),
        )

    assert exc_info.value.status_code == 403
    assert (
        exc_info.value.detail
        == "Only the battle creator or an admin may access this battle"
    )


def test_get_battle_allows_creator_and_keeps_run_stats_hidden_before_vote() -> None:
    principal, battle = _creator_principal_and_battle(status="completed")
    task = _task()
    battle.task_id = task.id
    runs = [
        SimpleNamespace(
            id=uuid.uuid4(),
            side="A",
            output_text="Alpha",
            stats={"request_id": "req-a"},
            error_text=None,
        ),
        SimpleNamespace(
            id=uuid.uuid4(),
            side="B",
            output_text="Beta",
            stats={"request_id": "req-b"},
            error_text=None,
        ),
    ]
    db = _QueueDB(
        [[*runs], []],
        get_map={
            (battles.Battle, battle.id): battle,
            (Task, task.id): task,
        },
    )

    response = battles.get_battle(
        str(battle.id),
        db=db,  # type: ignore[arg-type]
        principal=principal,
    )

    assert response.id == str(battle.id)
    assert response.status == "completed"
    assert response.retry_allowed is False
    assert response.run_a is not None
    assert response.run_a.stats is None
    assert response.run_b is not None
    assert response.run_b.stats is None


def test_get_battle_allows_admin_reader() -> None:
    battle_id = uuid.uuid4()
    task = _task()
    admin_group_name = "arena-admins"
    battle = SimpleNamespace(
        id=battle_id,
        task_id=task.id,
        mode="jp2zh_ab",
        status="completed",
        metadata_json={"requester_user_id": str(uuid.uuid4())},
    )
    runs = [
        SimpleNamespace(
            id=uuid.uuid4(),
            side="A",
            output_text="Alpha",
            stats={"request_id": "req-a"},
            error_text=None,
        ),
        SimpleNamespace(
            id=uuid.uuid4(),
            side="B",
            output_text="Beta",
            stats={"request_id": "req-b"},
            error_text=None,
        ),
    ]
    db = _QueueDB(
        [[*runs], []],
        get_map={
            (battles.Battle, battle_id): battle,
            (Task, task.id): task,
        },
    )

    principal = cast(
        Principal,
        _principal(
            authenticated=True,
            user_id=str(uuid.uuid4()),
            claims={"groups": [admin_group_name]},
        ),
    )

    get_settings = battles.get_settings
    battles.get_settings = lambda: cast(  # type: ignore[assignment]
        Settings,
        SimpleNamespace(
            oidc_admin_group_claim="groups",
            oidc_admin_group_name=admin_group_name,
        ),
    )
    try:
        response = battles.get_battle(
            str(battle_id),
            db=db,  # type: ignore[arg-type]
            principal=principal,
        )
    finally:
        battles.get_settings = get_settings  # type: ignore[assignment]

    assert response.id == str(battle_id)
    assert response.status == "completed"


def test_get_battle_exposes_retry_allowed_for_failed_creator() -> None:
    principal, battle = _creator_principal_and_battle(status="failed")
    task = _task()
    battle.task_id = task.id
    runs = [
        SimpleNamespace(
            id=uuid.uuid4(),
            side="A",
            output_text=None,
            stats=None,
            error_text="failure",
        ),
        SimpleNamespace(
            id=uuid.uuid4(),
            side="B",
            output_text=None,
            stats=None,
            error_text="failure",
        ),
    ]
    db = _QueueDB(
        [[*runs], []],
        get_map={
            (battles.Battle, battle.id): battle,
            (Task, task.id): task,
        },
    )

    response = battles.get_battle(
        str(battle.id),
        db=db,  # type: ignore[arg-type]
        principal=principal,
    )

    assert response.status == "failed"
    assert response.retry_allowed is True


def test_get_battle_retry_allowed_is_false_when_failed_battle_has_vote() -> None:
    principal, battle = _creator_principal_and_battle(status="failed")
    task = _task()
    battle.task_id = task.id
    vote_id = uuid.uuid4()
    runs = [
        SimpleNamespace(
            id=uuid.uuid4(),
            side="A",
            output_text=None,
            stats=None,
            error_text="failure",
        ),
        SimpleNamespace(
            id=uuid.uuid4(),
            side="B",
            output_text=None,
            stats=None,
            error_text="failure",
        ),
    ]
    db = _QueueDB(
        [[*runs], [vote_id]],
        get_map={
            (battles.Battle, battle.id): battle,
            (Task, task.id): task,
        },
    )

    response = battles.get_battle(
        str(battle.id),
        db=db,  # type: ignore[arg-type]
        principal=principal,
    )

    assert response.status == "failed"
    assert response.retry_allowed is False


# ── retry_battle tests ──


class _RetryDB:
    """Minimal DB mock for retry_battle with get/execute/add/commit/refresh."""

    def __init__(
        self,
        *,
        battle: SimpleNamespace,
        runs: list[SimpleNamespace],
        vote_ids: list[object] | None = None,
        task: SimpleNamespace | None = None,
    ) -> None:
        self._battle = battle
        self._runs = runs
        self._vote_ids = vote_ids or []
        self._task = task
        self._execute_count = 0
        self.added: list[object] = []
        self.committed = False
        self.refreshed: list[object] = []
        self._battle_lookup_count = 0

    def get(self, model: type[object], key: uuid.UUID) -> object | None:
        if model is Task and self._task is not None and key == self._task.id:
            return self._task
        if model is battles.Battle and key == self._battle.id:
            return self._battle
        return None

    def execute(self, _stmt: object) -> _Result:
        if "FROM battles" in str(_stmt):
            self._battle_lookup_count += 1
            if self._battle_lookup_count > 1 and self._battle.status != "failed":
                return _Result(
                    [
                        SimpleNamespace(
                            id=self._battle.id,
                            task_id=self._battle.task_id,
                            mode=self._battle.mode,
                            status=self._battle.status,
                            metadata_json=self._battle.metadata_json,
                        )
                    ]
                )
            return _Result([self._battle])

        self._execute_count += 1
        if self._execute_count == 1:
            return _Result(self._vote_ids)
        if self._execute_count == 2:
            return _Result(self._runs)
        return _Result(self._runs)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.committed = True

    def refresh(self, obj: object) -> None:
        self.refreshed.append(obj)


class _RetryLockDB(_RetryDB):
    def __init__(
        self,
        *,
        battle: SimpleNamespace,
        runs: list[SimpleNamespace],
        vote_ids: list[object] | None = None,
        task: SimpleNamespace | None = None,
    ) -> None:
        super().__init__(battle=battle, runs=runs, vote_ids=vote_ids, task=task)
        self.retry_stmt: object | None = None

    def execute(self, stmt: object) -> _Result:
        if self.retry_stmt is None:
            self.retry_stmt = stmt
        return super().execute(stmt)


def _failed_battle(*, task_id: uuid.UUID | None = None) -> SimpleNamespace:
    _, battle = _creator_principal_and_battle(task_id=task_id, status="failed")
    return battle


def _stale_run(*, battle_id: uuid.UUID, side: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        battle_id=battle_id,
        side=side,
        output_text="stale output",
        output_text_raw="stale raw output",
        error_text="stale error",
        stats={"latency_ms": 123, "request_id": "old-req"},
        request_json={"model": "old-model", "messages": []},
        prompt_rendered={"system_prompt": "old prompt"},
    )


def test_retry_battle_clears_all_run_artifacts() -> None:
    principal, battle = _creator_principal_and_battle(status="failed")
    run_a = _stale_run(battle_id=battle.id, side="A")
    run_b = _stale_run(battle_id=battle.id, side="B")

    db = _RetryDB(battle=battle, runs=[run_a, run_b])
    battles.retry_battle(
        str(battle.id),
        request=_request(),
        db=db,  # type: ignore[arg-type]
        principal=principal,
    )

    for run in [run_a, run_b]:
        assert run.output_text is None
        assert run.output_text_raw is None
        assert run.error_text is None
        assert run.stats is None
        assert run.request_json is None
        assert run.prompt_rendered is None

    assert battle.status == "pending"
    assert db.committed


def test_retry_battle_resets_automatic_retry_budget() -> None:
    principal, battle = _creator_principal_and_battle(
        status="failed",
        metadata_json={
            "task_snapshot": {
                "source_text": "JP text",
                "source_lang": "ja",
                "target_lang": "zh",
            },
            "requester_user_id": "placeholder",
            "automatic_retry_count": 1,
        },
    )
    battle.metadata_json["requester_user_id"] = principal.user_id
    run_a = _stale_run(battle_id=battle.id, side="A")
    run_b = _stale_run(battle_id=battle.id, side="B")

    db = _RetryDB(battle=battle, runs=[run_a, run_b])
    result = battles.retry_battle(
        str(battle.id),
        request=_request(),
        db=db,  # type: ignore[arg-type]
        principal=principal,
    )

    assert battle.metadata_json["automatic_retry_count"] == 0
    assert result.status == "pending"


def test_retry_battle_rejects_non_failed_status() -> None:
    for status in ("pending", "running", "completed"):
        principal, battle = _creator_principal_and_battle(status=status)
        db = _RetryDB(battle=battle, runs=[])

        with pytest.raises(HTTPException) as exc_info:
            battles.retry_battle(
                str(battle.id),
                request=_request(),
                db=db,  # type: ignore[arg-type]
                principal=principal,
            )

        assert exc_info.value.status_code == 409
        assert "Only failed battles" in exc_info.value.detail


def test_retry_battle_rejects_when_vote_exists() -> None:
    principal, battle = _creator_principal_and_battle(status="failed")
    vote_id = uuid.uuid4()
    db = _RetryDB(battle=battle, runs=[], vote_ids=[vote_id])

    with pytest.raises(HTTPException) as exc_info:
        battles.retry_battle(
            str(battle.id),
            request=_request(),
            db=db,  # type: ignore[arg-type]
            principal=principal,
        )

    assert exc_info.value.status_code == 409
    assert "already has a vote" in exc_info.value.detail


def test_retry_battle_returns_404_for_missing_battle() -> None:
    class _MissingRetryDB:
        def execute(self, _stmt: object) -> _Result:
            return _Result([])

    db = _MissingRetryDB()
    principal, _battle = _creator_principal_and_battle(status="failed")

    with pytest.raises(HTTPException) as exc_info:
        battles.retry_battle(
            str(uuid.uuid4()),
            request=_request(),
            db=db,  # type: ignore[arg-type]
            principal=principal,
        )

    assert exc_info.value.status_code == 404


def test_retry_battle_returns_pending_battle_public() -> None:
    principal, battle = _creator_principal_and_battle(status="failed")
    run_a = _stale_run(battle_id=battle.id, side="A")
    run_b = _stale_run(battle_id=battle.id, side="B")

    db = _RetryDB(battle=battle, runs=[run_a, run_b])
    result = battles.retry_battle(
        str(battle.id),
        request=_request(),
        db=db,  # type: ignore[arg-type]
        principal=principal,
    )

    assert result.status == "pending"
    assert result.source_text == "JP text"
    assert result.retry_allowed is False
    assert result.run_a is not None
    assert result.run_a.output_text is None
    assert result.run_a.stats is None
    assert result.run_b is not None
    assert result.run_b.output_text is None


def test_retry_lock_uses_row_lock_select() -> None:
    principal, battle = _creator_principal_and_battle(status="failed")
    run_a = _stale_run(battle_id=battle.id, side="A")
    run_b = _stale_run(battle_id=battle.id, side="B")
    db = _RetryLockDB(battle=battle, runs=[run_a, run_b])

    battles.retry_battle(
        str(battle.id),
        request=_request(),
        db=db,  # type: ignore[arg-type]
        principal=principal,
    )

    assert db.retry_stmt is not None
    assert "for update" in str(db.retry_stmt).lower()


def test_retry_lock_serializes_retry_with_row_lock() -> None:
    principal, battle = _creator_principal_and_battle(status="failed")
    run_a = _stale_run(battle_id=battle.id, side="A")
    run_b = _stale_run(battle_id=battle.id, side="B")
    db = _RetryDB(battle=battle, runs=[run_a, run_b])

    first = battles.retry_battle(
        str(battle.id),
        request=_request(),
        db=db,  # type: ignore[arg-type]
        principal=principal,
    )
    assert first.status == "pending"

    with pytest.raises(HTTPException) as exc_info:
        battles.retry_battle(
            str(battle.id),
            request=_request(),
            db=db,  # type: ignore[arg-type]
            principal=principal,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Only failed battles can be retried"


def test_stats_visible_after_vote() -> None:
    principal, battle = _creator_principal_and_battle(status="completed")
    battle_id = battle.id
    task = _task()
    battle.task_id = task.id
    vote_id = uuid.uuid4()
    runs = [
        SimpleNamespace(
            id=uuid.uuid4(),
            side="A",
            output_text="Alpha",
            stats={"request_id": "req-a"},
            error_text=None,
        ),
        SimpleNamespace(
            id=uuid.uuid4(),
            side="B",
            output_text="Beta",
            stats={"request_id": "req-b"},
            error_text=None,
        ),
    ]
    db = _QueueDB(
        [[*runs], [vote_id]],
        get_map={
            (battles.Battle, battle_id): battle,
            (Task, task.id): task,
        },
    )

    response = battles.get_battle(
        str(battle_id),
        db=db,  # type: ignore[arg-type]
        principal=principal,
    )

    assert response.run_a is not None
    assert response.run_b is not None
    assert response.retry_allowed is False
    assert response.run_a.stats == {"request_id": "req-a"}
    assert response.run_b.stats == {"request_id": "req-b"}


def test_is_battle_creator_matches_authenticated_creator() -> None:
    user_id = str(uuid.uuid4())
    battle = SimpleNamespace(
        metadata_json={
            "requester_user_id": user_id,
        }
    )
    principal = SimpleNamespace(is_authenticated=True, user_id=user_id)

    assert battles._is_battle_creator(
        cast(Battle, battle),
        principal=cast(Principal, principal),
    )


def test_is_battle_creator_returns_false_for_non_creator() -> None:
    battle = SimpleNamespace(
        metadata_json={
            "requester_user_id": str(uuid.uuid4()),
        }
    )
    principal = SimpleNamespace(is_authenticated=True, user_id=str(uuid.uuid4()))

    assert not battles._is_battle_creator(
        cast(Battle, battle),
        principal=cast(Principal, principal),
    )


def test_turnstile_missing_token_is_rejected() -> None:
    settings = _settings(
        turnstile_secret_key="secret",
        turnstile_verify_url="https://turnstile.example/siteverify",
    )

    with pytest.raises(HTTPException) as exc_info:
        battles._verify_turnstile_or_raise(
            turnstile_token=None,
            request=_request(),
            settings=cast(Settings, settings),
        )

    assert exc_info.value.status_code == 400


def test_create_battle_records_authenticated_requester_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(battles, "_enforce_auth_battle_rate_limit", lambda **_kw: None)
    monkeypatch.setattr(battles, "_enforce_daily_vote_cap", lambda **_kw: None)

    task = _task()
    model_a_id = uuid.uuid4()
    model_b_id = uuid.uuid4()

    monkeypatch.setattr(battles, "_select_task", lambda **_kw: task)
    monkeypatch.setattr(
        battles, "_select_model_pair", lambda *_args, **_kw: (model_a_id, model_b_id)
    )

    added_objects: list[object] = []

    class _FakeDB:
        def add(self, obj: object) -> None:
            added_objects.append(obj)

        def add_all(self, objs: list[object]) -> None:
            added_objects.extend(objs)

        def flush(self) -> None:
            pass

        def commit(self) -> None:
            pass

        def refresh(self, obj: object) -> None:
            if isinstance(obj, Battle):
                obj.id = uuid.uuid4()

    principal = SimpleNamespace(is_authenticated=True, user_id=str(uuid.uuid4()))
    request = _request()
    settings = cast(Settings, _settings())

    result = battles.create_battle(
        payload=BattleCreate(),
        request=request,
        db=_FakeDB(),  # type: ignore[arg-type]
        principal=cast(Principal, principal),
        settings=settings,
    )

    battles_added = [o for o in added_objects if isinstance(o, Battle)]
    assert len(battles_added) == 1
    assert battles_added[0].metadata_json["requester_user_id"] == principal.user_id
    assert result.retry_allowed is False


@pytest.fixture()
def bot_battle_db(tmp_path) -> Iterator[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'bot-battles.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def _seed_bot_battle_context(db: Session, *, suffix: str) -> SimpleNamespace:
    service_account_id = uuid.uuid4()
    bot_user = User(
        oidc_issuer="system:service-account",
        oidc_sub=f"service-account:{service_account_id}",
        actor_type="bot",
    )
    db.add(bot_user)
    db.flush()

    service_account = ServiceAccount(
        id=service_account_id,
        name=f"Auto Judge {suffix}",
        bot_user_id=bot_user.id,
    )
    task = Task(source_text=f"原文 {suffix}", source_lang="ja", target_lang="zh")
    model_a = Model(
        display_name=f"Model A {suffix}",
        model_name=f"model-a-{suffix}",
        base_url="https://llm.example/v1",
    )
    model_b = Model(
        display_name=f"Model B {suffix}",
        model_name=f"model-b-{suffix}",
        base_url="https://llm.example/v1",
    )
    db.add_all([service_account, task, model_a, model_b])
    db.commit()

    return SimpleNamespace(
        service_account=service_account,
        bot_user=bot_user,
        task=task,
        model_a=model_a,
        model_b=model_b,
    )


def _bot_battle_principal(context: SimpleNamespace) -> Principal:
    return Principal(
        is_authenticated=True,
        actor_type="bot",
        user_id=str(context.bot_user.id),
        service_account_id=str(context.service_account.id),
        service_account_name=context.service_account.name,
        token_id=str(uuid.uuid4()),
        scopes=("battle:create", "battle:execute", "battle:read"),
    )


class _BotRouteOrchestrator:
    def __init__(self, db: Session, *, outcome: str) -> None:
        self._db = db
        self._outcome = outcome
        self.calls: list[tuple[uuid.UUID, int, str | None]] = []

    async def execute_battle_and_wait(
        self,
        battle_id: uuid.UUID,
        *,
        timeout_seconds: int,
        request_id: str | None = None,
    ) -> str:
        self.calls.append((battle_id, timeout_seconds, request_id))
        battle = self._db.get(Battle, battle_id)
        assert battle is not None
        runs = (
            self._db.execute(
                select(bot_battles.Run)
                .where(bot_battles.Run.battle_id == battle_id)
                .order_by(bot_battles.Run.side.asc())
            )
            .scalars()
            .all()
        )
        if self._outcome == "timeout":
            battle.status = "running"
            self._db.commit()
            return "timeout"
        if self._outcome == "timeout_completed":
            battle.status = "completed"
            for run in runs:
                run.output_text = f"{run.side} late output"
            self._db.commit()
            return "timeout"

        battle.status = "completed"
        for run in runs:
            run.output_text = f"{run.side} output"
        self._db.commit()
        return "completed"


def _patch_bot_battle_selection(
    monkeypatch: pytest.MonkeyPatch,
    context: SimpleNamespace,
) -> list[tuple[str, object]]:
    calls: list[tuple[str, object]] = []

    def fake_select_task(*, db: Session, payload: BattleCreate) -> Task:
        _ = db
        calls.append(("task", payload))
        return cast(Task, context.task)

    def fake_select_model_pair(
        db: Session,
        *,
        settings: Settings,
    ) -> tuple[uuid.UUID, uuid.UUID]:
        _ = (db, settings)
        calls.append(("models", settings))
        return context.model_a.id, context.model_b.id

    monkeypatch.setattr(bot_battles.human_battles, "_select_task", fake_select_task)
    monkeypatch.setattr(
        bot_battles.human_battles,
        "_select_model_pair",
        fake_select_model_pair,
    )
    return calls


def test_bot_create_and_wait_success_returns_completed_outputs_and_model_ids(
    bot_battle_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _seed_bot_battle_context(bot_battle_db, suffix="success")
    selection_calls = _patch_bot_battle_selection(monkeypatch, context)
    orchestrator = _BotRouteOrchestrator(bot_battle_db, outcome="completed")
    response = Response()

    result = asyncio.run(
        bot_battles.create_and_wait_battle(
            payload=BotBattleCreateAndWaitRequest(
                task_id=str(context.task.id),
                timeout_seconds=5,
            ),
            request=_request(),
            response=response,
            idempotency_key="task-7-success",
            db=bot_battle_db,
            principal=_bot_battle_principal(context),
            settings=cast(Settings, _settings()),
            orchestrator=cast(bot_battles.BattleOrchestrator, orchestrator),
        )
    )

    assert response.status_code == 200
    assert result.status == "completed"
    assert result.status_url == f"/api/v1/bot/battles/{result.battle_id}"
    assert result.result is not None
    assert result.result.run_a is not None
    assert result.result.run_a.output_text == "A output"
    assert result.result.run_a.model_id == str(context.model_a.id)
    assert result.result.run_b is not None
    assert result.result.run_b.output_text == "B output"
    assert result.result.run_b.model_id == str(context.model_b.id)
    assert not hasattr(result.result, "winner")
    assert not hasattr(result.result, "reveal")

    battle = bot_battle_db.get(Battle, uuid.UUID(result.battle_id))
    assert battle is not None
    assert battle.requester_service_account_id == context.service_account.id
    assert battle.idempotency_key == "task-7-success"
    assert battle.metadata_json == {
        "task_snapshot": {
            "source_text": context.task.source_text,
            "source_lang": "ja",
            "target_lang": "zh",
        },
        "sampling": {
            "task": "weighted_v1",
            "models": "fastchat_weighted_v2",
        },
        "requester_user_id": str(context.bot_user.id),
        "requester_service_account_id": str(context.service_account.id),
        "automatic_retry_count": 0,
    }

    assert [name for name, _ in selection_calls] == ["task", "models"]
    task_payload = cast(BattleCreate, selection_calls[0][1])
    assert task_payload.task_id == str(context.task.id)


def test_bot_create_and_wait_timeout_returns_202_without_partial_outputs(
    bot_battle_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _seed_bot_battle_context(bot_battle_db, suffix="timeout")
    _patch_bot_battle_selection(monkeypatch, context)
    orchestrator = _BotRouteOrchestrator(bot_battle_db, outcome="timeout")
    response = Response()

    result = asyncio.run(
        bot_battles.create_and_wait_battle(
            payload=BotBattleCreateAndWaitRequest(
                task_id=str(context.task.id),
                timeout_seconds=1,
            ),
            request=_request(),
            response=response,
            idempotency_key="task-7-timeout",
            db=bot_battle_db,
            principal=_bot_battle_principal(context),
            settings=cast(Settings, _settings()),
            orchestrator=cast(bot_battles.BattleOrchestrator, orchestrator),
        )
    )

    assert response.status_code == 202
    assert result.status == "timeout"
    assert result.status_url == f"/api/v1/bot/battles/{result.battle_id}"
    assert result.result is None

    runs = (
        bot_battle_db.execute(
            select(bot_battles.Run).where(
                bot_battles.Run.battle_id == uuid.UUID(result.battle_id)
            )
        )
        .scalars()
        .all()
    )
    assert [run.output_text for run in runs] == [None, None]


def test_bot_create_and_wait_timeout_suppresses_concurrent_completed_result(
    bot_battle_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _seed_bot_battle_context(bot_battle_db, suffix="late-timeout")
    _patch_bot_battle_selection(monkeypatch, context)
    orchestrator = _BotRouteOrchestrator(bot_battle_db, outcome="timeout_completed")
    response = Response()

    result = asyncio.run(
        bot_battles.create_and_wait_battle(
            payload=BotBattleCreateAndWaitRequest(
                task_id=str(context.task.id),
                timeout_seconds=1,
            ),
            request=_request(),
            response=response,
            idempotency_key="task-7-timeout-completed",
            db=bot_battle_db,
            principal=_bot_battle_principal(context),
            settings=cast(Settings, _settings()),
            orchestrator=cast(bot_battles.BattleOrchestrator, orchestrator),
        )
    )

    assert response.status_code == 202
    assert result.status == "timeout"
    assert result.status_url == f"/api/v1/bot/battles/{result.battle_id}"
    assert result.result is None

    battle = bot_battle_db.get(Battle, uuid.UUID(result.battle_id))
    assert battle is not None
    assert battle.status == "completed"
    runs = (
        bot_battle_db.execute(
            select(bot_battles.Run)
            .where(bot_battles.Run.battle_id == battle.id)
            .order_by(bot_battles.Run.side.asc())
        )
        .scalars()
        .all()
    )
    assert [run.output_text for run in runs] == ["A late output", "B late output"]


def test_bot_create_and_wait_repeated_idempotency_key_returns_existing_battle(
    bot_battle_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _seed_bot_battle_context(bot_battle_db, suffix="idempotent")
    _patch_bot_battle_selection(monkeypatch, context)
    orchestrator = _BotRouteOrchestrator(bot_battle_db, outcome="completed")
    principal = _bot_battle_principal(context)

    first = asyncio.run(
        bot_battles.create_and_wait_battle(
            payload=BotBattleCreateAndWaitRequest(task_id=str(context.task.id)),
            request=_request(),
            response=Response(),
            idempotency_key="same-key",
            db=bot_battle_db,
            principal=principal,
            settings=cast(Settings, _settings()),
            orchestrator=cast(bot_battles.BattleOrchestrator, orchestrator),
        )
    )
    second = asyncio.run(
        bot_battles.create_and_wait_battle(
            payload=BotBattleCreateAndWaitRequest(task_id=str(context.task.id)),
            request=_request(),
            response=Response(),
            idempotency_key="same-key",
            db=bot_battle_db,
            principal=principal,
            settings=cast(Settings, _settings()),
            orchestrator=cast(bot_battles.BattleOrchestrator, orchestrator),
        )
    )

    assert second.battle_id == first.battle_id
    assert second.result is not None
    assert second.result.run_a is not None
    assert second.result.run_a.output_text == "A output"
    assert len(bot_battle_db.execute(select(Battle)).scalars().all()) == 1


def test_bot_status_route_isolates_battles_by_service_account(
    bot_battle_db: Session,
) -> None:
    owner_context = _seed_bot_battle_context(bot_battle_db, suffix="owner")
    other_context = _seed_bot_battle_context(bot_battle_db, suffix="other")
    battle = Battle(
        task_id=owner_context.task.id,
        mode="jp2zh_ab",
        status="completed",
        requester_service_account_id=owner_context.service_account.id,
        metadata_json={
            "task_snapshot": {
                "source_text": owner_context.task.source_text,
                "source_lang": "ja",
                "target_lang": "zh",
            },
            "requester_user_id": str(owner_context.bot_user.id),
            "requester_service_account_id": str(owner_context.service_account.id),
        },
    )
    bot_battle_db.add(battle)
    bot_battle_db.flush()
    bot_battle_db.add_all(
        [
            bot_battles.Run(
                battle_id=battle.id,
                side="A",
                model_id=owner_context.model_a.id,
                output_text="A done",
            ),
            bot_battles.Run(
                battle_id=battle.id,
                side="B",
                model_id=owner_context.model_b.id,
                output_text="B done",
            ),
        ]
    )
    bot_battle_db.commit()

    with pytest.raises(HTTPException) as exc_info:
        bot_battles.get_bot_battle(
            str(battle.id),
            db=bot_battle_db,
            principal=_bot_battle_principal(other_context),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Battle not found"

    owner_response = bot_battles.get_bot_battle(
        str(battle.id),
        db=bot_battle_db,
        principal=_bot_battle_principal(owner_context),
    )
    assert owner_response.battle_id == str(battle.id)
    assert owner_response.status == "completed"
    assert owner_response.result is not None
    assert owner_response.result.run_a is not None
    assert owner_response.result.run_a.model_id == str(owner_context.model_a.id)


def test_bot_create_reloads_idempotent_battle_after_integrity_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_account_id = uuid.uuid4()
    user_id = uuid.uuid4()
    task = _task()
    existing = Battle(
        id=uuid.uuid4(),
        task_id=task.id,
        requester_service_account_id=service_account_id,
        idempotency_key="race-key",
    )
    monkeypatch.setattr(bot_battles.human_battles, "_select_task", lambda **_kw: task)
    monkeypatch.setattr(
        bot_battles.human_battles,
        "_select_model_pair",
        lambda *_args, **_kw: (uuid.uuid4(), uuid.uuid4()),
    )

    class _IntegrityRaceDB:
        def __init__(self) -> None:
            self.rollback_called = False
            self.added: list[object] = []

        def add(self, obj: object) -> None:
            self.added.append(obj)

        def add_all(self, objs: list[object]) -> None:
            self.added.extend(objs)

        def flush(self) -> None:
            for item in self.added:
                if isinstance(item, Battle) and item.id is None:
                    item.id = uuid.uuid4()

        def commit(self) -> None:
            raise IntegrityError("insert", {}, Exception("duplicate"))

        def rollback(self) -> None:
            self.rollback_called = True

        def execute(self, _stmt: object) -> _Result:
            return _Result([existing])

    fake_db = _IntegrityRaceDB()
    returned = bot_battles._create_bot_battle(
        db=cast(Session, fake_db),
        payload=BotBattleCreateAndWaitRequest(task_id=str(task.id)),
        principal=Principal(
            is_authenticated=True,
            actor_type="bot",
            user_id=str(user_id),
            service_account_id=str(service_account_id),
        ),
        service_account_id=service_account_id,
        idempotency_key="race-key",
        settings=cast(Settings, _settings()),
    )

    assert returned is existing
    assert fake_db.rollback_called is True


def test_bot_create_reloads_idempotent_battle_after_flush_integrity_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_account_id = uuid.uuid4()
    user_id = uuid.uuid4()
    task = _task()
    existing = Battle(
        id=uuid.uuid4(),
        task_id=task.id,
        requester_service_account_id=service_account_id,
        idempotency_key="flush-race-key",
    )
    monkeypatch.setattr(bot_battles.human_battles, "_select_task", lambda **_kw: task)
    monkeypatch.setattr(
        bot_battles.human_battles,
        "_select_model_pair",
        lambda *_args, **_kw: (uuid.uuid4(), uuid.uuid4()),
    )

    class _FlushIntegrityRaceDB:
        def __init__(self) -> None:
            self.rollback_called = False
            self.commit_called = False
            self.added: list[object] = []

        def add(self, obj: object) -> None:
            self.added.append(obj)

        def add_all(self, objs: list[object]) -> None:
            self.added.extend(objs)

        def flush(self) -> None:
            raise IntegrityError("flush", {}, Exception("duplicate"))

        def commit(self) -> None:
            self.commit_called = True

        def rollback(self) -> None:
            self.rollback_called = True

        def execute(self, _stmt: object) -> _Result:
            return _Result([existing])

    fake_db = _FlushIntegrityRaceDB()
    returned = bot_battles._create_bot_battle(
        db=cast(Session, fake_db),
        payload=BotBattleCreateAndWaitRequest(task_id=str(task.id)),
        principal=Principal(
            is_authenticated=True,
            actor_type="bot",
            user_id=str(user_id),
            service_account_id=str(service_account_id),
        ),
        service_account_id=service_account_id,
        idempotency_key="flush-race-key",
        settings=cast(Settings, _settings()),
    )

    assert returned is existing
    assert fake_db.rollback_called is True
    assert fake_db.commit_called is False
