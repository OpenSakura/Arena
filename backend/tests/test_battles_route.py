from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from typing import cast
import uuid

from fastapi import HTTPException, Request
import pytest
from pydantic import ValidationError

from app.api.routes import battles
from app.core.config import Settings
from app.core.security import Principal
from app.models.battle import Battle
from app.models.task import Task
from app.schemas.battles import BattleCreate
from app.services.sampling import CandidateModel, SamplingPolicy


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
        "anon_battle_create_rate_limit_window_seconds": 60,
        "anon_battle_stream_rate_limit_window_seconds": 60,
        "anon_ip_hash_salt": "ip-salt",
        "trust_x_forwarded_for": False,
        "turnstile_secret_key": None,
        "turnstile_verify_url": "https://turnstile.example/siteverify",
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
    """The current implementation uses a single SQL query with
    func.random() / (1 + battle_count) for weighted task selection.
    Test that the result is returned when available."""
    task = _task()
    db = _QueueDB([[task]])

    selected = battles._select_task(db=db, payload=BattleCreate())  # type: ignore[arg-type]

    assert selected is task
    # One query should have been issued.
    assert len(db.statements) == 1


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


def test_enforce_anon_battle_rate_limit_allows_request_below_threshold(
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
    monkeypatch.setattr(battles, "_get_battle_create_rate_limiter", lambda: limiter)

    def fake_key_builder(**kwargs: object) -> str:
        captured.update(kwargs)
        return "anon-key"

    monkeypatch.setattr(battles, "build_anon_rate_limit_key", fake_key_builder)

    battles._enforce_anon_battle_rate_limit(
        request=_request(),
        settings=cast(Settings, _settings()),
    )

    assert captured["scope"] == "anon_battle_create"
    assert limiter.seen_keys == ["anon-key"]


def test_enforce_anon_battle_rate_limit_raises_429_when_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Limiter:
        def is_limited(self, _key: str) -> bool:
            return True

    monkeypatch.setattr(battles, "_get_battle_create_rate_limiter", lambda: _Limiter())
    monkeypatch.setattr(battles, "build_anon_rate_limit_key", lambda **_kwargs: "anon")

    settings = _settings(anon_battle_create_rate_limit_window_seconds=45)

    with pytest.raises(HTTPException) as exc_info:
        battles._enforce_anon_battle_rate_limit(
            request=_request(),
            settings=cast(Settings, settings),
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "Too many anonymous battle creation requests"
    assert exc_info.value.headers == {"Retry-After": "45"}


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
    *, authenticated: bool = False, user_id: str | None = None
) -> SimpleNamespace:
    return SimpleNamespace(is_authenticated=authenticated, user_id=user_id)


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
        "requester_anon_id": None,
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


def test_enforce_daily_vote_cap_allows_when_disabled() -> None:
    """Cap <= 0 means disabled — should not raise."""
    battles._enforce_daily_vote_cap(
        db=_CountDB(999),  # type: ignore[arg-type]
        principal=cast(Principal, _principal()),
        request=_request(),
        settings=cast(Settings, _settings(leaderboard_refresh_daily_vote_cap=0)),
    )


def test_enforce_daily_vote_cap_allows_when_under_limit() -> None:
    battles._enforce_daily_vote_cap(
        db=_CountDB(2),  # type: ignore[arg-type]
        principal=cast(
            Principal, _principal(authenticated=True, user_id=str(uuid.uuid4()))
        ),
        request=_request(),
        settings=cast(
            Settings,
            _settings(
                leaderboard_refresh_daily_vote_cap=5,
                anon_user_agent_hash_salt="ua-salt",
            ),
        ),
    )


def test_enforce_daily_vote_cap_raises_429_at_limit() -> None:
    with pytest.raises(HTTPException) as exc_info:
        battles._enforce_daily_vote_cap(
            db=_CountDB(5),  # type: ignore[arg-type]
            principal=cast(
                Principal, _principal(authenticated=True, user_id=str(uuid.uuid4()))
            ),
            request=_request(),
            settings=cast(
                Settings,
                _settings(
                    leaderboard_refresh_daily_vote_cap=5,
                    anon_user_agent_hash_salt="ua-salt",
                ),
            ),
        )

    assert exc_info.value.status_code == 429
    assert "Daily vote limit reached" in exc_info.value.detail
    assert "(5 votes per day)" in exc_info.value.detail


def test_enforce_daily_vote_cap_anonymous_allows_when_unidentifiable() -> None:
    """When we can't identify the anonymous user, allow through (rate limiter
    catches truly anonymous traffic anyway)."""
    # Create a request with no cookies, no user-agent, and no client IP.
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [],
        "client": None,
        "server": ("testserver", 80),
    }
    req = Request(scope)

    # Should not raise even though the cap is 1, because the user is
    # unidentifiable — no anon_id cookie, no IP, no user-agent.
    battles._enforce_daily_vote_cap(
        db=_CountDB(999),  # type: ignore[arg-type]
        principal=cast(Principal, _principal()),
        request=req,
        settings=cast(
            Settings,
            _settings(
                leaderboard_refresh_daily_vote_cap=1,
                anon_user_agent_hash_salt="ua-salt",
            ),
        ),
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


def test_get_battle_keeps_run_stats_hidden() -> None:
    battle_id = uuid.uuid4()
    task = _task()
    battle = SimpleNamespace(
        id=battle_id,
        task_id=task.id,
        mode="jp2zh_ab",
        status="completed",
        metadata_json=None,
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

    response = battles.get_battle(str(battle_id), db=db)  # type: ignore[arg-type]

    assert response.run_a is not None
    assert response.run_b is not None
    assert response.run_a.stats is None
    assert response.run_b.stats is None


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

    with pytest.raises(HTTPException) as exc_info:
        battles.retry_battle(
            str(uuid.uuid4()),
            request=_request(),
            db=db,  # type: ignore[arg-type]
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
    battle_id = uuid.uuid4()
    task = _task()
    battle = SimpleNamespace(
        id=battle_id,
        task_id=task.id,
        mode="jp2zh_ab",
        status="completed",
        metadata_json=None,
    )
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

    response = battles.get_battle(str(battle_id), db=db)  # type: ignore[arg-type]

    assert response.run_a is not None
    assert response.run_b is not None
    assert response.run_a.stats == {"request_id": "req-a"}
    assert response.run_b.stats == {"request_id": "req-b"}


def _request_with_cookie(cookie_value: str) -> Request:
    cookie_header = f"arena_anon_id={cookie_value}".encode("latin-1")
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"cookie", cookie_header)],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


def test_is_battle_creator_uses_anon_id_from_metadata() -> None:
    anon_id = uuid.uuid4().hex
    battle = SimpleNamespace(
        metadata_json={
            "requester_user_id": None,
            "requester_anon_id": anon_id,
        }
    )
    principal = SimpleNamespace(is_authenticated=False, user_id=None)

    assert battles._is_battle_creator(
        cast(Battle, battle),
        principal=cast(Principal, principal),
        request=_request_with_cookie(anon_id),
    )


def test_is_battle_creator_returns_false_when_anon_id_mismatch() -> None:
    battle = SimpleNamespace(
        metadata_json={
            "requester_user_id": None,
            "requester_anon_id": uuid.uuid4().hex,
        }
    )
    principal = SimpleNamespace(is_authenticated=False, user_id=None)

    assert not battles._is_battle_creator(
        cast(Battle, battle),
        principal=cast(Principal, principal),
        request=_request_with_cookie(uuid.uuid4().hex),
    )


def test_is_battle_creator_returns_false_when_creator_anon_id_is_none() -> None:
    battle = SimpleNamespace(
        metadata_json={
            "requester_user_id": None,
            "requester_anon_id": None,
        }
    )
    principal = SimpleNamespace(is_authenticated=False, user_id=None)

    assert not battles._is_battle_creator(
        cast(Battle, battle),
        principal=cast(Principal, principal),
        request=_request_with_cookie(uuid.uuid4().hex),
    )


def test_turnstile_failure_does_not_consume_rate_limit_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limit_checked = []

    class _NeverCalledLimiter:
        def is_limited(self, key: str) -> bool:
            limit_checked.append(key)
            return False

    monkeypatch.setattr(
        battles, "_get_battle_create_rate_limiter", lambda: _NeverCalledLimiter()
    )
    monkeypatch.setattr(battles, "build_anon_rate_limit_key", lambda **_kw: "key")

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
    assert limit_checked == [], "rate limiter must not be called before Turnstile check"


def test_create_battle_anon_id_stored_from_get_or_set_return_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fresh_anon_id = uuid.uuid4().hex

    def fake_get_or_set_anon_id(**_kwargs: object) -> str:
        return fresh_anon_id

    monkeypatch.setattr(battles, "get_or_set_anon_id", fake_get_or_set_anon_id)
    monkeypatch.setattr(battles, "_verify_turnstile_or_raise", lambda **_kw: None)
    monkeypatch.setattr(battles, "_enforce_anon_battle_rate_limit", lambda **_kw: None)
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

    principal = SimpleNamespace(is_authenticated=False, user_id=None)
    request = _request()
    response_obj = SimpleNamespace(set_cookie=lambda **_kw: None)
    settings = cast(Settings, _settings(anon_id_cookie_secure=False))

    battles.create_battle(
        payload=BattleCreate(),
        request=request,
        response=response_obj,  # type: ignore[arg-type]
        db=_FakeDB(),  # type: ignore[arg-type]
        principal=cast(Principal, principal),
        settings=settings,
    )

    battles_added = [o for o in added_objects if isinstance(o, Battle)]
    assert len(battles_added) == 1
    stored_anon_id = battles_added[0].metadata_json["requester_anon_id"]
    assert stored_anon_id == fresh_anon_id, (
        "requester_anon_id must come from get_or_set_anon_id return value, "
        "not request.cookies (which would be None for a brand-new cookie)"
    )
