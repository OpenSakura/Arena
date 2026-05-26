"""Bot battle routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.csrf import csrf_exempt
from app.core.security import Principal, require_scopes
from app.db.session import get_db
from app.models.battle import Battle, Run
from app.schemas._types import UuidStr
from app.schemas.battles import BattleCreate
from app.schemas.bot import (
    BotBattleCreateAndWaitRequest,
    BotBattleCreateAndWaitResponse,
    BotBattleResult,
    BotBattleStatusResponse,
    BotRunPublic,
)
from app.services.battle_orchestrator import BattleOrchestrator, get_battle_orchestrator
from app.utils.id import parse_uuid_or_422
from app.utils.llm_queue import get_llm_request_queue
from app.api.routes import battles as human_battles


require_bot_battle_create_scopes = require_scopes(["battle:create", "battle:execute"])
require_bot_battle_read_scopes = require_scopes(["battle:read"])


def require_bot_battle_create_principal(
    principal: Principal = Depends(require_bot_battle_create_scopes),
) -> Principal:
    return principal


def require_bot_battle_read_principal(
    principal: Principal = Depends(require_bot_battle_read_scopes),
) -> Principal:
    return principal


router = APIRouter(
    prefix="/bot/battles",
    tags=["bot", "battles"],
)


@router.post(
    "/create-and-wait",
    response_model=BotBattleCreateAndWaitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@csrf_exempt("bearer/service-token-only endpoint")
async def create_and_wait_battle(
    payload: BotBattleCreateAndWaitRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        max_length=128,
    ),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_bot_battle_create_principal),
    settings: Settings = Depends(get_settings),
    orchestrator: BattleOrchestrator = Depends(get_battle_orchestrator),
) -> BotBattleCreateAndWaitResponse:
    service_account_id = _require_service_account_id(principal)

    battle = None
    if idempotency_key is not None:
        battle = _load_idempotent_battle(
            db=db,
            service_account_id=service_account_id,
            idempotency_key=idempotency_key,
        )

    if battle is None:
        battle = _create_bot_battle(
            db=db,
            payload=payload,
            principal=principal,
            service_account_id=service_account_id,
            idempotency_key=idempotency_key,
            settings=settings,
        )

    if battle.status == "pending":
        _raise_llm_backpressure_if_saturated()

    request_id = getattr(request.state, "request_id", None)
    wait_status = await orchestrator.execute_battle_and_wait(
        battle.id,
        timeout_seconds=payload.timeout_seconds,
        request_id=request_id,
    )

    db.expire_all()
    battle = _load_owned_battle(
        db=db,
        battle_id=battle.id,
        service_account_id=service_account_id,
    )
    if battle is None:
        raise HTTPException(status_code=404, detail="Battle not found")

    runs = _load_battle_runs(db=db, battle_id=battle.id)
    if wait_status == "timeout":
        response.status_code = status.HTTP_202_ACCEPTED
        return _to_bot_timeout_response(battle=battle)

    response.status_code = status.HTTP_200_OK
    return _to_bot_create_response(battle=battle, runs=runs)


@router.get(
    "/{battle_id}",
    response_model=BotBattleStatusResponse,
)
def get_bot_battle(
    battle_id: UuidStr,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_bot_battle_read_principal),
) -> BotBattleStatusResponse:
    service_account_id = _require_service_account_id(principal)
    battle_uuid = parse_uuid_or_422(battle_id, "battle_id")
    battle = _load_owned_battle(
        db=db,
        battle_id=battle_uuid,
        service_account_id=service_account_id,
    )
    if battle is None:
        raise HTTPException(status_code=404, detail="Battle not found")

    runs = _load_battle_runs(db=db, battle_id=battle.id)
    return _to_bot_status_response(battle=battle, runs=runs)


def _require_service_account_id(principal: Principal) -> uuid.UUID:
    service_account_id = principal.service_account_id
    if service_account_id is None:
        raise HTTPException(status_code=403, detail="Service account principal required")
    try:
        return uuid.UUID(service_account_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=403,
            detail="Service account principal required",
        ) from exc


def _create_bot_battle(
    *,
    db: Session,
    payload: BotBattleCreateAndWaitRequest,
    principal: Principal,
    service_account_id: uuid.UUID,
    idempotency_key: str | None,
    settings: Settings,
) -> Battle:
    battle_payload = BattleCreate(
        task_set_id=payload.task_set_id,
        task_id=payload.task_id,
        mode=payload.mode,
    )
    task = human_battles._select_task(db=db, payload=battle_payload)
    model_a_id, model_b_id = human_battles._select_model_pair(db, settings=settings)

    battle = Battle(
        task_id=task.id,
        mode=payload.mode,
        status="pending",
        requester_service_account_id=service_account_id,
        idempotency_key=idempotency_key,
        metadata_json={
            "task_snapshot": {
                "source_text": task.source_text,
                "source_lang": task.source_lang,
                "target_lang": task.target_lang,
            },
            "sampling": {
                "task": "weighted_v1",
                "models": "fastchat_weighted_v2",
            },
            "requester_user_id": principal.user_id,
            "requester_service_account_id": str(service_account_id),
            "automatic_retry_count": 0,
        },
    )
    try:
        db.add(battle)
        db.flush()

        run_a = Run(battle_id=battle.id, side="A", model_id=model_a_id)
        run_b = Run(battle_id=battle.id, side="B", model_id=model_b_id)
        db.add_all([run_a, run_b])
        db.commit()
    except IntegrityError as exc:
        return _handle_bot_battle_integrity_error(
            db=db,
            service_account_id=service_account_id,
            idempotency_key=idempotency_key,
            exc=exc,
        )

    db.refresh(battle)
    db.refresh(run_a)
    db.refresh(run_b)
    return battle


def _handle_bot_battle_integrity_error(
    *,
    db: Session,
    service_account_id: uuid.UUID,
    idempotency_key: str | None,
    exc: IntegrityError,
) -> Battle:
    db.rollback()
    if idempotency_key is not None:
        existing = _load_idempotent_battle(
            db=db,
            service_account_id=service_account_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return existing
    raise HTTPException(
        status_code=409,
        detail="Selected model was removed during battle creation, please retry",
    ) from exc


def _load_idempotent_battle(
    *,
    db: Session,
    service_account_id: uuid.UUID,
    idempotency_key: str,
) -> Battle | None:
    return db.execute(
        select(Battle).where(
            Battle.requester_service_account_id == service_account_id,
            Battle.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()


def _load_owned_battle(
    *,
    db: Session,
    battle_id: uuid.UUID,
    service_account_id: uuid.UUID,
) -> Battle | None:
    return db.execute(
        select(Battle).where(
            Battle.id == battle_id,
            Battle.requester_service_account_id == service_account_id,
        )
    ).scalar_one_or_none()


def _load_battle_runs(*, db: Session, battle_id: uuid.UUID) -> list[Run]:
    return list(
        db.execute(
            select(Run).where(Run.battle_id == battle_id).order_by(Run.side.asc())
        )
        .scalars()
        .all()
    )


def _status_url(battle_id: uuid.UUID) -> str:
    return f"/api/v1/bot/battles/{battle_id}"


def _raise_llm_backpressure_if_saturated() -> None:
    stats = get_llm_request_queue().stats()
    queued = stats.get("queued", 0)
    capacity = stats.get("capacity", 1)
    in_flight = stats.get("in_flight", 0)
    max_concurrent = stats.get("max_concurrent", 1)
    if (
        isinstance(queued, int)
        and isinstance(capacity, int)
        and isinstance(in_flight, int)
        and isinstance(max_concurrent, int)
        and queued >= capacity
        and in_flight >= max_concurrent
    ):
        raise HTTPException(
            status_code=503,
            detail="LLM backpressure, please retry",
            headers={"Retry-After": "1"},
        )


def _to_bot_run_public(run: Run | None) -> BotRunPublic | None:
    if run is None:
        return None
    return BotRunPublic(
        id=str(run.id),
        side=run.side,
        model_id=str(run.model_id),
        output_text=run.output_text,
        error_text=run.error_text,
    )


def _to_bot_battle_result(
    *,
    battle: Battle,
    runs: list[Run],
) -> BotBattleResult | None:
    run_by_side = {run.side: run for run in runs}
    if battle.status == "completed":
        return BotBattleResult(
            battle_id=str(battle.id),
            run_a=_to_bot_run_public(run_by_side.get("A")),
            run_b=_to_bot_run_public(run_by_side.get("B")),
        )
    if battle.status == "failed":
        errors = [run.error_text for run in runs if run.error_text]
        return BotBattleResult(
            battle_id=str(battle.id),
            run_a=_to_bot_run_public(run_by_side.get("A")),
            run_b=_to_bot_run_public(run_by_side.get("B")),
            error="; ".join(errors) if errors else "Battle failed",
        )
    return None


def _to_bot_create_response(
    *,
    battle: Battle,
    runs: list[Run],
    status_override: str | None = None,
) -> BotBattleCreateAndWaitResponse:
    return BotBattleCreateAndWaitResponse(
        battle_id=str(battle.id),
        status=status_override or battle.status,
        status_url=_status_url(battle.id),
        result=_to_bot_battle_result(battle=battle, runs=runs),
    )


def _to_bot_timeout_response(*, battle: Battle) -> BotBattleCreateAndWaitResponse:
    return BotBattleCreateAndWaitResponse(
        battle_id=str(battle.id),
        status="timeout",
        status_url=_status_url(battle.id),
        result=None,
    )


def _to_bot_status_response(
    *,
    battle: Battle,
    runs: list[Run],
) -> BotBattleStatusResponse:
    status_url = _status_url(battle.id)

    return BotBattleStatusResponse(
        battle_id=str(battle.id),
        status=battle.status,
        status_url=status_url,
        result_url=status_url if battle.status in {"completed", "failed"} else None,
        result=_to_bot_battle_result(battle=battle, runs=runs),
    )
