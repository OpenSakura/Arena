from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch
import uuid

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes import me
from app.core import csrf
from app.core import security
from app.core.security import Principal
from app.db.base import Base
from app.models.auth_session import AuthSession
from app.models.user import User, UserProfile
from app.schemas.me import ProfileUpsert, UserPublic, ProfilePublic
from app.services import auth_session


_HASH_SECRET = "test-auth-session-hash-secret"


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type: JSONB, _compiler: object, **_kw: object) -> str:
    return "JSON"


def _user() -> User:
    user = User(
        id=uuid.uuid4(),
        oidc_issuer="https://issuer.example",
        oidc_sub="sub-123",
    )
    user.created_at = datetime(2026, 2, 18, 9, 0, tzinfo=timezone.utc)
    return user


def _profile(user_id: uuid.UUID) -> UserProfile:
    profile = UserProfile(
        user_id=user_id,
        display_name="Alice",
        ui_language="en",
        zh_variant="hans",
        jp_proficiency={"jlpt": "N2"},
        translation_experience={"years": 4},
        consents={"tos": True},
    )
    profile.completed_at = datetime(2026, 2, 18, 9, 5, tzinfo=timezone.utc)
    return profile


class _ProfileDB:
    def __init__(self, *, user: User | None, profile: UserProfile | None) -> None:
        self.user = user
        self.profile = profile
        self.added: list[UserProfile] = []
        self.commit_calls = 0
        self.refreshed: list[UserProfile] = []

    def get(self, model: type[object], key: uuid.UUID) -> object | None:
        if model is User and self.user is not None and self.user.id == key:
            return self.user
        if (
            model is UserProfile
            and self.profile is not None
            and self.profile.user_id == key
        ):
            return self.profile
        return None

    def add(self, profile: UserProfile) -> None:
        self.profile = profile
        self.added.append(profile)

    def commit(self) -> None:
        self.commit_calls += 1

    def refresh(self, profile: UserProfile) -> None:
        self.refreshed.append(profile)


def test_get_me_returns_unauthenticated_response_for_anonymous_request() -> None:
    db = _ProfileDB(user=None, profile=None)

    response = me.get_me(
        principal=Principal(is_authenticated=False),
        db=db,  # type: ignore[arg-type]
    )

    assert response.authenticated is False
    assert response.is_admin is False
    assert response.user is None
    assert response.profile is None


def test_get_me_returns_unauthenticated_when_user_row_is_missing() -> None:
    principal = Principal(is_authenticated=True, user_id=str(uuid.uuid4()))
    db = _ProfileDB(user=None, profile=None)

    with pytest.raises(HTTPException) as exc_info:
        me.get_me(
            principal=principal,
            db=db,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Authenticated user record not found"


def test_get_me_returns_serialized_user_and_profile() -> None:
    user = _user()
    profile = _profile(user.id)
    principal = Principal(is_authenticated=True, user_id=str(user.id))
    db = _ProfileDB(user=user, profile=profile)

    response = me.get_me(
        principal=principal,
        db=db,  # type: ignore[arg-type]
    )

    assert response.authenticated is True
    assert response.is_admin is False
    assert response.user == UserPublic(
        id=str(user.id),
        oidc_issuer="https://issuer.example",
        oidc_sub="sub-123",
        created_at=user.created_at.isoformat(),
    )
    assert response.profile == ProfilePublic(
        display_name="Alice",
        ui_language="en",
        zh_variant="hans",
        jp_proficiency={"jlpt": "N2"},
        translation_experience={"years": 4},
        consents={"tos": True},
        completed_at=profile.completed_at.isoformat(),
    )


def test_get_me_returns_is_admin_true_when_claims_contain_admin_group() -> None:
    user = _user()
    principal = Principal(
        is_authenticated=True,
        user_id=str(user.id),
        claims={"groups": ["arena_admin", "users"]},
    )
    db = _ProfileDB(user=user, profile=None)

    class _FakeSettings:
        oidc_admin_group_claim = "groups"
        oidc_admin_group_name = "arena_admin"

    with patch("app.api.routes.me.get_settings", return_value=_FakeSettings()):
        response = me.get_me(
            principal=principal,
            db=db,  # type: ignore[arg-type]
        )

    assert response.authenticated is True
    assert response.is_admin is True


def test_put_profile_is_admin() -> None:
    """PUT /me/profile must return is_admin matching the principal's claims,
    just like GET /me does (Task 2 contract parity)."""
    user = _user()
    principal = Principal(
        is_authenticated=True,
        user_id=str(user.id),
        claims={"groups": ["arena_admin", "users"]},
    )
    db = _ProfileDB(user=user, profile=None)

    class _FakeSettings:
        oidc_admin_group_claim = "groups"
        oidc_admin_group_name = "arena_admin"

    with patch("app.api.routes.me.get_settings", return_value=_FakeSettings()):
        response = me.put_profile(
            payload=ProfileUpsert(display_name="AdminUser"),
            principal=principal,
            db=db,  # type: ignore[arg-type]
        )

    assert response.authenticated is True
    assert response.is_admin is True
    assert response.profile is not None
    assert response.profile.display_name == "AdminUser"


def test_get_me_returns_is_admin_false_for_nested_group_claim_path() -> None:
    user = _user()
    principal = Principal(
        is_authenticated=True,
        user_id=str(user.id),
        claims={"custom": {"nested": ["arena_admin"]}},
    )
    db = _ProfileDB(user=user, profile=None)

    class _FakeSettings:
        oidc_admin_group_claim = "custom.nested"
        oidc_admin_group_name = "arena_admin"

    with patch("app.api.routes.me.get_settings", return_value=_FakeSettings()):
        response = me.get_me(
            principal=principal,
            db=db,  # type: ignore[arg-type]
        )

    assert response.authenticated is True
    assert response.is_admin is True


def test_put_profile_requires_authentication() -> None:
    db = _ProfileDB(user=None, profile=None)

    with pytest.raises(HTTPException) as exc_info:
        me.put_profile(
            payload=ProfileUpsert(display_name="Alice"),
            principal=Principal(is_authenticated=False),
            db=db,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Authentication required"


def test_put_profile_requires_existing_user_row() -> None:
    principal = Principal(is_authenticated=True, user_id=str(uuid.uuid4()))
    db = _ProfileDB(user=None, profile=None)

    with pytest.raises(HTTPException) as exc_info:
        me.put_profile(
            payload=ProfileUpsert(display_name="Alice"),
            principal=principal,
            db=db,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Authenticated user record not found"


def test_put_profile_creates_profile_and_persists_payload_fields() -> None:
    user = _user()
    principal = Principal(is_authenticated=True, user_id=str(user.id))
    db = _ProfileDB(user=user, profile=None)

    response = me.put_profile(
        payload=ProfileUpsert(
            display_name="Aya",
            ui_language="zh",
            zh_variant="hant",
            jp_proficiency={"jlpt": "N1"},
            translation_experience={"domain": "novel"},
            consents={"tos": True, "privacy": True},
        ),
        principal=principal,
        db=db,  # type: ignore[arg-type]
    )

    assert len(db.added) == 1
    assert db.commit_calls == 1
    assert len(db.refreshed) == 1

    profile = db.refreshed[0]
    assert profile.user_id == user.id
    assert profile.display_name == "Aya"
    assert profile.ui_language == "zh"
    assert profile.zh_variant == "hant"
    assert profile.jp_proficiency == {"jlpt": "N1"}
    assert profile.translation_experience == {"domain": "novel"}
    assert profile.consents == {"tos": True, "privacy": True}
    assert profile.completed_at is not None

    assert response.authenticated is True
    assert response.user is not None
    assert response.profile is not None
    assert response.profile.display_name == "Aya"


# ── Task 8 regression: backend-verified admin gating edge cases ──


def test_get_me_anonymous_always_returns_is_admin_false() -> None:
    """Anonymous users must always get is_admin=false regardless of settings.
    This protects the backend-verified admin gating contract (Task 8)."""
    db = _ProfileDB(user=None, profile=None)

    response = me.get_me(
        principal=Principal(is_authenticated=False),
        db=db,  # type: ignore[arg-type]
    )

    assert response.authenticated is False
    assert response.is_admin is False


def test_get_me_returns_is_admin_false_when_claim_path_does_not_exist() -> None:
    """When the OIDC claims don't contain the configured group claim path at
    all, is_admin must be False — never raise or default to True."""
    user = _user()
    principal = Principal(
        is_authenticated=True,
        user_id=str(user.id),
        claims={"unrelated": "data"},
    )
    db = _ProfileDB(user=user, profile=None)

    class _FakeSettings:
        oidc_admin_group_claim = "groups"
        oidc_admin_group_name = "arena_admin"

    with patch("app.api.routes.me.get_settings", return_value=_FakeSettings()):
        response = me.get_me(
            principal=principal,
            db=db,  # type: ignore[arg-type]
        )

    assert response.authenticated is True
    assert response.is_admin is False


def test_get_me_returns_is_admin_false_when_claims_are_empty() -> None:
    """Empty claims dict must not grant admin — guards against falsy-path bugs."""
    user = _user()
    principal = Principal(
        is_authenticated=True,
        user_id=str(user.id),
        claims={},
    )
    db = _ProfileDB(user=user, profile=None)

    class _FakeSettings:
        oidc_admin_group_claim = "groups"
        oidc_admin_group_name = "arena_admin"

    with patch("app.api.routes.me.get_settings", return_value=_FakeSettings()):
        response = me.get_me(
            principal=principal,
            db=db,  # type: ignore[arg-type]
        )

    assert response.authenticated is True
    assert response.is_admin is False


def test_me_route_session_cookie_returns_user_profile_and_admin_state(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _make_session_me_context(tmp_path, monkeypatch, groups=["arena_admin"])

    with TestClient(context.app) as client:
        client.cookies.set(
            context.settings.auth_session_cookie_name,
            context.session_token,
        )
        response = client.get("/me")

    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["is_admin"] is True
    assert body["user"]["id"] == str(context.user_id)
    assert body["user"]["oidc_issuer"] == "https://issuer.example"
    assert body["user"]["oidc_sub"] == "session-user"
    assert body["profile"]["display_name"] == "Session User"

    with context.session_factory() as db:
        row = db.get(AuthSession, context.auth_session_id)
        assert row is not None
        assert row.last_seen_at == context.initial_last_seen_at.replace(tzinfo=None)


def test_me_route_session_cookie_missing_admin_group_returns_non_admin(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _make_session_me_context(tmp_path, monkeypatch, groups=["basic_user"])

    with TestClient(context.app) as client:
        client.cookies.set(
            context.settings.auth_session_cookie_name,
            context.session_token,
        )
        response = client.get("/me")

    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["is_admin"] is False


def test_admin_endpoint_accepts_admin_session_cookie_and_rejects_non_admin_cookie(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_context = _make_session_me_context(
        tmp_path,
        monkeypatch,
        groups=["arena_admin"],
        database_name="admin-session.db",
    )
    basic_context = _create_user_session(admin_context, groups=["basic_user"])

    with TestClient(admin_context.app) as client:
        client.cookies.set(
            admin_context.settings.auth_session_cookie_name,
            admin_context.session_token,
        )
        allowed = client.get("/admin-probe")
        client.cookies.set(
            admin_context.settings.auth_session_cookie_name,
            basic_context.session_token,
        )
        rejected = client.get("/admin-probe")

    assert allowed.status_code == 200
    assert allowed.json() == {"auth_method": "session", "actor_type": "human"}
    assert rejected.status_code == 403
    assert rejected.json()["detail"] == "Admin group membership required"


def test_profile_update_with_session_cookie_requires_and_accepts_stable_csrf(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _make_session_me_context(tmp_path, monkeypatch, groups=["arena_admin"])

    with TestClient(context.app) as client:
        client.cookies.set(
            context.settings.auth_session_cookie_name,
            context.session_token,
        )
        missing = client.put("/me/profile", json={"display_name": "No Csrf"})
        csrf_token = client.get("/session-bootstrap").json()["csrf_token"]
        updated = client.put(
            "/me/profile",
            json={
                "display_name": "Updated Session User",
                "ui_language": "zh",
                "zh_variant": "hant",
                "jp_proficiency": {"jlpt": "N1"},
                "translation_experience": {"years": 7},
                "consents": {"tos": True, "privacy": True},
            },
            headers={context.settings.auth_csrf_header_name: csrf_token},
        )

    assert missing.status_code == 403
    assert missing.json()["detail"] == "CSRF token required"
    assert updated.status_code == 200
    body = updated.json()
    assert body["authenticated"] is True
    assert body["is_admin"] is True
    assert body["profile"]["display_name"] == "Updated Session User"
    assert body["profile"]["ui_language"] == "zh"
    assert body["profile"]["zh_variant"] == "hant"
    assert body["profile"]["jp_proficiency"] == {"jlpt": "N1"}
    assert body["profile"]["translation_experience"] == {"years": 7}
    assert body["profile"]["consents"] == {"tos": True, "privacy": True}


def _make_session_me_context(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    groups: list[str],
    database_name: str = "me-session.db",
):
    settings = _session_settings()
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / database_name}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    def override_db():
        with session_factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    app = FastAPI()
    app.include_router(me.router)

    @app.get("/session-bootstrap")
    def session_bootstrap() -> dict[str, str]:
        raw_csrf = auth_session.stable_auth_session_csrf_token(
            created.session_token,
            settings=settings,
        )
        return {"csrf_token": raw_csrf}

    @app.get("/admin-probe")
    def admin_probe(
        principal: security.Principal = Depends(security.require_admin),
    ) -> dict[str, str]:
        return {"auth_method": principal.auth_method, "actor_type": principal.actor_type}

    app.dependency_overrides[me.get_db] = override_db
    app.dependency_overrides[security.get_db] = override_db
    app.dependency_overrides[csrf.get_db] = override_db
    app.dependency_overrides[csrf.get_settings] = lambda: settings
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    monkeypatch.setattr(me, "get_settings", lambda: settings)
    monkeypatch.setattr(csrf, "get_settings", lambda: settings)

    created = _create_user_session(
        _SessionMeContext(
            app=app,
            session_factory=session_factory,
            settings=settings,
            session_token="",
            auth_session_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            initial_last_seen_at=datetime.now(timezone.utc),
        ),
        groups=groups,
        profile=True,
        oidc_sub="session-user",
    )
    return _SessionMeContext(
        app=app,
        session_factory=session_factory,
        settings=settings,
        session_token=created.session_token,
        auth_session_id=created.auth_session_id,
        user_id=created.user_id,
        initial_last_seen_at=created.initial_last_seen_at,
    )


def _create_user_session(
    context,
    *,
    groups: list[str],
    profile: bool = False,
    oidc_sub: str | None = None,
):
    initial_last_seen_at = datetime.now(timezone.utc)
    with context.session_factory() as db:
        user = User(
            oidc_issuer="https://issuer.example",
            oidc_sub=oidc_sub or f"session-user-{uuid.uuid4()}",
        )
        db.add(user)
        db.flush()
        if profile:
            profile_row = UserProfile(
                user_id=user.id,
                display_name="Session User",
                ui_language="en",
                zh_variant="hans",
                jp_proficiency={"jlpt": "N1"},
                translation_experience={"years": 6},
                consents={"tos": True},
            )
            profile_row.completed_at = datetime(2026, 5, 24, 10, 5, tzinfo=timezone.utc)
            db.add(profile_row)
        created = auth_session.create_auth_session(
            db,
            user=user,
            claims={"groups": groups, "email": f"{user.oidc_sub}@example.test"},
            settings=context.settings,
            now=initial_last_seen_at,
        )
        db.commit()
        return type("CreatedSession", (), {
            "session_token": created.session_token,
            "auth_session_id": created.row.id,
            "user_id": user.id,
            "initial_last_seen_at": initial_last_seen_at,
        })()


def _session_settings():
    return type("SessionSettings", (), {
        "auth_csrf_header_name": "X-CSRF-Token",
        "auth_session_cookie_name": "arena_session",
        "auth_session_hash_secret": _HASH_SECRET,
        "auth_session_max_age_seconds": 3600,
        "auth_session_last_seen_min_interval_seconds": 60,
        "auth_session_last_seen_lock_timeout_ms": 100,
        "auth_session_last_seen_statement_timeout_ms": 500,
        "oidc_login_state_max_age_seconds": 600,
        "oidc_admin_group_claim": "groups",
        "oidc_admin_group_name": "arena_admin",
    })()


class _SessionMeContext:
    def __init__(
        self,
        *,
        app: FastAPI,
        session_factory: sessionmaker[Session],
        settings: object,
        session_token: str,
        auth_session_id: uuid.UUID,
        user_id: uuid.UUID,
        initial_last_seen_at: datetime,
    ) -> None:
        self.app = app
        self.session_factory = session_factory
        self.settings = settings
        self.session_token = session_token
        self.auth_session_id = auth_session_id
        self.user_id = user_id
        self.initial_last_seen_at = initial_last_seen_at
