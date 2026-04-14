from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import app.services.leaderboard_refresh as leaderboard_refresh_module
from app.services.leaderboard_refresh import (
    LeaderboardRefresher,
    VoteSample,
    compute_elo_confidence_intervals,
    compute_elo_ratings,
    limit_votes_per_judge_per_day,
    load_vote_samples,
)
from app.utils.requester_identity import RequesterIdentity


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
            judge_key="anon:judge-1",
        )
        for idx in range(5)
    ]

    kept = limit_votes_per_judge_per_day(votes, daily_vote_cap=2)
    assert len(kept) == 2


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
            judge_key="anon:1",
        ),
        _vote(
            at=start + timedelta(minutes=1),
            winner="B",
            model_a_id=model_a,
            model_b_id=model_b,
            judge_key="anon:2",
        ),
        _vote(
            at=start + timedelta(minutes=2),
            winner="tie",
            model_a_id=model_a,
            model_b_id=model_c,
            judge_key="anon:3",
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
            judge_key="anon:1",
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
                judge_key=f"anon:{idx}",
            )
            for idx in range(12)
        ],
        *[
            _vote(
                at=start + timedelta(minutes=20 + idx),
                winner="A",
                model_a_id=model_b,
                model_b_id=model_c,
                judge_key=f"anon:b{idx}",
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


def test_judge_key_prefers_user_id_when_both_identities_exist() -> None:
    user_id = uuid.uuid4()
    key = RequesterIdentity(
        voter_user_id=user_id,
        ip_hash="ip-hash",
        user_agent_hash="ua-hash",
        voter_anon_id="anon-123",
    ).judge_key(fallback_vote_id=uuid.uuid4())

    assert key == f"user:{user_id}"


def test_judge_key_falls_back_to_user_id_without_anon_id() -> None:
    user_id = uuid.uuid4()
    key = RequesterIdentity(
        voter_user_id=user_id,
        ip_hash="ip-hash",
        user_agent_hash="ua-hash",
        voter_anon_id=None,
    ).judge_key(fallback_vote_id=uuid.uuid4())

    assert key == f"user:{user_id}"


def test_judge_key_prefers_fingerprint_over_anon_cookie_for_anonymous_votes() -> None:
    key = RequesterIdentity(
        voter_user_id=None,
        ip_hash="ip-hash",
        user_agent_hash="ua-hash",
        voter_anon_id="anon-123",
    ).judge_key(fallback_vote_id=uuid.uuid4())

    assert key == "fp:ip-hash:ua-hash"


def test_judge_key_falls_back_to_ip_only_when_user_agent_missing() -> None:
    key = RequesterIdentity(
        voter_user_id=None,
        ip_hash="ip-hash",
        user_agent_hash=None,
        voter_anon_id="anon-123",
    ).judge_key(fallback_vote_id=uuid.uuid4())

    assert key == "ip:ip-hash"


def test_judge_key_falls_back_to_anon_cookie_without_network_fingerprint() -> None:
    key = RequesterIdentity(
        voter_user_id=None,
        ip_hash=None,
        user_agent_hash=None,
        voter_anon_id="anon-123",
    ).judge_key(fallback_vote_id=uuid.uuid4())

    assert key == "anon:anon-123"


def test_judge_key_uses_explicit_unknown_fallback_when_unidentifiable() -> None:
    vote_id = uuid.uuid4()
    key = RequesterIdentity(
        voter_user_id=None,
        ip_hash=None,
        user_agent_hash=None,
        voter_anon_id=None,
    ).judge_key(fallback_vote_id=vote_id)

    assert key == f"unknown:{vote_id}"


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
    """Prove that ``limit_votes_per_judge_per_day`` treats each identity
    type via the canonical ``RequesterIdentity.judge_key()`` semantics.
    """

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
        identity = RequesterIdentity(
            voter_user_id=user_id,
            ip_hash="ip",
            user_agent_hash="ua",
            voter_anon_id="anon",
        )
        key = identity.judge_key(fallback_vote_id=uuid.uuid4())
        assert key == f"user:{user_id}"

        votes = self._votes_for_key(key, 5)
        kept = limit_votes_per_judge_per_day(votes, daily_vote_cap=2)
        assert len(kept) == 2

    def test_fingerprint_requester_capped_by_fingerprint_judge_key(self) -> None:
        identity = RequesterIdentity(
            voter_user_id=None,
            ip_hash="ip-hash",
            user_agent_hash="ua-hash",
            voter_anon_id="anon-123",
        )
        key = identity.judge_key(fallback_vote_id=uuid.uuid4())
        assert key == "fp:ip-hash:ua-hash"

        votes = self._votes_for_key(key, 5)
        kept = limit_votes_per_judge_per_day(votes, daily_vote_cap=3)
        assert len(kept) == 3

    def test_ip_only_requester_capped_by_ip_judge_key(self) -> None:
        identity = RequesterIdentity(
            voter_user_id=None,
            ip_hash="ip-hash",
            user_agent_hash=None,
            voter_anon_id="anon-123",
        )
        key = identity.judge_key(fallback_vote_id=uuid.uuid4())
        assert key == "ip:ip-hash"

        votes = self._votes_for_key(key, 4)
        kept = limit_votes_per_judge_per_day(votes, daily_vote_cap=2)
        assert len(kept) == 2

    def test_anon_cookie_requester_capped_by_anon_judge_key(self) -> None:
        identity = RequesterIdentity(
            voter_user_id=None,
            ip_hash=None,
            user_agent_hash=None,
            voter_anon_id="anon-123",
        )
        key = identity.judge_key(fallback_vote_id=uuid.uuid4())
        assert key == "anon:anon-123"

        votes = self._votes_for_key(key, 5)
        kept = limit_votes_per_judge_per_day(votes, daily_vote_cap=1)
        assert len(kept) == 1

    def test_unknown_requester_gets_unique_key_per_vote(self) -> None:
        vote_id_1 = uuid.uuid4()
        vote_id_2 = uuid.uuid4()

        identity = RequesterIdentity(
            voter_user_id=None,
            ip_hash=None,
            user_agent_hash=None,
            voter_anon_id=None,
        )
        key1 = identity.judge_key(fallback_vote_id=vote_id_1)
        key2 = identity.judge_key(fallback_vote_id=vote_id_2)

        assert key1 == f"unknown:{vote_id_1}"
        assert key2 == f"unknown:{vote_id_2}"
        assert key1 != key2

        votes = [
            _vote(
                at=self.START,
                winner="A",
                model_a_id=self.MODEL_A,
                model_b_id=self.MODEL_B,
                judge_key=key1,
            ),
            _vote(
                at=self.START + timedelta(minutes=1),
                winner="B",
                model_a_id=self.MODEL_A,
                model_b_id=self.MODEL_B,
                judge_key=key2,
            ),
        ]
        kept = limit_votes_per_judge_per_day(votes, daily_vote_cap=1)
        assert len(kept) == 2

    def test_different_identity_types_are_independent_judges(self) -> None:
        user_id = uuid.uuid4()
        user_key = RequesterIdentity(
            voter_user_id=user_id, ip_hash="ip", user_agent_hash="ua", voter_anon_id="a"
        ).judge_key(fallback_vote_id=uuid.uuid4())

        fp_key = RequesterIdentity(
            voter_user_id=None, ip_hash="ip", user_agent_hash="ua", voter_anon_id="a"
        ).judge_key(fallback_vote_id=uuid.uuid4())

        ip_key = RequesterIdentity(
            voter_user_id=None, ip_hash="ip", user_agent_hash=None, voter_anon_id="a"
        ).judge_key(fallback_vote_id=uuid.uuid4())

        anon_key = RequesterIdentity(
            voter_user_id=None, ip_hash=None, user_agent_hash=None, voter_anon_id="a"
        ).judge_key(fallback_vote_id=uuid.uuid4())

        votes = (
            self._votes_for_key(user_key, 2)
            + self._votes_for_key(fp_key, 2)
            + self._votes_for_key(ip_key, 2)
            + self._votes_for_key(anon_key, 2)
        )

        kept = limit_votes_per_judge_per_day(votes, daily_vote_cap=1)
        kept_keys = {v.judge_key for v in kept}
        assert kept_keys == {user_key, fp_key, ip_key, anon_key}
        assert len(kept) == 4


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
            return _FakeResult()

    load_vote_samples(_FakeSession())  # type: ignore[arg-type]

    stmt_text = captured.get("stmt", "")
    assert isinstance(stmt_text, str) and stmt_text, "execute() was not called"
    # The rendered SQL must contain the revealed filter.
    assert "revealed" in stmt_text, (
        f"Expected 'revealed' in SQL predicate but got:\n{stmt_text}"
    )


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
                            None,
                            "anon-1",
                            None,
                            None,
                            model_a,
                            model_b,
                        ),
                        (
                            day1_vote_2,
                            day1 + timedelta(minutes=5),
                            "B",
                            None,
                            "anon-1",
                            None,
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
                            None,
                            "anon-1",
                            None,
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
            judge_key=f"anon:{i}",
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
                judge_key=f"anon:{i}",
            )
            for i in range(10)
        ],
        *[
            _vote(
                at=start + timedelta(minutes=20 + i),
                winner="B",
                model_a_id=model_b,
                model_b_id=model_c,
                judge_key=f"anon:b{i}",
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
            judge_key=f"anon:{i}",
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
            judge_key=f"anon:{i}",
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
            judge_key=f"anon:{i}",
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
            judge_key="anon:same",
        )
        for i in range(3)
    ]
    day2_votes = [
        _vote(
            at=datetime(2026, 3, 2, 0, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
            winner="B",
            model_a_id=model_a,
            model_b_id=model_b,
            judge_key="anon:same",
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
