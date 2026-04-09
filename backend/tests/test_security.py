from __future__ import annotations

import asyncio
from types import SimpleNamespace
import uuid

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
import pytest
from sqlalchemy.exc import IntegrityError

from app.core import security
from app.models.user import User
from app.services.oidc import OIDCConfigurationError, OIDCVerificationError


def _integrity_error(message: str) -> IntegrityError:
    return IntegrityError("stmt", {}, Exception(message))


class _ScalarResult:
    def __init__(self, value: User | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> User | None:
        return self._value


class _Savepoint:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class _QueueExecuteDB:
    def __init__(
        self,
        execute_results: list[User | None],
        *,
        flush_error: Exception | None = None,
    ) -> None:
        self._execute_results = list(execute_results)
        self._flush_error = flush_error

        self.added: list[User] = []
        self.flush_calls = 0
        self.rollback_calls = 0

    def execute(self, _stmt: object) -> _ScalarResult:
        value = self._execute_results.pop(0) if self._execute_results else None
        return _ScalarResult(value)

    def add(self, user: User) -> None:
        self.added.append(user)

    def begin_nested(self) -> _Savepoint:
        return _Savepoint()

    def flush(self) -> None:
        self.flush_calls += 1
        if self._flush_error is not None:
            raise self._flush_error

    def rollback(self) -> None:
        self.rollback_calls += 1


def test_upsert_user_returns_existing_row_without_write() -> None:
    existing = User(
        id=uuid.uuid4(),
        oidc_issuer="https://issuer.example",
        oidc_sub="sub-123",
    )
    db = _QueueExecuteDB([existing])

    user = security._upsert_user(
        db,  # type: ignore[arg-type]
        issuer="https://issuer.example",
        sub="sub-123",
    )

    assert user is existing
    assert db.added == []
    assert db.flush_calls == 0


def test_upsert_user_inserts_missing_user() -> None:
    db = _QueueExecuteDB([None])

    user = security._upsert_user(
        db,  # type: ignore[arg-type]
        issuer="https://issuer.example",
        sub="sub-123",
    )

    assert user.oidc_issuer == "https://issuer.example"
    assert user.oidc_sub == "sub-123"
    assert db.added == [user]
    assert db.flush_calls == 1
    assert db.rollback_calls == 0


def test_upsert_user_recovers_from_insert_race() -> None:
    existing = User(
        id=uuid.uuid4(),
        oidc_issuer="https://issuer.example",
        oidc_sub="sub-123",
    )
    db = _QueueExecuteDB(
        [None, existing],
        flush_error=_integrity_error(
            'duplicate key value violates unique constraint "uq_users_oidc_issuer_sub"'
        ),
    )

    user = security._upsert_user(
        db,  # type: ignore[arg-type]
        issuer="https://issuer.example",
        sub="sub-123",
    )

    assert user is existing
    assert db.flush_calls == 1
    assert db.rollback_calls == 0


def test_upsert_user_reraises_integrity_error_if_user_still_missing() -> None:
    db = _QueueExecuteDB(
        [None, None],
        flush_error=_integrity_error(
            'duplicate key value violates unique constraint "uq_users_oidc_issuer_sub"'
        ),
    )

    with pytest.raises(IntegrityError):
        security._upsert_user(
            db,  # type: ignore[arg-type]
            issuer="https://issuer.example",
            sub="sub-123",
        )

    assert db.flush_calls == 1
    assert db.rollback_calls == 0


class _Verifier:
    def __init__(
        self,
        *,
        claims: dict[str, object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._claims = claims
        self._error = error
        self.tokens: list[str] = []

    async def verify(self, token: str) -> dict[str, object]:
        self.tokens.append(token)
        if self._error is not None:
            raise self._error
        assert self._claims is not None
        return dict(self._claims)


def test_get_principal_optional_returns_anon_without_creds() -> None:
    principal = asyncio.run(
        security.get_principal_optional(
            creds=None,
            db=object(),
            oidc_verifier=_Verifier(claims={}),  # type: ignore[arg-type]
        )
    )

    assert principal.is_authenticated is False
    assert principal.user_id is None
    assert principal.claims == {}


@pytest.mark.parametrize(
    "error",
    [
        OIDCConfigurationError("misconfigured"),
        OIDCVerificationError("invalid token"),
    ],
)
def test_get_principal_optional_returns_anon_on_verifier_errors(
    error: Exception,
) -> None:
    verifier = _Verifier(error=error)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")

    principal = asyncio.run(
        security.get_principal_optional(
            creds=creds,
            db=object(),
            oidc_verifier=verifier,  # type: ignore[arg-type]
        )
    )

    assert principal.is_authenticated is False
    assert verifier.tokens == ["token"]


def test_get_principal_optional_rejects_missing_issuer_claim() -> None:
    verifier = _Verifier(claims={"sub": "sub-123"})
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")

    principal = asyncio.run(
        security.get_principal_optional(
            creds=creds,
            db=object(),
            oidc_verifier=verifier,  # type: ignore[arg-type]
        )
    )

    assert principal.is_authenticated is False
    assert principal.claims == {"sub": "sub-123"}


def test_get_principal_optional_rejects_missing_subject_claim() -> None:
    verifier = _Verifier(claims={"iss": "https://issuer.example"})
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")

    principal = asyncio.run(
        security.get_principal_optional(
            creds=creds,
            db=object(),
            oidc_verifier=verifier,  # type: ignore[arg-type]
        )
    )

    assert principal.is_authenticated is False
    assert principal.claims == {"iss": "https://issuer.example"}


def test_get_principal_optional_raises_500_when_user_upsert_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_args: object, **_kwargs: object) -> User:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(security, "_upsert_user", _raise)

    verifier = _Verifier(
        claims={
            "iss": "https://issuer.example",
            "sub": "sub-123",
        }
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")

    with pytest.raises(HTTPException, match="Failed to persist user") as exc_info:
        asyncio.run(
            security.get_principal_optional(
                creds=creds,
                db=object(),
                oidc_verifier=verifier,  # type: ignore[arg-type]
            )
        )

    assert exc_info.value.status_code == 500


def test_get_principal_optional_returns_authenticated_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(
        id=uuid.uuid4(),
        oidc_issuer="https://issuer.example",
        oidc_sub="sub-123",
    )
    monkeypatch.setattr(security, "_upsert_user", lambda *_args, **_kwargs: user)

    verifier = _Verifier(
        claims={
            "iss": "https://issuer.example",
            "sub": "sub-123",
            "groups": ["arena_admin"],
        }
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")

    principal = asyncio.run(
        security.get_principal_optional(
            creds=creds,
            db=object(),
            oidc_verifier=verifier,  # type: ignore[arg-type]
        )
    )

    assert principal.is_authenticated is True
    assert principal.user_id == str(user.id)
    assert principal.oidc_issuer == "https://issuer.example"
    assert principal.oidc_sub == "sub-123"
    assert principal.claims["groups"] == ["arena_admin"]


def test_claim_by_path_reads_nested_claims() -> None:
    claims = {
        "realm": {
            "access": {
                "groups": ["arena_admin"],
            }
        }
    }

    assert security._claim_by_path(claims, "realm.access.groups") == ["arena_admin"]
    assert security._claim_by_path(claims, "realm.access.missing") is None
    assert security._claim_by_path(claims, "realm.access.groups.value") is None


def test_normalize_groups_handles_supported_input_shapes() -> None:
    assert security._normalize_groups(None) == set()
    assert security._normalize_groups("arena_admin,ops team") == {
        "arena_admin",
        "ops",
        "team",
    }
    assert security._normalize_groups(["arena_admin", "", "ops", 123]) == {
        "arena_admin",
        "ops",
    }


def test_require_admin_rejects_unauthenticated_principal() -> None:
    with pytest.raises(HTTPException, match="Authentication required") as exc_info:
        security.require_admin(security.Principal(is_authenticated=False))

    assert exc_info.value.status_code == 401


def test_require_admin_rejects_principal_without_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        oidc_admin_group_claim="groups",
        oidc_admin_group_name="arena_admin",
    )
    monkeypatch.setattr(security, "get_settings", lambda: settings)

    principal = security.Principal(
        is_authenticated=True,
        claims={"groups": ["basic_user"]},
    )

    with pytest.raises(
        HTTPException, match="Admin group membership required"
    ) as exc_info:
        security.require_admin(principal)

    assert exc_info.value.status_code == 403


def test_require_admin_allows_admin_group_from_nested_claim_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        oidc_admin_group_claim="realm.access.roles",
        oidc_admin_group_name="arena_admin",
    )
    monkeypatch.setattr(security, "get_settings", lambda: settings)

    principal = security.Principal(
        is_authenticated=True,
        claims={"realm": {"access": {"roles": "arena_admin,ops"}}},
    )

    assert security.require_admin(principal) is principal
