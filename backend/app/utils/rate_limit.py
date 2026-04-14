"""app.utils.rate_limit

Redis-backed rolling-window rate limiter for request throttling.

Architecture
~~~~~~~~~~~~
Rate limiting in this project uses **only** Redis as the backend store so that
limits are enforced consistently across all Uvicorn worker processes.  There is
no in-memory fallback — if Redis is unavailable the limiter fails closed
(requests are blocked) to prevent abuse during outages.

How it works
~~~~~~~~~~~~
``RollingWindowRateLimiter`` divides time into fixed-size buckets
(default 10 s).  On each request it atomically:

1. Sums the request counts in the last ``ceil(window / bucket)`` buckets.
2. If the total already meets or exceeds the configured limit → **blocked**.
3. Otherwise, increments the current bucket and sets a TTL so Redis
   auto-evicts stale buckets.

Steps 1-3 are executed inside a single Lua script
(``_LUA_CHECK_AND_INCREMENT``) so the check-and-increment is atomic and free
of TOCTOU races.  On the first call the full script text is sent via ``EVAL``;
on subsequent calls the cached SHA1 digest is sent via ``EVALSHA`` to save
bandwidth.

A non-atomic fallback (``_is_limited_redis_fallback``) exists for Redis
clients that lack ``eval``/``evalsha`` support (e.g. simple test mocks).
The fallback is intentionally kept simple — it is not atomic and is only
suitable for testing or low-concurrency environments.

Key construction
~~~~~~~~~~~~~~~~
Authenticated write paths use stable keys derived from the internal user UUID.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lua script for atomic check-and-increment (prevents TOCTOU races)
# ---------------------------------------------------------------------------
# KEYS[1..N] — bucket keys covering the rolling window (newest first).
# ARGV[1]   — limit (max allowed requests in the window).
# ARGV[2]   — expire_seconds (TTL set on the current bucket key).
# ARGV[3]   — num_bucket_keys (== N, used by the loop).
#
# Returns 1 when the request should be rate-limited, 0 otherwise.
_LUA_CHECK_AND_INCREMENT = """
local bucket_key = KEYS[1]
local limit = tonumber(ARGV[1])
local expire_seconds = tonumber(ARGV[2])
local num_bucket_keys = tonumber(ARGV[3])

-- Sum all bucket counts
local total = 0
for i = 1, num_bucket_keys do
    local val = redis.call('GET', KEYS[i])
    if val then
        total = total + tonumber(val)
    end
end

-- Check against limit (>= for correct boundary)
if total >= limit then
    return 1
end

-- Atomically increment the current bucket and set expiry
redis.call('INCR', bucket_key)
redis.call('EXPIRE', bucket_key, expire_seconds)
return 0
"""


class RollingWindowRateLimiter:
    """Redis-backed rolling-window rate limiter.

    Parameters
    ----------
    limit:
        Maximum number of requests allowed within ``window_seconds``.
        Set to ``<= 0`` to disable this limiter entirely.
    window_seconds:
        Duration of the sliding window in seconds.
    bucket_seconds:
        Granularity of the time buckets.  Smaller values give finer
        granularity but use more Redis keys.  Default is 10 s.
    enabled:
        Master switch.  When ``False`` (or when ``redis_client`` is
        ``None``, or ``limit <= 0``), ``is_limited()`` always returns
        ``False``.
    now_fn:
        Injectable clock for deterministic testing.  Defaults to
        ``time.time``.
    redis_client:
        A ``redis.Redis`` instance (or compatible mock).  ``None``
        disables rate limiting.
    redis_prefix:
        Key namespace prefix used in Redis.  Default ``"arena"``.
    """

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: int,
        bucket_seconds: int = 10,
        enabled: bool = True,
        now_fn: Callable[[], float] | None = None,
        redis_client: object | None = None,
        redis_prefix: str = "arena",
    ) -> None:
        self._limit = max(int(limit), 0)
        self._window_seconds = max(int(window_seconds), 1)
        self._bucket_seconds = max(int(bucket_seconds), 1)
        self._enabled = enabled and self._limit > 0 and redis_client is not None
        self._now_fn = now_fn or time.time
        self._redis = redis_client
        self._redis_prefix = redis_prefix or "arena"

        # Cached SHA1 digest of the Lua script.  Populated lazily on the
        # first successful ``EVALSHA``/``EVAL`` call so that subsequent
        # invocations can use ``EVALSHA`` and avoid resending the full
        # script text over the wire.
        self._lua_script_sha: str | None = None

        # Use ceil(window / bucket) so we cover (roughly) one full window
        # without keeping an extra bucket past the configured window length.
        self._num_buckets = max(
            1,
            (self._window_seconds + self._bucket_seconds - 1) // self._bucket_seconds,
        )

    def is_limited(self, key: str) -> bool:
        """Return ``True`` if the request identified by *key* should be
        rate-limited (i.e. blocked).

        When Redis is unavailable the limiter **fails closed** — the
        request is blocked and the error is logged.
        """

        if not self._enabled:
            return False

        try:
            return self._is_limited_redis(key)
        except Exception:
            # Redis is the only backend; fail-closed when unavailable to
            # prevent abuse during outages.
            logger.exception("Rate limiter Redis error for key=%s", key)
            return True

    # -----------------------------------------------------------------
    # Redis implementation
    # -----------------------------------------------------------------

    def _is_limited_redis(self, key: str) -> bool:
        bucket = int(self._now_fn()) // self._bucket_seconds
        bucket_keys = [
            self._redis_bucket_key(key=key, bucket=bucket - idx)
            for idx in range(self._num_buckets)
        ]
        # The current bucket key is always the first in the list.
        current_bucket_key = bucket_keys[0]

        redis_client = self._redis
        if redis_client is None:
            # _is_limited_redis is only called when self._enabled is True,
            # which requires redis_client to be non-None (see __init__).
            # This branch is unreachable in normal operation but guards
            # against future refactors that might break that invariant.
            raise RuntimeError(
                "_is_limited_redis called with redis_client=None; "
                "this is a programming error"
            )

        expire_seconds = self._window_seconds + self._bucket_seconds

        # Try EVALSHA first (cheaper — sends only the SHA1 hash, not the
        # full script text).  Fall back to EVAL on a cache miss, which also
        # caches the script server-side for future EVALSHA calls.
        try:
            result = self._eval_lua(
                redis_client=redis_client,
                bucket_keys=bucket_keys,
                expire_seconds=expire_seconds,
            )
            return bool(int(result))
        except AttributeError:
            # Fallback for Redis clients that don't support eval/evalsha
            # (e.g. simple test mocks).
            return self._is_limited_redis_fallback(
                bucket_keys=bucket_keys,
                current_bucket_key=current_bucket_key,
                expire_seconds=expire_seconds,
                redis_client=redis_client,
            )

    def _eval_lua(
        self,
        *,
        redis_client: object,
        bucket_keys: list[str],
        expire_seconds: int,
    ) -> int:
        """Execute the Lua rate-limit script, preferring ``EVALSHA``."""

        args = [self._limit, expire_seconds, self._num_buckets]
        num_keys = len(bucket_keys)

        # Fast path: use the cached SHA if available.
        if self._lua_script_sha is not None:
            try:
                return redis_client.evalsha(  # type: ignore[attr-defined]
                    self._lua_script_sha, num_keys, *bucket_keys, *args
                )
            except Exception as exc:
                # NOSCRIPT — script was evicted from the server-side cache.
                # Fall through to full EVAL below to re-cache it.
                if "NOSCRIPT" not in str(exc):
                    raise

        # Full EVAL — the server caches the script automatically.
        result = redis_client.eval(  # type: ignore[attr-defined]
            _LUA_CHECK_AND_INCREMENT, num_keys, *bucket_keys, *args
        )

        # Cache the SHA1 digest for subsequent EVALSHA calls.  The digest
        # is deterministic (SHA1 of the script text) so it is safe to
        # compute locally.
        self._lua_script_sha = hashlib.sha1(  # noqa: S324
            _LUA_CHECK_AND_INCREMENT.encode("utf-8")
        ).hexdigest()

        return result

    def _is_limited_redis_fallback(
        self,
        *,
        bucket_keys: list[str],
        current_bucket_key: str,
        expire_seconds: int,
        redis_client: object,
    ) -> bool:
        """Non-atomic fallback for Redis clients without ``eval`` support.

        .. warning::

            This path is **not atomic** — concurrent requests can read the
            same count, all pass the limit check, and all increment.  The
            effective limit can be exceeded by up to N concurrent requests.
            Only suitable for testing or low-concurrency environments.
        """

        counts = redis_client.mget(bucket_keys)  # type: ignore[attr-defined]
        total = sum(_redis_int(value) for value in counts)
        if total >= self._limit:
            return True

        redis_client.incr(current_bucket_key)  # type: ignore[attr-defined]
        redis_client.expire(  # type: ignore[attr-defined]
            current_bucket_key, expire_seconds
        )
        return False

    def _redis_bucket_key(self, *, key: str, bucket: int) -> str:
        return f"{self._redis_prefix}:ratelimit:{key}:{bucket}"


def build_auth_rate_limit_key(*, scope: str, user_id: str) -> str:
    """Build a rate-limit key for an authenticated user.

    Keyed directly on the internal user UUID — stable and unspoofable.
    """
    return f"{scope}:user:{user_id}"


def _redis_int(value: object) -> int:
    """Coerce a Redis response value to ``int``, returning ``0`` on failure.

    Redis returns values as ``bytes`` (when ``decode_responses=False``) or
    ``str`` (when ``decode_responses=True``).  This helper normalises both
    forms plus ``None`` (missing key).
    """

    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return 0
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    try:
        return int(str(value))  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0
