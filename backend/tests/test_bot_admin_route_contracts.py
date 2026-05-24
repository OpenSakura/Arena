from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
import uuid

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app.api.routes import bot_battles
from app.core.security import Principal
import app.core.config as config_module
from app.db.session import get_db
import app.main as main


def _schema_contains(schema: object, key: str, value: object) -> bool:
    if isinstance(schema, dict):
        if schema.get(key) == value:
            return True
        return any(_schema_contains(item, key, value) for item in schema.values())
    if isinstance(schema, list):
        return any(_schema_contains(item, key, value) for item in schema)
    return False


class _Closable:
    async def aclose(self) -> None:
        return None


class _Orchestrator:
    llm_client = _Closable()


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        app_name="OpenSakura Arena API (route contract tests)",
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
    )


def _bot_principal(*, scopes: tuple[str, ...]) -> Principal:
    return Principal(
        is_authenticated=True,
        actor_type="bot",
        user_id=str(uuid.uuid4()),
        service_account_id=str(uuid.uuid4()),
        service_account_name="Auto Judge",
        token_id=str(uuid.uuid4()),
        scopes=scopes,
    )


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    settings = _settings()
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "configure_logging", lambda _settings: None)
    monkeypatch.setattr(main, "bootstrap_schema", lambda: None)
    monkeypatch.setattr(main, "acquire_battle_process_lock", lambda: None)
    monkeypatch.setattr(main, "release_battle_process_lock", lambda: None)
    monkeypatch.setattr(main, "close_all_redis_clients", lambda: None)
    monkeypatch.setattr(main, "get_oidc_confidential_client", lambda: _Closable())
    monkeypatch.setattr(main, "get_battle_orchestrator", lambda: _Orchestrator())

    app = main.create_app()
    app.dependency_overrides[get_db] = lambda: iter((object(),))

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_openapi_exposes_bot_admin_route_contracts(client: TestClient) -> None:
    openapi = client.get("/openapi.json").json()
    paths = openapi["paths"]

    expected_routes = {
        ("/api/v1/admin/service-accounts", "get"),
        ("/api/v1/admin/service-accounts", "post"),
        ("/api/v1/admin/service-accounts/{service_account_id}", "patch"),
        ("/api/v1/admin/service-accounts/{service_account_id}/tokens", "post"),
        ("/api/v1/admin/service-account-tokens/{token_id}/revoke", "post"),
        ("/api/v1/bot/battles/create-and-wait", "post"),
        ("/api/v1/bot/battles/{battle_id}", "get"),
    }

    for path, method in expected_routes:
        assert path in paths
        assert method in paths[path]


def test_bot_create_contract_declares_timeout_and_idempotency_header(
    client: TestClient,
) -> None:
    openapi = client.get("/openapi.json").json()
    operation = openapi["paths"]["/api/v1/bot/battles/create-and-wait"]["post"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    request_schema_name = request_schema["$ref"].rsplit("/", 1)[-1]

    timeout_schema = openapi["components"]["schemas"][request_schema_name]["properties"][
        "timeout_seconds"
    ]
    assert timeout_schema["default"] == 60
    assert timeout_schema["maximum"] == 120

    idempotency_header = parameters["Idempotency-Key"]
    assert idempotency_header["in"] == "header"
    assert idempotency_header["required"] is False
    assert _schema_contains(idempotency_header["schema"], "maxLength", 128)


def test_service_account_list_contract_omits_plaintext_token_fields(
    client: TestClient,
) -> None:
    openapi = client.get("/openapi.json").json()
    components = openapi["components"]["schemas"]
    response_schema = openapi["paths"]["/api/v1/admin/service-accounts"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]
    list_schema_name = response_schema["$ref"].rsplit("/", 1)[-1]
    item_ref = components[list_schema_name]["properties"]["service_accounts"]["items"][
        "$ref"
    ]
    item_schema_name = item_ref.rsplit("/", 1)[-1]
    field_names = set(components[item_schema_name]["properties"])

    assert "plaintext_token" not in field_names
    assert "token" not in field_names
    assert "secret" not in field_names


def test_unauthenticated_contract_routes_are_registered_and_auth_gated(
    client: TestClient,
) -> None:
    service_account_id = str(uuid.uuid4())
    token_id = str(uuid.uuid4())
    battle_id = str(uuid.uuid4())

    cases: list[tuple[str, str, object | None]] = [
        ("GET", "/api/v1/admin/service-accounts", None),
        ("POST", "/api/v1/admin/service-accounts", {"name": "Bot Judge"}),
        (
            "PATCH",
            f"/api/v1/admin/service-accounts/{service_account_id}",
            {"enabled": False},
        ),
        (
            "POST",
            f"/api/v1/admin/service-accounts/{service_account_id}/tokens",
            {"scopes": ["battle:create"]},
        ),
        (
            "POST",
            f"/api/v1/admin/service-account-tokens/{token_id}/revoke",
            None,
        ),
        ("POST", "/api/v1/bot/battles/create-and-wait", {}),
        ("GET", f"/api/v1/bot/battles/{battle_id}", None),
    ]

    for method, path, body in cases:
        response = client.request(method, path, json=body)
        assert response.status_code in {401, 403}, (method, path, response.text)
        assert response.status_code != 404


def test_bot_create_and_wait_dependency_requires_create_and_execute_scopes() -> None:
    under_scoped = _bot_principal(scopes=("battle:create",))
    with pytest.raises(HTTPException) as exc_info:
        bot_battles.require_bot_battle_create_scopes(under_scoped)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Required service token scope missing"

    fully_scoped = _bot_principal(scopes=("battle:create", "battle:execute"))
    assert bot_battles.require_bot_battle_create_scopes(fully_scoped) is fully_scoped


def test_bot_status_dependency_requires_read_scope_only() -> None:
    read_principal = _bot_principal(scopes=("battle:read",))
    assert bot_battles.require_bot_battle_read_scopes(read_principal) is read_principal

    create_execute_principal = _bot_principal(
        scopes=("battle:create", "battle:execute")
    )
    with pytest.raises(HTTPException) as exc_info:
        bot_battles.require_bot_battle_read_scopes(create_execute_principal)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Required service token scope missing"


def test_bot_battle_dependencies_reject_human_principals() -> None:
    human_principal = Principal(
        is_authenticated=True,
        actor_type="human",
        user_id=str(uuid.uuid4()),
    )

    with pytest.raises(HTTPException) as create_exc:
        bot_battles.require_bot_battle_create_scopes(human_principal)
    assert create_exc.value.status_code == 403
    assert create_exc.value.detail == "Service token principal required"

    with pytest.raises(HTTPException) as read_exc:
        bot_battles.require_bot_battle_read_scopes(human_principal)
    assert read_exc.value.status_code == 403
    assert read_exc.value.detail == "Service token principal required"
