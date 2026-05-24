from __future__ import annotations

import asyncio
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from threading import Lock
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse
import uuid

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes import auth, me as me_route
from app.core import crypto
from app.db.base import Base
from app.models.auth_session import AuthSession
from app.models.user import User, UserProfile
from app.services import auth_session
from app.services.oidc_client import OIDCTokenExchangeError


_HASH_SECRET = "test-auth-session-hash-secret"
_AUTH_CODE = "raw-auth-code"


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type: JSONB, _compiler: object, **_kw: object) -> str:
    return "JSON"


@dataclass(frozen=True)
class _AuthHarness:
    app: FastAPI
    session_factory: sessionmaker[Session]
    settings: SimpleNamespace
    oidc_client: _FakeOIDCClient


class _FakeOIDCClient:
    def __init__(self) -> None:
        self.authorization_requests = 0
        self.exchange_calls: list[dict[str, str]] = []
        self.exchange_error: Exception | None = None
        self.exchange_delay_seconds = 0.0
        self._lock = Lock()

    async def create_authorization_request(self) -> SimpleNamespace:
        self.authorization_requests += 1
        count = self.authorization_requests
        nonce = f"nonce-{count}"
        code_verifier = f"code-verifier-{count}"
        return SimpleNamespace(
            authorization_url=(
                "https://issuer.example/authorize?"
                "response_type=code&client_id=arena-client&"
                f"state=provider-state-{count}&nonce={nonce}&"
                "code_challenge=challenge&code_challenge_method=S256"
            ),
            state=f"provider-state-{count}",
            nonce=nonce,
            code_verifier=code_verifier,
            code_challenge="challenge",
            redirect_uri="https://arena.example/api/v1/auth/callback",
        )

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        nonce: str,
    ) -> SimpleNamespace:
        if self.exchange_delay_seconds:
            await asyncio.sleep(self.exchange_delay_seconds)
        with self._lock:
            self.exchange_calls.append(
                {"code": code, "code_verifier": code_verifier, "nonce": nonce}
            )
        if self.exchange_error is not None:
            raise self.exchange_error
        return SimpleNamespace(
            claims={
                "iss": "https://issuer.example",
                "sub": "user-123",
                "email": "user@example.test",
                "groups": ["arena_admin"],
                "name": "Test User",
                "preferred_username": "test-user",
                "nonce": nonce,
                "access_token": "raw-access-token",
                "accessToken": "raw-access-token-camel",
                "ACCESS-TOKEN": "raw-access-token-hyphen",
                "refresh_token": "raw-refresh-token",
                "refreshToken": "raw-refresh-token-camel",
                "REFRESH-TOKEN": "raw-refresh-token-hyphen",
                "id_token": "raw-id-token",
                "idToken": "raw-id-token-camel",
                "ID-TOKEN": "raw-id-token-hyphen",
                "client_secret": "raw-client-secret",
                "clientSecret": "raw-client-secret-camel",
                "CLIENT-SECRET": "raw-client-secret-hyphen",
                "code_verifier": code_verifier,
                "codeVerifier": "raw-code-verifier-camel",
                "CODE-VERIFIER": "raw-code-verifier-hyphen",
                "auth_code": code,
                "authCode": "raw-auth-code-camel",
                "AUTH-CODE": "raw-auth-code-hyphen",
                "authorization_code": "raw-authorization-code",
                "authorizationCode": "raw-authorization-code-camel",
                "AUTHORIZATION-CODE": "raw-authorization-code-hyphen",
                "code": "raw-code-claim",
                "NoNcE": "raw-nonce-mixed-case",
            },
            token_type="Bearer",
            expires_in=300,
            scope="openid email profile",
        )


@pytest.fixture(autouse=True)
def _crypto_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    settings = SimpleNamespace(
        arena_master_key=Fernet.generate_key().decode("ascii"),
        arena_master_key_old="",
    )
    monkeypatch.setattr(crypto, "get_settings", lambda: settings)
    crypto.reset_fernet()
    yield
    crypto.reset_fernet()


@pytest.fixture()
def harness(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_AuthHarness]:
    settings = _settings()
    oidc_client = _FakeOIDCClient()
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'auth-routes.db'}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    def override_db() -> Iterator[Session]:
        with session_factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1")
    app.dependency_overrides[auth.get_db] = override_db
    app.dependency_overrides[auth.get_settings] = lambda: settings
    app.dependency_overrides[auth.get_oidc_confidential_client] = lambda: oidc_client
    monkeypatch.setattr(me_route, "get_settings", lambda: settings)

    yield _AuthHarness(
        app=app,
        session_factory=session_factory,
        settings=settings,
        oidc_client=oidc_client,
    )

    engine.dispose()


def test_login_redirect_sets_binding_cookie_and_sanitizes_return_to(
    harness: _AuthHarness,
) -> None:
    with TestClient(harness.app) as client:
        response = client.get(
            "/api/v1/auth/login?returnTo=https%3A%2F%2Fevil.example%2Fsteal",
            follow_redirects=False,
        )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("https://issuer.example/authorize?")
    params = parse_qs(urlparse(location).query)
    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["arena-client"]
    assert params["nonce"] == ["nonce-1"]
    assert params["state"] != ["provider-state-1"]
    assert harness.oidc_client.authorization_requests == 1

    cookie_header = _single_set_cookie(response, harness.settings.auth_login_state_cookie_name)
    assert f"{harness.settings.auth_login_state_cookie_name}=" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header
    assert "Path=/api/v1/auth/callback" in cookie_header
    assert "Max-Age=600" in cookie_header
    assert "Secure" not in cookie_header

    with harness.session_factory() as db:
        login_state = auth_session.load_oauth_login_state(
            db,
            state=params["state"][0],
            settings=harness.settings,
        )
        assert login_state is not None
        assert login_state.return_to == "/"
        assert login_state.consumed_at is None


def test_login_cookie_is_secure_in_staging_even_if_setting_is_false(
    harness: _AuthHarness,
) -> None:
    harness.settings.app_env = "staging"
    harness.settings.auth_cookie_secure = False

    with TestClient(harness.app, base_url="https://testserver") as client:
        response = client.get("/api/v1/auth/login", follow_redirects=False)
        state = _state_from_location(response.headers["location"])
        callback = client.get(
            f"/api/v1/auth/callback?code={_AUTH_CODE}&state={state}",
            follow_redirects=False,
        )

    cookie_header = _single_set_cookie(response, harness.settings.auth_login_state_cookie_name)
    assert "Secure" in cookie_header
    session_cookie = _single_set_cookie(callback, harness.settings.auth_session_cookie_name)
    assert "Secure" in session_cookie


def test_open_redirect_return_to_is_sanitized_on_login(harness: _AuthHarness) -> None:
    with TestClient(harness.app) as client:
        response = client.get(
            "/api/v1/auth/login?returnTo=//evil.example/callback",
            follow_redirects=False,
        )

    assert response.status_code == 303
    state = _state_from_location(response.headers["location"])
    with harness.session_factory() as db:
        login_state = auth_session.load_oauth_login_state(
            db,
            state=state,
            settings=harness.settings,
        )
        assert login_state is not None
        assert login_state.return_to == "/"


def test_same_origin_absolute_return_to_is_sanitized_to_relative(
    harness: _AuthHarness,
) -> None:
    with TestClient(harness.app) as client:
        response = client.get(
            "/api/v1/auth/login?returnTo=https%3A%2F%2Farena.example%2Fleaderboard%3Ftab%3Dtop%23today",
            follow_redirects=False,
        )

    assert response.status_code == 303
    state = _state_from_location(response.headers["location"])
    with harness.session_factory() as db:
        login_state = auth_session.load_oauth_login_state(
            db,
            state=state,
            settings=harness.settings,
        )
        assert login_state is not None
        assert login_state.return_to == "/leaderboard?tab=top#today"


def test_callback_success_consumes_state_upserts_user_sets_session_and_session_endpoint(
    harness: _AuthHarness,
) -> None:
    with TestClient(harness.app) as client:
        login = client.get(
            "/api/v1/auth/login?returnTo=/battles/abc%3Ftab%3Dvote%23details",
            follow_redirects=False,
        )
        state = _state_from_location(login.headers["location"])
        callback = client.get(
            f"/api/v1/auth/callback?code={_AUTH_CODE}&state={state}",
            follow_redirects=False,
        )
        session_response = client.get("/api/v1/auth/session")

    assert callback.status_code == 303
    assert callback.headers["location"] == "/battles/abc?tab=vote#details"
    assert harness.oidc_client.exchange_calls == [
        {"code": _AUTH_CODE, "code_verifier": "code-verifier-1", "nonce": "nonce-1"}
    ]

    session_cookie = _single_set_cookie(callback, harness.settings.auth_session_cookie_name)
    assert f"{harness.settings.auth_session_cookie_name}=" in session_cookie
    assert "HttpOnly" in session_cookie
    assert "SameSite=lax" in session_cookie
    assert "Path=/" in session_cookie
    assert "Max-Age=3600" in session_cookie
    assert "Secure" not in session_cookie
    cleared_login_cookie = _single_set_cookie(
        callback,
        harness.settings.auth_login_state_cookie_name,
    )
    assert "Max-Age=0" in cleared_login_cookie

    body = session_response.json()
    assert session_response.status_code == 200
    assert body["authenticated"] is True
    assert body["is_admin"] is True
    assert body["user"]["oidc_issuer"] == "https://issuer.example"
    assert body["user"]["oidc_sub"] == "user-123"
    assert body["profile"] is None
    assert isinstance(body["csrf_token"], str)
    body_json = json.dumps(body)
    assert "access_token" not in body_json
    assert "refresh_token" not in body_json
    assert "id_token" not in body_json
    assert "raw-access-token" not in body_json
    assert "raw-refresh-token" not in body_json
    assert "raw-id-token" not in body_json

    with harness.session_factory() as db:
        login_state = auth_session.load_oauth_login_state(
            db,
            state=state,
            settings=harness.settings,
        )
        assert login_state is not None
        assert login_state.consumed_at is not None
        assert db.scalar(select(func.count()).select_from(User)) == 1
        auth_row = db.execute(select(AuthSession)).scalar_one()
        _assert_hmac(body["csrf_token"], auth_row.csrf_token_hash)
        serialized_row = _serialized_row(auth_row)
        assert body["csrf_token"] not in serialized_row
        assert _AUTH_CODE not in serialized_row
        assert "nonce" not in auth_row.claims
        assert auth_row.claims["iss"] == "https://issuer.example"
        assert auth_row.claims["sub"] == "user-123"
        assert auth_row.claims["email"] == "user@example.test"
        assert auth_row.claims["groups"] == ["arena_admin"]
        assert auth_row.claims["name"] == "Test User"
        assert auth_row.claims["preferred_username"] == "test-user"
        for sensitive_key in (
            "access_token",
            "accessToken",
            "ACCESS-TOKEN",
            "refresh_token",
            "refreshToken",
            "REFRESH-TOKEN",
            "id_token",
            "idToken",
            "ID-TOKEN",
            "client_secret",
            "clientSecret",
            "CLIENT-SECRET",
            "code_verifier",
            "codeVerifier",
            "CODE-VERIFIER",
            "auth_code",
            "authCode",
            "AUTH-CODE",
            "authorization_code",
            "authorizationCode",
            "AUTHORIZATION-CODE",
            "code",
            "NoNcE",
        ):
            assert sensitive_key not in auth_row.claims
        for raw_secret in (
            "raw-access-token",
            "raw-access-token-camel",
            "raw-access-token-hyphen",
            "raw-refresh-token",
            "raw-refresh-token-camel",
            "raw-refresh-token-hyphen",
            "raw-id-token",
            "raw-id-token-camel",
            "raw-id-token-hyphen",
            "raw-client-secret",
            "raw-client-secret-camel",
            "raw-client-secret-hyphen",
            "code-verifier-1",
            "raw-code-verifier-camel",
            "raw-code-verifier-hyphen",
            _AUTH_CODE,
            "raw-auth-code-camel",
            "raw-auth-code-hyphen",
            "raw-authorization-code",
            "raw-authorization-code-camel",
            "raw-authorization-code-hyphen",
            "raw-code-claim",
            "raw-nonce-mixed-case",
        ):
            assert raw_secret not in serialized_row


def test_session_safe_claims_removes_sensitive_variants_without_mutating_input() -> None:
    raw_claims = {
        "iss": "https://issuer.example",
        "sub": "user-123",
        "email": "user@example.test",
        "groups": ["arena_admin"],
        "display_name": "Aya",
        "nonce": "raw-nonce",
        "NoNcE": "raw-nonce-case",
        "accessToken": "raw-access-token",
        "refresh-token": "raw-refresh-token",
        "ID_TOKEN": "raw-id-token",
        "clientSecret": "raw-client-secret",
        "codeVerifier": "raw-code-verifier",
        "auth-code": "raw-auth-code",
        "authorizationCode": "raw-authorization-code",
        "code": "raw-code",
    }

    safe_claims = auth._session_safe_claims(raw_claims)

    assert raw_claims["nonce"] == "raw-nonce"
    assert raw_claims["accessToken"] == "raw-access-token"
    assert safe_claims == {
        "iss": "https://issuer.example",
        "sub": "user-123",
        "email": "user@example.test",
        "groups": ["arena_admin"],
        "display_name": "Aya",
    }


@pytest.mark.parametrize(
    ("path", "expected_name"),
    [
        ("/api/v1/auth/callback?code=abc", "callback_missing_state"),
        ("/api/v1/auth/callback?error=access_denied&error_description=raw-secret", "provider_error"),
    ],
)
def test_callback_missing_state_and_provider_error_clear_cookie_without_exchange(
    harness: _AuthHarness,
    path: str,
    expected_name: str,
) -> None:
    _ = expected_name
    with TestClient(harness.app) as client:
        login = client.get("/api/v1/auth/login", follow_redirects=False)
        assert login.status_code == 303
        response = client.get(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/?auth_error=login"
    assert harness.oidc_client.exchange_calls == []
    cookie_header = _single_set_cookie(response, harness.settings.auth_login_state_cookie_name)
    assert "Max-Age=0" in cookie_header
    assert "raw-secret" not in str(response.headers)


def test_provider_error_with_state_consumes_bound_state_without_exchange(
    harness: _AuthHarness,
) -> None:
    with TestClient(harness.app) as client:
        login = client.get("/api/v1/auth/login", follow_redirects=False)
        state = _state_from_location(login.headers["location"])
        response = client.get(
            f"/api/v1/auth/callback?error=access_denied&state={state}",
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/?auth_error=login"
    assert harness.oidc_client.exchange_calls == []
    with harness.session_factory() as db:
        login_state = auth_session.load_oauth_login_state(
            db,
            state=state,
            settings=harness.settings,
        )
        assert login_state is not None
        assert login_state.consumed_at is not None
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0


def test_callback_missing_code_with_valid_state_rejects_without_exchange(
    harness: _AuthHarness,
) -> None:
    with TestClient(harness.app) as client:
        login = client.get("/api/v1/auth/login", follow_redirects=False)
        state = _state_from_location(login.headers["location"])
        response = client.get(
            f"/api/v1/auth/callback?state={state}",
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/?auth_error=login"
    assert harness.oidc_client.exchange_calls == []
    assert "Max-Age=0" in _single_set_cookie(
        response,
        harness.settings.auth_login_state_cookie_name,
    )
    with harness.session_factory() as db:
        login_state = auth_session.load_oauth_login_state(
            db,
            state=state,
            settings=harness.settings,
        )
        assert login_state is not None
        assert login_state.consumed_at is None
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0


def test_callback_missing_binding_cookie_rejects_before_token_exchange(
    harness: _AuthHarness,
) -> None:
    with TestClient(harness.app) as browser_a:
        login = browser_a.get("/api/v1/auth/login", follow_redirects=False)
        state = _state_from_location(login.headers["location"])

    with TestClient(harness.app) as browser_b:
        response = browser_b.get(
            f"/api/v1/auth/callback?code={_AUTH_CODE}&state={state}",
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/?auth_error=login"
    assert harness.oidc_client.exchange_calls == []
    with harness.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0


def test_callback_wrong_binding_cookie_rejects_before_token_exchange(
    harness: _AuthHarness,
) -> None:
    with TestClient(harness.app) as client:
        login = client.get("/api/v1/auth/login", follow_redirects=False)
        state = _state_from_location(login.headers["location"])
        client.cookies.set(
            harness.settings.auth_login_state_cookie_name,
            "wrong-binding-token",
            domain="testserver.local",
            path="/api/v1/auth/callback",
        )
        response = client.get(
            f"/api/v1/auth/callback?code={_AUTH_CODE}&state={state}",
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/?auth_error=login"
    assert harness.oidc_client.exchange_calls == []


def test_callback_invalid_state_rejects_before_token_exchange(
    harness: _AuthHarness,
) -> None:
    with TestClient(harness.app) as client:
        login = client.get("/api/v1/auth/login", follow_redirects=False)
        assert login.status_code == 303
        response = client.get(
            f"/api/v1/auth/callback?code={_AUTH_CODE}&state=not-the-state",
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/?auth_error=login"
    assert harness.oidc_client.exchange_calls == []


def test_copied_callback_url_from_another_browser_is_rejected_before_exchange(
    harness: _AuthHarness,
) -> None:
    with TestClient(harness.app) as original_browser:
        login = original_browser.get("/api/v1/auth/login", follow_redirects=False)
        state = _state_from_location(login.headers["location"])
        copied_url = f"/api/v1/auth/callback?code={_AUTH_CODE}&state={state}"

    with TestClient(harness.app) as copied_browser:
        response = copied_browser.get(copied_url, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/?auth_error=login"
    assert harness.oidc_client.exchange_calls == []


def test_replayed_state_cannot_create_second_session(harness: _AuthHarness) -> None:
    with TestClient(harness.app) as client:
        login = client.get("/api/v1/auth/login", follow_redirects=False)
        state = _state_from_location(login.headers["location"])
        first = client.get(
            f"/api/v1/auth/callback?code={_AUTH_CODE}&state={state}",
            follow_redirects=False,
        )
        second = client.get(
            f"/api/v1/auth/callback?code={_AUTH_CODE}&state={state}",
            follow_redirects=False,
        )

    assert first.status_code == 303
    assert first.headers["location"] == "/"
    assert second.status_code == 303
    assert second.headers["location"] == "/?auth_error=login"
    assert len(harness.oidc_client.exchange_calls) == 1
    with harness.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 1


def test_concurrent_callback_race_creates_at_most_one_session(
    harness: _AuthHarness,
) -> None:
    harness.oidc_client.exchange_delay_seconds = 0.05
    with TestClient(harness.app) as client:
        login = client.get("/api/v1/auth/login", follow_redirects=False)
        state = _state_from_location(login.headers["location"])
        binding_cookie = client.cookies.get(harness.settings.auth_login_state_cookie_name)

    assert binding_cookie is not None

    def run_callback() -> tuple[int, str]:
        with TestClient(harness.app) as callback_client:
            callback_client.cookies.set(
                harness.settings.auth_login_state_cookie_name,
                binding_cookie,
                domain="testserver.local",
                path="/api/v1/auth/callback",
            )
            response = callback_client.get(
                f"/api/v1/auth/callback?code={_AUTH_CODE}&state={state}",
                follow_redirects=False,
            )
            return response.status_code, response.headers["location"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run_callback(), range(2)))

    assert results.count((303, "/")) == 1
    assert results.count((303, "/?auth_error=login")) == 1
    assert len(harness.oidc_client.exchange_calls) == 1
    with harness.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 1


def test_token_exchange_failure_clears_cookie_and_does_not_create_session(
    harness: _AuthHarness,
) -> None:
    harness.oidc_client.exchange_error = OIDCTokenExchangeError("token endpoint failed")
    with TestClient(harness.app) as client:
        login = client.get("/api/v1/auth/login", follow_redirects=False)
        state = _state_from_location(login.headers["location"])
        response = client.get(
            f"/api/v1/auth/callback?code={_AUTH_CODE}&state={state}",
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/?auth_error=login"
    assert len(harness.oidc_client.exchange_calls) == 1
    assert "Max-Age=0" in _single_set_cookie(
        response,
        harness.settings.auth_login_state_cookie_name,
    )
    with harness.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0


def test_auth_session_endpoint_returns_profile_and_rotates_csrf(
    harness: _AuthHarness,
) -> None:
    harness.settings.auth_session_last_seen_min_interval_seconds = 0
    created = _create_user_session(
        harness,
        claims={"groups": ["arena_admin"], "email": "profile@example.test"},
        profile=True,
    )
    with harness.session_factory() as db:
        row = db.get(AuthSession, created.session_id)
        assert row is not None
        before_last_seen = row.last_seen_at

    with TestClient(harness.app) as client:
        client.cookies.set(harness.settings.auth_session_cookie_name, created.session_token)
        first = client.get("/api/v1/auth/session")
        second = client.get("/api/v1/auth/session")

    assert first.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert first_body["authenticated"] is True
    assert first_body["is_admin"] is True
    assert first_body["profile"]["display_name"] == "Aya"
    assert first_body["csrf_token"] != second_body["csrf_token"]
    assert second_body["csrf_token"]

    with harness.session_factory() as db:
        row = db.get(AuthSession, created.session_id)
        assert row is not None
        _assert_hmac(second_body["csrf_token"], row.csrf_token_hash)
        assert row.last_seen_at > before_last_seen
        assert first_body["csrf_token"] not in _serialized_row(row)
        assert second_body["csrf_token"] not in _serialized_row(row)


def test_session_endpoint_rejects_missing_expired_and_revoked_sessions(
    harness: _AuthHarness,
) -> None:
    expired = _create_user_session(
        harness,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    revoked = _create_user_session(harness)
    with harness.session_factory() as db:
        assert auth_session.revoke_auth_session(
            db,
            session_token=revoked.session_token,
            settings=harness.settings,
        )
        db.commit()

    with TestClient(harness.app) as client:
        missing = client.get("/api/v1/auth/session")
        client.cookies.set(harness.settings.auth_session_cookie_name, expired.session_token)
        expired_response = client.get("/api/v1/auth/session")
        client.cookies.set(harness.settings.auth_session_cookie_name, revoked.session_token)
        revoked_response = client.get("/api/v1/auth/session")

    assert missing.json() == {
        "authenticated": False,
        "is_admin": False,
        "user": None,
        "profile": None,
        "csrf_token": None,
    }
    assert expired_response.json()["authenticated"] is False
    assert revoked_response.json()["authenticated"] is False
    assert "Max-Age=0" in _single_set_cookie(
        expired_response,
        harness.settings.auth_session_cookie_name,
    )


def test_logout_requires_latest_csrf_revokes_session_and_clears_cookie(
    harness: _AuthHarness,
) -> None:
    created = _create_user_session(harness)
    with TestClient(harness.app) as client:
        client.cookies.set(harness.settings.auth_session_cookie_name, created.session_token)
        missing = client.post("/api/v1/auth/logout")
        first_csrf = client.get("/api/v1/auth/session").json()["csrf_token"]
        wrong = client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": "wrong-token"},
        )
        second_csrf = client.get("/api/v1/auth/session").json()["csrf_token"]
        stale = client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": first_csrf},
        )
        latest = client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": second_csrf},
        )
        after_logout = client.get("/api/v1/auth/session")

    assert missing.status_code == 403
    assert missing.json()["detail"] == "CSRF token required"
    assert wrong.status_code == 403
    assert wrong.json()["detail"] == "Invalid CSRF token"
    assert stale.status_code == 403
    assert latest.status_code == 200
    assert latest.json() == {"ok": True, "authenticated": False, "logout_url": None}
    assert "Max-Age=0" in _single_set_cookie(
        latest,
        harness.settings.auth_session_cookie_name,
    )
    assert after_logout.json()["authenticated"] is False

    with harness.session_factory() as db:
        row = db.get(AuthSession, created.session_id)
        assert row is not None
        assert row.revoked_at is not None


def test_logout_without_valid_session_is_safe_and_clears_stale_cookie(
    harness: _AuthHarness,
) -> None:
    with TestClient(harness.app) as client:
        client.cookies.set(harness.settings.auth_session_cookie_name, "stale-token")
        response = client.post("/api/v1/auth/logout")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "authenticated": False, "logout_url": None}
    assert "Max-Age=0" in _single_set_cookie(
        response,
        harness.settings.auth_session_cookie_name,
    )


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        app_env="test",
        auth_cookie_secure=None,
        auth_csrf_header_name="X-CSRF-Token",
        auth_login_state_cookie_name="arena_oauth_state",
        auth_session_cookie_name="arena_session",
        auth_session_hash_secret=_HASH_SECRET,
        auth_session_last_seen_min_interval_seconds=60,
        auth_session_last_seen_lock_timeout_ms=100,
        auth_session_last_seen_statement_timeout_ms=500,
        auth_session_max_age_seconds=3600,
        public_base_url="https://arena.example",
        oidc_admin_group_claim="groups",
        oidc_admin_group_name="arena_admin",
        oidc_login_state_max_age_seconds=600,
        oidc_redirect_path="/api/v1/auth/callback",
    )


def _state_from_location(location: str) -> str:
    state = parse_qs(urlparse(location).query).get("state", [])
    assert len(state) == 1
    return state[0]


def _single_set_cookie(response: Any, cookie_name: str) -> str:
    headers = [
        header
        for header in response.headers.get_list("set-cookie")
        if header.startswith(f"{cookie_name}=")
    ]
    assert len(headers) == 1, response.headers.get_list("set-cookie")
    return headers[0]


def _create_user_session(
    harness: _AuthHarness,
    *,
    claims: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
    profile: bool = False,
) -> SimpleNamespace:
    with harness.session_factory() as db:
        user = User(
            oidc_issuer="https://issuer.example",
            oidc_sub=f"user-{uuid.uuid4()}",
        )
        db.add(user)
        db.flush()
        if profile:
            db.add(
                UserProfile(
                    user_id=user.id,
                    display_name="Aya",
                    ui_language="zh",
                    zh_variant="hant",
                    jp_proficiency={"jlpt": "N1"},
                    translation_experience={"years": 5},
                    consents={"tos": True},
                )
            )
        created = auth_session.create_auth_session(
            db,
            user=user,
            claims=claims or {},
            expires_at=expires_at,
            settings=harness.settings,
        )
        db.commit()
        return SimpleNamespace(
            session_token=created.session_token,
            session_id=created.row.id,
            user_id=user.id,
        )


def _assert_hmac(raw_token: str, stored_hash: str) -> None:
    expected_hash = hmac.new(
        _HASH_SECRET.encode("utf-8"),
        raw_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert stored_hash == expected_hash
    assert len(stored_hash) == 64
    assert stored_hash != raw_token


def _serialized_row(row: object) -> str:
    return json.dumps(
        {column.name: str(getattr(row, column.name)) for column in row.__table__.columns},
        sort_keys=True,
    )
