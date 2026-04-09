from __future__ import annotations

import builtins
import sys
from types import SimpleNamespace

import pytest

from app.utils import redis as redis_utils


@pytest.fixture(autouse=True)
def _clear_redis_client_cache() -> None:
    redis_utils.get_rate_limit_redis_client.cache_clear()
    yield
    redis_utils.get_rate_limit_redis_client.cache_clear()


def test_get_rate_limit_redis_client_returns_none_when_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        redis_utils,
        "get_settings",
        lambda: SimpleNamespace(
            rate_limit_redis_url="   ",
            rate_limit_redis_timeout_seconds=0.25,
        ),
    )

    assert redis_utils.get_rate_limit_redis_client() is None


def test_get_rate_limit_redis_client_returns_none_when_package_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        redis_utils,
        "get_settings",
        lambda: SimpleNamespace(
            rate_limit_redis_url="redis://localhost:6379/0",
            rate_limit_redis_timeout_seconds=0.25,
        ),
    )

    monkeypatch.delitem(sys.modules, "redis", raising=False)

    original_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object):
        if name == "redis":
            raise ImportError("redis unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    warnings: list[str] = []
    monkeypatch.setattr(
        redis_utils.logger, "warning", lambda message: warnings.append(message)
    )

    assert redis_utils.get_rate_limit_redis_client() is None
    assert warnings


def test_get_rate_limit_redis_client_uses_from_url_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        redis_utils,
        "get_settings",
        lambda: SimpleNamespace(
            rate_limit_redis_url="redis://localhost:6379/0",
            rate_limit_redis_timeout_seconds=0.5,
        ),
    )

    calls: list[tuple[str, dict[str, object]]] = []

    class _FakeRedis:
        @staticmethod
        def from_url(url: str, **kwargs: object) -> dict[str, object]:
            calls.append((url, dict(kwargs)))
            return {"url": url, "kwargs": dict(kwargs)}

    monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=_FakeRedis))

    first = redis_utils.get_rate_limit_redis_client()
    second = redis_utils.get_rate_limit_redis_client()

    assert first is second
    assert calls == [
        (
            "redis://localhost:6379/0",
            {
                "decode_responses": False,
                "socket_timeout": 0.5,
                "socket_connect_timeout": 0.5,
            },
        )
    ]
