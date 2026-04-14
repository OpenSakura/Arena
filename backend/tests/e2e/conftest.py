from __future__ import annotations

from collections.abc import Generator
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

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
AUTHENTIK_CLIENT_SECRET = "arena-e2e-secret"


@dataclass(frozen=True)
class E2EStack:
    compose_file: Path
    compose_project: str


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
                "client_secret": "arena-e2e-secret",
                "issuer_mode": "per_provider",
                "_redirect_uris": [
                    {"matching_mode": "strict", "url": "http://localhost:3000/callback"},
                ],
            },
        )

        provider.authorization_flow = authorization_flow
        provider.invalidation_flow = invalidation_flow
        provider.client_type = "confidential"
        provider.client_id = "arena-e2e-client"
        provider.client_secret = "arena-e2e-secret"
        provider.issuer_mode = "per_provider"
        provider._redirect_uris = [
            {"matching_mode": "strict", "url": "http://localhost:3000/callback"},
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


def _run_backend_migrations() -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = POSTGRES_URL
    _run(
        ["uv", "run", "python", "-m", "app.db.bootstrap"],
        cwd=BACKEND_DIR,
        env=env,
    )


def _reset_backend_singletons() -> None:
    from app.api.routes.battles import _get_battle_create_rate_limiter
    from app.api.routes.votes import _get_vote_submit_rate_limiter
    from app.core.config import get_settings
    from app.services.oidc import get_oidc_verifier
    from app.utils.redis import get_rate_limit_redis_client

    import app.db.session as session_module

    get_settings.cache_clear()
    get_oidc_verifier.cache_clear()
    get_rate_limit_redis_client.cache_clear()
    _get_battle_create_rate_limiter.cache_clear()
    _get_vote_submit_rate_limiter.cache_clear()

    session_module._engine = None
    session_module._SessionLocal = None


def _request_authentik_token() -> str:
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


@pytest.fixture(scope="session")
def e2e_stack() -> Generator[E2EStack, None, None]:
    stack = E2EStack(compose_file=COMPOSE_FILE, compose_project=COMPOSE_PROJECT)

    _run(_compose_command(stack, "up", "-d", "--wait"), cwd=E2E_DIR)
    try:
        _bootstrap_authentik(stack)
        _run_backend_migrations()
        yield stack
    finally:
        _run(
            _compose_command(stack, "down", "-v", "--remove-orphans"),
            cwd=E2E_DIR,
            check=False,
        )


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
    monkeypatch.setenv("OIDC_AUDIENCE", AUTHENTIK_CLIENT_ID)
    monkeypatch.setenv("ANON_ID_COOKIE_SECURE", "false")
    monkeypatch.setenv("ANON_BATTLE_CREATE_RATE_LIMIT", "1")
    monkeypatch.setenv("ANON_BATTLE_CREATE_RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("ANON_VOTE_SUBMIT_RATE_LIMIT", "1")
    monkeypatch.setenv("ANON_VOTE_SUBMIT_RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("AUTH_BATTLE_CREATE_RATE_LIMIT", "1")
    monkeypatch.setenv("AUTH_BATTLE_CREATE_RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("AUTH_VOTE_SUBMIT_RATE_LIMIT", "1")
    monkeypatch.setenv("AUTH_VOTE_SUBMIT_RATE_LIMIT_WINDOW_SECONDS", "60")

    _reset_backend_singletons()


@pytest.fixture(autouse=True)
def clear_rate_limit_redis(configured_backend_env: None) -> None:
    del configured_backend_env
    client = redis.Redis.from_url(RATE_LIMIT_REDIS_URL)
    client.flushdb()


@pytest.fixture
def backend_client(configured_backend_env: None):
    del configured_backend_env

    from fastapi.testclient import TestClient  # pyright: ignore[reportMissingImports]

    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client


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
