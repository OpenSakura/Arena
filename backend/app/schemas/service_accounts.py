from __future__ import annotations

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, Field, field_validator

SERVICE_ACCOUNT_SCOPES = (
    "battle:create",
    "battle:read",
    "battle:execute",
    "vote:create",
)
_SERVICE_ACCOUNT_SCOPE_SET = frozenset(SERVICE_ACCOUNT_SCOPES)

ServiceAccountScope: TypeAlias = Literal[
    "battle:create",
    "battle:read",
    "battle:execute",
    "vote:create",
]
ServiceAccountTokenStatus: TypeAlias = Literal["active", "expired", "revoked"]


def _normalize_scopes(value: Any) -> Any:
    if value is None or not isinstance(value, list):
        return value

    normalized: set[str] = set()
    for scope in value:
        if not isinstance(scope, str):
            return value

        selected_scope = scope.strip()
        if not selected_scope:
            raise ValueError("Service-account scope cannot be empty")
        if selected_scope.startswith("admin:"):
            raise ValueError("Admin scopes are not supported for service accounts")
        if selected_scope not in _SERVICE_ACCOUNT_SCOPE_SET:
            raise ValueError(f"Unsupported service-account scope: {selected_scope}")

        normalized.add(selected_scope)

    return [scope for scope in SERVICE_ACCOUNT_SCOPES if scope in normalized]


class ServiceAccountTokenCreate(BaseModel):
    scopes: list[ServiceAccountScope] = Field(
        ...,
        min_length=1,
        max_length=len(SERVICE_ACCOUNT_SCOPES),
    )
    expires_at: str | None = None

    @field_validator("scopes", mode="before")
    @classmethod
    def _validate_scopes(cls, value: Any) -> Any:
        return _normalize_scopes(value)


class ServiceAccountTokenRedacted(BaseModel):
    id: str
    service_account_id: str
    token_prefix: str
    status: ServiceAccountTokenStatus = "active"
    scopes: list[ServiceAccountScope] = Field(default_factory=list)
    created_at: str
    expires_at: str | None = None
    last_used_at: str | None = None
    revoked_at: str | None = None

    @field_validator("scopes", mode="before")
    @classmethod
    def _validate_scopes(cls, value: Any) -> Any:
        return _normalize_scopes(value)


class ServiceAccountPublic(BaseModel):
    id: str
    name: str
    description: str | None = None
    enabled: bool
    scopes: list[ServiceAccountScope] = Field(default_factory=list)
    tokens: list[ServiceAccountTokenRedacted] = Field(default_factory=list)
    created_at: str
    updated_at: str

    @field_validator("scopes", mode="before")
    @classmethod
    def _validate_scopes(cls, value: Any) -> Any:
        return _normalize_scopes(value)


class ServiceAccountListResponse(BaseModel):
    service_accounts: list[ServiceAccountPublic]


class ServiceAccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4096)
    enabled: bool = True
    scopes: list[ServiceAccountScope] = Field(
        default_factory=list,
        max_length=len(SERVICE_ACCOUNT_SCOPES),
    )

    @field_validator("scopes", mode="before")
    @classmethod
    def _validate_scopes(cls, value: Any) -> Any:
        return _normalize_scopes(value)


class ServiceAccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4096)
    enabled: bool | None = None
    scopes: list[ServiceAccountScope] | None = Field(
        default=None,
        max_length=len(SERVICE_ACCOUNT_SCOPES),
    )

    @field_validator("scopes", mode="before")
    @classmethod
    def _validate_scopes(cls, value: Any) -> Any:
        return _normalize_scopes(value)


class ServiceAccountTokenCreateResponse(BaseModel):
    service_account: ServiceAccountPublic
    token: ServiceAccountTokenRedacted
    plaintext_token: str = Field(..., min_length=1)
