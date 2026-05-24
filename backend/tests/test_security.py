from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import String, UniqueConstraint, create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.core import security, service_tokens
from app.models.user import User
from app.utils.rate_limit import build_auth_rate_limit_key


_SERVICE_TOKEN_HASH_SECRET = "test-service-token-hash-secret"
_AUTH_SESSION_HASH_SECRET = "test-auth-session-hash-secret"


def _integrity_error(message: str) -> IntegrityError:
    return IntegrityError("stmt", {}, Exception(message))


def _auth_session_settings() -> SimpleNamespace:
    return SimpleNamespace(
        auth_session_cookie_name="arena_session",
        auth_session_hash_secret=_AUTH_SESSION_HASH_SECRET,
        auth_session_max_age_seconds=3600,
        oidc_login_state_max_age_seconds=600,
    )


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


def test_upsert_user_duplicate_insert_race_keeps_outer_transaction_usable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    class _Base(DeclarativeBase):
        pass

    class SqlUser(_Base):
        __tablename__ = "users"
        __table_args__ = (
            UniqueConstraint(
                "oidc_issuer",
                "oidc_sub",
                name="uq_users_oidc_issuer_sub",
            ),
        )

        id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
        oidc_issuer: Mapped[str] = mapped_column(String(512), nullable=False)
        oidc_sub: Mapped[str] = mapped_column(String(512), nullable=False)

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'oidc.db'}")
    _Base.metadata.create_all(engine)

    issuer = "https://issuer.example"
    sub = "sub-123"

    class RaceSession(Session):
        race_inserted = False

        def execute(self, statement, *args, **kwargs):
            result = super().execute(statement, *args, **kwargs)
            if not self.race_inserted and "FROM users" in str(statement):
                self.race_inserted = True
                self.connection().execute(
                    SqlUser.__table__.insert().values(
                        oidc_issuer=issuer,
                        oidc_sub=sub,
                    )
                )
            return result

    monkeypatch.setattr(security, "User", SqlUser)

    with RaceSession(bind=engine, autoflush=False, expire_on_commit=False) as db:
        db.add(SqlUser(oidc_issuer=issuer, oidc_sub="outer-marker"))

        user = security._upsert_user(
            db,  # type: ignore[arg-type]
            issuer=issuer,
            sub=sub,
        )

        assert user.oidc_issuer == issuer
        assert user.oidc_sub == sub

        db.add(SqlUser(oidc_issuer=issuer, oidc_sub="after-race"))
        db.commit()

    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(SqlUser)) == 3
        assert db.scalar(select(func.count()).where(SqlUser.oidc_sub == sub)) == 1


class _AllResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return list(self._rows)


class _ServiceTokenDB:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows
        self.statements: list[object] = []
        self.added: list[object] = []
        self.flush_calls = 0

    def execute(self, stmt: object) -> _AllResult:
        self.statements.append(stmt)
        return _AllResult(self._rows)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        self.flush_calls += 1


def _service_token_settings(secret: str = _SERVICE_TOKEN_HASH_SECRET) -> SimpleNamespace:
    return SimpleNamespace(service_token_hash_secret=secret)


def _service_token_plaintext(secret: str = "a" * 43) -> str:
    return f"{service_tokens.SERVICE_TOKEN_PREFIX}{secret}"


def _service_token_rows(
    *,
    plaintext: str | None = None,
    scopes: list[str] | None = None,
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    is_active: bool = True,
    actor_type: str = "bot",
) -> tuple[str, SimpleNamespace, SimpleNamespace, SimpleNamespace]:
    selected_plaintext = plaintext or _service_token_plaintext()
    stored = service_tokens.hash_service_token(
        selected_plaintext,
        settings=_service_token_settings(),
    )
    service_account_id = uuid.uuid4()
    token = SimpleNamespace(
        id=uuid.uuid4(),
        service_account_id=service_account_id,
        token_prefix=stored.token_prefix,
        token_hash=stored.token_hash,
        scopes=scopes or ["battle:read"],
        expires_at=expires_at,
        revoked_at=revoked_at,
        last_used_at=None,
    )
    service_account = SimpleNamespace(
        id=service_account_id,
        name="Auto Judge",
        bot_user_id=uuid.uuid4(),
        is_active=is_active,
    )
    bot_user = SimpleNamespace(
        id=service_account.bot_user_id,
        oidc_issuer="system:service-account",
        oidc_sub=f"service-account:{service_account_id}",
        actor_type=actor_type,
    )
    return selected_plaintext, token, service_account, bot_user


def _request(
    *,
    cookies: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        cookies=cookies or {},
        headers=headers or {},
        state=SimpleNamespace(),
    )


async def _with_auth_session_functions(
    *,
    call,
    load_session=None,
    load_user=None,
    refresh=None,
):
    original_load_session = security.load_auth_session
    original_load_user = security.load_user_for_auth_session
    original_refresh = security.refresh_auth_session_last_seen
    try:
        if load_session is not None:
            security.load_auth_session = load_session  # type: ignore[assignment]
        if load_user is not None:
            security.load_user_for_auth_session = load_user  # type: ignore[assignment]
        if refresh is not None:
            security.refresh_auth_session_last_seen = refresh  # type: ignore[assignment]
        return await call()
    finally:
        security.load_auth_session = original_load_session
        security.load_user_for_auth_session = original_load_user
        security.refresh_auth_session_last_seen = original_refresh


def test_get_principal_optional_returns_anon_without_creds() -> None:
    principal = asyncio.run(
        security.get_principal_optional(
            request=_request(),
            creds=None,
            db=object(),
            settings=_auth_session_settings(),
        )
    )

    assert principal.is_authenticated is False
    assert principal.auth_method == "none"
    assert principal.user_id is None
    assert principal.claims == {}


def test_get_principal_optional_authenticates_session_cookie() -> None:
    settings = SimpleNamespace(auth_session_cookie_name="arena_session")
    auth_session = SimpleNamespace(
        id=uuid.uuid4(),
        oidc_issuer="https://issuer.example",
        oidc_sub="sub-123",
        claims={"groups": ["arena_admin"]},
    )
    user = User(
        id=uuid.uuid4(),
        oidc_issuer="https://issuer.example",
        oidc_sub="sub-123",
        actor_type="human",
    )
    request = _request(cookies={"arena_session": "raw-session-token"})
    refresh_calls: list[object] = []

    def fake_load_auth_session(*_args: object, **kwargs: object) -> object:
        assert kwargs["session_token"] == "raw-session-token"
        assert kwargs["settings"] is settings
        return auth_session

    def fake_load_user(*_args: object, **kwargs: object) -> User:
        assert kwargs["auth_session"] is auth_session
        return user

    def fake_refresh(*_args: object, **kwargs: object) -> object:
        refresh_calls.append(kwargs["auth_session"])
        return auth_session

    principal = asyncio.run(
        _with_auth_session_functions(
            load_session=fake_load_auth_session,
            load_user=fake_load_user,
            refresh=fake_refresh,
            call=lambda: security.get_principal_optional(
                request=request,
                creds=None,
                db=object(),
                settings=settings,  # type: ignore[arg-type]
            ),
        )
    )

    assert principal.is_authenticated is True
    assert principal.actor_type == "human"
    assert principal.auth_method == "session"
    assert principal.user_id == str(user.id)
    assert principal.auth_session_id == str(auth_session.id)
    assert principal.oidc_issuer == "https://issuer.example"
    assert principal.oidc_sub == "sub-123"
    assert principal.claims == {"groups": ["arena_admin"]}
    assert refresh_calls == [auth_session]
    assert request.state.auth_session is auth_session
    assert request.state.auth_session_id == str(auth_session.id)


def test_get_principal_optional_session_cookie_always_builds_human_principal() -> None:
    settings = SimpleNamespace(auth_session_cookie_name="arena_session")
    auth_session = SimpleNamespace(
        id=uuid.uuid4(),
        oidc_issuer="https://issuer.example",
        oidc_sub="sub-123",
        claims={"groups": ["arena_admin"]},
    )
    bot_like_user = User(
        id=uuid.uuid4(),
        oidc_issuer="https://issuer.example",
        oidc_sub="sub-123",
        actor_type="bot",
    )

    principal = asyncio.run(
        _with_auth_session_functions(
            load_session=lambda *_args, **_kwargs: auth_session,
            load_user=lambda *_args, **_kwargs: bot_like_user,
            refresh=lambda *_args, **_kwargs: auth_session,
            call=lambda: security.get_principal_optional(
                request=_request(cookies={"arena_session": "raw-session-token"}),
                creds=None,
                db=object(),
                settings=settings,  # type: ignore[arg-type]
            ),
        )
    )

    assert principal.is_authenticated is True
    assert principal.actor_type == "human"
    assert principal.auth_method == "session"


def test_session_cookie_principal_claims_authorize_admin_check() -> None:
    principal = security.Principal(
        is_authenticated=True,
        actor_type="human",
        auth_method="session",
        user_id=str(uuid.uuid4()),
        auth_session_id=str(uuid.uuid4()),
        claims={"groups": ["arena_admin", "users"]},
    )

    assert security.require_admin(principal) is principal


def test_get_principal_optional_returns_anon_for_missing_session_cookie() -> None:
    settings = SimpleNamespace(auth_session_cookie_name="arena_session")
    request = _request(cookies={"arena_session": "missing-session-token"})

    def fake_load_auth_session(*_args: object, **kwargs: object) -> None:
        assert kwargs["session_token"] == "missing-session-token"
        return None

    principal = asyncio.run(
        _with_auth_session_functions(
            load_session=fake_load_auth_session,
            call=lambda: security.get_principal_optional(
                request=request,
                creds=None,
                db=object(),
                settings=settings,  # type: ignore[arg-type]
            ),
        )
    )

    assert principal.is_authenticated is False
    assert principal.auth_method == "none"


def test_get_principal_optional_returns_anon_when_session_user_missing() -> None:
    settings = SimpleNamespace(auth_session_cookie_name="arena_session")
    auth_session = SimpleNamespace(
        id=uuid.uuid4(),
        oidc_issuer="https://issuer.example",
        oidc_sub="sub-123",
        claims={},
    )
    request = _request(cookies={"arena_session": "raw-session-token"})

    principal = asyncio.run(
        _with_auth_session_functions(
            load_session=lambda *_args, **_kwargs: auth_session,
            load_user=lambda *_args, **_kwargs: None,
            call=lambda: security.get_principal_optional(
                request=request,
                creds=None,
                db=object(),
                settings=settings,  # type: ignore[arg-type]
            ),
        )
    )

    assert principal.is_authenticated is False
    assert principal.auth_method == "none"


def test_get_principal_optional_invalid_bearer_precedence_skips_session_cookie() -> None:
    def fail_load_session(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("session cookie fallback was used")

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid-token")

    principal = asyncio.run(
        _with_auth_session_functions(
            load_session=fail_load_session,
            call=lambda: security.get_principal_optional(
                request=_request(cookies={"arena_session": "valid-session-token"}),
                creds=creds,
                db=object(),
                settings=_auth_session_settings(),
            ),
        )
    )

    assert principal.is_authenticated is False
    assert principal.auth_method == "none"


def test_get_principal_optional_unknown_bearer_never_authenticates_human() -> None:
    def fail_load_session(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("session cookie fallback was used")

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="valid-token")

    principal = asyncio.run(
        _with_auth_session_functions(
            load_session=fail_load_session,
            call=lambda: security.get_principal_optional(
                request=_request(cookies={"arena_session": "other-session-token"}),
                creds=creds,
                db=object(),
                settings=_auth_session_settings(),
            ),
        )
    )

    assert principal.is_authenticated is False
    assert principal.auth_method == "none"
    assert principal.user_id is None
    assert principal.claims == {}


def test_get_principal_optional_malformed_bearer_precedence_skips_session_cookie() -> None:
    def fail_load_session(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("session cookie fallback was used")

    principal = asyncio.run(
        _with_auth_session_functions(
            load_session=fail_load_session,
            call=lambda: security.get_principal_optional(
                request=_request(
                    cookies={"arena_session": "valid-session-token"},
                    headers={"authorization": "Bearer"},
                ),
                creds=None,
                db=object(),
                settings=_auth_session_settings(),
            ),
        )
    )

    assert principal.is_authenticated is False
    assert principal.auth_method == "none"


def test_get_principal_optional_returns_bot_principal_for_service_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: _service_token_settings(),
    )
    plaintext, token, service_account, bot_user = _service_token_rows(
        scopes=["vote:create", "battle:read"]
    )
    db = _ServiceTokenDB([(token, service_account, bot_user)])
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=plaintext)

    principal = asyncio.run(
        security.get_principal_optional(
            request=_request(),
            creds=creds,
            db=db,  # type: ignore[arg-type]
            settings=_auth_session_settings(),
        )
    )

    assert principal.is_authenticated is True
    assert principal.actor_type == "bot"
    assert principal.auth_method == "service_token"
    assert principal.user_id == str(bot_user.id)
    assert principal.oidc_issuer == "system:service-account"
    assert principal.oidc_sub == f"service-account:{service_account.id}"
    assert principal.service_account_id == str(service_account.id)
    assert principal.service_account_name == "Auto Judge"
    assert principal.token_id == str(token.id)
    assert principal.scopes == ("battle:read", "vote:create")
    assert principal.claims == {}
    assert token.last_used_at is not None
    assert db.added == [token]
    assert db.flush_calls == 1


def test_service_account_token_route_accepts_bot_bearer_without_cookie_or_csrf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: _service_token_settings(),
    )
    plaintext, token, service_account, bot_user = _service_token_rows(
        scopes=["battle:create", "battle:execute"]
    )
    db = _ServiceTokenDB([(token, service_account, bot_user)])
    app = FastAPI()

    def override_db():
        yield db

    @app.post("/service-token-route")
    def service_token_route(
        principal: security.Principal = Depends(security.require_scopes(["battle:create"])),
    ) -> dict[str, object]:
        return {
            "actor_type": principal.actor_type,
            "auth_method": principal.auth_method,
            "service_account_id": principal.service_account_id,
            "scopes": list(principal.scopes),
        }

    app.dependency_overrides[security.get_db] = override_db
    with TestClient(app) as client:
        response = client.post(
            "/service-token-route",
            headers={"Authorization": f"Bearer {plaintext}"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "actor_type": "bot",
        "auth_method": "service_token",
        "service_account_id": str(service_account.id),
        "scopes": ["battle:create", "battle:execute"],
    }
    assert token.last_used_at is not None
    assert db.flush_calls == 1


@pytest.mark.parametrize(
    ("credentials", "secret", "rows"),
    [
        (f"{service_tokens.SERVICE_TOKEN_PREFIX}short", _SERVICE_TOKEN_HASH_SECRET, []),
        (_service_token_plaintext(), " ", []),
        (_service_token_plaintext("b" * 43), _SERVICE_TOKEN_HASH_SECRET, []),
    ],
)
def test_get_principal_optional_rejects_invalid_service_tokens_before_oidc(
    monkeypatch: pytest.MonkeyPatch,
    credentials: str,
    secret: str,
    rows: list[object],
) -> None:
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: _service_token_settings(secret),
    )
    db = _ServiceTokenDB(rows)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=credentials)

    with pytest.raises(HTTPException, match="Invalid service token") as exc_info:
        asyncio.run(
            security.get_principal_optional(
                request=_request(),
                creds=creds,
                db=db,  # type: ignore[arg-type]
                settings=_auth_session_settings(),
            )
        )

    assert exc_info.value.status_code == 401


@pytest.mark.parametrize(
    ("overrides", "expected_status", "expected_detail"),
    [
        (
            {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)},
            401,
            "Service token expired",
        ),
        (
            {"revoked_at": datetime.now(timezone.utc)},
            401,
            "Service token revoked",
        ),
        ({"is_active": False}, 403, "Service account is inactive"),
        ({"actor_type": "human"}, 403, "Service account backing user is invalid"),
    ],
)
def test_get_principal_optional_rejects_inactive_service_token_state(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
    expected_status: int,
    expected_detail: str,
) -> None:
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: _service_token_settings(),
    )
    plaintext, token, service_account, bot_user = _service_token_rows(**overrides)
    db = _ServiceTokenDB([(token, service_account, bot_user)])
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=plaintext)

    with pytest.raises(HTTPException, match=expected_detail) as exc_info:
        asyncio.run(
            security.get_principal_optional(
                request=_request(),
                creds=creds,
                db=db,  # type: ignore[arg-type]
                settings=_auth_session_settings(),
            )
        )

    assert exc_info.value.status_code == expected_status
    assert token.last_used_at is None
    assert db.flush_calls == 0


def test_claim_by_path_reads_nested_claims() -> None:
    claims = {
        "realm": {
            "access": {
                "groups": ["arena_admin"],
            }
        }
    }

    assert security.claim_by_path(claims, "realm.access.groups") == ["arena_admin"]
    assert security.claim_by_path(claims, "realm.access.missing") is None
    assert security.claim_by_path(claims, "realm.access.groups.value") is None


def test_normalize_groups_handles_supported_input_shapes() -> None:
    assert security.normalize_groups(None) == set()
    assert security.normalize_groups("arena_admin,ops team") == {
        "arena_admin",
        "ops",
        "team",
    }
    assert security.normalize_groups(["arena_admin", "", "ops", 123]) == {
        "arena_admin",
        "ops",
    }


def test_require_admin_rejects_unauthenticated_principal() -> None:
    with pytest.raises(HTTPException, match="Authentication required") as exc_info:
        security.require_admin(security.Principal(is_authenticated=False))

    assert exc_info.value.status_code == 401


def test_require_admin_rejects_bot_principal_with_admin_like_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        oidc_admin_group_claim="groups",
        oidc_admin_group_name="arena_admin",
    )
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    principal = security.Principal(
        is_authenticated=True,
        actor_type="bot",
        user_id=str(uuid.uuid4()),
        claims={"groups": ["arena_admin"]},
        scopes=("battle:create",),
    )

    with pytest.raises(HTTPException, match="Human admin principal required") as exc_info:
        security.require_admin(principal)

    assert exc_info.value.status_code == 403


def test_require_admin_rejects_service_token_bot_principal_with_admin_like_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        oidc_admin_group_claim="groups",
        oidc_admin_group_name="arena_admin",
    )
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    principal = security.Principal(
        is_authenticated=True,
        actor_type="bot",
        auth_method="service_token",
        user_id=str(uuid.uuid4()),
        claims={"groups": ["arena_admin"]},
        scopes=("battle:create",),
    )

    with pytest.raises(HTTPException, match="Human admin principal required") as exc_info:
        security.require_admin(principal)

    assert exc_info.value.status_code == 403


def test_require_human_principal_rejects_service_token_bot() -> None:
    principal = security.Principal(
        is_authenticated=True,
        actor_type="bot",
        auth_method="service_token",
        user_id=str(uuid.uuid4()),
        claims={"groups": ["arena_admin"]},
    )

    with pytest.raises(HTTPException, match="Human principal required") as exc_info:
        security.require_human_principal(principal)

    assert exc_info.value.status_code == 403


def test_get_principal_required_rejects_unauthenticated_principal() -> None:
    with pytest.raises(HTTPException, match="Authentication required") as exc_info:
        security.get_principal_required(security.Principal(is_authenticated=False))

    assert exc_info.value.status_code == 401


def test_get_principal_required_returns_authenticated_principal() -> None:
    principal = security.Principal(is_authenticated=True, user_id=str(uuid.uuid4()))
    assert security.get_principal_required(principal) is principal


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


def test_require_scopes_allows_bot_with_required_scope() -> None:
    principal = security.Principal(
        is_authenticated=True,
        actor_type="bot",
        user_id=str(uuid.uuid4()),
        scopes=("battle:read", "vote:create"),
    )
    dependency = security.require_scopes(["vote:create"])

    assert dependency(principal) is principal


@pytest.mark.parametrize(
    ("principal", "expected_status", "expected_detail"),
    [
        (
            security.Principal(is_authenticated=False),
            401,
            "Authentication required",
        ),
        (
            security.Principal(
                is_authenticated=True,
                actor_type="human",
                user_id=str(uuid.uuid4()),
                scopes=("vote:create",),
            ),
            403,
            "Service token principal required",
        ),
        (
            security.Principal(
                is_authenticated=True,
                actor_type="bot",
                user_id=str(uuid.uuid4()),
                scopes=("battle:read",),
            ),
            403,
            "Required service token scope missing",
        ),
    ],
)
def test_require_scopes_rejects_unauthenticated_human_or_under_scoped_principals(
    principal: security.Principal,
    expected_status: int,
    expected_detail: str,
) -> None:
    dependency = security.require_scopes(["vote:create"])

    with pytest.raises(HTTPException, match=expected_detail) as exc_info:
        dependency(principal)

    assert exc_info.value.status_code == expected_status


def test_build_auth_rate_limit_key_uses_service_account_or_token_identity() -> None:
    assert build_auth_rate_limit_key(scope="vote", user_id="user-1") == "vote:user:user-1"
    assert (
        build_auth_rate_limit_key(
            scope="vote",
            user_id="user-1",
            service_account_id="service-account-1",
            token_id="token-1",
        )
        == "vote:service_account:service-account-1"
    )
    assert (
        build_auth_rate_limit_key(scope="vote", token_id="token-1")
        == "vote:token:token-1"
    )
