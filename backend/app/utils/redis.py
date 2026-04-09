"""app.utils.redis

Redis helpers used by anonymous rate limiting.

Notes:
- Redis is optional for local/dev; callers should handle ``None`` gracefully.
- We import the Redis package lazily so tests can run without it installed.
"""

from __future__ import annotations

from functools import lru_cache
import logging
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_rate_limit_redis_client() -> Any | None:
    """Return a lazily initialized Redis client for rate limiting.

    Returns ``None`` when Redis is not configured or the package is unavailable.
    """

    settings = get_settings()
    redis_url = settings.rate_limit_redis_url.strip()
    if not redis_url:
        return None

    try:
        import redis  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "RATE_LIMIT_REDIS_URL is set but redis package is not installed; "
            "anonymous rate limiting is disabled"
        )
        return None

    return redis.Redis.from_url(
        redis_url,
        decode_responses=False,
        socket_timeout=settings.rate_limit_redis_timeout_seconds,
        socket_connect_timeout=settings.rate_limit_redis_timeout_seconds,
        max_connections=50,
        health_check_interval=30,
    )


def close_rate_limit_redis_client() -> None:
    """Close the cached Redis client (e.g. on application shutdown)."""
    try:
        client = get_rate_limit_redis_client()
    except Exception:  # noqa: BLE001
        return
    if client is not None:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
    get_rate_limit_redis_client.cache_clear()
