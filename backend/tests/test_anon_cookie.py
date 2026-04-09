from __future__ import annotations

from fastapi import Request, Response

from app.utils.anon import get_or_set_anon_id as get_battle_anon_id
from app.utils.anon import get_or_set_anon_id as get_vote_anon_id


def _request(cookie: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if cookie is not None:
        headers.append((b"cookie", cookie.encode("ascii")))

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


def test_battle_anon_cookie_sets_secure_flag_when_enabled() -> None:
    response = Response()

    anon_id = get_battle_anon_id(request=_request(), response=response, secure=True)

    set_cookie = response.headers.get("set-cookie")
    assert anon_id
    assert set_cookie is not None
    assert "arena_anon_id=" in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "secure" in set_cookie.lower()


def test_vote_anon_cookie_omits_secure_flag_when_disabled() -> None:
    response = Response()

    anon_id = get_vote_anon_id(request=_request(), response=response, secure=False)

    set_cookie = response.headers.get("set-cookie")
    assert anon_id
    assert set_cookie is not None
    assert "secure" not in set_cookie.lower()


def test_vote_anon_cookie_reuses_existing_cookie_without_resetting() -> None:
    response = Response()

    anon_id = get_vote_anon_id(
        request=_request("arena_anon_id=existing-cookie"),
        response=response,
        secure=True,
    )

    assert anon_id == "existing-cookie"
    assert response.headers.get("set-cookie") is None


def test_vote_anon_cookie_replaces_invalid_existing_cookie() -> None:
    response = Response()
    invalid_cookie = "a" * 80

    anon_id = get_vote_anon_id(
        request=_request(f"arena_anon_id={invalid_cookie}"),
        response=response,
        secure=True,
    )

    set_cookie = response.headers.get("set-cookie")
    assert anon_id != invalid_cookie
    assert len(anon_id) == 32
    assert set_cookie is not None


def test_battle_anon_cookie_replaces_invalid_existing_cookie() -> None:
    response = Response()
    invalid_cookie = "<>bad-cookie<>"

    anon_id = get_battle_anon_id(
        request=_request(f"arena_anon_id={invalid_cookie}"),
        response=response,
        secure=False,
    )

    set_cookie = response.headers.get("set-cookie")
    assert anon_id != invalid_cookie
    assert len(anon_id) == 32
    assert set_cookie is not None
