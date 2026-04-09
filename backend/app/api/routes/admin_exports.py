"""app.api.routes.admin_exports

Admin-only JSONL export endpoints for reproducibility and dataset building.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
import json
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.session import get_db
from app.models.battle import Battle, Run
from app.models.rating import ModelRating, RatingEvent
from app.models.task import Task
from app.models.vote import Vote

SCHEMA_VERSION = "arena_export_v1"

router = APIRouter(
    prefix="/admin/export",
    tags=["admin", "export"],
    dependencies=[Depends(require_admin)],
)


@router.get("/tasks.jsonl")
def export_tasks(db: Session = Depends(get_db)) -> StreamingResponse:
    # Use yield_per for server-side cursor to avoid loading all records into
    # memory.  Keep the session alive for the duration of the streaming response.
    tasks = (
        db.execute(select(Task).order_by(Task.created_at.asc()))
        .scalars()
        .yield_per(500)
    )

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
def export_runs(db: Session = Depends(get_db)) -> StreamingResponse:
    runs = (
        db.execute(select(Run).order_by(Run.created_at.asc())).scalars().yield_per(500)
    )

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
                "stats": run.stats,
                "error_text": run.error_text,
                "created_at": run.created_at,
            }

    return _jsonl_response(records(), filename="runs.jsonl")


@router.get("/battles.jsonl")
def export_battles(db: Session = Depends(get_db)) -> StreamingResponse:
    battles = (
        db.execute(select(Battle).order_by(Battle.created_at.asc()))
        .scalars()
        .yield_per(500)
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
def export_votes(db: Session = Depends(get_db)) -> StreamingResponse:
    votes = (
        db.execute(select(Vote).order_by(Vote.created_at.asc()))
        .scalars()
        .yield_per(500)
    )

    def records() -> Iterable[dict[str, object]]:
        for vote in votes:
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
                "voter_anon_id": vote.voter_anon_id,
                "ip_hash": vote.ip_hash,
                "user_agent_hash": vote.user_agent_hash,
                "created_at": vote.created_at,
            }

    return _jsonl_response(records(), filename="votes.jsonl")


@router.get("/ratings.jsonl")
def export_ratings(db: Session = Depends(get_db)) -> StreamingResponse:
    # Use yield_per for server-side cursor to avoid loading all records into
    # memory.
    ratings = (
        db.execute(select(ModelRating).order_by(ModelRating.updated_at.asc()))
        .scalars()
        .yield_per(500)
    )
    events = (
        db.execute(select(RatingEvent).order_by(RatingEvent.created_at.asc()))
        .scalars()
        .yield_per(500)
    )

    def records() -> Iterable[dict[str, object]]:
        for rating in ratings:
            yield {
                "schema_version": SCHEMA_VERSION,
                "record_type": "model_rating",
                "model_id": str(rating.model_id),
                "rating": rating.rating,
                "games_played": rating.games_played,
                "updated_at": rating.updated_at,
            }

        for event in events:
            yield {
                "schema_version": SCHEMA_VERSION,
                "record_type": "rating_event",
                "id": str(event.id),
                "vote_id": str(event.vote_id),
                "model_a_id": str(event.model_a_id),
                "model_b_id": str(event.model_b_id),
                "delta_a": event.delta_a,
                "delta_b": event.delta_b,
                "created_at": event.created_at,
            }

    return _jsonl_response(records(), filename="ratings.jsonl")


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
