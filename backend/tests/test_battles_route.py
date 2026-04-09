from __future__ import annotations

from types import SimpleNamespace
import uuid

from fastapi import HTTPException, Request
import pytest
from pydantic import ValidationError

from app.api.routes import battles
from app.models.task import Task
from app.schemas.battles import BattleCreate


class _Result:
    def __init__(self, rows: list[object]) -> None:
        self._rows = list(rows)

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[object]:
        return list(self._rows)


class _QueueDB:
    def __init__(
        self,
        result_sets: list[list[object]],
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
        "trust_x_forwarded_for": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_battle_create_rejects_mode_longer_than_db_limit() -> None:
    with pytest.raises(ValidationError) as exc_info:
        BattleCreate(mode="x" * 65)

    # mode is a Literal type; any invalid value triggers a literal_error.
    assert "literal_error" in str(exc_info.value) or "Input should be" in str(
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
    db = _QueueDB([])

    with pytest.raises(HTTPException) as exc_info:
        battles._select_task(db=db, payload=BattleCreate(task_id="bad-id"))  # type: ignore[arg-type]

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Invalid task_id"


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


def test_select_task_uses_inverse_battle_count_weights(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_a = _task()
    task_b = _task()
    db = _QueueDB(
        [
            [task_a, task_b],
            [(task_a.id, 3)],
        ]
    )

    captured: dict[str, object] = {}

    def fake_choices(
        population: list[SimpleNamespace],
        *,
        weights: list[float],
        k: int,
    ) -> list[SimpleNamespace]:
        captured["population"] = population
        captured["weights"] = weights
        captured["k"] = k
        return [population[1]]

    class _MockRng:
        def choices(self, population, *, weights, k):
            return fake_choices(population, weights=weights, k=k)

    monkeypatch.setattr(battles.random, "Random", lambda _seed: _MockRng())

    selected = battles._select_task(db=db, payload=BattleCreate())  # type: ignore[arg-type]

    assert selected is task_b
    assert captured["population"] == [task_a, task_b]
    assert captured["k"] == 1
    assert captured["weights"] == pytest.approx([0.25, 1.0])


def test_select_task_applies_task_set_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    task = _task()
    task_set_id = uuid.uuid4()
    db = _QueueDB([[task], []])

    class _MockRng:
        def choices(self, population, *, weights, k):
            return [population[0]]

    monkeypatch.setattr(
        battles.random,
        "Random",
        lambda _seed: _MockRng(),
    )

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

    candidates = captured["candidates"]
    assert [candidate.model_name for candidate in candidates] == ["model-a", "model-b"]
    assert [candidate.games_played for candidate in candidates] == [5, 1]

    policy = captured["policy"]
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

    assert battles._battle_task_snapshot(battle) == ("JP text", "ja", "zh")


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
    assert battles._battle_task_snapshot(battle) is None


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
        settings=_settings(),
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
            settings=settings,
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "Too many anonymous battle creation requests"
    assert exc_info.value.headers == {"Retry-After": "45"}
