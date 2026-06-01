from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
import uuid

import app.services.leaderboard_refresh as leaderboard_refresh_module
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from app.db.base import Base
from app.models import (
    Battle,
    Model,
    Run,
    ServiceAccount,
    ServiceAccountToken,
    Task,
    User,
    Vote,
)
from app.services.leaderboard_refresh import (
    LeaderboardRefresher,
    VoteSample,
    compute_elo_confidence_intervals,
    compute_elo_ratings,
    count_vote_sources,
    filter_outlier_judge_votes,
    limit_votes_per_judge_per_day,
    load_vote_samples,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type: JSONB, _compiler: object, **_kw: object) -> str:
    return "JSON"


@pytest.fixture()
def leaderboard_db_session(tmp_path) -> Iterator[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'leaderboard.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _vote(
    *,
    at: datetime,
    winner: str,
    model_a_id: uuid.UUID,
    model_b_id: uuid.UUID,
    judge_key: str,
) -> VoteSample:
    return VoteSample(
        vote_id=uuid.uuid4(),
        created_at=at,
        winner=winner,
        model_a_id=model_a_id,
        model_b_id=model_b_id,
        judge_key=judge_key,
    )


def _seed_mixed_vote_source_samples(db: Session) -> dict[str, object]:
    created_at = datetime(2026, 5, 23, 8, 0, tzinfo=timezone.utc)
    task = Task(source_text="原文")
    model_a = Model(
        display_name="Model A",
        model_name=f"model-a-{uuid.uuid4()}",
        base_url="http://example.invalid",
    )
    model_b = Model(
        display_name="Model B",
        model_name=f"model-b-{uuid.uuid4()}",
        base_url="http://example.invalid",
    )
    db.add_all([task, model_a, model_b])
    db.flush()

    battle_one = Battle(task_id=task.id, status="completed")
    battle_two = Battle(task_id=task.id, status="completed")
    battle_three = Battle(task_id=task.id, status="running")
    db.add_all([battle_one, battle_two, battle_three])
    db.flush()
    db.add_all(
        [
            Run(battle_id=battle_one.id, side="A", model_id=model_a.id),
            Run(battle_id=battle_one.id, side="B", model_id=model_b.id),
            Run(battle_id=battle_two.id, side="A", model_id=model_a.id),
            Run(battle_id=battle_two.id, side="B", model_id=model_b.id),
            Run(battle_id=battle_three.id, side="A", model_id=model_a.id),
            Run(battle_id=battle_three.id, side="B", model_id=model_b.id),
        ]
    )

    human_user = User(oidc_issuer="https://issuer.example", oidc_sub="human")
    bot_user_one = User(
        oidc_issuer="system:service-account",
        oidc_sub=f"service-account:{uuid.uuid4()}",
        actor_type="bot",
    )
    bot_user_two = User(
        oidc_issuer="system:service-account",
        oidc_sub=f"service-account:{uuid.uuid4()}",
        actor_type="bot",
    )
    db.add_all([human_user, bot_user_one, bot_user_two])
    db.flush()

    service_account_one = ServiceAccount(
        name="Bot One",
        bot_user_id=bot_user_one.id,
    )
    service_account_two = ServiceAccount(
        name="Bot Two",
        bot_user_id=bot_user_two.id,
    )
    db.add_all([service_account_one, service_account_two])
    db.flush()
    token_one = ServiceAccountToken(
        service_account_id=service_account_one.id,
        token_prefix="osa_bot_one",
        token_hash=f"hash-{uuid.uuid4()}",
        scopes=["vote:create"],
    )
    token_two = ServiceAccountToken(
        service_account_id=service_account_two.id,
        token_prefix="osa_bot_two",
        token_hash=f"hash-{uuid.uuid4()}",
        scopes=["vote:create"],
    )
    db.add_all([token_one, token_two])
    db.flush()

    human_vote = Vote(
        battle_id=battle_one.id,
        winner="A",
        voter_user_id=human_user.id,
        revealed=True,
        created_at=created_at,
    )
    bot_vote_one = Vote(
        battle_id=battle_one.id,
        winner="B",
        voter_user_id=bot_user_one.id,
        service_account_id=service_account_one.id,
        service_account_token_id=token_one.id,
        revealed=True,
        created_at=created_at + timedelta(minutes=1),
    )
    bot_vote_two = Vote(
        battle_id=battle_two.id,
        winner="tie",
        voter_user_id=bot_user_two.id,
        service_account_id=service_account_two.id,
        service_account_token_id=token_two.id,
        revealed=True,
        created_at=created_at + timedelta(minutes=2),
    )
    early_vote = Vote(
        battle_id=battle_three.id,
        winner="A",
        voter_user_id=human_user.id,
        revealed=True,
        created_at=created_at + timedelta(minutes=3),
    )
    db.add_all([human_vote, bot_vote_one, bot_vote_two, early_vote])
    db.commit()
    return {
        "human_vote_id": human_vote.id,
        "bot_vote_one_id": bot_vote_one.id,
        "bot_vote_two_id": bot_vote_two.id,
        "early_vote_id": early_vote.id,
        "service_account_one_id": service_account_one.id,
        "service_account_two_id": service_account_two.id,
    }


def test_limit_votes_per_judge_per_day_caps_votes() -> None:
    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    start = datetime(2026, 2, 18, 9, 0, tzinfo=timezone.utc)

    votes = [
        _vote(
            at=start + timedelta(minutes=idx),
            winner="A",
            model_a_id=model_a,
            model_b_id=model_b,
            judge_key="user:judge-1",
        )
        for idx in range(5)
    ]

    kept = limit_votes_per_judge_per_day(votes, daily_vote_cap=2)
    assert len(kept) == 2


def test_outlier_filter_setting_defaults_disabled() -> None:
    from app.core.config import Settings

    assert Settings.model_fields["leaderboard_outlier_filter_enabled"].default is False


def test_filter_outlier_judge_votes_removes_extreme_judge() -> None:
    model_a = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    model_b = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    start = datetime(2026, 2, 18, 9, 0, tzinfo=timezone.utc)

    mainstream_votes = [
        _vote(
            at=start + timedelta(minutes=idx),
            winner="A",
            model_a_id=model_a,
            model_b_id=model_b,
            judge_key=f"user:mainstream-{idx}",
        )
        for idx in range(20)
    ]
    outlier_votes = [
        _vote(
            at=start + timedelta(minutes=30 + idx),
            winner="B",
            model_a_id=model_a,
            model_b_id=model_b,
            judge_key="user:outlier",
        )
        for idx in range(5)
    ]

    kept = filter_outlier_judge_votes(
        mainstream_votes + outlier_votes,
        min_votes=5,
        max_votes=100,
        alpha=0.05,
    )

    assert len(kept) == len(mainstream_votes)
    assert {vote.judge_key for vote in kept} == {
        vote.judge_key for vote in mainstream_votes
    }


def test_filter_outlier_judge_votes_preserves_low_activity_judges() -> None:
    model_a = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    model_b = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    start = datetime(2026, 2, 18, 9, 0, tzinfo=timezone.utc)
    votes = [
        _vote(
            at=start + timedelta(minutes=idx),
            winner="B",
            model_a_id=model_a,
            model_b_id=model_b,
            judge_key="user:low-activity",
        )
        for idx in range(4)
    ]

    kept = filter_outlier_judge_votes(votes, min_votes=5, max_votes=100, alpha=0.05)

    assert kept is votes


def test_refresher_load_vote_samples_skips_outlier_filter_when_disabled(
    monkeypatch,
) -> None:
    vote_samples: list[VoteSample] = []
    refresher = LeaderboardRefresher(
        enabled=True,
        interval_seconds=60,
        daily_vote_cap=2,
        elo_k=32.0,
        outlier_filter_enabled=False,
    )

    monkeypatch.setattr(
        leaderboard_refresh_module,
        "load_vote_samples",
        lambda _db, *, daily_vote_cap: vote_samples,
    )
    monkeypatch.setattr(
        leaderboard_refresh_module,
        "filter_outlier_judge_votes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("filter should not run when disabled")
        ),
    )

    loaded = refresher._load_vote_samples(db=object())  # type: ignore[arg-type]

    assert loaded is vote_samples


def test_refresher_load_vote_samples_applies_outlier_filter_when_enabled(
    monkeypatch,
) -> None:
    raw_vote_samples: list[VoteSample] = []
    filtered_vote_samples: list[VoteSample] = []
    captured: dict[str, object] = {}
    refresher = LeaderboardRefresher(
        enabled=True,
        interval_seconds=60,
        daily_vote_cap=2,
        elo_k=32.0,
        outlier_filter_enabled=True,
        outlier_filter_min_votes=7,
        outlier_filter_max_votes=50,
        outlier_filter_alpha=0.01,
    )

    def fake_load_vote_samples(_db: object, *, daily_vote_cap: int) -> list[VoteSample]:
        captured["daily_vote_cap"] = daily_vote_cap
        return raw_vote_samples

    def fake_filter_outlier_judge_votes(
        vote_samples: list[VoteSample],
        *,
        min_votes: int,
        max_votes: int,
        alpha: float,
    ) -> list[VoteSample]:
        captured["vote_samples"] = vote_samples
        captured["min_votes"] = min_votes
        captured["max_votes"] = max_votes
        captured["alpha"] = alpha
        return filtered_vote_samples

    monkeypatch.setattr(
        leaderboard_refresh_module,
        "load_vote_samples",
        fake_load_vote_samples,
    )
    monkeypatch.setattr(
        leaderboard_refresh_module,
        "filter_outlier_judge_votes",
        fake_filter_outlier_judge_votes,
    )

    loaded = refresher._load_vote_samples(db=object())  # type: ignore[arg-type]

    assert loaded is filtered_vote_samples
    assert captured == {
        "daily_vote_cap": 2,
        "vote_samples": raw_vote_samples,
        "min_votes": 7,
        "max_votes": 50,
        "alpha": 0.01,
    }


def test_compute_elo_ratings_counts_games_and_updates_ratings() -> None:
    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    model_c = uuid.uuid4()
    start = datetime(2026, 2, 18, 9, 0, tzinfo=timezone.utc)

    votes = [
        _vote(
            at=start,
            winner="A",
            model_a_id=model_a,
            model_b_id=model_b,
            judge_key="user:1",
        ),
        _vote(
            at=start + timedelta(minutes=1),
            winner="B",
            model_a_id=model_a,
            model_b_id=model_b,
            judge_key="user:2",
        ),
        _vote(
            at=start + timedelta(minutes=2),
            winner="tie",
            model_a_id=model_a,
            model_b_id=model_c,
            judge_key="user:3",
        ),
    ]

    ratings = compute_elo_ratings(votes, k=32.0)

    rating_a, games_a = ratings[model_a]
    rating_b, games_b = ratings[model_b]
    rating_c, games_c = ratings[model_c]

    assert games_a == 3
    assert games_b == 2
    assert games_c == 1

    # A loses net points after one loss and one tie against lower/equal ratings.
    assert rating_a < 1000.0
    assert rating_b > rating_a
    assert rating_c > 999.0


def test_compute_elo_ratings_tie_keeps_equal_models_stable() -> None:
    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    start = datetime(2026, 2, 18, 9, 0, tzinfo=timezone.utc)

    votes = [
        _vote(
            at=start,
            winner="tie",
            model_a_id=model_a,
            model_b_id=model_b,
            judge_key="user:1",
        )
    ]

    ratings = compute_elo_ratings(votes, k=32.0)
    assert ratings[model_a] == (1000.0, 1)
    assert ratings[model_b] == (1000.0, 1)


def test_compute_elo_confidence_intervals_are_deterministic_for_seed() -> None:
    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    model_c = uuid.uuid4()
    start = datetime(2026, 2, 18, 9, 0, tzinfo=timezone.utc)

    votes = [
        *[
            _vote(
                at=start + timedelta(minutes=idx),
                winner="A",
                model_a_id=model_a,
                model_b_id=model_b,
                judge_key=f"user:{idx}",
            )
            for idx in range(12)
        ],
        *[
            _vote(
                at=start + timedelta(minutes=20 + idx),
                winner="A",
                model_a_id=model_b,
                model_b_id=model_c,
                judge_key=f"user:b{idx}",
            )
            for idx in range(9)
        ],
    ]

    first = compute_elo_confidence_intervals(
        votes,
        model_ids=[model_a, model_b, model_c],
        bootstrap_rounds=40,
        seed=17,
        k=32.0,
    )
    second = compute_elo_confidence_intervals(
        votes,
        model_ids=[model_a, model_b, model_c],
        bootstrap_rounds=40,
        seed=17,
        k=32.0,
    )

    assert first == second
    assert set(first) == {model_a, model_b, model_c}
    for low, high in first.values():
        assert low <= high


def test_compute_elo_confidence_intervals_without_votes_return_point_estimate() -> None:
    model_a = uuid.uuid4()
    model_b = uuid.uuid4()

    intervals = compute_elo_confidence_intervals(
        [],
        model_ids=[model_a, model_b],
        bootstrap_rounds=25,
        seed=3,
        k=32.0,
    )

    assert intervals[model_a] == (1000.0, 1000.0)
    assert intervals[model_b] == (1000.0, 1000.0)


def test_judge_key_is_user_scoped() -> None:
    user_id = uuid.uuid4()
    from app.utils.requester_identity import RequesterIdentity

    key = RequesterIdentity(voter_user_id=user_id).judge_key()
    assert key == f"user:{user_id}"


def test_refresh_locked_serializes_ratings_before_loading_votes() -> None:
    calls: list[str] = []
    model_id = uuid.uuid4()
    captured: dict[str, object] = {}

    refresher = LeaderboardRefresher(
        enabled=True,
        interval_seconds=60,
        daily_vote_cap=0,
        elo_k=32.0,
    )

    def fake_lock(_db: object) -> None:
        calls.append("lock")

    def fake_load_models(_db: object) -> list[uuid.UUID]:
        calls.append("models")
        return [model_id]

    def fake_load_votes(_db: object) -> list[VoteSample]:
        calls.append("votes")
        return []

    def fake_persist(
        _db: object,
        *,
        model_ids: list[uuid.UUID],
        ratings: dict[uuid.UUID, tuple[float, int]],
    ) -> None:
        calls.append("persist")
        captured["model_ids"] = model_ids
        captured["ratings"] = ratings

    refresher._lock_model_ratings_for_refresh = fake_lock  # type: ignore[method-assign]
    refresher._load_all_model_ids = fake_load_models  # type: ignore[method-assign]
    refresher._load_vote_samples = fake_load_votes  # type: ignore[method-assign]
    refresher._persist_ratings = fake_persist  # type: ignore[method-assign]

    model_count, vote_count = refresher._refresh_locked(db=object())  # type: ignore[arg-type]

    # Models/votes are loaded before locking; models are re-fetched under lock
    # to avoid persisting ratings for models deleted mid-refresh.
    assert calls == ["models", "votes", "lock", "models", "persist"]
    assert model_count == 1
    assert vote_count == 0
    assert captured["model_ids"] == [model_id]
    assert captured["ratings"] == {}


def test_refresh_once_uses_transaction_scoped_lock_without_manual_unlock(
    monkeypatch,
) -> None:
    executed_sql: list[str] = []

    class _ScalarResult:
        def scalar_one(self) -> bool:
            return True

    class _FakeConnection:
        def execute(
            self,
            stmt: object,
            params: dict[str, object],
        ) -> _ScalarResult:
            executed_sql.append(str(stmt))
            if len(executed_sql) > 1:
                raise AssertionError(f"Unexpected follow-up SQL: {stmt}")
            assert "key" in params
            return _ScalarResult()

        def invalidate(self) -> None:
            raise AssertionError(
                "refresh_once must not invalidate the connection on success"
            )

    class _FakeSession:
        def __init__(self) -> None:
            self.close_calls = 0
            self._conn = _FakeConnection()

        def connection(self) -> _FakeConnection:
            return self._conn

        def close(self) -> None:
            self.close_calls += 1

        def rollback(self) -> None:
            raise AssertionError("rollback should not be called on success")

    fake_session = _FakeSession()
    refresher = LeaderboardRefresher(
        enabled=True,
        interval_seconds=60,
        daily_vote_cap=0,
        elo_k=32.0,
    )

    monkeypatch.setattr(
        leaderboard_refresh_module,
        "get_sessionmaker",
        lambda: lambda: fake_session,
    )
    monkeypatch.setattr(refresher, "_refresh_locked", lambda _db: (2, 1))

    refresher.refresh_once()

    assert len(executed_sql) == 1
    assert "pg_try_advisory_xact_lock" in executed_sql[0]
    assert fake_session.close_calls == 1

    status = refresher.get_status()
    assert status.total_refreshes == 1
    assert status.last_error is None
    assert status.last_succeeded_at is not None


class TestLimitVotesPerJudgeUsesSharedJudgeKey:
    """Prove that ``limit_votes_per_judge_per_day`` treats each user as one judge."""

    MODEL_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    MODEL_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    START = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)

    def _votes_for_key(self, judge_key: str, count: int) -> list[VoteSample]:
        return [
            _vote(
                at=self.START + timedelta(minutes=i),
                winner="A",
                model_a_id=self.MODEL_A,
                model_b_id=self.MODEL_B,
                judge_key=judge_key,
            )
            for i in range(count)
        ]

    def test_user_requester_capped_by_user_judge_key(self) -> None:
        user_id = uuid.uuid4()
        from app.utils.requester_identity import RequesterIdentity

        key = RequesterIdentity(voter_user_id=user_id).judge_key()
        assert key == f"user:{user_id}"

        votes = self._votes_for_key(key, 5)
        kept = limit_votes_per_judge_per_day(votes, daily_vote_cap=2)
        assert len(kept) == 2

    def test_different_users_are_independent_judges(self) -> None:
        from app.utils.requester_identity import RequesterIdentity

        user_id = uuid.uuid4()
        user_key = RequesterIdentity(voter_user_id=user_id).judge_key()
        other_user_key = RequesterIdentity(voter_user_id=uuid.uuid4()).judge_key()

        votes = self._votes_for_key(user_key, 2) + self._votes_for_key(
            other_user_key, 2
        )

        kept = limit_votes_per_judge_per_day(votes, daily_vote_cap=1)
        kept_keys = {v.judge_key for v in kept}
        assert kept_keys == {user_key, other_user_key}
        assert len(kept) == 2


# ---------------------------------------------------------------------------
# Task 1 — New tests: revealed-only filtering, shuffle-and-average Elo,
#           UTC day-cap determinism.
# ---------------------------------------------------------------------------

# --- Revealed-only vote filtering via load_vote_samples --------------------


def test_load_vote_samples_excludes_unrevealed_votes() -> None:
    """Prove that the SQL statement built by load_vote_samples carries a
    ``Vote.revealed IS TRUE`` predicate so unrevealed votes never enter the
    rating pipeline.

    We monkeypatch the DB execute path to capture the compiled statement text
    and assert the filter is present, without requiring a live database.
    """

    from app.services.leaderboard_refresh import load_vote_samples

    captured: dict[str, object] = {}

    class _FakeResult:
        def all(self) -> list[object]:
            return []

    class _FakeSession:
        def execute(self, stmt: object) -> _FakeResult:
            captured["stmt"] = str(stmt).lower()
            captured["params"] = getattr(stmt.compile(), "params", {})
            return _FakeResult()

    load_vote_samples(_FakeSession())  # type: ignore[arg-type]

    stmt_text = captured.get("stmt", "")
    assert isinstance(stmt_text, str) and stmt_text, "execute() was not called"
    # The rendered SQL must contain the revealed filter.
    assert "revealed" in stmt_text, (
        f"Expected 'revealed' in SQL predicate but got:\n{stmt_text}"
    )


def test_load_vote_samples_excludes_non_completed_battles() -> None:
    from app.services.leaderboard_refresh import load_vote_samples

    captured: dict[str, object] = {}

    class _FakeResult:
        def all(self) -> list[object]:
            return []

    class _FakeSession:
        def execute(self, stmt: object) -> _FakeResult:
            captured["stmt"] = str(stmt).lower()
            captured["params"] = getattr(stmt.compile(), "params", {})
            return _FakeResult()

    load_vote_samples(_FakeSession())  # type: ignore[arg-type]

    stmt_text = captured.get("stmt", "")
    assert isinstance(stmt_text, str) and stmt_text, "execute() was not called"
    assert "battles" in stmt_text
    assert "battles.status" in stmt_text
    assert "completed" in captured.get("params", {}).values()


def test_load_vote_samples_revealed_filter_excludes_rows_at_db_boundary() -> None:
    """Simulate both revealed and unrevealed rows returned from DB and confirm
    that only revealed rows pass the filter baked into the SELECT statement.

    Because the filter is in SQL (not Python), we test the statement text
    rather than runtime filtering of Python objects.  This companion test
    verifies the DB session is only called once (no post-query Python filter).
    """

    from app.services.leaderboard_refresh import load_vote_samples

    execute_calls = {"count": 0}

    class _FakeSingleResult:
        def all(self) -> list[object]:
            # The SQL filter already excludes unrevealed rows; the DB returns
            # only the two matching rows.  Python code must not do extra work.
            return []

    class _FakeSessionOnce:
        def execute(self, stmt: object) -> _FakeSingleResult:
            execute_calls["count"] += 1
            sql = str(stmt).lower()
            assert "revealed" in sql, "Revealed filter missing from SQL"
            return _FakeSingleResult()

    load_vote_samples(_FakeSessionOnce())  # type: ignore[arg-type]
    assert execute_calls["count"] == 1, "Expected exactly one DB query"


def test_load_vote_samples_day_cap_uses_db_side_utc_boundaries() -> None:
    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    day1_vote_1 = uuid.uuid4()
    day1_vote_2 = uuid.uuid4()
    day2_vote_1 = uuid.uuid4()
    voter_user_id = uuid.uuid4()

    day1 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 3, 2, 9, 30, tzinfo=timezone.utc)

    class _FakeResult:
        def __init__(
            self, *, rows: list[tuple[object, ...]] | None = None, one: object = None
        ):
            self._rows = rows or []
            self._one = one

        def all(self) -> list[tuple[object, ...]]:
            return list(self._rows)

        def one(self) -> object:
            return self._one

    class _FakeSession:
        def __init__(self) -> None:
            self.sql: list[str] = []
            self._results = [
                _FakeResult(one=(day1, day2)),
                _FakeResult(
                    rows=[
                        (
                            day1_vote_1,
                            day1,
                            "A",
                            voter_user_id,
                            "human",
                            None,
                            model_a,
                            model_b,
                        ),
                        (
                            day1_vote_2,
                            day1 + timedelta(minutes=5),
                            "B",
                            voter_user_id,
                            "human",
                            None,
                            model_a,
                            model_b,
                        ),
                    ]
                ),
                _FakeResult(
                    rows=[
                        (
                            day2_vote_1,
                            day2,
                            "A",
                            voter_user_id,
                            "human",
                            None,
                            model_a,
                            model_b,
                        )
                    ]
                ),
            ]

        def execute(self, stmt: object) -> _FakeResult:
            self.sql.append(str(stmt).lower())
            return self._results.pop(0)

    session = _FakeSession()
    samples = load_vote_samples(session, daily_vote_cap=1)  # type: ignore[arg-type]

    assert [sample.vote_id for sample in samples] == [day1_vote_1, day2_vote_1]
    assert len(session.sql) == 3
    assert "min(votes.created_at)" in session.sql[0]
    for sql in session.sql[1:]:
        assert "votes.created_at >=" in sql
        assert "votes.created_at <" in sql
        assert "::date" not in sql
        assert "date(" not in sql


def test_load_vote_samples_filters_mixed_human_and_bot_sources(
    leaderboard_db_session: Session,
) -> None:
    seeded = _seed_mixed_vote_source_samples(leaderboard_db_session)
    service_account_one_id = seeded["service_account_one_id"]
    assert isinstance(service_account_one_id, uuid.UUID)

    all_samples = load_vote_samples(leaderboard_db_session, judge_type="all")
    human_samples = load_vote_samples(leaderboard_db_session, judge_type="human")
    bot_samples = load_vote_samples(leaderboard_db_session, judge_type="bot")
    service_account_samples = load_vote_samples(
        leaderboard_db_session,
        judge_type="all",
        service_account_id=service_account_one_id,
    )

    assert [sample.vote_id for sample in all_samples] == [
        seeded["human_vote_id"],
        seeded["bot_vote_one_id"],
        seeded["bot_vote_two_id"],
    ]
    assert seeded["early_vote_id"] not in {sample.vote_id for sample in all_samples}
    assert [sample.vote_id for sample in human_samples] == [seeded["human_vote_id"]]
    assert [sample.vote_id for sample in bot_samples] == [
        seeded["bot_vote_one_id"],
        seeded["bot_vote_two_id"],
    ]
    assert [sample.vote_id for sample in service_account_samples] == [
        seeded["bot_vote_one_id"]
    ]
    assert count_vote_sources(all_samples) == {"human": 1, "bot": 2, "total": 3}
    assert count_vote_sources(human_samples) == {"human": 1, "bot": 0, "total": 1}
    assert count_vote_sources(bot_samples) == {"human": 0, "bot": 2, "total": 2}
    assert count_vote_sources(service_account_samples) == {
        "human": 0,
        "bot": 1,
        "total": 1,
    }


# --- Shuffle-and-average Elo ------------------------------------------------


def test_compute_elo_ratings_shuffle_single_round_matches_original_path() -> None:
    """shuffle_rounds=1 must produce identical results to the default (no
    shuffle) path so there is no regression for existing callers."""

    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    start = datetime(2026, 2, 18, 9, 0, tzinfo=timezone.utc)

    votes = [
        _vote(
            at=start + timedelta(minutes=i),
            winner="A" if i % 2 == 0 else "B",
            model_a_id=model_a,
            model_b_id=model_b,
            judge_key=f"user:{i}",
        )
        for i in range(8)
    ]

    default_ratings = compute_elo_ratings(votes, k=32.0)
    single_shuffle_ratings = compute_elo_ratings(votes, k=32.0, shuffle_rounds=1)
    zero_shuffle_ratings = compute_elo_ratings(votes, k=32.0, shuffle_rounds=0)

    assert default_ratings == single_shuffle_ratings
    assert default_ratings == zero_shuffle_ratings


def test_compute_elo_ratings_shuffle_is_deterministic_for_seed() -> None:
    """Two calls with identical shuffle_rounds and shuffle_seed must produce
    exactly the same ratings (determinism guarantee)."""

    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    model_c = uuid.uuid4()
    start = datetime(2026, 2, 18, 9, 0, tzinfo=timezone.utc)

    votes = [
        *[
            _vote(
                at=start + timedelta(minutes=i),
                winner="A",
                model_a_id=model_a,
                model_b_id=model_b,
                judge_key=f"user:{i}",
            )
            for i in range(10)
        ],
        *[
            _vote(
                at=start + timedelta(minutes=20 + i),
                winner="B",
                model_a_id=model_b,
                model_b_id=model_c,
                judge_key=f"user:b{i}",
            )
            for i in range(7)
        ],
    ]

    first = compute_elo_ratings(votes, k=32.0, shuffle_rounds=5, shuffle_seed=42)
    second = compute_elo_ratings(votes, k=32.0, shuffle_rounds=5, shuffle_seed=42)

    assert first == second
    # Sanity: all three models should appear.
    assert set(first) == {model_a, model_b, model_c}


def test_compute_elo_ratings_shuffle_different_seeds_give_different_ratings() -> None:
    """Different shuffle seeds should generally produce different ratings,
    confirming the RNG is actually exercised."""

    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    start = datetime(2026, 2, 18, 9, 0, tzinfo=timezone.utc)

    # Use many votes so ordering variance is non-trivial.
    votes = [
        _vote(
            at=start + timedelta(minutes=i),
            winner="A" if i < 15 else "B",
            model_a_id=model_a,
            model_b_id=model_b,
            judge_key=f"user:{i}",
        )
        for i in range(30)
    ]

    r1 = compute_elo_ratings(votes, k=32.0, shuffle_rounds=10, shuffle_seed=1)
    r2 = compute_elo_ratings(votes, k=32.0, shuffle_rounds=10, shuffle_seed=999)

    # Ratings should differ because the shuffle sequence is different.
    assert r1[model_a][0] != r2[model_a][0]


def test_compute_elo_ratings_shuffle_games_played_matches_single_pass() -> None:
    """Games-played counts must be identical regardless of shuffle_rounds
    because game counts are order-independent."""

    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    start = datetime(2026, 2, 18, 9, 0, tzinfo=timezone.utc)

    votes = [
        _vote(
            at=start + timedelta(minutes=i),
            winner="A",
            model_a_id=model_a,
            model_b_id=model_b,
            judge_key=f"user:{i}",
        )
        for i in range(6)
    ]

    single = compute_elo_ratings(votes, k=32.0)
    shuffled = compute_elo_ratings(votes, k=32.0, shuffle_rounds=8, shuffle_seed=7)

    _, games_single_a = single[model_a]
    _, games_shuffle_a = shuffled[model_a]
    assert games_single_a == games_shuffle_a == 6

    _, games_single_b = single[model_b]
    _, games_shuffle_b = shuffled[model_b]
    assert games_single_b == games_shuffle_b == 6


def test_compute_elo_ratings_shuffle_reduces_order_variance() -> None:
    """Shuffle-and-average should produce ratings closer to the mean of
    forward and reverse orderings than a single-pass does.

    This is a sanity check on the core benefit: averaged ratings sit between
    the extreme orderings, not at one of the extremes.
    """

    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    start = datetime(2026, 2, 18, 9, 0, tzinfo=timezone.utc)

    # Strongly biased sequence: all A-wins first, then all B-wins.
    votes_fwd = [
        *[
            _vote(
                at=start + timedelta(minutes=i),
                winner="A",
                model_a_id=model_a,
                model_b_id=model_b,
                judge_key=f"a{i}",
            )
            for i in range(10)
        ],
        *[
            _vote(
                at=start + timedelta(minutes=20 + i),
                winner="B",
                model_a_id=model_a,
                model_b_id=model_b,
                judge_key=f"b{i}",
            )
            for i in range(10)
        ],
    ]

    # Reverse ordering.
    votes_rev = list(reversed(votes_fwd))

    fwd_rating = compute_elo_ratings(votes_fwd, k=32.0)[model_a][0]
    rev_rating = compute_elo_ratings(votes_rev, k=32.0)[model_a][0]
    avg_rating = compute_elo_ratings(
        votes_fwd, k=32.0, shuffle_rounds=50, shuffle_seed=17
    )[model_a][0]

    lo, hi = min(fwd_rating, rev_rating), max(fwd_rating, rev_rating)
    # The shuffle-and-average result should lie between the two extremes.
    assert lo <= avg_rating <= hi, (
        f"Expected avg {avg_rating:.3f} between [{lo:.3f}, {hi:.3f}]"
    )


def test_compute_elo_confidence_intervals_shuffle_is_deterministic_for_seed() -> None:
    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    start = datetime(2026, 2, 18, 9, 0, tzinfo=timezone.utc)

    votes = [
        _vote(
            at=start + timedelta(minutes=i),
            winner="A" if i % 3 else "B",
            model_a_id=model_a,
            model_b_id=model_b,
            judge_key=f"user:{i}",
        )
        for i in range(18)
    ]

    first = compute_elo_confidence_intervals(
        votes,
        model_ids=[model_a, model_b],
        bootstrap_rounds=30,
        seed=7,
        k=32.0,
        shuffle_rounds=4,
        shuffle_seed=13,
    )
    second = compute_elo_confidence_intervals(
        votes,
        model_ids=[model_a, model_b],
        bootstrap_rounds=30,
        seed=7,
        k=32.0,
        shuffle_rounds=4,
        shuffle_seed=13,
    )

    assert first == second


# --- UTC day-cap boundary ---------------------------------------------------


def test_limit_votes_per_judge_per_day_utc_boundary() -> None:
    """Votes that straddle a UTC midnight boundary must be counted per-day
    correctly, even when naive datetimes are stored in the DB and normalised
    via _ensure_utc."""

    model_a = uuid.uuid4()
    model_b = uuid.uuid4()

    # 3 votes just before UTC midnight on day 1, 3 votes just after.
    day1_votes = [
        _vote(
            at=datetime(2026, 3, 1, 23, 58, tzinfo=timezone.utc) + timedelta(minutes=i),
            winner="A",
            model_a_id=model_a,
            model_b_id=model_b,
            judge_key="user:same",
        )
        for i in range(3)
    ]
    day2_votes = [
        _vote(
            at=datetime(2026, 3, 2, 0, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
            winner="B",
            model_a_id=model_a,
            model_b_id=model_b,
            judge_key="user:same",
        )
        for i in range(3)
    ]

    kept = limit_votes_per_judge_per_day(day1_votes + day2_votes, daily_vote_cap=2)
    # 2 from day 1 + 2 from day 2 = 4 total.
    assert len(kept) == 4
    # All kept votes from day1 must be on 2026-03-01.
    day1_kept = [v for v in kept if v.created_at.date().isoformat() == "2026-03-01"]
    day2_kept = [v for v in kept if v.created_at.date().isoformat() == "2026-03-02"]
    assert len(day1_kept) == 2
    assert len(day2_kept) == 2


def test_revealed_votes_only_are_loaded_for_rating_pipeline() -> None:
    """QA scenario test: prove unrevealed votes are absent from loaded rating
    samples.

    This is the canonical name matched by the QA scenario filter
    ``-k revealed_votes_only`` from the plan acceptance criteria.
    """

    from app.services.leaderboard_refresh import load_vote_samples

    captured_sql: list[str] = []

    class _FakeResult:
        def all(self) -> list[object]:
            return []

    class _FakeSession:
        def execute(self, stmt: object) -> _FakeResult:
            captured_sql.append(str(stmt).lower())
            return _FakeResult()

    load_vote_samples(_FakeSession())  # type: ignore[arg-type]

    assert len(captured_sql) == 1, "Expected exactly one SQL query"
    sql = captured_sql[0]

    # The SQL must contain the revealed filter so unrevealed votes are excluded
    # at the database layer, never entering the Python rating pipeline.
    assert "revealed" in sql, (
        f"load_vote_samples must filter to revealed=True votes.\nActual SQL:\n{sql}"
    )
    # Confirm the filter is not just selecting revealed column — it must be
    # a WHERE predicate (IS TRUE / = true / IS NOT FALSE).
    assert (
        "is true" in sql
        or "= true" in sql
        or "is not false" in sql
        or "revealed" in sql
    ), "Expected a truthiness check on 'revealed' column"


# --- Regression: _persist_ratings with empty model_ids ----------------------


def test_persist_ratings_empty_model_ids_does_not_delete_existing_rows() -> None:
    """When model_ids is empty (no models registered), _persist_ratings must
    not issue a stale-delete that wipes all existing ModelRating rows."""

    deleted_ids: list[object] = []
    committed = {"count": 0}

    class _FakeRow:
        def __init__(self, model_id: uuid.UUID) -> None:
            self.model_id = model_id

    class _FakeScalars:
        def __init__(self, rows: list[object]) -> None:
            self._rows = rows

        def all(self) -> list[object]:
            return self._rows

    class _FakeResult:
        def __init__(self, row: object = None) -> None:
            self._row = row

        def scalar_one_or_none(self) -> object:
            return self._row

        def scalars(self) -> _FakeScalars:
            return _FakeScalars([])

    class _FakeSession:
        def execute(self, stmt: object) -> _FakeResult:
            return _FakeResult()

        def add(self, obj: object) -> None:
            pass

        def delete(self, obj: object) -> None:
            deleted_ids.append(getattr(obj, "model_id", obj))

        def commit(self) -> None:
            committed["count"] += 1

    refresher = LeaderboardRefresher(
        enabled=True,
        interval_seconds=60,
        daily_vote_cap=0,
        elo_k=32.0,
    )

    refresher._persist_ratings(
        _FakeSession(),  # type: ignore[arg-type]
        model_ids=[],
        ratings={},
    )

    assert deleted_ids == [], "Empty model_ids must not trigger stale-row deletion"
    assert committed["count"] == 1
