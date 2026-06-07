from __future__ import annotations


class _FakeRedis:
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


def test_rolling_window_rate_limiter_limits_after_threshold() -> None:
    from app.utils.rate_limit import RollingWindowRateLimiter

    now = 1_700_000_000.0

    def now_fn() -> float:
        return now

    limiter = RollingWindowRateLimiter(
        limit=2,
        window_seconds=60,
        bucket_seconds=10,
        now_fn=now_fn,
        redis_client=_FakeRedis(),
    )

    key = "k"
    assert limiter.is_limited(key) is False
    assert limiter.is_limited(key) is False
    assert limiter.is_limited(key) is True


def test_rolling_window_rate_limiter_expires_old_buckets() -> None:
    from app.utils.rate_limit import RollingWindowRateLimiter

    now = 1_700_000_000.0

    def now_fn() -> float:
        return now

    limiter = RollingWindowRateLimiter(
        limit=1,
        window_seconds=20,
        bucket_seconds=10,
        now_fn=now_fn,
        redis_client=_FakeRedis(),
    )

    key = "k"
    assert limiter.is_limited(key) is False
    assert limiter.is_limited(key) is True

    # Move far enough ahead that old buckets are dropped.
    now += 60
    assert limiter.is_limited(key) is False


def test_rolling_window_rate_limiter_honors_window_boundary() -> None:
    from app.utils.rate_limit import RollingWindowRateLimiter

    now = 0.0

    def now_fn() -> float:
        return now

    limiter = RollingWindowRateLimiter(
        limit=1,
        window_seconds=60,
        bucket_seconds=10,
        now_fn=now_fn,
        redis_client=_FakeRedis(),
    )

    key = "k"
    assert limiter.is_limited(key) is False
    assert limiter.is_limited(key) is True

    # At t=61s, the first request at t=0 should be outside the 60s window.
    now = 61.0
    assert limiter.is_limited(key) is False


def test_rolling_window_rate_limiter_uses_redis_buckets() -> None:
    from app.utils.rate_limit import RollingWindowRateLimiter

    now = 1_700_000_000.0

    def now_fn() -> float:
        return now

    limiter = RollingWindowRateLimiter(
        limit=2,
        window_seconds=60,
        bucket_seconds=10,
        now_fn=now_fn,
        redis_client=_FakeRedis(),
        redis_prefix="test",
    )

    key = "k"
    assert limiter.is_limited(key) is False
    assert limiter.is_limited(key) is False
    assert limiter.is_limited(key) is True


def test_rolling_window_rate_limiter_is_disabled_without_redis() -> None:
    from app.utils.rate_limit import RollingWindowRateLimiter

    limiter = RollingWindowRateLimiter(
        limit=1,
        window_seconds=60,
        bucket_seconds=10,
        redis_client=None,
    )

    key = "k"
    assert limiter.is_limited(key) is False
    assert limiter.is_limited(key) is False


def test_rolling_window_rate_limiter_fails_closed_on_redis_errors() -> None:
    """When Redis is unavailable, requests should be BLOCKED (fail-closed)
    to prevent abuse during outages."""

    from app.utils.rate_limit import RollingWindowRateLimiter

    class BrokenRedis:
        def incr(self, key: str) -> int:
            _ = key
            raise RuntimeError("redis unavailable")

        def mget(self, keys: list[str]) -> list[int | None]:
            raise RuntimeError("redis unavailable")

    limiter = RollingWindowRateLimiter(
        limit=1,
        window_seconds=60,
        bucket_seconds=10,
        redis_client=BrokenRedis(),
    )

    key = "k"
    # Fail-closed: both calls should be limited (blocked).
    assert limiter.is_limited(key) is True
    assert limiter.is_limited(key) is True


def test_rolling_window_rate_limiter_evalsha_optimization() -> None:
    """Verify that EVALSHA is used after the first successful EVAL."""

    from app.utils.rate_limit import RollingWindowRateLimiter

    eval_calls: list[str] = []

    class TrackingRedis(_FakeRedis):
        def eval(self, script: str, num_keys: int, *args: object) -> int:
            eval_calls.append("eval")
            return 0

        def evalsha(self, sha: str, num_keys: int, *args: object) -> int:
            eval_calls.append("evalsha")
            return 0

    limiter = RollingWindowRateLimiter(
        limit=10,
        window_seconds=60,
        bucket_seconds=10,
        redis_client=TrackingRedis(),
    )

    key = "k"
    limiter.is_limited(key)  # first call → EVAL
    limiter.is_limited(key)  # second call → EVALSHA
    limiter.is_limited(key)  # third call → EVALSHA

    assert eval_calls == ["eval", "evalsha", "evalsha"]


def test_rolling_window_rate_limiter_evalsha_noscript_fallback() -> None:
    """When EVALSHA returns NOSCRIPT (script evicted from server cache),
    it should fall back to EVAL and re-cache the SHA."""

    from app.utils.rate_limit import RollingWindowRateLimiter

    eval_calls: list[str] = []

    class NoscriptRedis(_FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self._evalsha_fail_next = False

        def eval(self, script: str, num_keys: int, *args: object) -> int:
            eval_calls.append("eval")
            return 0

        def evalsha(self, sha: str, num_keys: int, *args: object) -> int:
            if self._evalsha_fail_next:
                self._evalsha_fail_next = False
                raise Exception("NOSCRIPT No matching script")
            eval_calls.append("evalsha")
            return 0

    redis = NoscriptRedis()
    limiter = RollingWindowRateLimiter(
        limit=10,
        window_seconds=60,
        bucket_seconds=10,
        redis_client=redis,
    )

    key = "k"
    limiter.is_limited(key)  # EVAL (first call)
    limiter.is_limited(key)  # EVALSHA (cached)

    # Simulate server-side script eviction.
    redis._evalsha_fail_next = True
    limiter.is_limited(key)  # EVALSHA fails → EVAL

    assert eval_calls == ["eval", "evalsha", "eval"]


def test_rolling_window_rate_limiter_evalsha_redis_noscript_error_fallback() -> None:
    from redis.exceptions import NoScriptError

    from app.utils.rate_limit import RollingWindowRateLimiter

    eval_calls: list[str] = []

    class NoscriptRedis(_FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self._evalsha_fail_next = False

        def eval(self, script: str, num_keys: int, *args: object) -> int:
            eval_calls.append("eval")
            return 0

        def evalsha(self, sha: str, num_keys: int, *args: object) -> int:
            if self._evalsha_fail_next:
                self._evalsha_fail_next = False
                raise NoScriptError("No matching script. Please use EVAL.")
            eval_calls.append("evalsha")
            return 0

    redis = NoscriptRedis()
    limiter = RollingWindowRateLimiter(
        limit=10,
        window_seconds=60,
        bucket_seconds=10,
        redis_client=redis,
    )

    key = "k"
    assert limiter.is_limited(key) is False
    assert limiter.is_limited(key) is False

    redis._evalsha_fail_next = True
    assert limiter.is_limited(key) is False

    assert eval_calls == ["eval", "evalsha", "eval"]
