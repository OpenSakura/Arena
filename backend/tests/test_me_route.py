from __future__ import annotations

from datetime import datetime, timezone
import uuid

from fastapi import HTTPException
import pytest

from app.api.routes import me
from app.core.security import Principal
from app.models.user import User, UserProfile
from app.schemas.me import ProfileUpsert


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
    assert response.user == {
        "id": str(user.id),
        "oidc_issuer": "https://issuer.example",
        "oidc_sub": "sub-123",
        "created_at": user.created_at.isoformat(),
    }
    assert response.profile == {
        "display_name": "Alice",
        "ui_language": "en",
        "zh_variant": "hans",
        "jp_proficiency": {"jlpt": "N2"},
        "translation_experience": {"years": 4},
        "consents": {"tos": True},
        "completed_at": profile.completed_at.isoformat(),
    }


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

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Authenticated user not found"


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
    assert response.profile["display_name"] == "Aya"
