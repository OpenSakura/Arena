"""app.services.oidc

OIDC token verification and user upsert.

Notes:
- This project integrates with Authentik via OIDC.
- Implementation plan:
  1) Discover JWKS endpoint via issuer discovery document.
  2) Cache JWKS keys (in-memory is OK for MVP).
  3) Verify JWT signature + issuer + audience.
  4) Upsert `users` row keyed by (issuer, sub).
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
import json
import time
from typing import Any

import httpx
import jwt
from jwt import InvalidTokenError
from jwt.algorithms import get_default_algorithms

from app.core.config import get_settings


class OIDCConfigurationError(RuntimeError):
    """Raised when OIDC discovery/JWKS configuration is invalid."""


class OIDCVerificationError(RuntimeError):
    """Raised when an OIDC token cannot be verified."""


class OIDCVerifier:
    """Verifies OIDC access tokens using discovery + JWKS caching."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str | None,
        cache_ttl_seconds: int,
        http_timeout_seconds: float,
    ) -> None:
        self._issuer = issuer
        self._issuer_normalized = issuer.rstrip("/")
        self._audience = audience
        self._cache_ttl_seconds = max(cache_ttl_seconds, 1)
        self._http_timeout_seconds = max(http_timeout_seconds, 1.0)

        self._discovery_doc: dict[str, Any] | None = None
        self._discovery_expiry_monotonic = 0.0

        self._jwks_doc: dict[str, Any] | None = None
        self._jwks_expiry_monotonic = 0.0

        self._last_forced_refresh_monotonic: float = 0.0
        self._forced_refresh_min_interval: float = 30.0

        self._http_client: httpx.AsyncClient | None = None
        self._cache_lock = asyncio.Lock()
        self._http_client_lock = asyncio.Lock()

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Return (or lazily create) a long-lived httpx client for OIDC fetches.

        Uses ``_http_client_lock`` to prevent concurrent creation from leaking
        connection pools at startup.
        """
        client = self._http_client
        if client is not None and not client.is_closed:
            return client
        async with self._http_client_lock:
            client = self._http_client
            if client is not None and not client.is_closed:
                return client
            client = httpx.AsyncClient(
                timeout=self._http_timeout_seconds,
                follow_redirects=True,
            )
            self._http_client = client
            return client

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    async def verify(self, token: str) -> dict[str, Any]:
        if not self._issuer:
            raise OIDCConfigurationError("OIDC_ISSUER is not configured")

        header = self._get_unverified_header(token)

        alg = str(header.get("alg", ""))
        # Allowlist: only accept well-known asymmetric algorithms.
        # A denylist (blocking "none"/"HS*") is fragile because new weak
        # algorithms could bypass it.
        _ALLOWED_ALGORITHMS = {
            "RS256",
            "RS384",
            "RS512",
            "ES256",
            "ES384",
            "ES512",
            "PS256",
            "PS384",
            "PS512",
            "EdDSA",
        }
        if not alg or alg not in _ALLOWED_ALGORITHMS:
            raise OIDCVerificationError(f"Unsupported JWT algorithm: {alg}")

        kid = header.get("kid")
        signing_jwk = await self._get_signing_jwk(kid)
        signing_key = self._jwk_to_public_key(signing_jwk, alg)

        decode_kwargs: dict[str, Any] = {
            "key": signing_key,
            "algorithms": [alg],
            # We verify issuer manually with trailing-slash normalization.
            "options": {
                "verify_aud": self._audience is not None,
                "verify_iss": False,
                "verify_exp": True,
                # Require critical claims so tokens missing them are rejected
                # outright rather than silently accepted as valid forever.
                "require": ["exp", "iat", "sub"],
            },
        }
        if self._audience is not None:
            decode_kwargs["audience"] = self._audience

        try:
            claims = jwt.decode(token, **decode_kwargs)
        except InvalidTokenError as exc:
            raise OIDCVerificationError("JWT verification failed") from exc

        if not isinstance(claims, dict):
            raise OIDCVerificationError("Token claims payload is invalid")

        issuer_claim = claims.get("iss")
        if not isinstance(issuer_claim, str) or not issuer_claim:
            raise OIDCVerificationError("Token missing issuer claim")
        if issuer_claim.rstrip("/") != self._issuer_normalized:
            raise OIDCVerificationError("JWT issuer mismatch")

        return claims

    @staticmethod
    def _get_unverified_header(token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except InvalidTokenError as exc:
            raise OIDCVerificationError("Invalid JWT header") from exc

        if not isinstance(header, dict):
            raise OIDCVerificationError("Invalid JWT header")

        return header

    async def _get_signing_jwk(self, kid: str | None) -> dict[str, Any]:
        jwks_doc = await self._get_jwks()
        jwk = self._select_jwk(jwks_doc, kid)

        if jwk is None and kid is not None:
            # Serialize forced refreshes under the cache lock to prevent
            # concurrent requests from all triggering duplicate JWKS fetches.
            should_force = False
            async with self._cache_lock:
                now = time.monotonic()
                if (
                    now - self._last_forced_refresh_monotonic
                    >= self._forced_refresh_min_interval
                ):
                    self._last_forced_refresh_monotonic = now
                    should_force = True
                else:
                    # Not enough time has passed — re-read the in-memory
                    # cache directly.  Calling ``_get_jwks()`` here would
                    # deadlock because it tries to acquire ``_cache_lock``
                    # which we already hold (``asyncio.Lock`` is not
                    # re-entrant).
                    if self._jwks_doc is not None:
                        jwk = self._select_jwk(self._jwks_doc, kid)
                        if jwk is not None:
                            return jwk

            # Perform the forced refresh outside the lock but only if the
            # rate-limit gate allowed it.
            if jwk is None and should_force:
                jwks_doc = await self._get_jwks(force_refresh=True)
                jwk = self._select_jwk(jwks_doc, kid)

        if jwk is None:
            raise OIDCVerificationError("No matching signing key found in JWKS")

        return jwk

    async def _get_discovery(self, *, force_refresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if (
            not force_refresh
            and self._discovery_doc is not None
            and now < self._discovery_expiry_monotonic
        ):
            return self._discovery_doc

        async with self._cache_lock:
            now = time.monotonic()
            if (
                not force_refresh
                and self._discovery_doc is not None
                and now < self._discovery_expiry_monotonic
            ):
                return self._discovery_doc

            discovery_url = (
                f"{self._issuer_normalized}/.well-known/openid-configuration"
            )
            discovery_doc = await self._fetch_json(discovery_url)

            discovered_issuer = discovery_doc.get("issuer")
            if isinstance(discovered_issuer, str):
                if discovered_issuer.rstrip("/") != self._issuer_normalized:
                    raise OIDCConfigurationError(
                        "OIDC issuer mismatch between config and discovery"
                    )

            self._discovery_doc = discovery_doc
            self._discovery_expiry_monotonic = now + self._cache_ttl_seconds
            return discovery_doc

    async def _get_jwks(self, *, force_refresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if (
            not force_refresh
            and self._jwks_doc is not None
            and now < self._jwks_expiry_monotonic
        ):
            return self._jwks_doc

        discovery_doc = await self._get_discovery(force_refresh=force_refresh)
        jwks_uri = discovery_doc.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not jwks_uri:
            raise OIDCConfigurationError("OIDC discovery document missing jwks_uri")

        async with self._cache_lock:
            now = time.monotonic()
            if (
                not force_refresh
                and self._jwks_doc is not None
                and now < self._jwks_expiry_monotonic
            ):
                return self._jwks_doc

            jwks_doc = await self._fetch_json(jwks_uri)
            if not isinstance(jwks_doc.get("keys"), list):
                raise OIDCConfigurationError("JWKS payload missing keys list")

            self._jwks_doc = jwks_doc
            self._jwks_expiry_monotonic = now + self._cache_ttl_seconds
            return jwks_doc

    async def _fetch_json(self, url: str) -> dict[str, Any]:
        try:
            client = await self._get_http_client()
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise OIDCConfigurationError(f"Failed to fetch OIDC URL: {url}") from exc
        except ValueError as exc:
            raise OIDCConfigurationError(f"Invalid JSON at OIDC URL: {url}") from exc

        if not isinstance(payload, dict):
            raise OIDCConfigurationError(
                f"OIDC URL did not return a JSON object: {url}"
            )

        return payload

    @staticmethod
    def _select_jwk(jwks_doc: dict[str, Any], kid: str | None) -> dict[str, Any] | None:
        keys = jwks_doc.get("keys")
        if not isinstance(keys, list):
            return None

        if kid is not None:
            for entry in keys:
                if isinstance(entry, dict) and entry.get("kid") == kid:
                    return entry
            return None

        if len(keys) == 1 and isinstance(keys[0], dict):
            return keys[0]

        for entry in keys:
            if isinstance(entry, dict) and entry.get("use", "sig") == "sig":
                return entry

        return None

    @staticmethod
    def _jwk_to_public_key(jwk: dict[str, Any], alg: str) -> Any:
        jwk_alg = jwk.get("alg")
        if isinstance(jwk_alg, str) and jwk_alg and jwk_alg != alg:
            raise OIDCVerificationError("JWT algorithm does not match JWK algorithm")

        algorithm = get_default_algorithms().get(alg)
        if algorithm is None:
            raise OIDCVerificationError("Unsupported JWT algorithm")

        try:
            return algorithm.from_jwk(json.dumps(jwk))
        except Exception as exc:  # noqa: BLE001
            raise OIDCVerificationError("Failed to decode JWK signing key") from exc


@lru_cache(maxsize=1)
def get_oidc_verifier() -> OIDCVerifier:
    settings = get_settings()
    return OIDCVerifier(
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        cache_ttl_seconds=settings.oidc_jwks_cache_ttl_seconds,
        http_timeout_seconds=settings.oidc_http_timeout_seconds,
    )


def reset_oidc_verifier() -> None:
    """Clear the cached singleton.

    Intended for test teardown: the cached instance holds an
    ``asyncio.Lock`` bound to the event loop active at creation time.
    """
    try:
        verifier = get_oidc_verifier()
    except Exception:  # noqa: BLE001
        pass
    else:
        try:
            import asyncio as _asyncio

            loop = _asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(verifier.aclose())
            else:
                loop.run_until_complete(verifier.aclose())
        except Exception:  # noqa: BLE001
            pass
    get_oidc_verifier.cache_clear()
