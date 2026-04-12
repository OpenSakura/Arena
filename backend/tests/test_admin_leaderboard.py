from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.routes import admin_leaderboard


@dataclass
class _Status:
    enabled: bool
    interval_seconds: int
    daily_vote_cap: int
    last_attempted_at: datetime | None
    last_succeeded_at: datetime | None
    last_error: str | None
    total_refreshes: int


class _Refresher:
    def __init__(self, status: _Status) -> None:
        self._status = status
        self.refresh_calls = 0

    def get_status(self) -> _Status:
        return self._status

    def refresh_once(self) -> None:
        self.refresh_calls += 1
        self._status.total_refreshes += 1


def test_get_refresh_status_returns_current_refresher_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 2, 18, 10, 0, tzinfo=timezone.utc)
    status = _Status(
        enabled=True,
        interval_seconds=300,
        daily_vote_cap=40,
        last_attempted_at=now,
        last_succeeded_at=now,
        last_error=None,
        total_refreshes=12,
    )
    refresher = _Refresher(status)
    monkeypatch.setattr(
        admin_leaderboard,
        "get_leaderboard_refresher",
        lambda: refresher,
    )

    response = asyncio.run(admin_leaderboard.get_refresh_status())

    assert response == {
        "enabled": True,
        "interval_seconds": 300,
        "daily_vote_cap": 40,
        "last_attempted_at": now,
        "last_succeeded_at": now,
        "last_error": None,
        "total_refreshes": 12,
    }


def test_run_refresh_now_returns_success_when_last_error_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 2, 18, 10, 5, tzinfo=timezone.utc)
    status = _Status(
        enabled=True,
        interval_seconds=300,
        daily_vote_cap=40,
        last_attempted_at=now,
        last_succeeded_at=now,
        last_error=None,
        total_refreshes=13,
    )
    refresher = _Refresher(status)
    monkeypatch.setattr(
        admin_leaderboard,
        "get_leaderboard_refresher",
        lambda: refresher,
    )

    response = asyncio.run(admin_leaderboard.run_refresh_now())

    assert refresher.refresh_calls == 1
    assert response == {
        "ok": True,
        "last_succeeded_at": now,
        "last_error": None,
        "total_refreshes": 14,
    }


def test_run_refresh_now_returns_failure_when_last_error_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 2, 18, 10, 10, tzinfo=timezone.utc)
    status = _Status(
        enabled=True,
        interval_seconds=300,
        daily_vote_cap=40,
        last_attempted_at=now,
        last_succeeded_at=now,
        last_error="database unavailable",
        total_refreshes=14,
    )
    refresher = _Refresher(status)
    monkeypatch.setattr(
        admin_leaderboard,
        "get_leaderboard_refresher",
        lambda: refresher,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(admin_leaderboard.run_refresh_now())

    assert refresher.refresh_calls == 1
    assert exc_info.value.status_code == 500
    assert "database unavailable" in exc_info.value.detail


def test_refresh_runs_via_asyncio_to_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 2, 18, 10, 15, tzinfo=timezone.utc)
    status = _Status(
        enabled=True,
        interval_seconds=300,
        daily_vote_cap=40,
        last_attempted_at=now,
        last_succeeded_at=now,
        last_error=None,
        total_refreshes=15,
    )
    refresher = _Refresher(status)
    monkeypatch.setattr(
        admin_leaderboard,
        "get_leaderboard_refresher",
        lambda: refresher,
    )

    to_thread_funcs: list[str] = []
    _real_to_thread = asyncio.to_thread

    async def _tracking_to_thread(func, *args, **kwargs):  # type: ignore[no-untyped-def]
        to_thread_funcs.append(getattr(func, "__name__", str(func)))
        return await _real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(admin_leaderboard.asyncio, "to_thread", _tracking_to_thread)

    asyncio.run(admin_leaderboard.run_refresh_now())

    assert refresher.refresh_calls == 1
    assert "refresh_once" in to_thread_funcs


class _SkipRefresher:
    """Simulates advisory-lock-busy: refresh_once does not increment total_refreshes."""

    def __init__(self, status: _Status) -> None:
        self._status = status
        self.refresh_calls = 0

    def get_status(self) -> _Status:
        return self._status

    def refresh_once(self) -> None:
        self.refresh_calls += 1


def test_run_refresh_now_returns_409_when_advisory_lock_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 2, 18, 10, 20, tzinfo=timezone.utc)
    status = _Status(
        enabled=True,
        interval_seconds=300,
        daily_vote_cap=40,
        last_attempted_at=now,
        last_succeeded_at=now,
        last_error=None,
        total_refreshes=16,
    )
    refresher = _SkipRefresher(status)
    monkeypatch.setattr(
        admin_leaderboard,
        "get_leaderboard_refresher",
        lambda: refresher,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(admin_leaderboard.run_refresh_now())

    assert refresher.refresh_calls == 1
    assert exc_info.value.status_code == 409
    assert "advisory lock busy" in exc_info.value.detail
