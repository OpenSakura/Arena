from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
import fcntl
import json
import os
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
import time
from textwrap import dedent
import uuid

import httpx
import pytest
import redis  # pyright: ignore[reportMissingImports]


E2E_DIR = Path(__file__).resolve().parent
BACKEND_DIR = E2E_DIR.parents[1]
COMPOSE_FILE = E2E_DIR / "docker-compose.yaml"
COMPOSE_PROJECT = "arena-e2e"

POSTGRES_URL = "postgresql+psycopg://postgres:postgres@localhost:15432/arena_e2e"
RATE_LIMIT_REDIS_URL = "redis://localhost:16379/15"
AUTHENTIK_BASE_URL = "http://localhost:19000"
AUTHENTIK_APP_SLUG = "arena-e2e"
AUTHENTIK_ISSUER = f"{AUTHENTIK_BASE_URL}/application/o/{AUTHENTIK_APP_SLUG}/"
AUTHENTIK_CLIENT_ID = "arena-e2e-client"
AUTHENTIK_CLIENT_SECRET = "arena-e2e-confidential-client-secret"
AUTHENTIK_REDIRECT_URI = "http://localhost:13000/api/v1/auth/callback"
PUBLIC_BASE_URL = "http://localhost:13000"
AUTH_SESSION_HASH_SECRET = "arena-e2e-auth-session-hash-secret"
ARENA_MASTER_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
SERVICE_READY_TIMEOUT_SECONDS = 120.0
STACK_LIFECYCLE_TIMEOUT_SECONDS = 180.0
STACK_LOCK_FILE = Path(tempfile.gettempdir()) / "opensakura-arena-e2e-stack.lock"


@dataclass(frozen=True)
class E2EStack:
    compose_file: Path
    compose_project: str


@dataclass(frozen=True)
class E2EAuthenticatedClient:
    client: object
    headers: dict[str, str]
    user_id: str


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            "Command failed: "
            f"{' '.join(command)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def _compose_command(stack: E2EStack, *args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-f",
        str(stack.compose_file),
        "-p",
        stack.compose_project,
        *args,
    ]


@contextmanager
def _stack_lifecycle_lock() -> Generator[None, None, None]:
    with STACK_LOCK_FILE.open("w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _arena_e2e_container_names() -> list[str]:
    result = _run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"name={COMPOSE_PROJECT}",
            "--format",
            "{{.Names}}",
        ],
        cwd=E2E_DIR,
        check=False,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _wait_for_no_arena_e2e_containers() -> None:
    deadline = time.monotonic() + STACK_LIFECYCLE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if not _arena_e2e_container_names():
            return
        time.sleep(1)
    remaining = ", ".join(_arena_e2e_container_names()) or "unknown"
    raise RuntimeError(f"Timed out waiting for arena-e2e containers to be removed: {remaining}")


def _compose_down(stack: E2EStack) -> None:
    _run(
        _compose_command(stack, "down", "-v", "--remove-orphans"),
        cwd=E2E_DIR,
        check=False,
    )
    _wait_for_no_arena_e2e_containers()


def _compose_up_detached(stack: E2EStack) -> None:
    deadline = time.monotonic() + STACK_LIFECYCLE_TIMEOUT_SECONDS
    last_error: RuntimeError | None = None
    while time.monotonic() < deadline:
        try:
            _run(_compose_command(stack, "up", "-d"), cwd=E2E_DIR)
            return
        except RuntimeError as error:
            last_error = error
            if "marked for removal" not in str(error):
                raise
            _compose_down(stack)
            time.sleep(1)
    raise RuntimeError("Timed out starting backend e2e Docker stack") from last_error


def _wait_for_tcp_port(*, host: str, port: int, label: str) -> None:
    deadline = time.monotonic() + SERVICE_READY_TIMEOUT_SECONDS
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError as error:
            last_error = error
            time.sleep(1)
    raise RuntimeError(f"Timed out waiting for {label} on {host}:{port}") from last_error


def _wait_for_postgres() -> None:
    deadline = time.monotonic() + SERVICE_READY_TIMEOUT_SECONDS
    last_error: RuntimeError | None = None
    while time.monotonic() < deadline:
        try:
            _run(
                ["docker", "compose", "-f", str(COMPOSE_FILE), "-p", COMPOSE_PROJECT, "exec", "-T", "postgres", "pg_isready", "-U", "postgres", "-d", "postgres"],
                cwd=E2E_DIR,
            )
            return
        except RuntimeError as error:
            last_error = error
            time.sleep(1)
    raise RuntimeError("Timed out waiting for backend e2e Postgres") from last_error


def _wait_for_redis() -> None:
    deadline = time.monotonic() + SERVICE_READY_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            redis.Redis.from_url(RATE_LIMIT_REDIS_URL, socket_connect_timeout=1).ping()
            return
        except redis.RedisError as error:
            last_error = error
            time.sleep(1)
    raise RuntimeError("Timed out waiting for backend e2e Redis") from last_error


def _wait_for_authentik_health(stack: E2EStack) -> None:
    deadline = time.monotonic() + SERVICE_READY_TIMEOUT_SECONDS
    last_error: RuntimeError | None = None
    while time.monotonic() < deadline:
        try:
            _run(
                _compose_command(stack, "exec", "-T", "authentik-server", "ak", "healthcheck"),
                cwd=E2E_DIR,
            )
            return
        except RuntimeError as error:
            last_error = error
            time.sleep(1)
    raise RuntimeError("Timed out waiting for backend e2e Authentik healthcheck") from last_error


def _wait_for_compose_ports() -> None:
    _wait_for_tcp_port(host="127.0.0.1", port=15432, label="backend e2e Postgres")
    _wait_for_tcp_port(host="127.0.0.1", port=16379, label="backend e2e Redis")
    _wait_for_tcp_port(host="127.0.0.1", port=19000, label="backend e2e Authentik")
    _wait_for_postgres()
    _wait_for_redis()


def _bootstrap_authentik(stack: E2EStack) -> None:
    # Configure a deterministic OAuth2 provider so backend e2e tests can request
    # RS256-signed JWTs from a real Authentik instance.
    script = dedent(
        """
        from datetime import datetime, timedelta, timezone
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        from authentik.core.models import Application
        from authentik.crypto.models import CertificateKeyPair
        from authentik.flows.models import Flow
        from authentik.providers.oauth2.models import OAuth2Provider

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "arena-e2e-signing")])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
            .sign(key, hashes.SHA256())
        )

        cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
        key_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()

        signing_key, _ = CertificateKeyPair.objects.update_or_create(
            name="arena-e2e-signing",
            defaults={
                "certificate_data": cert_pem,
                "key_data": key_pem,
            },
        )

        authorization_flow = Flow.objects.get(slug="default-provider-authorization-implicit-consent")
        invalidation_flow = Flow.objects.get(slug="default-provider-invalidation-flow")

        provider, _ = OAuth2Provider.objects.update_or_create(
            name="arena-e2e-provider",
            defaults={
                "authorization_flow": authorization_flow,
                "invalidation_flow": invalidation_flow,
                "client_type": "confidential",
                "client_id": "arena-e2e-client",
                "client_secret": "arena-e2e-confidential-client-secret",
                "issuer_mode": "per_provider",
                "_redirect_uris": [
                    {"matching_mode": "strict", "url": "http://localhost:13000/api/v1/auth/callback"},
                ],
            },
        )

        provider.authorization_flow = authorization_flow
        provider.invalidation_flow = invalidation_flow
        provider.client_type = "confidential"
        provider.client_id = "arena-e2e-client"
        provider.client_secret = "arena-e2e-confidential-client-secret"
        provider.issuer_mode = "per_provider"
        provider._redirect_uris = [
            {"matching_mode": "strict", "url": "http://localhost:13000/api/v1/auth/callback"},
        ]
        provider.signing_key = signing_key
        provider.save()

        Application.objects.update_or_create(
            slug="arena-e2e",
            defaults={
                "name": "Arena E2E",
                "provider": provider,
            },
        )
        """
    ).strip()

    last_error: RuntimeError | None = None
    for attempt in range(12):
        try:
            _run(
                _compose_command(
                    stack,
                    "exec",
                    "-T",
                    "authentik-server",
                    "ak",
                    "shell",
                    "-c",
                    script,
                ),
                cwd=E2E_DIR,
            )
            return
        except RuntimeError as error:
            last_error = error
            retryable = (
                "Flow matching query does not exist" in str(error)
                or "does not exist" in str(error)
            )
            if not retryable or attempt == 11:
                raise
            time.sleep(5)

    if last_error is not None:
        raise last_error


def _run_backend_migrations() -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = POSTGRES_URL
    _run(
        ["uv", "run", "python", "-m", "app.db.bootstrap"],
        cwd=BACKEND_DIR,
        env=env,
    )


def _flush_rate_limit_redis() -> None:
    deadline = time.monotonic() + SERVICE_READY_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            redis.Redis.from_url(RATE_LIMIT_REDIS_URL, socket_connect_timeout=1).flushdb()
            return
        except redis.RedisError as error:
            last_error = error
            time.sleep(1)
    raise RuntimeError("Timed out flushing backend e2e Redis") from last_error


def _reset_backend_singletons() -> None:
    from app.core.crypto import reset_fernet
    from app.api.routes.battles import _get_auth_battle_create_rate_limiter
    from app.api.routes.votes import _get_auth_vote_submit_rate_limiter
    from app.core.config import get_settings
    from app.services.oidc_client import get_oidc_confidential_client
    from app.utils.redis import get_rate_limit_redis_client

    import app.db.session as session_module

    get_settings.cache_clear()
    get_oidc_confidential_client.cache_clear()
    reset_fernet()
    get_rate_limit_redis_client.cache_clear()
    _get_auth_battle_create_rate_limiter.cache_clear()
    _get_auth_vote_submit_rate_limiter.cache_clear()

    session_module._engine = None
    session_module._SessionLocal = None


def _request_authentik_token() -> str:
    deadline = time.monotonic() + SERVICE_READY_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.post(
                f"{AUTHENTIK_BASE_URL}/application/o/token/",
                data={"grant_type": "client_credentials", "scope": "openid"},
                auth=(AUTHENTIK_CLIENT_ID, AUTHENTIK_CLIENT_SECRET),
                timeout=10.0,
            )
            response.raise_for_status()

            payload = response.json()
            token = payload.get("access_token")
            if not isinstance(token, str) or not token:
                raise RuntimeError("Authentik token response did not include access_token")
            return token
        except (httpx.HTTPError, RuntimeError) as error:
            last_error = error
            time.sleep(1)

    raise RuntimeError("Timed out requesting Authentik e2e token") from last_error


def _read_authentik_provider_config(stack: E2EStack) -> dict[str, object]:
    script = dedent(
        f"""
        import json
        from authentik.providers.oauth2.models import OAuth2Provider

        provider = OAuth2Provider.objects.get(name="arena-e2e-provider")
        print(json.dumps({{
            "client_id": provider.client_id,
            "client_secret_configured": bool(provider.client_secret),
            "client_secret_matches_expected": provider.client_secret == {AUTHENTIK_CLIENT_SECRET!r},
            "client_type": provider.client_type,
            "redirect_uris": provider._redirect_uris,
        }}, sort_keys=True))
        """
    ).strip()
    result = _run(
        _compose_command(
            stack,
            "exec",
            "-T",
            "authentik-server",
            "ak",
            "shell",
            "-c",
            script,
        ),
        cwd=E2E_DIR,
    )

    for line in reversed(result.stdout.splitlines()):
        candidate = line.strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            payload = json.loads(candidate)
            if isinstance(payload, dict):
                return payload

    raise RuntimeError(
        "Authentik provider config command did not return JSON:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


@pytest.fixture(scope="session")
def e2e_stack() -> Generator[E2EStack, None, None]:
    stack = E2EStack(compose_file=COMPOSE_FILE, compose_project=COMPOSE_PROJECT)

    with _stack_lifecycle_lock():
        _compose_down(stack)
        _compose_up_detached(stack)
        try:
            _wait_for_compose_ports()
            _wait_for_authentik_health(stack)
            _bootstrap_authentik(stack)
            _run_backend_migrations()
            yield stack
        finally:
            _compose_down(stack)


@pytest.fixture
def configured_backend_env(
    e2e_stack: E2EStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    del e2e_stack

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "warning")
    monkeypatch.setenv("LEADERBOARD_REFRESH_ENABLED", "false")
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    monkeypatch.setenv("RATE_LIMIT_REDIS_URL", RATE_LIMIT_REDIS_URL)
    monkeypatch.setenv("OIDC_ISSUER", AUTHENTIK_ISSUER)
    monkeypatch.setenv("OIDC_CLIENT_ID", AUTHENTIK_CLIENT_ID)
    monkeypatch.setenv("OIDC_CLIENT_SECRET", AUTHENTIK_CLIENT_SECRET)
    monkeypatch.setenv("OIDC_ADMIN_GROUP_CLAIM", "groups")
    monkeypatch.setenv("OIDC_ADMIN_GROUP_NAME", "arena_admin")
    monkeypatch.setenv("OIDC_REDIRECT_PATH", "/api/v1/auth/callback")
    monkeypatch.setenv("PUBLIC_BASE_URL", PUBLIC_BASE_URL)
    monkeypatch.setenv("AUTH_SESSION_HASH_SECRET", AUTH_SESSION_HASH_SECRET)
    monkeypatch.setenv("ARENA_MASTER_KEY", ARENA_MASTER_KEY)
    monkeypatch.setenv(
        "SERVICE_TOKEN_HASH_SECRET", "arena-e2e-service-token-hmac-secret"
    )
    monkeypatch.setenv("AUTH_BATTLE_CREATE_RATE_LIMIT", "1")
    monkeypatch.setenv("AUTH_BATTLE_CREATE_RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("AUTH_VOTE_SUBMIT_RATE_LIMIT", "1")
    monkeypatch.setenv("AUTH_VOTE_SUBMIT_RATE_LIMIT_WINDOW_SECONDS", "60")

    _reset_backend_singletons()


@pytest.fixture(autouse=True)
def clear_rate_limit_redis(configured_backend_env: None) -> None:
    del configured_backend_env
    _flush_rate_limit_redis()


@pytest.fixture
def backend_client(configured_backend_env: None):
    del configured_backend_env

    from fastapi.testclient import TestClient  # pyright: ignore[reportMissingImports]

    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client


def _attach_session_to_client(
    *,
    client: object,
    db_session: object,
    admin: bool = False,
) -> E2EAuthenticatedClient:
    from app.core.config import get_settings
    from app.models.user import User
    from app.services import auth_session

    settings = get_settings()
    user = User(
        oidc_issuer=AUTHENTIK_ISSUER.rstrip("/"),
        oidc_sub=f"e2e-user-{uuid.uuid4()}",
    )
    db_session.add(user)
    db_session.flush()
    created = auth_session.create_auth_session(
        db_session,
        user=user,
        claims={"groups": ["arena_admin"]} if admin else {"groups": []},
        settings=settings,
    )
    user_id = str(user.id)
    db_session.commit()

    client.cookies.set(settings.auth_session_cookie_name, created.session_token)
    session_response = client.get("/api/v1/auth/session")
    assert session_response.status_code == 200, session_response.text
    csrf_token = session_response.json()["csrf_token"]
    assert isinstance(csrf_token, str)
    return E2EAuthenticatedClient(
        client=client,
        headers={settings.auth_csrf_header_name: csrf_token},
        user_id=user_id,
    )


@pytest.fixture
def authenticated_backend_client(backend_client, db_session) -> E2EAuthenticatedClient:
    return _attach_session_to_client(client=backend_client, db_session=db_session)


@pytest.fixture
def admin_authenticated_backend_client(backend_client, db_session) -> E2EAuthenticatedClient:
    return _attach_session_to_client(
        client=backend_client,
        db_session=db_session,
        admin=True,
    )


@pytest.fixture
def db_session(configured_backend_env: None):
    del configured_backend_env

    from app.db.session import get_sessionmaker

    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def authentik_token(e2e_stack: E2EStack) -> str:
    del e2e_stack
    return _request_authentik_token()


@pytest.fixture(scope="session")
def authentik_provider_config(e2e_stack: E2EStack) -> dict[str, object]:
    return _read_authentik_provider_config(e2e_stack)
