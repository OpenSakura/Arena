from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
import hashlib
import hmac
import json
from types import SimpleNamespace
import uuid

from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from app.core import csrf, security
import app.core.config as config_module
from app.db.base import Base
from app.db.session import get_db
import app.main as main
from app.models.auth_session import AuthSession
from app.models.user import User
from app.services import auth_session


_HASH_SECRET = "test-auth-session-hash-secret"


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type: JSONB, _compiler: object, **_kw: object) -> str:
    return "JSON"


class _Closable:
    async def aclose(self) -> None:
        return None


class _Orchestrator:
    llm_client = _Closable()


@dataclass(frozen=True)
class _CreatedSession:
    session_token: str
    session_id: uuid.UUID
    user_id: uuid.UUID


@dataclass(frozen=True)
class _CsrfClient:
    client: TestClient
    session_factory: sessionmaker[Session]
    created_session: _CreatedSession
    principal_holder: dict[str, security.Principal]
    settings: SimpleNamespace


def _csrf_settings() -> SimpleNamespace:
    return SimpleNamespace(
        auth_csrf_header_name="X-CSRF-Token",
        auth_session_cookie_name="arena_session",
        auth_session_hash_secret=_HASH_SECRET,
        auth_session_max_age_seconds=3600,
        oidc_login_state_max_age_seconds=600,
    )


def _main_settings() -> SimpleNamespace:
    return SimpleNamespace(
        app_name="OpenSakura Arena API (csrf tests)",
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
        auth_csrf_header_name="X-CSRF-Token",
        auth_session_cookie_name="arena_session",
        auth_session_hash_secret=_HASH_SECRET,
        auth_session_max_age_seconds=3600,
        oidc_login_state_max_age_seconds=600,
        oidc_admin_group_claim="groups",
        oidc_admin_group_name="arena_admin",
        service_token_hash_secret="test-service-token-hash-secret",
    )


@pytest.fixture()
def csrf_client(tmp_path) -> Iterator[_CsrfClient]:
    settings = _csrf_settings()
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'csrf.db'}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    with session_factory() as db:
        user = User(
            oidc_issuer="https://issuer.example",
            oidc_sub="session-user",
        )
        db.add(user)
        db.flush()
        created = auth_session.create_auth_session(
            db,
            user=user,
            claims={"groups": ["arena_admin"]},
            settings=settings,
        )
        db.commit()
        created_session = _CreatedSession(
            session_token=created.session_token,
            session_id=created.row.id,
            user_id=user.id,
        )

    principal_holder = {
        "principal": _session_principal(
            created_session.user_id,
            created_session.session_id,
        )
    }

    def override_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    async def override_principal() -> security.Principal:
        return principal_holder["principal"]

    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(csrf.require_csrf_for_session)])
    def safe_endpoint() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/protected", dependencies=[Depends(csrf.require_csrf_for_session)])
    def unsafe_endpoint() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/session-bootstrap")
    def session_bootstrap() -> dict[str, str]:
        raw_csrf = auth_session.stable_auth_session_csrf_token(
            created_session.session_token,
            settings=settings,
        )
        return {"csrf_token": raw_csrf}

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[security.get_principal_optional] = override_principal
    app.dependency_overrides[csrf.get_settings] = lambda: settings

    with TestClient(app) as client:
        client.cookies.set(settings.auth_session_cookie_name, created_session.session_token)
        yield _CsrfClient(
            client=client,
            session_factory=session_factory,
            created_session=created_session,
            principal_holder=principal_holder,
            settings=settings,
        )

    engine.dispose()


def test_safe_methods_do_not_require_csrf_for_session_principal(
    csrf_client: _CsrfClient,
) -> None:
    response = csrf_client.client.get("/protected")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_cookie_requires_csrf_missing_wrong_and_correct(
    csrf_client: _CsrfClient,
) -> None:
    bootstrap = csrf_client.client.get("/session-bootstrap")
    assert bootstrap.status_code == 200
    csrf_token = bootstrap.json()["csrf_token"]

    missing = csrf_client.client.post("/protected")
    wrong = csrf_client.client.post(
        "/protected",
        headers={"X-CSRF-Token": "wrong-token"},
    )
    correct = csrf_client.client.post(
        "/protected",
        headers={"X-CSRF-Token": csrf_token},
    )

    assert missing.status_code == 403
    assert missing.json()["detail"] == "CSRF token required"
    assert wrong.status_code == 403
    assert wrong.json()["detail"] == "Invalid CSRF token"
    assert correct.status_code == 200
    assert correct.json() == {"ok": True}


def test_session_context_marker_requires_csrf_even_before_principal_auth_method_cutover(
    csrf_client: _CsrfClient,
) -> None:
    csrf_client.principal_holder["principal"] = security.Principal(
        is_authenticated=True,
        actor_type="human",
        user_id=str(csrf_client.created_session.user_id),
        auth_session_id=str(csrf_client.created_session.session_id),
        claims={"groups": ["arena_admin"]},
    )
    bootstrap = csrf_client.client.get("/session-bootstrap")
    csrf_token = bootstrap.json()["csrf_token"]

    missing = csrf_client.client.post("/protected")
    correct = csrf_client.client.post(
        "/protected",
        headers={"X-CSRF-Token": csrf_token},
    )

    assert missing.status_code == 403
    assert correct.status_code == 200


def test_service_token_bypasses_csrf_header_requirement(
    csrf_client: _CsrfClient,
) -> None:
    csrf_client.principal_holder["principal"] = security.Principal(
        is_authenticated=True,
        actor_type="bot",
        auth_method="service_token",
        user_id=str(uuid.uuid4()),
        service_account_id=str(uuid.uuid4()),
        token_id=str(uuid.uuid4()),
        scopes=("battle:create", "battle:execute"),
    )

    response = csrf_client.client.post("/protected")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_csrf_session_bootstrap_is_idempotent_and_keeps_first_token_valid(
    csrf_client: _CsrfClient,
) -> None:
    first_bootstrap = csrf_client.client.get("/session-bootstrap")
    assert first_bootstrap.status_code == 200
    assert "set-cookie" not in first_bootstrap.headers
    first_token = first_bootstrap.json()["csrf_token"]

    assert csrf_client.client.post(
        "/protected",
        headers={"X-CSRF-Token": first_token},
    ).status_code == 200

    bootstrap_response = csrf_client.client.get("/session-bootstrap")
    assert bootstrap_response.status_code == 200
    assert "set-cookie" not in bootstrap_response.headers
    second_token = bootstrap_response.json()["csrf_token"]
    assert second_token == first_token

    with csrf_client.session_factory() as db:
        row = db.execute(select(AuthSession)).scalar_one()
        _assert_hmac(first_token, row.csrf_token_hash)
        serialized_row = _serialized_row(row)
        assert first_token not in serialized_row
        assert second_token not in serialized_row

    first_after_reload = csrf_client.client.post(
        "/protected",
        headers={"X-CSRF-Token": first_token},
    )
    second_after_reload = csrf_client.client.post(
        "/protected",
        headers={"X-CSRF-Token": second_token},
    )
    reload_bootstrap = csrf_client.client.get("/session-bootstrap")
    third_token = reload_bootstrap.json()["csrf_token"]
    latest_after_reload = csrf_client.client.post(
        "/protected",
        headers={"X-CSRF-Token": third_token},
    )

    assert first_after_reload.status_code == 200
    assert second_after_reload.status_code == 200
    assert reload_bootstrap.status_code == 200
    assert third_token == second_token
    assert latest_after_reload.status_code == 200


def test_csrf_accepts_stable_bootstrap_for_existing_random_csrf_hash(
    csrf_client: _CsrfClient,
) -> None:
    with csrf_client.session_factory() as db:
        row = db.get(AuthSession, csrf_client.created_session.session_id)
        assert row is not None
        previous_csrf = auth_session.rotate_auth_session_csrf_token(
            db,
            auth_session=row,
            settings=csrf_client.settings,
        )
        db.commit()

    stable_bootstrap = csrf_client.client.get("/session-bootstrap")
    stable_csrf = stable_bootstrap.json()["csrf_token"]
    previous = csrf_client.client.post(
        "/protected",
        headers={"X-CSRF-Token": previous_csrf},
    )
    stable = csrf_client.client.post(
        "/protected",
        headers={"X-CSRF-Token": stable_csrf},
    )

    assert stable_bootstrap.status_code == 200
    assert stable_csrf != previous_csrf
    assert previous.status_code == 200
    assert stable.status_code == 200


def test_cors_preflight_accepts_csrf_and_authorization_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _create_main_app(monkeypatch)

    with TestClient(app) as client:
        response = client.options(
            "/api/v1/readyz",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": (
                    "Authorization, X-CSRF-Token, Content-Type, X-Request-ID"
                ),
            },
        )

    assert response.status_code == 200
    allow_headers = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allow_headers
    assert "x-csrf-token" in allow_headers
    assert "content-type" in allow_headers
    assert "x-request-id" in allow_headers


def test_route_inventory_covers_all_unsafe_authenticated_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _create_main_app(monkeypatch)
    uncovered: list[str] = []
    covered: set[tuple[str, str]] = set()
    exempt: dict[tuple[str, str], str] = {}

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        unsafe_methods = csrf.UNSAFE_METHODS.intersection(route.methods or set())
        if not unsafe_methods or not _route_uses_auth(route):
            continue
        has_csrf = _route_has_dependency(route, csrf.require_csrf_for_session)
        exemption_reason = csrf.get_csrf_exempt_reason(route.endpoint)
        for method in sorted(unsafe_methods):
            key = (method, route.path)
            if has_csrf:
                covered.add(key)
            elif exemption_reason is not None:
                exempt[key] = exemption_reason
            else:
                uncovered.append(f"{method} {route.path}")

    assert uncovered == []
    assert {
        ("PUT", "/api/v1/me/profile"),
        ("POST", "/api/v1/battles"),
        ("POST", "/api/v1/battles/{battle_id}/retry"),
        ("POST", "/api/v1/battles/{battle_id}/vote"),
        ("POST", "/api/v1/admin/models"),
        ("PUT", "/api/v1/admin/models/{model_id}"),
        ("DELETE", "/api/v1/admin/models/{model_id}"),
        ("POST", "/api/v1/admin/models/{model_id}/test"),
        ("POST", "/api/v1/admin/task-sets"),
        ("PUT", "/api/v1/admin/task-sets/{task_set_id}"),
        ("DELETE", "/api/v1/admin/task-sets/{task_set_id}"),
        ("POST", "/api/v1/admin/tasks"),
        ("PUT", "/api/v1/admin/tasks/{task_id}"),
        ("DELETE", "/api/v1/admin/tasks/{task_id}"),
        ("POST", "/api/v1/admin/tasks/import-jsonl"),
        ("POST", "/api/v1/admin/leaderboard/refresh"),
        ("POST", "/api/v1/admin/service-accounts"),
        ("PATCH", "/api/v1/admin/service-accounts/{service_account_id}"),
        ("POST", "/api/v1/admin/service-accounts/{service_account_id}/tokens"),
        ("POST", "/api/v1/admin/service-account-tokens/{token_id}/revoke"),
    }.issubset(covered)
    assert exempt[("POST", "/api/v1/bot/battles/create-and-wait")] == (
        "bearer/service-token-only endpoint"
    )


def _session_principal(user_id: uuid.UUID, session_id: uuid.UUID) -> security.Principal:
    return security.Principal(
        is_authenticated=True,
        actor_type="human",
        auth_method="session",
        user_id=str(user_id),
        auth_session_id=str(session_id),
        oidc_issuer="https://issuer.example",
        oidc_sub="session-user",
        claims={"groups": ["arena_admin"]},
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


def _create_main_app(monkeypatch: pytest.MonkeyPatch):
    settings = _main_settings()
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    monkeypatch.setattr(csrf, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "configure_logging", lambda _settings: None)
    monkeypatch.setattr(main, "bootstrap_schema", lambda: None)
    monkeypatch.setattr(main, "acquire_battle_process_lock", lambda: None)
    monkeypatch.setattr(main, "release_battle_process_lock", lambda: None)
    monkeypatch.setattr(main, "close_all_redis_clients", lambda: None)
    monkeypatch.setattr(main, "get_oidc_confidential_client", lambda: _Closable())
    monkeypatch.setattr(main, "get_battle_orchestrator", lambda: _Orchestrator())

    app = main.create_app()
    return app


def _dependency_calls(route: APIRoute) -> list[Callable[..., object]]:
    calls: list[Callable[..., object]] = []

    def visit(dependant: object) -> None:
        dependencies = getattr(dependant, "dependencies", [])
        for dependency in dependencies:
            call = getattr(dependency, "call", None)
            if callable(call):
                calls.append(call)
            visit(dependency)

    visit(route.dependant)
    return calls


def _route_has_dependency(route: APIRoute, expected: Callable[..., object]) -> bool:
    return any(call is expected for call in _dependency_calls(route))


def _route_uses_auth(route: APIRoute) -> bool:
    auth_dependency_names = {
        "get_principal_optional",
        "get_principal_required",
        "require_admin",
        "require_oidc_admin",
        "require_bot_battle_create_principal",
        "require_bot_battle_read_principal",
    }
    for call in _dependency_calls(route):
        if call in {
            security.get_principal_optional,
            security.get_principal_required,
            security.require_admin,
        }:
            return True
        if getattr(call, "__name__", "") in auth_dependency_names:
            return True
        if getattr(call, "__module__", "") == security.__name__ and getattr(
            call,
            "__qualname__",
            "",
        ).startswith("require_scopes.<locals>."):
            return True
    return False
