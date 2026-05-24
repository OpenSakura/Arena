from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Iterable
import uuid

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from app.api.routes.battles import _get_auth_battle_create_rate_limiter
from app.api.routes.votes import _get_auth_vote_submit_rate_limiter
from app.core.config import get_settings
from app.models.battle import Battle, Run
from app.models.model_registry import Model
from app.models.rating import ModelRating
from app.models.task import Task
from app.models.vote import Vote
from app.services.oidc_client import get_oidc_confidential_client
from app.utils.redis import get_rate_limit_redis_client


pytestmark = pytest.mark.e2e


_REPO_ROOT = Path(__file__).resolve().parents[3]
_EVIDENCE_DIR = _REPO_ROOT / ".omo" / "evidence"


def _parse_sse_events(
    lines: Iterable[str | bytes],
) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    event_name = "message"
    data_lines: list[str] = []

    for raw_line in lines:
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line

        if line == "":
            payload = _parse_sse_payload(data_lines)
            events.append((event_name, payload))
            event_name = "message"
            data_lines = []
            continue

        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
            continue

        if line.startswith("data:"):
            data = line.split(":", 1)[1]
            if data.startswith(" "):
                data = data[1:]
            data_lines.append(data)

    if data_lines:
        payload = _parse_sse_payload(data_lines)
        events.append((event_name, payload))

    return events


def _parse_sse_payload(data_lines: list[str]) -> dict[str, object]:
    if not data_lines:
        return {}

    payload = json.loads("\n".join(data_lines))
    if not isinstance(payload, dict):
        raise AssertionError("Expected SSE JSON payload to be an object")
    return payload


def _seed_task_and_models(db_session, *, suffix: str) -> tuple[Task, Model, Model]:
    task = Task(
        source_lang="ja",
        target_lang="zh",
        source_text=f"stream-source-{suffix}",
    )
    model_a = Model(
        display_name=f"E2E Stream Model A {suffix}",
        provider_type="openai",
        model_name=f"e2e-stream-a-{suffix}",
        base_url="http://example.invalid",
        enabled=True,
        visibility="public",
    )
    model_b = Model(
        display_name=f"E2E Stream Model B {suffix}",
        provider_type="openai",
        model_name=f"e2e-stream-b-{suffix}",
        base_url="http://example.invalid",
        enabled=True,
        visibility="public",
    )

    db_session.add_all([task, model_a, model_b])
    db_session.commit()
    return task, model_a, model_b


def _seed_completed_battle(
    db_session, *, suffix: str, requester_user_id: str | None = None
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    task = Task(
        source_lang="ja",
        target_lang="zh",
        source_text=f"vote-source-{suffix}",
    )
    model_a = Model(
        display_name=f"Vote Pipeline Model A {suffix}",
        provider_type="openai",
        model_name=f"vote-pipeline-a-{suffix}",
        base_url="http://example.invalid",
        enabled=True,
        visibility="public",
    )
    model_b = Model(
        display_name=f"Vote Pipeline Model B {suffix}",
        provider_type="openai",
        model_name=f"vote-pipeline-b-{suffix}",
        base_url="http://example.invalid",
        enabled=True,
        visibility="public",
    )

    db_session.add_all([task, model_a, model_b])
    db_session.flush()

    battle = Battle(
        task_id=task.id,
        mode="jp2zh_ab",
        status="completed",
        metadata_json={"requester_user_id": requester_user_id}
        if requester_user_id
        else None,
    )
    db_session.add(battle)
    db_session.flush()

    db_session.add_all(
        [
            Run(
                battle_id=battle.id,
                side="A",
                model_id=model_a.id,
                output_text="Alpha output",
            ),
            Run(
                battle_id=battle.id,
                side="B",
                model_id=model_b.id,
                output_text="Beta output",
            ),
        ]
    )
    db_session.commit()

    return battle.id, model_a.id, model_b.id


def _rating_snapshot(db_session, model_id: uuid.UUID) -> tuple[float, int]:
    rating = db_session.get(ModelRating, model_id)
    if rating is None:
        return 1000.0, 0
    return rating.rating, rating.games_played


def _revealed_vote_sample_count_for_model(db_session, model_id: uuid.UUID) -> int:
    count = db_session.execute(
        select(func.count(func.distinct(Vote.id)))
        .join(Run, Run.battle_id == Vote.battle_id)
        .where(Vote.revealed.is_(True), Run.model_id == model_id)
    ).scalar_one()
    return int(count)


def _reset_backend_singletons() -> None:
    import app.db.session as session_module

    get_settings.cache_clear()
    get_oidc_confidential_client.cache_clear()
    get_rate_limit_redis_client.cache_clear()
    _get_auth_battle_create_rate_limiter.cache_clear()
    _get_auth_vote_submit_rate_limiter.cache_clear()
    session_module._engine = None
    session_module._SessionLocal = None


@pytest.fixture
def backend_client_with_deprecated_turnstile_config(
    configured_backend_env: None,
    monkeypatch: pytest.MonkeyPatch,
):
    del configured_backend_env

    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "arena-e2e-turnstile-secret")
    monkeypatch.setenv("TURNSTILE_VERIFY_URL", "https://turnstile.example/siteverify")
    _reset_backend_singletons()

    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client


def test_battle_stream_executes_and_persists_terminal_state(
    authenticated_backend_client,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.llm_client import LLMClient, LLMStreamChunk

    async def fake_stream_chat_completion(self, *, model: str, **kwargs):
        _ = (self, kwargs)
        yield LLMStreamChunk(text_delta=f"{model}:part-1 ")
        yield LLMStreamChunk(
            text_delta=f"{model}:part-2",
            usage={"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            finish_reason="stop",
            request_id=f"req-{model}",
        )

    monkeypatch.setattr(
        LLMClient, "stream_chat_completion", fake_stream_chat_completion
    )

    suffix = uuid.uuid4().hex[:8]
    task, _, _ = _seed_task_and_models(db_session, suffix=suffix)

    create = authenticated_backend_client.client.post(
        "/api/v1/battles",
        headers=authenticated_backend_client.headers,
        json={"task_id": str(task.id)},
    )
    assert create.status_code == 201
    battle_id = create.json()["id"]

    with authenticated_backend_client.client.stream(
        "GET", f"/api/v1/battles/{battle_id}/stream"
    ) as response:
        assert response.status_code == 200
        events = _parse_sse_events(response.iter_lines())

    event_names = [name for name, _ in events]
    assert "battle.started" in event_names
    assert event_names[-1] == "battle.completed"
    assert any(name == "run.delta" for name, _ in events)

    battle_uuid = uuid.UUID(battle_id)
    db_session.expire_all()

    battle = db_session.get(Battle, battle_uuid)
    assert battle is not None
    assert battle.status == "completed"

    runs = (
        db_session.execute(
            select(Run).where(Run.battle_id == battle_uuid).order_by(Run.side.asc())
        )
        .scalars()
        .all()
    )
    assert len(runs) == 2
    for run in runs:
        assert run.output_text
        assert run.error_text is None
        assert isinstance(run.stats, dict)
        assert run.stats.get("finish_reason") == "stop"
        usage = run.stats.get("usage")
        assert isinstance(usage, dict)


def test_vote_pipeline_handles_idempotency_and_conflicts(
    authenticated_backend_client,
    db_session,
) -> None:
    suffix = uuid.uuid4().hex[:8]
    requester_user_id = authenticated_backend_client.user_id
    battle_id, model_a_id, model_b_id = _seed_completed_battle(
        db_session, suffix=suffix, requester_user_id=requester_user_id
    )

    # Submit initial vote — immediately reveals and locks the vote.
    first = authenticated_backend_client.client.post(
        f"/api/v1/battles/{battle_id}/vote",
        headers=authenticated_backend_client.headers,
        json={"winner": "A"},
    )
    assert first.status_code == 201
    first_payload = first.json()
    assert first_payload["battle_id"] == str(battle_id)
    assert first_payload["winner"] == "A"
    assert first_payload["reveal"]["A"]["model_id"] == str(model_a_id)
    assert first_payload["reveal"]["B"]["model_id"] == str(model_b_id)
    first_vote_id = first_payload["vote_id"]

    # Re-submitting with the same winner is idempotent.
    second = authenticated_backend_client.client.post(
        f"/api/v1/battles/{battle_id}/vote",
        headers=authenticated_backend_client.headers,
        json={"winner": "A"},
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["vote_id"] == first_vote_id
    assert second_payload["winner"] == "A"
    assert second_payload["reveal"]["A"]["model_id"] == str(model_a_id)
    assert second_payload["reveal"]["B"]["model_id"] == str(model_b_id)

    # Changing winner after submit is rejected because the submit already reveals.
    conflicting = authenticated_backend_client.client.post(
        f"/api/v1/battles/{battle_id}/vote",
        headers=authenticated_backend_client.headers,
        json={"winner": "B"},
    )
    assert conflicting.status_code == 409
    assert conflicting.json()["detail"] == "Vote already revealed and cannot be changed"

    db_session.expire_all()
    stored_votes = (
        db_session.execute(select(Vote).where(Vote.battle_id == battle_id))
        .scalars()
        .all()
    )
    assert len(stored_votes) == 1
    assert str(stored_votes[0].id) == first_vote_id


def test_admin_routes_require_admin_group_claim(
    authenticated_backend_client,
) -> None:
    response = authenticated_backend_client.client.get("/api/v1/admin/models")

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin group membership required"


def test_admin_routes_allow_access_when_session_claim_matches_admin_group(
    admin_authenticated_backend_client,
) -> None:
    response = admin_authenticated_backend_client.client.get("/api/v1/admin/models")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("models"), list)


def test_battle_stream_vote_and_leaderboard_reflect_rating_updates(
    authenticated_backend_client,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.llm_client import LLMClient, LLMStreamChunk
    from app.services.leaderboard_refresh import get_leaderboard_refresher

    async def fake_stream_chat_completion(self, *, model: str, **kwargs):
        _ = (self, kwargs)
        yield LLMStreamChunk(text_delta=f"{model}:opening ")
        yield LLMStreamChunk(
            text_delta=f"{model}:final",
            usage={"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
            finish_reason="stop",
            request_id=f"req-{model}",
        )

    monkeypatch.setattr(
        LLMClient, "stream_chat_completion", fake_stream_chat_completion
    )

    suffix = uuid.uuid4().hex[:8]
    task, _, _ = _seed_task_and_models(db_session, suffix=suffix)

    create = authenticated_backend_client.client.post(
        "/api/v1/battles",
        headers=authenticated_backend_client.headers,
        json={"task_id": str(task.id)},
    )
    assert create.status_code == 201
    battle_id = create.json()["id"]

    with authenticated_backend_client.client.stream(
        "GET", f"/api/v1/battles/{battle_id}/stream"
    ) as stream_response:
        assert stream_response.status_code == 200
        events = _parse_sse_events(stream_response.iter_lines())

    assert any(name == "run.delta" for name, _ in events)
    assert events[-1][0] == "battle.completed"

    battle_uuid = uuid.UUID(battle_id)
    db_session.expire_all()

    runs = (
        db_session.execute(
            select(Run).where(Run.battle_id == battle_uuid).order_by(Run.side.asc())
        )
        .scalars()
        .all()
    )
    assert len(runs) == 2
    run_map = {run.side: run for run in runs}
    run_a = run_map["A"]
    run_b = run_map["B"]

    before_a_rating, _ = _rating_snapshot(db_session, run_a.model_id)
    before_b_rating, _ = _rating_snapshot(db_session, run_b.model_id)

    vote = authenticated_backend_client.client.post(
        f"/api/v1/battles/{battle_id}/vote",
        headers=authenticated_backend_client.headers,
        json={"winner": "A"},
    )
    assert vote.status_code == 201
    vote_payload = vote.json()
    assert vote_payload["winner"] == "A"
    assert vote_payload["reveal"]["A"]["model_id"] == str(run_a.model_id)
    assert vote_payload["reveal"]["B"]["model_id"] == str(run_b.model_id)

    db_session.expire_all()
    expected_a_games = _revealed_vote_sample_count_for_model(db_session, run_a.model_id)
    expected_b_games = _revealed_vote_sample_count_for_model(db_session, run_b.model_id)

    # Elo leaderboard reads persisted ModelRating snapshots. In this e2e
    # harness the periodic refresher is disabled, so trigger one refresh cycle
    # explicitly before asserting the persisted leaderboard view.
    get_leaderboard_refresher().refresh_once()

    leaderboard = authenticated_backend_client.client.get("/api/v1/leaderboard?method=elo")
    assert leaderboard.status_code == 200
    leaderboard_payload = leaderboard.json()

    rows_by_id = {row["model_id"]: row for row in leaderboard_payload["models"]}
    row_a = rows_by_id[str(run_a.model_id)]
    row_b = rows_by_id[str(run_b.model_id)]

    assert row_a["games_played"] == expected_a_games
    assert row_b["games_played"] == expected_b_games
    assert row_a["games_played"] >= 1
    assert row_b["games_played"] >= 1
    if expected_a_games == 1:
        assert row_a["rating"] > before_a_rating
    if expected_b_games == 1:
        assert row_b["rating"] < before_b_rating


def test_battle_create_requires_authentication_with_deprecated_turnstile_config(
    backend_client_with_deprecated_turnstile_config,
    authenticated_backend_client,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.routes import battles as battles_route

    # Seed tasks and models so battle creation can find them.
    suffix = uuid.uuid4().hex[:8]
    _seed_task_and_models(db_session, suffix=suffix)

    class _TurnstileResponse:
        def __init__(self, *, success: bool) -> None:
            self._success = success

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, bool]:
            return {"success": self._success}

    verification_calls: list[dict[str, object]] = []

    class _FakeTurnstileClient:
        def post(self, url: str, *, data: dict[str, str]) -> _TurnstileResponse:
            verification_calls.append(
                {
                    "url": url,
                    "data": dict(data),
                }
            )
            return _TurnstileResponse(
                success=data.get("response") == "valid-turnstile-token"
            )

    monkeypatch.setattr(
        battles_route, "_get_turnstile_http_client", lambda: _FakeTurnstileClient()
    )

    # 1. Unauthenticated battle creation is rejected before Turnstile runs.
    missing_token = backend_client_with_deprecated_turnstile_config.post(
        "/api/v1/battles",
        headers={"User-Agent": "arena-e2e-turnstile-missing"},
        json={},
    )
    assert missing_token.status_code == 401
    assert missing_token.json()["detail"] == "Authentication required"
    assert verification_calls == []

    # 2. Turnstile still does not rescue unauthenticated callers.
    valid_token = backend_client_with_deprecated_turnstile_config.post(
        "/api/v1/battles",
        headers={"User-Agent": "arena-e2e-turnstile-valid"},
        json={"turnstile_token": "valid-turnstile-token"},
    )
    assert valid_token.status_code == 401
    assert valid_token.json()["detail"] == "Authentication required"
    assert verification_calls == []

    # 3. Authenticated battle creation still skips Turnstile verification.
    def fail_if_called():
        raise AssertionError(
            "Turnstile verification must be skipped for authenticated battles"
        )

    monkeypatch.setattr(battles_route, "_get_turnstile_http_client", fail_if_called)

    authed_battle = authenticated_backend_client.client.post(
        "/api/v1/battles",
        headers=authenticated_backend_client.headers,
        json={},
    )
    assert authed_battle.status_code == 201


def test_integrated_bot_service_account_workflow_regression(
    admin_authenticated_backend_client,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.routes import bot_battles
    from app.services.leaderboard_refresh import get_leaderboard_refresher
    from app.services.llm_client import LLMClient, LLMStreamChunk

    async def fake_stream_chat_completion(self, *, model: str, **kwargs):
        _ = (self, kwargs)
        yield LLMStreamChunk(text_delta=f"{model}: task-12 output ")
        yield LLMStreamChunk(
            text_delta="complete",
            usage={"prompt_tokens": 9, "completion_tokens": 5, "total_tokens": 14},
            finish_reason="stop",
            request_id=f"task-12-{model}",
        )

    monkeypatch.setattr(
        LLMClient, "stream_chat_completion", fake_stream_chat_completion
    )

    suffix = uuid.uuid4().hex[:8]
    task, model_a, model_b = _seed_task_and_models(
        db_session, suffix=f"task12-{suffix}"
    )
    monkeypatch.setattr(
        bot_battles.human_battles,
        "_select_model_pair",
        lambda db, *, settings: (model_a.id, model_b.id),
    )

    client = admin_authenticated_backend_client.client
    admin_headers = admin_authenticated_backend_client.headers

    create_account_response = client.post(
        "/api/v1/admin/service-accounts",
        headers=admin_headers,
        json={
            "name": f"Task 12 Judge Bot {suffix}",
            "description": "Integrated bot workflow regression",
            "enabled": True,
        },
    )
    assert create_account_response.status_code == 201
    service_account = create_account_response.json()
    service_account_id = uuid.UUID(service_account["id"])
    assert service_account["tokens"] == []

    scopes = ["battle:create", "battle:read", "battle:execute", "vote:create"]
    create_token_response = client.post(
        f"/api/v1/admin/service-accounts/{service_account_id}/tokens",
        headers=admin_headers,
        json={"scopes": scopes, "expires_at": None},
    )
    assert create_token_response.status_code == 201
    create_token_payload = create_token_response.json()
    plaintext_token = create_token_payload["plaintext_token"]
    assert isinstance(plaintext_token, str)
    assert plaintext_token.startswith("osa_bot_")
    token = create_token_payload["token"]
    service_account_token_id = uuid.UUID(token["id"])
    assert uuid.UUID(token["service_account_id"]) == service_account_id
    assert token["scopes"] == scopes

    list_response = client.get(
        "/api/v1/admin/service-accounts",
        headers=admin_headers,
    )
    assert list_response.status_code == 200
    list_payload_text = json.dumps(list_response.json())
    assert "plaintext_token" not in list_payload_text
    assert plaintext_token not in list_payload_text

    bot_headers = {"Authorization": f"Bearer {plaintext_token}"}
    idempotency_key = f"task-12-{suffix}"
    create_wait_response = client.post(
        "/api/v1/bot/battles/create-and-wait",
        headers={**bot_headers, "Idempotency-Key": idempotency_key},
        json={"task_id": str(task.id), "timeout_seconds": 30},
    )
    assert create_wait_response.status_code == 200
    create_wait_payload = create_wait_response.json()
    battle_id = uuid.UUID(create_wait_payload["battle_id"])
    assert create_wait_payload["status"] == "completed"
    assert create_wait_payload["status_url"] == f"/api/v1/bot/battles/{battle_id}"
    result = create_wait_payload["result"]
    assert result["battle_id"] == str(battle_id)
    assert result["run_a"]["model_id"] == str(model_a.id)
    assert result["run_b"]["model_id"] == str(model_b.id)
    assert result["run_a"]["output_text"].endswith("task-12 output complete")
    assert result["run_b"]["output_text"].endswith("task-12 output complete")

    status_response = client.get(
        f"/api/v1/bot/battles/{battle_id}",
        headers=bot_headers,
    )
    assert status_response.status_code == 200
    assert status_response.json()["result"]["battle_id"] == str(battle_id)

    db_session.expire_all()
    stored_battle = db_session.get(Battle, battle_id)
    assert stored_battle is not None
    assert stored_battle.requester_service_account_id == service_account_id
    assert stored_battle.idempotency_key == idempotency_key
    assert stored_battle.metadata_json["requester_service_account_id"] == str(
        service_account_id
    )

    bot_metadata = {
        "external_run_id": f"task-12-judge-{suffix}",
        "judge": "simulated-external-judge",
        "score": 0.87,
        "rationale": "A is more faithful and fluent in the simulated judge run.",
    }
    vote_response = client.post(
        f"/api/v1/battles/{battle_id}/vote",
        headers=bot_headers,
        json={
            "winner": "A",
            "comment": "simulated external judge vote",
            "bot_metadata": bot_metadata,
        },
    )
    assert vote_response.status_code == 201
    vote_payload = vote_response.json()
    assert vote_payload["voter_actor_type"] == "bot"
    assert vote_payload["service_account_id"] == str(service_account_id)
    assert vote_payload["service_account_name"] == service_account["name"]
    assert vote_payload["service_account_token_id"] == str(service_account_token_id)
    assert vote_payload["bot_metadata"] == bot_metadata

    db_session.expire_all()
    stored_vote = db_session.execute(
        select(Vote).where(Vote.battle_id == battle_id)
    ).scalar_one()
    assert stored_vote.service_account_id == service_account_id
    assert stored_vote.service_account_token_id == service_account_token_id
    assert stored_vote.bot_metadata == bot_metadata

    get_leaderboard_refresher().refresh_once()

    default_leaderboard_response = client.get("/api/v1/leaderboard")
    assert default_leaderboard_response.status_code == 200
    default_leaderboard = default_leaderboard_response.json()
    default_counts = default_leaderboard["vote_source_counts"]
    assert default_counts["bot"] >= 1
    assert default_counts["total"] >= default_counts["bot"]
    default_rows = {row["model_id"]: row for row in default_leaderboard["models"]}
    assert default_rows[str(model_a.id)]["games_played"] == 1
    assert default_rows[str(model_b.id)]["games_played"] == 1

    human_leaderboard_response = client.get(
        "/api/v1/leaderboard?method=bt&judge_type=human"
    )
    bot_leaderboard_response = client.get(
        "/api/v1/leaderboard?method=bt&judge_type=bot"
    )
    assert human_leaderboard_response.status_code == 200
    assert bot_leaderboard_response.status_code == 200
    human_leaderboard = human_leaderboard_response.json()
    bot_leaderboard = bot_leaderboard_response.json()
    assert human_leaderboard["vote_source_counts"] != bot_leaderboard[
        "vote_source_counts"
    ]
    human_rows = {row["model_id"]: row for row in human_leaderboard["models"]}
    bot_rows = {row["model_id"]: row for row in bot_leaderboard["models"]}
    assert human_rows[str(model_a.id)]["games_played"] == 0
    assert human_rows[str(model_b.id)]["games_played"] == 0
    assert bot_rows[str(model_a.id)]["games_played"] == 1
    assert bot_rows[str(model_b.id)]["games_played"] == 1

    export_response = client.get(
        f"/api/v1/admin/export/votes.jsonl?service_account_id={service_account_id}",
        headers=admin_headers,
    )
    assert export_response.status_code == 200
    export_records = [
        json.loads(line) for line in export_response.text.splitlines() if line.strip()
    ]
    assert len(export_records) == 1
    export_record = export_records[0]
    assert export_record["battle_id"] == str(battle_id)
    assert export_record["voter_actor_type"] == "bot"
    assert export_record["service_account_id"] == str(service_account_id)
    assert export_record["service_account_name"] == service_account["name"]
    assert export_record["service_account_token_id"] == str(service_account_token_id)
    assert export_record["bot_metadata"] == bot_metadata
    assert "plaintext_token" not in export_record
    assert "token_hash" not in export_record
    assert "token_prefix" not in export_record

    _EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (_EVIDENCE_DIR / "task-12-full-bot-flow.txt").write_text(
        "Task 12 backend integrated bot workflow passed\n"
        f"service_account_id={service_account_id}\n"
        f"service_account_token_id={service_account_token_id}\n"
        f"battle_id={battle_id}\n"
        "plaintext_token=redacted; service-token prefix asserted in test only\n"
        f"bot_metadata={json.dumps(bot_metadata, sort_keys=True)}\n"
        f"default_vote_source_counts={default_counts}\n"
        f"human_vote_source_counts={human_leaderboard['vote_source_counts']}\n"
        f"bot_vote_source_counts={bot_leaderboard['vote_source_counts']}\n"
        "admin_export_fields="
        f"{sorted(export_record.keys())}\n",
        encoding="utf-8",
    )
