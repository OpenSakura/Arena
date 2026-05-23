from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from typing import NamedTuple
import uuid

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes import admin_service_accounts
from app.core import security, service_tokens
import app.core.config as config_module
from app.db.base import Base
from app.db.session import get_db
import app.main as main
from app.models.service_account import ServiceAccount, ServiceAccountToken
from app.models.user import User
from app.services.oidc import get_oidc_verifier


_HASH_SECRET = "test-service-token-hash-secret"


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type: JSONB, _compiler: object, **_kw: object) -> str:
    return "JSON"


class _Closable:
    async def aclose(self) -> None:
        return None


class _Orchestrator:
    llm_client = _Closable()


class _Verifier:
    async def verify(self, _token: str) -> dict[str, object]:
        return {}


class _ClientContext(NamedTuple):
    client: TestClient
    session_factory: sessionmaker[Session]
    admin_user_id: uuid.UUID
    principal_holder: dict[str, security.Principal]


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        app_name="OpenSakura Arena API (admin service account tests)",
        app_env="test",
        leaderboard_refresh_enabled=False,
        access_log_enabled=False,
        turnstile_secret_key="",
        cors_allow_origins=["http://localhost:3000"],
        api_v1_prefix="/api/v1",
        trust_x_forwarded_for=False,
        rate_limit_redis_url="",
        rate_limit_redis_timeout_seconds=0.5,
        web_concurrency=1,
        oidc_admin_group_claim="groups",
        oidc_admin_group_name="arena_admin",
        service_token_hash_secret=_HASH_SECRET,
    )


@pytest.fixture()
def client_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> Iterator[_ClientContext]:
    settings = _settings()
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    monkeypatch.setattr(service_tokens, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "configure_logging", lambda _settings: None)
    monkeypatch.setattr(main, "acquire_battle_process_lock", lambda: None)
    monkeypatch.setattr(main, "release_battle_process_lock", lambda: None)
    monkeypatch.setattr(main, "close_all_redis_clients", lambda: None)
    monkeypatch.setattr(main, "get_oidc_verifier", lambda: _Closable())
    monkeypatch.setattr(main, "get_battle_orchestrator", lambda: _Orchestrator())

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'admin-service-accounts.db'}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    admin_user_id = uuid.uuid4()
    with session_factory() as db:
        db.add(
            User(
                id=admin_user_id,
                oidc_issuer="https://issuer.example",
                oidc_sub="admin-user",
            )
        )
        db.commit()

    principal_holder = {
        "principal": _principal(
            user_id=admin_user_id,
            oidc_issuer="https://issuer.example",
            oidc_sub="admin-user",
            actor_type="human",
        )
    }

    def override_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    async def override_principal() -> security.Principal:
        return principal_holder["principal"]

    app = main.create_app()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[security.get_principal_optional] = override_principal
    app.dependency_overrides[get_oidc_verifier] = lambda: _Verifier()

    with TestClient(app) as test_client:
        yield _ClientContext(
            client=test_client,
            session_factory=session_factory,
            admin_user_id=admin_user_id,
            principal_holder=principal_holder,
        )

    app.dependency_overrides.clear()
    engine.dispose()


def test_admin_can_create_list_update_and_reactivate_service_account(
    client_context: _ClientContext,
) -> None:
    response = client_context.client.post(
        "/api/v1/admin/service-accounts",
        json={"name": "Judge Bot", "description": "Automated judge"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    service_account_id = uuid.UUID(body["id"])
    assert body["enabled"] is True
    assert body["tokens"] == []
    assert "token_hash" not in json.dumps(body)

    with client_context.session_factory() as db:
        service_account = db.get(ServiceAccount, service_account_id)
        assert service_account is not None
        assert service_account.created_by_user_id == client_context.admin_user_id
        bot_user = db.get(User, service_account.bot_user_id)
        assert bot_user is not None
        assert bot_user.actor_type == "bot"
        assert bot_user.oidc_issuer == "system:service-account"
        assert bot_user.oidc_sub == f"service-account:{service_account_id}"

    list_response = client_context.client.get("/api/v1/admin/service-accounts")
    assert list_response.status_code == 200, list_response.text
    listed = list_response.json()["service_accounts"]
    assert [item["id"] for item in listed] == [str(service_account_id)]

    deactivate_response = client_context.client.patch(
        f"/api/v1/admin/service-accounts/{service_account_id}",
        json={"enabled": False, "name": "Paused Judge Bot"},
    )
    assert deactivate_response.status_code == 200, deactivate_response.text
    assert deactivate_response.json()["enabled"] is False
    assert deactivate_response.json()["name"] == "Paused Judge Bot"

    reactivate_response = client_context.client.patch(
        f"/api/v1/admin/service-accounts/{service_account_id}",
        json={"enabled": True},
    )
    assert reactivate_response.status_code == 200, reactivate_response.text
    assert reactivate_response.json()["enabled"] is True


def test_admin_token_create_stores_hash_and_returns_plaintext_once(
    client_context: _ClientContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plaintext = "osa_bot_" + "a" * 48
    monkeypatch.setattr(
        admin_service_accounts,
        "generate_service_token",
        lambda: plaintext,
    )
    service_account_id = _create_service_account(client_context)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    response = client_context.client.post(
        f"/api/v1/admin/service-accounts/{service_account_id}/tokens",
        json={
            "scopes": ["vote:create", "battle:create", "vote:create"],
            "expires_at": expires_at,
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    serialized = json.dumps(body)
    assert body["plaintext_token"] == plaintext
    assert serialized.count(plaintext) == 1
    assert "token_hash" not in serialized
    assert body["token"]["status"] == "active"
    assert body["token"]["scopes"] == ["battle:create", "vote:create"]
    assert (
        body["token"]["token_prefix"]
        == plaintext[: service_tokens.SERVICE_TOKEN_STORED_PREFIX_CHARS]
    )

    token_id = uuid.UUID(body["token"]["id"])
    with client_context.session_factory() as db:
        token = db.get(ServiceAccountToken, token_id)
        assert token is not None
        assert token.token_prefix == body["token"]["token_prefix"]
        assert token.token_hash != plaintext
        assert len(token.token_hash) == 64
        assert token.scopes == ["battle:create", "vote:create"]

    list_response = client_context.client.get("/api/v1/admin/service-accounts")
    assert list_response.status_code == 200, list_response.text
    list_body = list_response.json()
    list_serialized = json.dumps(list_body)
    assert plaintext not in list_serialized
    assert "token_hash" not in list_serialized
    listed_token = list_body["service_accounts"][0]["tokens"][0]
    assert listed_token["id"] == str(token_id)
    assert listed_token["token_prefix"] == body["token"]["token_prefix"]
    assert listed_token["status"] == "active"


def test_create_token_rejects_inactive_service_account(
    client_context: _ClientContext,
) -> None:
    service_account_id = _create_service_account(client_context)
    deactivate_response = client_context.client.patch(
        f"/api/v1/admin/service-accounts/{service_account_id}",
        json={"enabled": False},
    )
    assert deactivate_response.status_code == 200, deactivate_response.text

    response = client_context.client.post(
        f"/api/v1/admin/service-accounts/{service_account_id}/tokens",
        json={"scopes": ["vote:create"]},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Inactive service accounts cannot receive new tokens"
    )


def test_create_token_validates_expiry_and_scope(
    client_context: _ClientContext,
) -> None:
    service_account_id = _create_service_account(client_context)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

    expired_response = client_context.client.post(
        f"/api/v1/admin/service-accounts/{service_account_id}/tokens",
        json={"scopes": ["vote:create"], "expires_at": past},
    )
    assert expired_response.status_code == 422

    scope_response = client_context.client.post(
        f"/api/v1/admin/service-accounts/{service_account_id}/tokens",
        json={"scopes": ["admin:tokens"]},
    )
    assert scope_response.status_code == 422


def test_revoke_service_account_token_is_idempotent(
    client_context: _ClientContext,
) -> None:
    service_account_id = _create_service_account(client_context)
    token_id = _create_token(client_context, service_account_id)

    first_response = client_context.client.post(
        f"/api/v1/admin/service-account-tokens/{token_id}/revoke"
    )
    assert first_response.status_code == 200, first_response.text
    assert first_response.json() == {"token_id": token_id, "revoked": True}

    with client_context.session_factory() as db:
        first_revoked_at = db.get(ServiceAccountToken, uuid.UUID(token_id)).revoked_at
        assert first_revoked_at is not None

    second_response = client_context.client.post(
        f"/api/v1/admin/service-account-tokens/{token_id}/revoke"
    )
    assert second_response.status_code == 200, second_response.text
    assert second_response.json() == {"token_id": token_id, "revoked": True}

    with client_context.session_factory() as db:
        second_revoked_at = db.get(ServiceAccountToken, uuid.UUID(token_id)).revoked_at
        assert second_revoked_at == first_revoked_at


def test_bot_principal_cannot_access_admin_service_accounts(
    client_context: _ClientContext,
) -> None:
    client_context.principal_holder["principal"] = _principal(
        user_id=client_context.admin_user_id,
        oidc_issuer="system:service-account",
        oidc_sub=f"service-account:{uuid.uuid4()}",
        actor_type="bot",
    )

    response = client_context.client.get("/api/v1/admin/service-accounts")

    assert response.status_code == 403


def _create_service_account(client_context: _ClientContext) -> str:
    response = client_context.client.post(
        "/api/v1/admin/service-accounts",
        json={"name": "Judge Bot"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_token(client_context: _ClientContext, service_account_id: str) -> str:
    response = client_context.client.post(
        f"/api/v1/admin/service-accounts/{service_account_id}/tokens",
        json={"scopes": ["vote:create"]},
    )
    assert response.status_code == 201, response.text
    return response.json()["token"]["id"]


def _principal(
    *,
    user_id: uuid.UUID,
    oidc_issuer: str,
    oidc_sub: str,
    actor_type: str,
) -> security.Principal:
    principal = security.Principal(
        is_authenticated=True,
        user_id=str(user_id),
        oidc_issuer=oidc_issuer,
        oidc_sub=oidc_sub,
        claims={"groups": ["arena_admin"]},
    )
    object.__setattr__(principal, "actor_type", actor_type)
    return principal
