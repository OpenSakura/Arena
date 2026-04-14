from __future__ import annotations

from types import SimpleNamespace
import uuid

from fastapi import HTTPException  # pyright: ignore[reportMissingImports]
import pytest

from app.api.routes import leaderboard
from app.schemas.leaderboard import LeaderboardResponse
from app.services.leaderboard_bt import PairwiseVote


class _FakeRedis:
    """Minimal Redis mock with ``mget``/``incr``/``expire`` for the
    ``RollingWindowRateLimiter`` fallback path."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key: str, seconds: int) -> bool:
        _ = (key, seconds)
        return True

    def mget(self, keys: list[str]) -> list[int | None]:
        return [self.store.get(key) for key in keys]


class _FakeConfidenceRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: str, *, ex: int) -> bool:
        self.store[key] = value
        self.ttls[key] = ex
        return True


def _settings(**overrides: object) -> SimpleNamespace:
    base = {
        "leaderboard_refresh_daily_vote_cap": 3,
        "leaderboard_refresh_elo_k": 32.0,
        "leaderboard_elo_shuffle_rounds": 5,
        "leaderboard_elo_shuffle_seed": 13,
        "leaderboard_elo_bootstrap_rounds": 50,
        "leaderboard_elo_bootstrap_seed": 7,
        "leaderboard_elo_confidence_level": 0.95,
        "leaderboard_confidence_cache_ttl_seconds": 15,
        "leaderboard_confidence_rate_limit": 6,
        "leaderboard_confidence_rate_limit_window_seconds": 60,
        "leaderboard_bt_max_iterations": 100,
        "leaderboard_bt_tolerance": 1e-6,
        "leaderboard_bt_prior": 1.0,
        "leaderboard_bt_bootstrap_rounds": 40,
        "leaderboard_bt_bootstrap_seed": 11,
        "leaderboard_bt_confidence_level": 0.9,
        "rate_limit_redis_key_prefix": "test",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _Result:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class _QueueDB:
    def __init__(self, rows_by_call: list[list[tuple[object, ...]]]) -> None:
        self._rows_by_call = list(rows_by_call)
        self.statements: list[object] = []
        self.execute_calls = 0

    def execute(self, stmt: object) -> _Result:
        self.statements.append(stmt)
        self.execute_calls += 1
        if not self._rows_by_call:
            raise AssertionError("Unexpected execute() call")
        return _Result(self._rows_by_call.pop(0))


@pytest.fixture(autouse=True)
def _reset_confidence_query_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset shared-confidence cache dependencies before each test."""

    from app.utils.rate_limit import RollingWindowRateLimiter

    monkeypatch.setattr(leaderboard, "_get_confidence_cache_client", lambda: None)

    monkeypatch.setattr(
        leaderboard,
        "_get_confidence_rate_limiter",
        lambda: RollingWindowRateLimiter(
            limit=6,
            window_seconds=60,
            bucket_seconds=10,
            redis_client=_FakeRedis(),
            redis_prefix="test",
        ),
    )


def test_get_leaderboard_dispatches_to_bt_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = LeaderboardResponse(models=[], method="bt", ci=True)

    monkeypatch.setattr(leaderboard, "_get_leaderboard_bt", lambda **_: expected)
    monkeypatch.setattr(
        leaderboard,
        "_get_leaderboard_elo",
        lambda **_: pytest.fail("Elo handler should not be called"),
    )

    response = leaderboard.get_leaderboard(
        method="bt",
        include_confidence=True,
        db=object(),  # type: ignore[arg-type]
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response is expected


def test_get_leaderboard_dispatches_to_elo_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = LeaderboardResponse(models=[], method="elo", ci=False)

    monkeypatch.setattr(leaderboard, "_get_leaderboard_elo", lambda **_: expected)
    monkeypatch.setattr(
        leaderboard,
        "_get_leaderboard_bt",
        lambda **_: pytest.fail("BT handler should not be called"),
    )

    response = leaderboard.get_leaderboard(
        method="elo",
        include_confidence=False,
        db=object(),  # type: ignore[arg-type]
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response is expected


def test_get_leaderboard_confidence_uses_cache_for_repeated_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = LeaderboardResponse(models=[], method="elo", ci=True)
    calls = {"elo": 0}
    cache_client = _FakeConfidenceRedis()

    def fake_get_leaderboard_elo(**_kwargs: object) -> LeaderboardResponse:
        calls["elo"] += 1
        return expected

    monkeypatch.setattr(leaderboard, "_get_leaderboard_elo", fake_get_leaderboard_elo)
    monkeypatch.setattr(
        leaderboard,
        "_get_confidence_cache_client",
        lambda: cache_client,
    )

    settings = _settings(
        leaderboard_confidence_cache_ttl_seconds=30,
        leaderboard_confidence_rate_limit=20,
    )

    first = leaderboard.get_leaderboard(
        method="elo",
        include_confidence=True,
        db=object(),  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
    )
    second = leaderboard.get_leaderboard(
        method="elo",
        include_confidence=True,
        db=object(),  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
    )

    assert calls["elo"] == 1
    assert first == expected
    assert second == expected
    assert len(cache_client.store) == 1
    assert set(cache_client.ttls.values()) == {30}


def test_get_leaderboard_confidence_rate_limits_uncached_recomputes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that the Redis-backed rate limiter blocks repeated uncached
    confidence requests once the limit is exhausted."""

    from app.utils.rate_limit import RollingWindowRateLimiter

    # Create a single limiter instance (so state persists across calls).
    limiter = RollingWindowRateLimiter(
        limit=1,
        window_seconds=60,
        bucket_seconds=10,
        redis_client=_FakeRedis(),
        redis_prefix="test",
    )
    monkeypatch.setattr(
        leaderboard,
        "_get_confidence_rate_limiter",
        lambda: limiter,
    )

    monkeypatch.setattr(
        leaderboard,
        "_get_leaderboard_elo",
        lambda **_kwargs: LeaderboardResponse(models=[], method="elo", ci=True),
    )

    settings = _settings(
        leaderboard_confidence_cache_ttl_seconds=0,
        leaderboard_confidence_rate_limit=1,
        leaderboard_confidence_rate_limit_window_seconds=60,
    )

    leaderboard.get_leaderboard(
        method="elo",
        include_confidence=True,
        db=object(),  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
    )

    with pytest.raises(HTTPException) as exc_info:
        leaderboard.get_leaderboard(
            method="elo",
            include_confidence=True,
            db=object(),  # type: ignore[arg-type]
            settings=settings,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "Too many confidence leaderboard requests"


def test_get_leaderboard_elo_returns_empty_response_without_public_models() -> None:
    db = _QueueDB(rows_by_call=[[]])

    response = leaderboard._get_leaderboard_elo(
        db=db,  # type: ignore[arg-type]
        include_confidence=False,
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response.method == "elo"
    assert response.ci is False
    assert response.models == []
    assert db.execute_calls == 1


def test_get_leaderboard_elo_uses_persisted_ratings_without_confidence() -> None:
    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    db = _QueueDB(
        rows_by_call=[
            [(model_a, "Model A"), (model_b, "Model B")],
            [
                (model_b, "Model B", 1120.5, 42),
                (model_a, "Model A", 998.0, 10),
            ],
        ]
    )

    response = leaderboard._get_leaderboard_elo(
        db=db,  # type: ignore[arg-type]
        include_confidence=False,
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response.method == "elo"
    assert response.ci is False
    assert [row.display_name for row in response.models] == ["Model B", "Model A"]
    assert response.models[0].rating == 1120.5
    assert response.models[0].games_played == 42
    assert response.models[1].rating == 998.0
    assert response.models[1].games_played == 10


def test_get_leaderboard_elo_filters_disabled_models_in_queries() -> None:
    model_a = uuid.uuid4()
    db = _QueueDB(
        rows_by_call=[
            [(model_a, "Model A")],
            [(model_a, "Model A", 1000.0, 0)],
        ]
    )

    leaderboard._get_leaderboard_elo(
        db=db,  # type: ignore[arg-type]
        include_confidence=False,
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert len(db.statements) == 2
    first_stmt = str(db.statements[0]).lower()
    second_stmt = str(db.statements[1]).lower()
    assert "models.enabled" in first_stmt
    assert "models.enabled" in second_stmt


def test_get_leaderboard_elo_with_confidence_applies_daily_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    db = _QueueDB(rows_by_call=[[(model_a, "Model A"), (model_b, "Model B")]])
    settings = _settings(
        leaderboard_refresh_daily_vote_cap=2,
        leaderboard_refresh_elo_k=24.0,
        leaderboard_elo_bootstrap_rounds=70,
        leaderboard_elo_bootstrap_seed=99,
        leaderboard_elo_confidence_level=0.8,
    )

    capped_votes = [object(), object()]

    calls: dict[str, object] = {}

    def fake_load_vote_samples(_db: object, *, daily_vote_cap: int) -> list[object]:
        calls["daily_vote_cap"] = daily_vote_cap
        return capped_votes

    def fake_compute_elo_ratings(votes: list[object], *, k: float, **kwargs: object):
        calls["k"] = k
        calls["shuffle_rounds"] = kwargs.get("shuffle_rounds")
        calls["shuffle_seed"] = kwargs.get("shuffle_seed")
        assert votes is capped_votes
        return {
            model_a: (910.0, 6),
            model_b: (1205.0, 4),
        }

    def fake_compute_intervals(
        votes: list[object],
        *,
        model_ids: list[uuid.UUID],
        bootstrap_rounds: int,
        seed: int,
        k: float,
        confidence_level: float,
        shuffle_rounds: int,
        shuffle_seed: int,
    ) -> dict[uuid.UUID, tuple[float, float]]:
        assert votes is capped_votes
        assert model_ids == [model_a, model_b]
        assert bootstrap_rounds == settings.leaderboard_elo_bootstrap_rounds
        assert seed == settings.leaderboard_elo_bootstrap_seed
        assert k == settings.leaderboard_refresh_elo_k
        assert confidence_level == settings.leaderboard_elo_confidence_level
        assert shuffle_rounds == settings.leaderboard_elo_shuffle_rounds
        assert shuffle_seed == settings.leaderboard_elo_shuffle_seed
        return {
            model_a: (890.0, 930.0),
            model_b: (1180.0, 1230.0),
        }

    monkeypatch.setattr(leaderboard, "load_vote_samples", fake_load_vote_samples)
    monkeypatch.setattr(leaderboard, "compute_elo_ratings", fake_compute_elo_ratings)
    monkeypatch.setattr(
        leaderboard,
        "compute_elo_confidence_intervals",
        fake_compute_intervals,
    )

    response = leaderboard._get_leaderboard_elo(
        db=db,  # type: ignore[arg-type]
        include_confidence=True,
        settings=settings,  # type: ignore[arg-type]
    )

    assert response.ci is True
    assert response.bootstrap_rounds == settings.leaderboard_elo_bootstrap_rounds
    assert [row.display_name for row in response.models] == ["Model B", "Model A"]
    assert response.models[0].rating_lower == 1180.0
    assert response.models[0].rating_upper == 1230.0
    assert response.models[1].rating_lower == 890.0
    assert response.models[1].rating_upper == 930.0
    assert calls == {
        "daily_vote_cap": settings.leaderboard_refresh_daily_vote_cap,
        "k": settings.leaderboard_refresh_elo_k,
        "shuffle_rounds": settings.leaderboard_elo_shuffle_rounds,
        "shuffle_seed": settings.leaderboard_elo_shuffle_seed,
    }


def test_get_leaderboard_elo_with_confidence_skips_daily_cap_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_a = uuid.uuid4()
    db = _QueueDB(rows_by_call=[[(model_a, "Model A")]])
    settings = _settings(leaderboard_refresh_daily_vote_cap=0)

    vote_samples = [object()]

    captured: dict[str, object] = {}

    def fake_load_vote_samples(_db: object, *, daily_vote_cap: int) -> list[object]:
        captured["daily_vote_cap"] = daily_vote_cap
        return vote_samples

    monkeypatch.setattr(leaderboard, "load_vote_samples", fake_load_vote_samples)
    monkeypatch.setattr(
        leaderboard,
        "compute_elo_ratings",
        lambda votes, **_kwargs: {model_a: (1001.0, len(votes))},
    )
    monkeypatch.setattr(
        leaderboard,
        "compute_elo_confidence_intervals",
        lambda *_args, **_kwargs: {model_a: (999.0, 1003.0)},
    )

    response = leaderboard._get_leaderboard_elo(
        db=db,  # type: ignore[arg-type]
        include_confidence=True,
        settings=settings,  # type: ignore[arg-type]
    )

    assert response.models[0].rating == 1001.0
    assert response.models[0].games_played == 1
    assert response.models[0].rating_lower == 999.0
    assert response.models[0].rating_upper == 1003.0
    assert captured == {"daily_vote_cap": 0}


def test_get_leaderboard_bt_returns_empty_response_without_models() -> None:
    db = _QueueDB(rows_by_call=[[]])

    response = leaderboard._get_leaderboard_bt(
        db=db,  # type: ignore[arg-type]
        include_confidence=False,
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response.method == "bt"
    assert response.ci is False
    assert response.models == []
    assert db.execute_calls == 1


def test_get_leaderboard_bt_filters_disabled_models_in_queries() -> None:
    db = _QueueDB(rows_by_call=[[]])

    leaderboard._get_leaderboard_bt(
        db=db,  # type: ignore[arg-type]
        include_confidence=False,
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert len(db.statements) == 1
    stmt_sql = str(db.statements[0]).lower()
    assert "models.enabled" in stmt_sql


def test_get_leaderboard_bt_returns_empty_when_no_public_enabled_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _QueueDB(
        rows_by_call=[
            [],  # model query returns nothing
        ]
    )

    response = leaderboard._get_leaderboard_bt(
        db=db,  # type: ignore[arg-type]
        include_confidence=True,
        settings=_settings(),  # type: ignore[arg-type]
    )

    assert response.models == []
    assert response.ci is False
    assert db.execute_calls == 1


def test_get_leaderboard_bt_without_confidence_computes_from_votes_not_model_ratings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime, timezone

    from app.services.leaderboard_refresh import VoteSample

    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    db = _QueueDB(
        rows_by_call=[
            [
                (model_a, "Model A"),
                (model_b, "Model B"),
            ],
        ]
    )
    settings = _settings(leaderboard_refresh_daily_vote_cap=2)
    captured: dict[str, object] = {}

    now = datetime.now(tz=timezone.utc)
    capped_vote_samples = [
        VoteSample(
            vote_id=uuid.uuid4(),
            created_at=now,
            winner="A",
            judge_key="j1",
            model_a_id=model_a,
            model_b_id=model_b,
        ),
        VoteSample(
            vote_id=uuid.uuid4(),
            created_at=now,
            winner="B",
            judge_key="j2",
            model_a_id=model_b,
            model_b_id=model_a,
        ),
    ]

    monkeypatch.setattr(
        leaderboard,
        "load_vote_samples",
        lambda _db, *, daily_vote_cap: (
            captured.update({"daily_vote_cap": daily_vote_cap}) or capped_vote_samples
        ),
    )

    def fake_compute_bt_ratings(
        *,
        model_ids: list[uuid.UUID],
        votes: list[PairwiseVote],
        max_iterations: int,
        tolerance: float,
        prior: float,
    ) -> dict[uuid.UUID, tuple[float, int]]:
        captured["model_ids"] = model_ids
        captured["votes"] = votes
        captured["max_iterations"] = max_iterations
        captured["tolerance"] = tolerance
        captured["prior"] = prior
        return {
            model_a: (1040.0, 1),
            model_b: (960.0, 1),
        }

    monkeypatch.setattr(leaderboard, "compute_bt_ratings", fake_compute_bt_ratings)
    monkeypatch.setattr(
        leaderboard,
        "compute_bt_confidence_intervals",
        lambda **_kwargs: pytest.fail(
            "Non-confidence BT path should not compute confidence intervals"
        ),
    )

    response = leaderboard._get_leaderboard_bt(
        db=db,  # type: ignore[arg-type]
        include_confidence=False,
        settings=settings,  # type: ignore[arg-type]
    )

    assert response.ci is False
    assert response.bootstrap_rounds is None
    assert [row.display_name for row in response.models] == ["Model A", "Model B"]
    assert captured["model_ids"] == [model_a, model_b]
    assert captured["daily_vote_cap"] == settings.leaderboard_refresh_daily_vote_cap
    assert len(db.statements) == 1
    assert "model_ratings" not in str(db.statements[0]).lower()

    votes = captured["votes"]
    assert isinstance(votes, list)
    assert votes == [
        PairwiseVote(model_a_id=model_a, model_b_id=model_b, winner="A"),
        PairwiseVote(model_a_id=model_b, model_b_id=model_a, winner="B"),
    ]
    assert captured["max_iterations"] == settings.leaderboard_bt_max_iterations
    assert captured["tolerance"] == settings.leaderboard_bt_tolerance
    assert captured["prior"] == settings.leaderboard_bt_prior
    assert db.execute_calls == 1


def test_get_leaderboard_bt_confidence_populates_intervals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime, timezone

    from app.services.leaderboard_refresh import VoteSample

    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    db = _QueueDB(
        rows_by_call=[
            [
                (model_a, "Model A"),
                (model_b, "Model B"),
            ],
        ]
    )
    settings = _settings(
        leaderboard_bt_bootstrap_rounds=120,
        leaderboard_bt_bootstrap_seed=3,
        leaderboard_bt_confidence_level=0.85,
    )

    now = datetime.now(tz=timezone.utc)
    vote_samples = [
        VoteSample(
            vote_id=uuid.uuid4(),
            created_at=now,
            winner="A",
            judge_key="j1",
            model_a_id=model_a,
            model_b_id=model_b,
        ),
    ]
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        leaderboard,
        "load_vote_samples",
        lambda _db, *, daily_vote_cap: (
            captured.update({"daily_vote_cap": daily_vote_cap}) or vote_samples
        ),
    )

    monkeypatch.setattr(
        leaderboard,
        "compute_bt_ratings",
        lambda **_kwargs: {
            model_a: (1115.0, 5),
            model_b: (980.0, 5),
        },
    )

    def fake_compute_bt_confidence_intervals(
        *,
        model_ids: list[uuid.UUID],
        votes: list[PairwiseVote],
        bootstrap_rounds: int,
        seed: int,
        max_iterations: int,
        tolerance: float,
        prior: float,
        confidence_level: float,
    ) -> dict[uuid.UUID, tuple[float, float]]:
        captured["model_ids"] = model_ids
        captured["votes"] = votes
        captured["bootstrap_rounds"] = bootstrap_rounds
        captured["seed"] = seed
        captured["confidence_level"] = confidence_level
        captured["max_iterations"] = max_iterations
        captured["tolerance"] = tolerance
        captured["prior"] = prior
        return {
            model_a: (1090.0, 1130.0),
            model_b: (950.0, 1010.0),
        }

    monkeypatch.setattr(
        leaderboard,
        "compute_bt_confidence_intervals",
        fake_compute_bt_confidence_intervals,
    )

    response = leaderboard._get_leaderboard_bt(
        db=db,  # type: ignore[arg-type]
        include_confidence=True,
        settings=settings,  # type: ignore[arg-type]
    )

    assert response.ci is True
    assert response.bootstrap_rounds == settings.leaderboard_bt_bootstrap_rounds
    assert response.models[0].display_name == "Model A"
    assert response.models[0].rating_lower == 1090.0
    assert response.models[0].rating_upper == 1130.0
    assert response.models[1].rating_lower == 950.0
    assert response.models[1].rating_upper == 1010.0
    assert captured["model_ids"] == [model_a, model_b]
    assert captured["votes"] == [
        PairwiseVote(model_a_id=model_a, model_b_id=model_b, winner="A")
    ]
    assert captured["bootstrap_rounds"] == settings.leaderboard_bt_bootstrap_rounds
    assert captured["seed"] == settings.leaderboard_bt_bootstrap_seed
    assert captured["confidence_level"] == settings.leaderboard_bt_confidence_level
    assert captured["max_iterations"] == settings.leaderboard_bt_max_iterations
    assert captured["tolerance"] == settings.leaderboard_bt_tolerance
    assert captured["prior"] == settings.leaderboard_bt_prior
    assert captured["daily_vote_cap"] == settings.leaderboard_refresh_daily_vote_cap
    assert len(db.statements) == 1
    assert "model_ratings" not in str(db.statements[0]).lower()


def test_confidence_cache_uses_redis_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_client = _FakeConfidenceRedis()
    response = LeaderboardResponse(models=[], method="elo", ci=True)
    settings = _settings(leaderboard_confidence_cache_ttl_seconds=45)

    monkeypatch.setattr(
        leaderboard,
        "_get_confidence_cache_client",
        lambda: cache_client,
    )

    leaderboard._store_cached_confidence_leaderboard(
        cache_key="elo:test",
        response=response,
        settings=settings,  # type: ignore[arg-type]
    )

    cached = leaderboard._load_cached_confidence_leaderboard(
        cache_key="elo:test",
        settings=settings,  # type: ignore[arg-type]
    )

    assert cached == response
    redis_key = leaderboard._confidence_cache_redis_key(
        cache_key="elo:test",
        settings=settings,  # type: ignore[arg-type]
    )
    assert cache_client.ttls[redis_key] == 45


def test_confidence_cache_redis_failures_fall_back_to_recompute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingRedis:
        def get(self, key: str) -> str | None:
            raise leaderboard.redis_utils.RedisError("get failed")

        def set(self, key: str, value: str, *, ex: int) -> bool:
            raise leaderboard.redis_utils.RedisError("set failed")

    calls = {"elo": 0}

    def fake_get_leaderboard_elo(**_kwargs: object) -> LeaderboardResponse:
        calls["elo"] += 1
        return LeaderboardResponse(models=[], method="elo", ci=True)

    monkeypatch.setattr(leaderboard, "_get_leaderboard_elo", fake_get_leaderboard_elo)
    monkeypatch.setattr(
        leaderboard,
        "_get_confidence_cache_client",
        lambda: _FailingRedis(),
    )

    settings = _settings(
        leaderboard_confidence_cache_ttl_seconds=30,
        leaderboard_confidence_rate_limit=20,
    )

    leaderboard.get_leaderboard(
        method="elo",
        include_confidence=True,
        db=object(),  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
    )
    leaderboard.get_leaderboard(
        method="elo",
        include_confidence=True,
        db=object(),  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
    )

    assert calls["elo"] == 2


class TestSharedIdentityContractConsistency:
    """Battle caps and leaderboard limiting share a user-id contract."""

    def test_user_requester_has_cap_filter_and_judge_key(self) -> None:
        from app.utils.requester_identity import RequesterIdentity

        user_id = uuid.uuid4()
        identity = RequesterIdentity(voter_user_id=user_id)
        assert identity.battle_lookup_filter() is not None
        assert identity.judge_key() == f"user:{user_id}"


# ---------------------------------------------------------------------------
# Task 1 — New tests: BT cache key includes daily_vote_cap,
#           confidence cache Redis-absent fallback.
# ---------------------------------------------------------------------------


def test_bt_cache_key_includes_daily_vote_cap() -> None:
    """BT cache key must change when daily_vote_cap changes so stale cached
    results are not served after the cap is reconfigured."""

    key_cap3 = leaderboard._confidence_cache_key(
        method="bt",
        settings=_settings(leaderboard_refresh_daily_vote_cap=3),  # type: ignore[arg-type]
    )
    key_cap10 = leaderboard._confidence_cache_key(
        method="bt",
        settings=_settings(leaderboard_refresh_daily_vote_cap=10),  # type: ignore[arg-type]
    )
    key_cap0 = leaderboard._confidence_cache_key(
        method="bt",
        settings=_settings(leaderboard_refresh_daily_vote_cap=0),  # type: ignore[arg-type]
    )

    assert key_cap3 != key_cap10, (
        "BT cache key must differ when daily_vote_cap differs (3 vs 10)"
    )
    assert key_cap3 != key_cap0, (
        "BT cache key must differ when daily_vote_cap differs (3 vs 0)"
    )
    assert key_cap10 != key_cap0, (
        "BT cache key must differ when daily_vote_cap differs (10 vs 0)"
    )


def test_daily_vote_cap_cache_invalidation() -> None:
    """Changing daily_vote_cap forces a fresh BT confidence computation by
    producing a different cache key — the cached result for cap=3 must NOT
    be returned when cap changes to 7."""

    calls = {"bt": 0}

    def fake_get_leaderboard_bt(**_kwargs: object) -> LeaderboardResponse:
        calls["bt"] += 1
        return LeaderboardResponse(models=[], method="bt", ci=True)

    cache_client = _FakeConfidenceRedis()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(leaderboard, "_get_leaderboard_bt", fake_get_leaderboard_bt)
    monkeypatch.setattr(
        leaderboard,
        "_get_confidence_cache_client",
        lambda: cache_client,
    )

    try:
        settings_cap3 = _settings(
            leaderboard_refresh_daily_vote_cap=3,
            leaderboard_confidence_cache_ttl_seconds=30,
            leaderboard_confidence_rate_limit=20,
        )
        settings_cap7 = _settings(
            leaderboard_refresh_daily_vote_cap=7,
            leaderboard_confidence_cache_ttl_seconds=30,
            leaderboard_confidence_rate_limit=20,
        )

        leaderboard.get_leaderboard(
            method="bt",
            include_confidence=True,
            db=object(),  # type: ignore[arg-type]
            settings=settings_cap3,  # type: ignore[arg-type]
        )
        assert calls["bt"] == 1

        leaderboard.get_leaderboard(
            method="bt",
            include_confidence=True,
            db=object(),  # type: ignore[arg-type]
            settings=settings_cap3,  # type: ignore[arg-type]
        )
        assert calls["bt"] == 1

        leaderboard.get_leaderboard(
            method="bt",
            include_confidence=True,
            db=object(),  # type: ignore[arg-type]
            settings=settings_cap7,  # type: ignore[arg-type]
        )
        assert calls["bt"] == 2
    finally:
        monkeypatch.undo()


def test_bt_cache_key_unchanged_when_other_bt_settings_same() -> None:
    """Sanity check: identical settings produce identical BT cache keys."""

    key_a = leaderboard._confidence_cache_key(
        method="bt",
        settings=_settings(),  # type: ignore[arg-type]
    )
    key_b = leaderboard._confidence_cache_key(
        method="bt",
        settings=_settings(),  # type: ignore[arg-type]
    )
    assert key_a == key_b


def test_confidence_cache_absent_redis_skips_shared_caching() -> None:
    """When Redis is absent, confidence requests recompute directly.

    Task 1 forbids falling back to a process-local shared cache because it is
    inconsistent across workers.
    """

    from app.utils.rate_limit import RollingWindowRateLimiter

    # Limiter with redis_client=None simulates the no-Redis fallback path.
    no_redis_limiter = RollingWindowRateLimiter(
        limit=20,
        window_seconds=60,
        bucket_seconds=10,
        redis_client=None,
        redis_prefix="test",
    )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        leaderboard,
        "_get_confidence_rate_limiter",
        lambda: no_redis_limiter,
    )

    calls = {"elo": 0}

    def fake_get_leaderboard_elo(**_kwargs: object) -> LeaderboardResponse:
        calls["elo"] += 1
        return LeaderboardResponse(models=[], method="elo", ci=True)

    monkeypatch.setattr(leaderboard, "_get_leaderboard_elo", fake_get_leaderboard_elo)

    try:
        settings = _settings(
            leaderboard_confidence_cache_ttl_seconds=30,
            leaderboard_confidence_rate_limit=20,
        )

        monkeypatch.setattr(leaderboard, "_get_confidence_cache_client", lambda: None)

        # First call — must hit backend.
        r1 = leaderboard.get_leaderboard(
            method="elo",
            include_confidence=True,
            db=object(),  # type: ignore[arg-type]
            settings=settings,  # type: ignore[arg-type]
        )
        assert calls["elo"] == 1
        assert r1.method == "elo"

        # Second call — no Redis, so there is no shared-cache hit.
        r2 = leaderboard.get_leaderboard(
            method="elo",
            include_confidence=True,
            db=object(),  # type: ignore[arg-type]
            settings=settings,  # type: ignore[arg-type]
        )
        assert calls["elo"] == 2
        assert r2 == r1
    finally:
        monkeypatch.undo()
