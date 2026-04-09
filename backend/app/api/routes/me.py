"""app.api.routes.me

User and profile endpoints.

Notes:
- Anonymous users are allowed in the product; `GET /me` should work without a
  token and report an unauthenticated session.
- When an OIDC access token is provided, the backend should upsert a `users`
  row keyed by (issuer, sub) and return the associated profile.
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import Principal, get_principal_optional
from app.db.session import get_db
from app.models.user import User, UserProfile
from app.schemas.me import MeResponse, ProfileUpsert

router = APIRouter(tags=["me"])


@router.get("/me")
def get_me(
    principal: Principal = Depends(get_principal_optional),
    db: Session = Depends(get_db),
) -> MeResponse:
    if not principal.is_authenticated or principal.user_id is None:
        return MeResponse(authenticated=False, user=None, profile=None)

    user = db.get(User, uuid.UUID(principal.user_id))
    if user is None:
        # The user row should exist because get_principal_optional upserts it.
        # A missing row indicates data corruption — log and report error.
        import logging

        logging.getLogger(__name__).error(
            "Authenticated user %s not found in database",
            principal.user_id,
        )
        raise HTTPException(
            status_code=500,
            detail="Authenticated user record not found",
        )

    profile = db.get(UserProfile, user.id)
    return MeResponse(
        authenticated=True,
        user=_serialize_user(user),
        profile=_serialize_profile(profile),
    )


@router.put("/me/profile")
def put_profile(
    payload: ProfileUpsert,
    principal: Principal = Depends(get_principal_optional),
    db: Session = Depends(get_db),
) -> MeResponse:
    if not principal.is_authenticated or principal.user_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = db.get(User, uuid.UUID(principal.user_id))
    if user is None:
        raise HTTPException(status_code=401, detail="Authenticated user not found")

    profile = db.get(UserProfile, user.id)
    if profile is None:
        profile = UserProfile(user_id=user.id)
        db.add(profile)

    patch = payload.model_dump(exclude_unset=True)
    for field in (
        "display_name",
        "ui_language",
        "zh_variant",
        "jp_proficiency",
        "translation_experience",
        "consents",
    ):
        if field in patch:
            setattr(profile, field, patch[field])
    if profile.completed_at is None:
        profile.completed_at = datetime.now(timezone.utc)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Profile update failed due to a constraint conflict",
        ) from exc
    db.refresh(profile)

    return MeResponse(
        authenticated=True,
        user=_serialize_user(user),
        profile=_serialize_profile(profile),
    )


def _serialize_user(user: User) -> dict[str, object]:
    return {
        "id": str(user.id),
        "oidc_issuer": user.oidc_issuer,
        "oidc_sub": user.oidc_sub,
        "created_at": user.created_at.isoformat(),
    }


def _serialize_profile(profile: UserProfile | None) -> dict[str, object] | None:
    if profile is None:
        return None

    return {
        "display_name": profile.display_name,
        "ui_language": profile.ui_language,
        "zh_variant": profile.zh_variant,
        "jp_proficiency": profile.jp_proficiency,
        "translation_experience": profile.translation_experience,
        "consents": profile.consents,
        "completed_at": (
            profile.completed_at.isoformat()
            if profile.completed_at is not None
            else None
        ),
    }
