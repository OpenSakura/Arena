"""app.utils.redis

Redis helpers used by Redis-backed rate limiting and shared confidence caching.

Notes:
- Redis is optional for local/dev; callers should handle ``None`` gracefully.
- We import the Redis package lazily so tests can run without it installed.
"""

from __future__ import annotations

from functools import lru_cache
import logging
from typing import Any, cast

from app.core.config import get_settings

logger = logging.getLogger(__name__)

try:
    from redis.exceptions import RedisError  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised when redis is not installed.

    class RedisError(Exception):
        """Fallback Redis error type when the redis package is unavailable."""


def _create_redis_client(*, decode_responses: bool) -> Any | None:
    settings = get_settings()
    redis_url = settings.rate_limit_redis_url.strip()
    if not redis_url:
        return None

    try:
        import redis  # pyright: ignore[reportMissingImports]
    except ImportError:
        logger.warning(
            "RATE_LIMIT_REDIS_URL is set but redis package is not installed; "
            "Redis-backed rate limiting and confidence caching are disabled"
        )
        return None

    redis_module = cast(Any, redis)
    return redis_module.Redis.from_url(
        redis_url,
        decode_responses=decode_responses,
        socket_timeout=settings.rate_limit_redis_timeout_seconds,
        socket_connect_timeout=settings.rate_limit_redis_timeout_seconds,
        max_connections=50,
        health_check_interval=30,
    )


@lru_cache(maxsize=1)
def get_rate_limit_redis_client() -> Any | None:
    """Return a lazily initialized Redis client for rate limiting.

    Returns ``None`` when Redis is not configured or the package is unavailable.
    """

    return _create_redis_client(decode_responses=False)


@lru_cache(maxsize=1)
def get_confidence_cache_redis_client() -> Any | None:
    """Return a lazily initialized Redis client for shared leaderboard caching."""

    return _create_redis_client(decode_responses=True)


def _close_cached_redis_client(getter: Any) -> None:
    try:
        client = getter()
    except Exception:  # noqa: BLE001
        return
    if client is not None:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
    getter.cache_clear()


def close_rate_limit_redis_client() -> None:
    """Close the cached Redis client (e.g. on application shutdown)."""

    _close_cached_redis_client(get_rate_limit_redis_client)


def close_confidence_cache_redis_client() -> None:
    """Close the cached shared-confidence Redis client."""

    _close_cached_redis_client(get_confidence_cache_redis_client)


def close_all_redis_clients() -> None:
    """Close all cached Redis clients (rate-limit + confidence cache)."""
    close_rate_limit_redis_client()
    close_confidence_cache_redis_client()
