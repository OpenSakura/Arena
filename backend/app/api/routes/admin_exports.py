"""app.api.routes.admin_exports

Admin-only JSONL export endpoints for reproducibility and dataset building.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
import json
from typing import Annotated, Any
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.session import get_db
from app.models.battle import Battle, Run
from app.models.rating import ModelRating
from app.models.service_account import ServiceAccount
from app.models.task import Task
from app.models.user import User
from app.models.vote import Vote

SCHEMA_VERSION = "arena_export_v1"

router = APIRouter(
    prefix="/admin/export",
    tags=["admin", "export"],
    dependencies=[Depends(require_admin, scope="function")],
)


@router.get("/tasks.jsonl")
def export_tasks(db: Session = Depends(get_db, scope="function")) -> StreamingResponse:
    # Materialize rows while the DB session is still alive.  The dependency
    # teardown (get_db) closes the session after the route handler returns,
    # but *before* FastAPI iterates the streaming body.  Eagerly loading via
    # .all() ensures rows are safely detached before session cleanup.
    tasks = db.execute(select(Task).order_by(Task.created_at.asc())).scalars().all()

    def records() -> Iterable[dict[str, object]]:
        for task in tasks:
            yield {
                "schema_version": SCHEMA_VERSION,
                "record_type": "task",
                "id": str(task.id),
                "task_set_id": str(task.task_set_id)
                if task.task_set_id is not None
                else None,
                "source_lang": task.source_lang,
                "target_lang": task.target_lang,
                "source_text": task.source_text,
                "metadata": task.metadata_json,
                "created_at": task.created_at,
            }

    return _jsonl_response(records(), filename="tasks.jsonl")


@router.get("/runs.jsonl")
def export_runs(db: Session = Depends(get_db, scope="function")) -> StreamingResponse:
    runs = db.execute(select(Run).order_by(Run.created_at.asc())).scalars().all()

    def records() -> Iterable[dict[str, object]]:
        for run in runs:
            yield {
                "schema_version": SCHEMA_VERSION,
                "record_type": "run",
                "id": str(run.id),
                "battle_id": str(run.battle_id),
                "side": run.side,
                "model_id": str(run.model_id),
                "request_json": run.request_json,
                "prompt_rendered": run.prompt_rendered,
                "output_text": run.output_text,
                "output_text_raw": run.output_text_raw,
                "stats": run.stats,
                "error_text": run.error_text,
                "created_at": run.created_at,
            }

    return _jsonl_response(records(), filename="runs.jsonl")


@router.get("/battles.jsonl")
def export_battles(db: Session = Depends(get_db, scope="function")) -> StreamingResponse:
    battles = (
        db.execute(select(Battle).order_by(Battle.created_at.asc())).scalars().all()
    )

    def records() -> Iterable[dict[str, object]]:
        for battle in battles:
            yield {
                "schema_version": SCHEMA_VERSION,
                "record_type": "battle",
                "id": str(battle.id),
                "task_id": str(battle.task_id),
                "mode": battle.mode,
                "status": battle.status,
                "metadata": battle.metadata_json,
                "created_at": battle.created_at,
            }

    return _jsonl_response(records(), filename="battles.jsonl")


@router.get("/votes.jsonl")
def export_votes(
    service_account_id: Annotated[uuid.UUID | None, Query()] = None,
    db: Session = Depends(get_db, scope="function"),
) -> StreamingResponse:
    stmt = (
        select(Vote, User.actor_type, ServiceAccount.name)
        .join(User, User.id == Vote.voter_user_id)
        .outerjoin(ServiceAccount, ServiceAccount.id == Vote.service_account_id)
        .order_by(Vote.created_at.asc())
    )
    if service_account_id is not None:
        stmt = stmt.where(Vote.service_account_id == service_account_id)
    votes = db.execute(stmt).all()

    def records() -> Iterable[dict[str, object]]:
        for row in votes:
            vote, voter_actor_type, service_account_name = _vote_export_row(row)
            voter_actor_type = _vote_actor_type_for_export(
                vote=vote,
                voter_actor_type=voter_actor_type,
            )
            vote_service_account_id = getattr(vote, "service_account_id", None)
            vote_service_account_token_id = getattr(
                vote, "service_account_token_id", None
            )
            yield {
                "schema_version": SCHEMA_VERSION,
                "record_type": "vote",
                "id": str(vote.id),
                "battle_id": str(vote.battle_id),
                "winner": vote.winner,
                "rubric": vote.rubric,
                "comment": vote.comment,
                "voter_user_id": str(vote.voter_user_id)
                if vote.voter_user_id
                else None,
                "voter_actor_type": voter_actor_type,
                "service_account_id": str(vote_service_account_id)
                if vote_service_account_id
                else None,
                "service_account_name": service_account_name,
                "service_account_token_id": str(vote_service_account_token_id)
                if vote_service_account_token_id
                else None,
                "bot_metadata": getattr(vote, "bot_metadata", None),
                "created_at": vote.created_at,
            }

    return _jsonl_response(records(), filename="votes.jsonl")


@router.get("/ratings.jsonl")
def export_ratings(db: Session = Depends(get_db, scope="function")) -> StreamingResponse:
    """Export persisted Elo snapshots from ``model_ratings``.

    Bradley-Terry ratings are computed on demand by ``/leaderboard?method=bt``
    and are intentionally not persisted or exported here.
    """

    ratings = (
        db.execute(select(ModelRating).order_by(ModelRating.updated_at.asc()))
        .scalars()
        .all()
    )

    def records() -> Iterable[dict[str, object]]:
        for rating in ratings:
            yield {
                "schema_version": SCHEMA_VERSION,
                "record_type": "model_rating",
                "rating_method": "elo",
                "model_id": str(rating.model_id),
                "rating": rating.rating,
                "games_played": rating.games_played,
                "updated_at": rating.updated_at,
            }

    return _jsonl_response(records(), filename="ratings.jsonl")


def _vote_export_row(row: object) -> tuple[object, str, str | None]:
    if isinstance(row, tuple):
        vote, actor_type, service_account_name = row
        return vote, _safe_actor_type(actor_type), service_account_name

    row_values = getattr(row, "_tuple", None)
    if callable(row_values):
        values = row_values()
        if len(values) == 3:
            vote, actor_type, service_account_name = values
            return vote, _safe_actor_type(actor_type), service_account_name

    return row, _safe_actor_type(getattr(row, "voter_actor_type", "human")), getattr(
        row,
        "service_account_name",
        None,
    )


def _vote_actor_type_for_export(*, vote: object, voter_actor_type: str) -> str:
    if getattr(vote, "service_account_id", None) is not None or voter_actor_type == "bot":
        return "bot"
    return "human"


def _safe_actor_type(value: Any) -> str:
    return value if value in {"human", "bot"} else "human"


def _jsonl_response(
    records: Iterable[dict[str, object]], *, filename: str
) -> StreamingResponse:
    def iterate() -> Iterable[bytes]:
        for record in records:
            normalized = _normalize(record)
            line = json.dumps(normalized, ensure_ascii=False)
            yield f"{line}\n".encode("utf-8")

    return StreamingResponse(
        iterate(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _normalize(value: object) -> object:
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value
