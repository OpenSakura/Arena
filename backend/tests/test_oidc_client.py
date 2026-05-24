from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import InvalidAudienceError, InvalidIssuerError
from jwt.algorithms import RSAAlgorithm
import pytest

from app.services import oidc_client
from app.services.oidc import OIDCConfigurationError, OIDCVerificationError
from app.services.oidc_client import OIDCConfidentialClient, OIDCTokenExchangeError


TEST_SECRET = "super-secret-client-password"
AUTH_CODE = "raw-auth-code"
CODE_VERIFIER = "raw-code-verifier"
ACCESS_TOKEN = "raw-access-token"
REFRESH_TOKEN = "raw-refresh-token"
ID_TOKEN = "raw-id-token"


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], *, raises: Exception | None = None) -> None:
        self._payload = payload
        self._raises = raises

    def raise_for_status(self) -> None:
        if self._raises is not None:
            raise self._raises

    def json(self) -> dict[str, Any]:
        return self._payload


def _make_client() -> OIDCConfidentialClient:
    return OIDCConfidentialClient(
        issuer="https://issuer.example/",
        client_id="arena-client",
        client_secret=TEST_SECRET,
        client_auth_method="client_secret_basic",
        scope="openid email profile",
        public_base_url="https://arena.example/",
        redirect_path="/api/v1/auth/callback",
        cache_ttl_seconds=60,
        http_timeout_seconds=1.0,
    )


def _discovery() -> dict[str, Any]:
    return {
        "issuer": "https://issuer.example",
        "authorization_endpoint": "https://issuer.example/authorize",
        "token_endpoint": "https://issuer.example/token",
        "jwks_uri": "https://issuer.example/jwks",
    }


def _query_params(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


def test_authorization_url_contains_confidential_client_pkce_and_no_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client()
    monkeypatch.setattr(client, "_fetch_json", lambda _url: _async_value(_discovery()))

    request = asyncio.run(client.create_authorization_request())
    params = _query_params(request.authorization_url)

    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["arena-client"]
    assert params["scope"] == ["openid email profile"]
    assert params["redirect_uri"] == ["https://arena.example/api/v1/auth/callback"]
    assert params["state"] == [request.state]
    assert params["nonce"] == [request.nonce]
    assert params["code_challenge"] == [request.code_challenge]
    assert params["code_challenge_method"] == ["S256"]
    assert "client_secret" not in params
    assert "offline_access" not in params["scope"][0]
    assert len(request.state) >= 40
    assert len(request.nonce) >= 40
    assert len(request.code_verifier) >= 80


def test_pkce_challenge_uses_s256() -> None:
    verifier = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~"
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    assert oidc_client.OIDCConfidentialClient._code_challenge_for_verifier(verifier) == expected


def test_authorization_request_generates_entropy_per_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client()
    monkeypatch.setattr(client, "_fetch_json", lambda _url: _async_value(_discovery()))

    first = asyncio.run(client.create_authorization_request())
    second = asyncio.run(client.create_authorization_request())

    assert first.state != second.state
    assert first.nonce != second.nonce
    assert first.code_verifier != second.code_verifier


def test_token_exchange_uses_basic_auth_and_code_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client()
    seen: dict[str, Any] = {}

    class _FakeAsyncClient:
        is_closed = False

        async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            seen["url"] = url
            seen.update(kwargs)
            return _FakeResponse({"id_token": ID_TOKEN, "token_type": "Bearer", "expires_in": 300})

        async def aclose(self) -> None:
            self.is_closed = True

    monkeypatch.setattr(client, "_get_discovery", lambda: _async_value(_discovery()))
    monkeypatch.setattr(client, "_get_http_client", lambda: _async_value(_FakeAsyncClient()))
    monkeypatch.setattr(client, "_validate_id_token", lambda token, *, expected_nonce: _async_value({"iss": "https://issuer.example", "sub": "user", "nonce": expected_nonce}))

    result = asyncio.run(
        client.exchange_code(code=AUTH_CODE, code_verifier=CODE_VERIFIER, nonce="nonce-1")
    )

    assert seen["url"] == "https://issuer.example/token"
    assert seen["auth"] == ("arena-client", TEST_SECRET)
    assert seen["data"] == {
        "grant_type": "authorization_code",
        "code": AUTH_CODE,
        "redirect_uri": "https://arena.example/api/v1/auth/callback",
        "code_verifier": CODE_VERIFIER,
    }
    assert "client_secret" not in seen["data"]
    assert result.claims["sub"] == "user"
    assert result.token_type == "Bearer"
    assert result.expires_in == 300


def test_id_token_validation_accepts_valid_claims_and_normalizes_issuer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client()
    captured: dict[str, Any] = {}

    def _decode(token: str, **kwargs: Any) -> dict[str, Any]:
        captured["token"] = token
        captured.update(kwargs)
        return {
            "iss": "https://issuer.example/",
            "aud": "arena-client",
            "sub": "user-123",
            "exp": int(time.time()) + 300,
            "iat": int(time.time()),
            "nonce": "nonce-1",
            "email": "user@example.com",
        }

    monkeypatch.setattr(oidc_client.jwt, "get_unverified_header", lambda _token: {"alg": "RS256", "kid": "key-1"})
    monkeypatch.setattr(client, "_get_signing_jwk", lambda _kid: _async_value({"kty": "RSA"}))
    monkeypatch.setattr(oidc_client.OIDCVerifier, "_jwk_to_public_key", lambda _jwk, _alg: "public-key")
    monkeypatch.setattr(oidc_client.jwt, "decode", _decode)

    claims = asyncio.run(client._validate_id_token(ID_TOKEN, expected_nonce="nonce-1"))

    assert claims["iss"] == "https://issuer.example"
    assert claims["sub"] == "user-123"
    assert captured["token"] == ID_TOKEN
    assert captured["key"] == "public-key"
    assert captured["algorithms"] == ["RS256"]
    assert captured["audience"] == "arena-client"
    assert captured["options"]["verify_exp"] is True
    assert captured["options"]["require"] == ["exp", "iat", "sub"]


def test_id_token_validation_rejects_invalid_nonce(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_decode_claims(
        monkeypatch,
        {
            "iss": "https://issuer.example",
            "aud": "arena-client",
            "sub": "user-123",
            "exp": int(time.time()) + 300,
            "iat": int(time.time()),
            "nonce": "wrong-nonce",
        },
    )

    with pytest.raises(OIDCVerificationError, match="nonce mismatch"):
        asyncio.run(_make_client()._validate_id_token(ID_TOKEN, expected_nonce="nonce-1"))


def test_id_token_validation_rejects_wrong_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_decode_error(monkeypatch, InvalidIssuerError("bad issuer"))

    with pytest.raises(OIDCVerificationError, match="ID token verification failed"):
        asyncio.run(_make_client()._validate_id_token(ID_TOKEN, expected_nonce="nonce-1"))


def test_id_token_validation_rejects_wrong_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_decode_error(monkeypatch, InvalidAudienceError("bad audience"))

    with pytest.raises(OIDCVerificationError, match="ID token verification failed"):
        asyncio.run(_make_client()._validate_id_token(ID_TOKEN, expected_nonce="nonce-1"))


def test_token_endpoint_failure_raises_without_secret_logging(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client()

    class _FakeAsyncClient:
        is_closed = False

        async def post(self, _url: str, **_kwargs: Any) -> _FakeResponse:
            return _FakeResponse({}, raises=oidc_client.httpx.HTTPStatusError("boom", request=None, response=None))

    monkeypatch.setattr(client, "_get_discovery", lambda: _async_value(_discovery()))
    monkeypatch.setattr(client, "_get_http_client", lambda: _async_value(_FakeAsyncClient()))

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(OIDCTokenExchangeError, match="token endpoint request failed"):
            asyncio.run(
                client.exchange_code(
                    code=AUTH_CODE,
                    code_verifier=CODE_VERIFIER,
                    nonce="nonce-1",
                )
            )

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    for raw_secret in (TEST_SECRET, AUTH_CODE, CODE_VERIFIER, ACCESS_TOKEN, REFRESH_TOKEN, ID_TOKEN):
        assert raw_secret not in log_text


def test_token_exchange_fails_closed_when_provider_rejects_wrong_redirect_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = OIDCConfidentialClient(
        issuer="https://issuer.example/",
        client_id="arena-client",
        client_secret=TEST_SECRET,
        client_auth_method="client_secret_basic",
        scope="openid email profile",
        public_base_url="https://arena.example/",
        redirect_path="/wrong/callback",
        cache_ttl_seconds=60,
        http_timeout_seconds=1.0,
    )
    seen: dict[str, Any] = {}

    class _FakeAsyncClient:
        is_closed = False

        async def post(self, _url: str, **kwargs: Any) -> _FakeResponse:
            seen.update(kwargs)
            assert kwargs["data"]["redirect_uri"] == "https://arena.example/wrong/callback"
            return _FakeResponse(
                {},
                raises=oidc_client.httpx.HTTPStatusError(
                    "redirect_uri mismatch",
                    request=None,
                    response=None,
                ),
            )

    monkeypatch.setattr(client, "_get_discovery", lambda: _async_value(_discovery()))
    monkeypatch.setattr(client, "_get_http_client", lambda: _async_value(_FakeAsyncClient()))

    with pytest.raises(OIDCTokenExchangeError, match="token endpoint request failed"):
        asyncio.run(
            client.exchange_code(
                code=AUTH_CODE,
                code_verifier=CODE_VERIFIER,
                nonce="nonce-1",
            )
        )

    assert seen["auth"] == ("arena-client", TEST_SECRET)
    assert seen["data"]["code_verifier"] == CODE_VERIFIER
    assert "client_secret" not in seen["data"]


def test_refresh_token_is_discarded_and_not_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()

    class _FakeAsyncClient:
        is_closed = False

        async def post(self, _url: str, **_kwargs: Any) -> _FakeResponse:
            return _FakeResponse(
                {
                    "id_token": ID_TOKEN,
                    "access_token": ACCESS_TOKEN,
                    "refresh_token": REFRESH_TOKEN,
                    "token_type": "Bearer",
                    "expires_in": 300,
                    "scope": "openid email profile",
                }
            )

    monkeypatch.setattr(client, "_get_discovery", lambda: _async_value(_discovery()))
    monkeypatch.setattr(client, "_get_http_client", lambda: _async_value(_FakeAsyncClient()))
    monkeypatch.setattr(client, "_validate_id_token", lambda _token, *, expected_nonce: _async_value({"iss": "https://issuer.example", "sub": "user", "nonce": expected_nonce}))

    result = asyncio.run(
        client.exchange_code(code=AUTH_CODE, code_verifier=CODE_VERIFIER, nonce="nonce-1")
    )

    assert result.claims == {"iss": "https://issuer.example", "sub": "user", "nonce": "nonce-1"}
    assert result.token_type == "Bearer"
    assert result.expires_in == 300
    assert result.scope == "openid email profile"
    assert not hasattr(result, "access_token")
    assert not hasattr(result, "refresh_token")
    assert not hasattr(result, "id_token")
    assert ACCESS_TOKEN not in repr(result)
    assert REFRESH_TOKEN not in repr(result)
    assert ID_TOKEN not in repr(result)


def test_default_authorization_url_omits_offline_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client()
    monkeypatch.setattr(client, "_fetch_json", lambda _url: _async_value(_discovery()))

    request = asyncio.run(client.create_authorization_request())

    assert "offline_access" not in _query_params(request.authorization_url)["scope"][0]


def test_discovery_uses_normalized_issuer_and_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client()
    urls: list[str] = []

    async def _fetch_json(url: str) -> dict[str, Any]:
        urls.append(url)
        return _discovery()

    monkeypatch.setattr(client, "_fetch_json", _fetch_json)

    first = asyncio.run(client._get_discovery())
    second = asyncio.run(client._get_discovery())

    assert first == second
    assert urls == ["https://issuer.example/.well-known/openid-configuration"]


def test_discovery_rejects_wrong_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()
    monkeypatch.setattr(
        client,
        "_fetch_json",
        lambda _url: _async_value({**_discovery(), "issuer": "https://wrong.example"}),
    )

    with pytest.raises(OIDCConfigurationError, match="issuer mismatch"):
        asyncio.run(client._get_discovery())


def test_real_rsa_id_token_validation_end_to_end() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = RSAAlgorithm.to_jwk(private_key.public_key())
    jwk_doc = dict(__import__("json").loads(jwk), kid="key-1", alg="RS256", use="sig")
    token = oidc_client.jwt.encode(
        {
            "iss": "https://issuer.example",
            "aud": "arena-client",
            "sub": "user-123",
            "exp": int(time.time()) + 300,
            "iat": int(time.time()),
            "nonce": "nonce-1",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )
    client = _make_client()

    async def _run() -> dict[str, Any]:
        client._discovery_doc = _discovery()
        client._discovery_expiry_monotonic = time.monotonic() + 60
        client._jwks_doc = {"keys": [jwk_doc]}
        client._jwks_expiry_monotonic = time.monotonic() + 60
        return await client._validate_id_token(token, expected_nonce="nonce-1")

    claims = asyncio.run(_run())

    assert claims["sub"] == "user-123"


def _patch_decode_claims(monkeypatch: pytest.MonkeyPatch, claims: dict[str, Any]) -> None:
    monkeypatch.setattr(oidc_client.jwt, "get_unverified_header", lambda _token: {"alg": "RS256", "kid": "key-1"})
    monkeypatch.setattr(oidc_client.OIDCConfidentialClient, "_get_signing_jwk", lambda self, _kid: _async_value({"kty": "RSA"}))
    monkeypatch.setattr(oidc_client.OIDCVerifier, "_jwk_to_public_key", lambda _jwk, _alg: "public-key")
    monkeypatch.setattr(oidc_client.jwt, "decode", lambda *_args, **_kwargs: claims)


def _patch_decode_error(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    monkeypatch.setattr(oidc_client.jwt, "get_unverified_header", lambda _token: {"alg": "RS256", "kid": "key-1"})
    monkeypatch.setattr(oidc_client.OIDCConfidentialClient, "_get_signing_jwk", lambda self, _kid: _async_value({"kty": "RSA"}))
    monkeypatch.setattr(oidc_client.OIDCVerifier, "_jwk_to_public_key", lambda _jwk, _alg: "public-key")

    def _decode(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise error

    monkeypatch.setattr(oidc_client.jwt, "decode", _decode)


async def _async_value(value: Any) -> Any:
    return value
