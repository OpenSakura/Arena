from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import InvalidTokenError

from app.core.config import get_settings
from app.services.oidc import OIDCConfigurationError, OIDCVerificationError, OIDCVerifier


_ALLOWED_ASYMMETRIC_ALGORITHMS = {
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


class OIDCTokenExchangeError(RuntimeError):
    pass


@dataclass(frozen=True)
class OIDCAuthorizationRequest:
    authorization_url: str
    state: str
    nonce: str
    code_verifier: str
    code_challenge: str
    redirect_uri: str


@dataclass(frozen=True)
class OIDCTokenExchangeResult:
    claims: dict[str, Any]
    token_type: str | None
    expires_in: int | None
    scope: str | None


class OIDCConfidentialClient:
    def __init__(
        self,
        *,
        issuer: str,
        client_id: str,
        client_secret: str,
        client_auth_method: str,
        scope: str,
        public_base_url: str,
        redirect_path: str,
        cache_ttl_seconds: int,
        http_timeout_seconds: float,
    ) -> None:
        self._issuer = issuer
        self._issuer_normalized = issuer.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._client_auth_method = client_auth_method
        self._scope = scope
        self._public_base_url = public_base_url
        self._redirect_path = redirect_path
        self._cache_ttl_seconds = max(cache_ttl_seconds, 1)
        self._http_timeout_seconds = max(http_timeout_seconds, 1.0)

        self._discovery_doc: dict[str, Any] | None = None
        self._discovery_expiry_monotonic = 0.0
        self._jwks_doc: dict[str, Any] | None = None
        self._jwks_expiry_monotonic = 0.0

        self._cache_lock = asyncio.Lock()
        self._http_client_lock = asyncio.Lock()
        self._http_client: httpx.AsyncClient | None = None

    async def _get_http_client(self) -> httpx.AsyncClient:
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
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    async def create_authorization_request(self) -> OIDCAuthorizationRequest:
        self._require_login_configuration()
        discovery_doc = await self._get_discovery()
        authorization_endpoint = discovery_doc.get("authorization_endpoint")
        if not isinstance(authorization_endpoint, str) or not authorization_endpoint:
            raise OIDCConfigurationError(
                "OIDC discovery document missing authorization_endpoint"
            )

        state = self._generate_urlsafe_secret()
        nonce = self._generate_urlsafe_secret()
        code_verifier = self._generate_urlsafe_secret(byte_count=64)
        code_challenge = self._code_challenge_for_verifier(code_verifier)
        redirect_uri = self.redirect_uri

        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._client_id,
                "redirect_uri": redirect_uri,
                "scope": self._scope,
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        separator = "&" if "?" in authorization_endpoint else "?"
        return OIDCAuthorizationRequest(
            authorization_url=f"{authorization_endpoint}{separator}{query}",
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
            code_challenge=code_challenge,
            redirect_uri=redirect_uri,
        )

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        nonce: str,
    ) -> OIDCTokenExchangeResult:
        self._require_login_configuration()
        discovery_doc = await self._get_discovery()
        token_endpoint = discovery_doc.get("token_endpoint")
        if not isinstance(token_endpoint, str) or not token_endpoint:
            raise OIDCConfigurationError("OIDC discovery document missing token_endpoint")

        token_response = await self._request_tokens(
            token_endpoint=token_endpoint,
            code=code,
            code_verifier=code_verifier,
        )
        id_token = token_response.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise OIDCTokenExchangeError("OIDC token response missing id_token")

        claims = await self._validate_id_token(id_token, expected_nonce=nonce)
        return OIDCTokenExchangeResult(
            claims=claims,
            token_type=self._optional_string(token_response.get("token_type")),
            expires_in=self._optional_int(token_response.get("expires_in")),
            scope=self._optional_string(token_response.get("scope")),
        )

    @property
    def redirect_uri(self) -> str:
        base_url = self._public_base_url.rstrip("/")
        redirect_path = self._redirect_path
        if not redirect_path.startswith("/"):
            redirect_path = f"/{redirect_path}"
        return f"{base_url}{redirect_path}"

    async def _request_tokens(
        self,
        *,
        token_endpoint: str,
        code: str,
        code_verifier: str,
    ) -> dict[str, Any]:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": code_verifier,
        }
        auth: tuple[str, str] | None = None
        if self._client_auth_method == "client_secret_basic":
            auth = (self._client_id, self._client_secret)
        elif self._client_auth_method == "client_secret_post":
            data["client_id"] = self._client_id
            data["client_secret"] = self._client_secret
        else:
            raise OIDCConfigurationError("Unsupported OIDC client auth method")

        try:
            client = await self._get_http_client()
            response = await client.post(
                token_endpoint,
                data=data,
                auth=auth,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise OIDCTokenExchangeError("OIDC token endpoint request failed") from exc
        except ValueError as exc:
            raise OIDCTokenExchangeError("OIDC token endpoint returned invalid JSON") from exc

        if not isinstance(payload, dict):
            raise OIDCTokenExchangeError("OIDC token endpoint returned invalid payload")
        return payload

    async def _validate_id_token(
        self, token: str, *, expected_nonce: str
    ) -> dict[str, Any]:
        header = self._get_unverified_header(token)
        alg = str(header.get("alg", ""))
        if not alg or alg not in _ALLOWED_ASYMMETRIC_ALGORITHMS:
            raise OIDCVerificationError(f"Unsupported JWT algorithm: {alg}")

        signing_jwk = await self._get_signing_jwk(header.get("kid"))
        signing_key = OIDCVerifier._jwk_to_public_key(signing_jwk, alg)
        try:
            claims = jwt.decode(
                token,
                key=signing_key,
                algorithms=[alg],
                audience=self._client_id,
                options={
                    "verify_aud": True,
                    "verify_iss": False,
                    "verify_exp": True,
                    "require": ["exp", "iat", "sub"],
                },
            )
        except InvalidTokenError as exc:
            raise OIDCVerificationError("ID token verification failed") from exc

        if not isinstance(claims, dict):
            raise OIDCVerificationError("ID token claims payload is invalid")

        issuer_claim = claims.get("iss")
        if not isinstance(issuer_claim, str) or not issuer_claim:
            raise OIDCVerificationError("ID token missing issuer claim")
        if issuer_claim.rstrip("/") != self._issuer_normalized:
            raise OIDCVerificationError("ID token issuer mismatch")

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise OIDCVerificationError("ID token missing subject claim")
        nonce_claim = claims.get("nonce")
        if not isinstance(nonce_claim, str) or nonce_claim != expected_nonce:
            raise OIDCVerificationError("ID token nonce mismatch")

        normalized_claims = dict(claims)
        normalized_claims["iss"] = issuer_claim.rstrip("/")
        return normalized_claims

    @staticmethod
    def _get_unverified_header(token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except InvalidTokenError as exc:
            raise OIDCVerificationError("Invalid ID token header") from exc
        if not isinstance(header, dict):
            raise OIDCVerificationError("Invalid ID token header")
        return header

    async def _get_signing_jwk(self, kid: str | None) -> dict[str, Any]:
        jwks_doc = await self._get_jwks()
        jwk = OIDCVerifier._select_jwk(jwks_doc, kid)
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

    def _require_login_configuration(self) -> None:
        if not self._issuer:
            raise OIDCConfigurationError("OIDC_ISSUER is not configured")
        if not self._client_id:
            raise OIDCConfigurationError("OIDC_CLIENT_ID is not configured")
        if not self._client_secret:
            raise OIDCConfigurationError("OIDC_CLIENT_SECRET is not configured")
        if not self._public_base_url:
            raise OIDCConfigurationError("PUBLIC_BASE_URL is not configured")

    @staticmethod
    def _generate_urlsafe_secret(*, byte_count: int = 32) -> str:
        return secrets.token_urlsafe(byte_count)

    @staticmethod
    def _code_challenge_for_verifier(code_verifier: str) -> str:
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    @staticmethod
    def _optional_string(value: object) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _optional_int(value: object) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None


@lru_cache(maxsize=1)
def get_oidc_confidential_client() -> OIDCConfidentialClient:
    settings = get_settings()
    return OIDCConfidentialClient(
        issuer=settings.oidc_issuer,
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        client_auth_method=settings.oidc_client_auth_method,
        scope=settings.oidc_scope,
        public_base_url=settings.public_base_url,
        redirect_path=settings.oidc_redirect_path,
        cache_ttl_seconds=settings.oidc_jwks_cache_ttl_seconds,
        http_timeout_seconds=settings.oidc_http_timeout_seconds,
    )


def reset_oidc_confidential_client() -> None:
    try:
        client = get_oidc_confidential_client()
    except Exception:  # noqa: BLE001
        pass
    else:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(client.aclose())
            else:
                loop.run_until_complete(client.aclose())
        except Exception:  # noqa: BLE001
            pass
    get_oidc_confidential_client.cache_clear()
