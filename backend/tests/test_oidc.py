from __future__ import annotations

import asyncio
from typing import Any

from jwt import InvalidTokenError
import pytest

from app.services import oidc
from app.services.oidc import (
    OIDCConfigurationError,
    OIDCVerificationError,
    OIDCVerifier,
)


class _StubVerifier(OIDCVerifier):
    _stub_header: dict[str, Any] = {}

    def __init__(
        self, *, header: dict[str, Any], audience: str | None = "arena"
    ) -> None:
        super().__init__(
            issuer="https://issuer.example/",
            audience=audience,
            cache_ttl_seconds=60,
            http_timeout_seconds=1.0,
        )
        # Store on the class so the @staticmethod override can read it.
        # Safe because tests are single-threaded and each test creates a
        # fresh _StubVerifier instance.
        type(self)._stub_header = dict(header)
        self.seen_kid: str | None = None

    @staticmethod
    def _get_unverified_header(token: str) -> dict[str, Any]:
        return dict(_StubVerifier._stub_header)

    async def _get_signing_jwk(self, kid: str | None) -> dict[str, Any]:
        self.seen_kid = kid
        return {"kty": "RSA"}

    @staticmethod
    def _jwk_to_public_key(jwk: dict[str, Any], alg: str) -> Any:
        return "public-key"


def test_select_jwk_prefers_matching_kid() -> None:
    jwks = {
        "keys": [
            {"kid": "old", "kty": "RSA"},
            {"kid": "target", "kty": "RSA"},
        ]
    }

    selected = OIDCVerifier._select_jwk(jwks, "target")
    assert selected == {"kid": "target", "kty": "RSA"}


def test_select_jwk_picks_single_key_when_no_kid() -> None:
    selected = OIDCVerifier._select_jwk({"keys": [{"kid": "only"}]}, None)
    assert selected == {"kid": "only"}


def test_select_jwk_picks_first_signing_key_without_kid() -> None:
    jwks = {
        "keys": [
            {"kid": "enc", "use": "enc"},
            {"kid": "sig", "use": "sig"},
        ]
    }

    selected = OIDCVerifier._select_jwk(jwks, None)
    assert selected == {"kid": "sig", "use": "sig"}


def test_select_jwk_returns_none_for_invalid_payload() -> None:
    assert OIDCVerifier._select_jwk({"keys": "not-a-list"}, "kid") is None


def test_jwk_to_public_key_rejects_algorithm_mismatch() -> None:
    with pytest.raises(
        OIDCVerificationError,
        match="JWT algorithm does not match JWK algorithm",
    ):
        OIDCVerifier._jwk_to_public_key({"kty": "RSA", "alg": "RS256"}, "ES256")


def test_jwk_to_public_key_rejects_unsupported_algorithms() -> None:
    with pytest.raises(OIDCVerificationError, match="Unsupported JWT algorithm"):
        OIDCVerifier._jwk_to_public_key({"kty": "RSA"}, "NOPE256")


@pytest.mark.parametrize("alg", ["none", "HS256"])
def test_verify_rejects_disallowed_jwt_algorithms(alg: str) -> None:
    verifier = _StubVerifier(header={"alg": alg})

    with pytest.raises(OIDCVerificationError, match="Unsupported JWT algorithm"):
        asyncio.run(verifier.verify("token"))


def test_verify_wraps_invalid_token_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    verifier = _StubVerifier(header={"alg": "RS256", "kid": "key-1"})

    def _decode(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise InvalidTokenError("bad token")

    monkeypatch.setattr(oidc.jwt, "decode", _decode)

    with pytest.raises(OIDCVerificationError, match="JWT verification failed"):
        asyncio.run(verifier.verify("token"))


def test_verify_requires_issuer_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    verifier = _StubVerifier(header={"alg": "RS256", "kid": "key-1"})
    monkeypatch.setattr(oidc.jwt, "decode", lambda *_args, **_kwargs: {"sub": "user"})

    with pytest.raises(OIDCVerificationError, match="Token missing issuer claim"):
        asyncio.run(verifier.verify("token"))


def test_verify_rejects_issuer_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    verifier = _StubVerifier(header={"alg": "RS256", "kid": "key-1"})
    monkeypatch.setattr(
        oidc.jwt,
        "decode",
        lambda *_args, **_kwargs: {
            "iss": "https://different.example",
            "sub": "user",
        },
    )

    with pytest.raises(OIDCVerificationError, match="JWT issuer mismatch"):
        asyncio.run(verifier.verify("token"))


def test_verify_accepts_normalized_issuer_and_passes_decode_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _StubVerifier(header={"alg": "RS256", "kid": "kid-7"}, audience="arena")

    captured_kwargs: dict[str, Any] = {}

    def _decode(_token: str, **kwargs: Any) -> dict[str, Any]:
        captured_kwargs.update(kwargs)
        return {
            "iss": "https://issuer.example",
            "sub": "user-123",
        }

    monkeypatch.setattr(oidc.jwt, "decode", _decode)

    claims = asyncio.run(verifier.verify("token"))

    assert claims["sub"] == "user-123"
    assert verifier.seen_kid == "kid-7"
    assert captured_kwargs["key"] == "public-key"
    assert captured_kwargs["algorithms"] == ["RS256"]
    assert captured_kwargs["audience"] == "arena"
    assert captured_kwargs["options"]["verify_aud"] is True
    assert captured_kwargs["options"]["verify_iss"] is False


def test_get_discovery_uses_cache_until_forced_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = OIDCVerifier(
        issuer="https://issuer.example",
        audience=None,
        cache_ttl_seconds=60,
        http_timeout_seconds=1.0,
    )
    fetch_urls: list[str] = []

    async def _fetch_json(url: str) -> dict[str, Any]:
        fetch_urls.append(url)
        return {
            "issuer": "https://issuer.example",
            "jwks_uri": "https://issuer.example/jwks",
        }

    monkeypatch.setattr(verifier, "_fetch_json", _fetch_json)

    first = asyncio.run(verifier._get_discovery())
    second = asyncio.run(verifier._get_discovery())
    forced = asyncio.run(verifier._get_discovery(force_refresh=True))

    assert first == second == forced
    assert len(fetch_urls) == 2


def test_get_discovery_rejects_issuer_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    verifier = OIDCVerifier(
        issuer="https://issuer.example",
        audience=None,
        cache_ttl_seconds=60,
        http_timeout_seconds=1.0,
    )

    async def _fetch_json(_url: str) -> dict[str, Any]:
        return {
            "issuer": "https://wrong.example",
            "jwks_uri": "https://issuer.example/jwks",
        }

    monkeypatch.setattr(verifier, "_fetch_json", _fetch_json)

    with pytest.raises(
        OIDCConfigurationError,
        match="OIDC issuer mismatch between config and discovery",
    ):
        asyncio.run(verifier._get_discovery())


def test_get_jwks_requires_jwks_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    verifier = OIDCVerifier(
        issuer="https://issuer.example",
        audience=None,
        cache_ttl_seconds=60,
        http_timeout_seconds=1.0,
    )

    async def _get_discovery(*, force_refresh: bool = False) -> dict[str, Any]:
        _ = force_refresh
        return {"issuer": "https://issuer.example"}

    monkeypatch.setattr(verifier, "_get_discovery", _get_discovery)

    with pytest.raises(
        OIDCConfigurationError,
        match="OIDC discovery document missing jwks_uri",
    ):
        asyncio.run(verifier._get_jwks())


def test_get_signing_jwk_forces_refresh_when_kid_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = OIDCVerifier(
        issuer="https://issuer.example",
        audience=None,
        cache_ttl_seconds=60,
        http_timeout_seconds=1.0,
    )

    refresh_flags: list[bool] = []

    async def _get_jwks(*, force_refresh: bool = False) -> dict[str, Any]:
        refresh_flags.append(force_refresh)
        if force_refresh:
            return {"keys": [{"kid": "target", "kty": "RSA"}]}
        return {"keys": [{"kid": "other", "kty": "RSA"}]}

    monkeypatch.setattr(verifier, "_get_jwks", _get_jwks)

    key = asyncio.run(verifier._get_signing_jwk("target"))

    assert key == {"kid": "target", "kty": "RSA"}
    assert refresh_flags == [False, True]


def test_get_signing_jwk_raises_when_key_never_appears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = OIDCVerifier(
        issuer="https://issuer.example",
        audience=None,
        cache_ttl_seconds=60,
        http_timeout_seconds=1.0,
    )

    async def _get_jwks(*, force_refresh: bool = False) -> dict[str, Any]:
        _ = force_refresh
        return {"keys": [{"kid": "other", "kty": "RSA"}]}

    monkeypatch.setattr(verifier, "_get_jwks", _get_jwks)

    with pytest.raises(OIDCVerificationError, match="No matching signing key found"):
        asyncio.run(verifier._get_signing_jwk("target"))


def test_http_client_lock_prevents_duplicate_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = OIDCVerifier(
        issuer="https://issuer.example",
        audience=None,
        cache_ttl_seconds=60,
        http_timeout_seconds=1.0,
    )

    creation_count = 0

    class _FakeAsyncClient:
        def __init__(self, *, timeout: float, follow_redirects: bool) -> None:
            nonlocal creation_count
            _ = (timeout, follow_redirects)
            creation_count += 1
            self.is_closed = False

        async def aclose(self) -> None:
            self.is_closed = True

    monkeypatch.setattr(oidc.httpx, "AsyncClient", _FakeAsyncClient)

    async def _run():
        barrier = asyncio.Barrier(5)

        async def _get_client_with_count():
            await barrier.wait()
            client = await verifier._get_http_client()
            return client

        tasks = [asyncio.create_task(_get_client_with_count()) for _ in range(5)]
        clients = await asyncio.gather(*tasks)

        for c in clients[1:]:
            assert c is clients[0]
        assert creation_count == 1

        await clients[0].aclose()

    asyncio.run(_run())


def test_get_jwks_first_fetch_does_not_deadlock_while_creating_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = OIDCVerifier(
        issuer="https://issuer.example",
        audience=None,
        cache_ttl_seconds=60,
        http_timeout_seconds=1.0,
    )

    creation_count = 0
    fetch_urls: list[str] = []

    class _FakeResponse:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._payload

    class _FakeAsyncClient:
        def __init__(self, *, timeout: float, follow_redirects: bool) -> None:
            nonlocal creation_count
            _ = (timeout, follow_redirects)
            creation_count += 1
            self.is_closed = False

        async def get(self, url: str) -> _FakeResponse:
            fetch_urls.append(url)
            if url.endswith("/.well-known/openid-configuration"):
                return _FakeResponse(
                    {
                        "issuer": "https://issuer.example",
                        "jwks_uri": "https://issuer.example/jwks",
                    }
                )
            if url.endswith("/jwks"):
                return _FakeResponse({"keys": [{"kid": "key-1", "kty": "RSA"}]})
            raise AssertionError(f"Unexpected URL fetched: {url}")

        async def aclose(self) -> None:
            self.is_closed = True

    monkeypatch.setattr(oidc.httpx, "AsyncClient", _FakeAsyncClient)

    async def _run() -> None:
        jwks = await asyncio.wait_for(verifier._get_jwks(), timeout=0.5)

        assert jwks == {"keys": [{"kid": "key-1", "kty": "RSA"}]}
        assert creation_count == 1
        assert fetch_urls == [
            "https://issuer.example/.well-known/openid-configuration",
            "https://issuer.example/jwks",
        ]

        await verifier.aclose()

    asyncio.run(_run())
