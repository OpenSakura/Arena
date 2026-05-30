from __future__ import annotations

from collections.abc import Iterator
import json
import logging
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import auth, health
from app.core.config import get_settings


RAW_CLIENT_SECRET = "task18-route-client-secret"
RAW_AUTH_CODE = "task18-route-auth-code"
RAW_CODE_VERIFIER = "task18-route-code-verifier"
RAW_STATE = "task18-route-state"
RAW_NONCE = "task18-route-nonce"
RAW_ACCESS_TOKEN = "task18-route-access-token"
RAW_REFRESH_TOKEN = "task18-route-refresh-token"
RAW_ID_TOKEN = "task18-route-id-token"
RAW_AUTH_SESSION_HASH_SECRET = "task18-route-session-hash-secret"

RAW_CONFIDENTIAL_VALUES = (
    RAW_CLIENT_SECRET,
    RAW_AUTH_CODE,
    RAW_CODE_VERIFIER,
    RAW_STATE,
    RAW_NONCE,
    RAW_ACCESS_TOKEN,
    RAW_REFRESH_TOKEN,
    RAW_ID_TOKEN,
    RAW_AUTH_SESSION_HASH_SECRET,
)

FORBIDDEN_PUBLIC_CONFIG_TERMS = (
    "OIDC_CLIENT_SECRET",
    "AUTH_SESSION_HASH_SECRET",
    "client_secret",
    "access_token",
    "refresh_token",
    "id_token",
    "oidc.user",
    "code_verifier",
    "authorization_code",
    "auth_code",
    "state",
    "nonce",
    "token_endpoint",
    "client_secret_basic",
)


def test_public_config_response_excludes_confidential_and_token_fields() -> None:
    app = FastAPI()
    app.include_router(health.router, prefix="/api/v1")
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        oidc_client_secret=RAW_CLIENT_SECRET,
        oidc_client_auth_method="client_secret_basic",
        auth_session_hash_secret=RAW_AUTH_SESSION_HASH_SECRET,
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/public-config")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "anon_battle_turnstile_required": False,
        "auth": {
            "mode": "backend_session",
            "login_path": "/api/v1/auth/login",
            "logout_path": "/api/v1/auth/logout",
            "session_path": "/api/v1/auth/session",
            "csrf_header_name": "X-CSRF-Token",
        },
    }

    serialized = json.dumps(body, sort_keys=True)
    normalized = serialized.lower()
    for forbidden in FORBIDDEN_PUBLIC_CONFIG_TERMS:
        assert forbidden.lower() not in normalized
    for raw_value in RAW_CONFIDENTIAL_VALUES:
        assert raw_value not in serialized


def test_auth_callback_failure_logs_and_headers_exclude_raw_oauth_values(
    caplog,
) -> None:
    settings = SimpleNamespace(
        app_env="test",
        auth_cookie_secure=None,
        auth_login_state_cookie_name="arena_oauth_state",
        oidc_redirect_path="/api/v1/auth/callback",
    )
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1")
    app.dependency_overrides[auth.get_db] = _unused_db
    app.dependency_overrides[auth.get_settings] = lambda: settings

    error_description = ":".join(RAW_CONFIDENTIAL_VALUES)
    callback_path = (
        "/api/v1/auth/callback"
        f"?error=access_denied&error_description={error_description}&code={RAW_AUTH_CODE}"
    )

    with caplog.at_level(logging.INFO, logger=auth.logger.name):
        with TestClient(app) as client:
            response = client.get(callback_path, follow_redirects=False)

    assert response.status_code == 303
    response_text = f"{response.headers!r} {response.text}"
    log_text = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == auth.logger.name
    )
    assert "OIDC provider returned an authorization error" in log_text
    for raw_value in RAW_CONFIDENTIAL_VALUES:
        assert raw_value not in log_text
        assert raw_value not in response_text


def _unused_db() -> Iterator[object]:
    yield object()
