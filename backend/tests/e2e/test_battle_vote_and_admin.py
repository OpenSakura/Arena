from __future__ import annotations

import json
from collections.abc import Iterable
import uuid

from fastapi.testclient import TestClient
import jwt
import pytest
from sqlalchemy import select

from app.api.routes.battles import _get_auth_battle_create_rate_limiter
from app.api.routes.votes import _get_auth_vote_submit_rate_limiter
from app.core.config import get_settings
from app.models.battle import Battle, Run
from app.models.model_registry import Model
from app.models.rating import ModelRating
from app.models.task import Task
from app.models.vote import Vote
from app.services.oidc import get_oidc_verifier
from app.utils.redis import get_rate_limit_redis_client


pytestmark = pytest.mark.e2e


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
    db_session, *, suffix: str
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

    battle = Battle(task_id=task.id, mode="jp2zh_ab", status="completed")
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


def _reset_backend_singletons() -> None:
    import app.db.session as session_module

    get_settings.cache_clear()
    get_oidc_verifier.cache_clear()
    get_rate_limit_redis_client.cache_clear()
    _get_auth_battle_create_rate_limiter.cache_clear()
    _get_auth_vote_submit_rate_limiter.cache_clear()
    session_module._engine = None
    session_module._SessionLocal = None


def _select_admin_claim_binding(token: str) -> tuple[str, str]:
    claims = jwt.decode(
        token,
        options={"verify_signature": False, "verify_aud": False},
        algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
    )
    if not isinstance(claims, dict):
        raise RuntimeError("OIDC token claims must be a JSON object")

    scope = claims.get("scope")
    if isinstance(scope, str):
        scope_values = [item for item in scope.replace(",", " ").split() if item]
        if scope_values:
            return "scope", scope_values[0]

    aud = claims.get("aud")
    if isinstance(aud, str) and aud:
        return "aud", aud
    if isinstance(aud, list):
        for item in aud:
            if isinstance(item, str) and item:
                return "aud", item

    sub = claims.get("sub")
    if isinstance(sub, str) and sub:
        return "sub", sub

    raise RuntimeError("Could not infer an admin claim binding from the OIDC token")


@pytest.fixture
def backend_client_with_token_claim_as_admin(
    configured_backend_env: None,
    monkeypatch: pytest.MonkeyPatch,
    authentik_token: str,
):
    del configured_backend_env

    claim_path, group_name = _select_admin_claim_binding(authentik_token)
    monkeypatch.setenv("OIDC_ADMIN_GROUP_CLAIM", claim_path)
    monkeypatch.setenv("OIDC_ADMIN_GROUP_NAME", group_name)
    _reset_backend_singletons()

    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def backend_client_with_turnstile_enabled(
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
    backend_client,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    authentik_token: str,
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

    auth_headers = {"Authorization": f"Bearer {authentik_token}"}

    create = backend_client.post(
        "/api/v1/battles",
        headers=auth_headers,
        json={"task_id": str(task.id)},
    )
    assert create.status_code == 201
    battle_id = create.json()["id"]

    with backend_client.stream(
        "GET", f"/api/v1/battles/{battle_id}/stream", headers=auth_headers
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
    backend_client,
    db_session,
    authentik_token: str,
) -> None:
    suffix = uuid.uuid4().hex[:8]
    battle_id, model_a_id, model_b_id = _seed_completed_battle(
        db_session, suffix=suffix
    )
    auth_headers = {"Authorization": f"Bearer {authentik_token}"}

    # Submit initial vote — returns reveal=null (vote is not yet revealed).
    first = backend_client.post(
        f"/api/v1/battles/{battle_id}/vote",
        headers=auth_headers,
        json={"winner": "A"},
    )
    assert first.status_code == 201
    first_payload = first.json()
    assert first_payload["battle_id"] == str(battle_id)
    assert first_payload["winner"] == "A"
    assert first_payload["reveal"] is None
    first_vote_id = first_payload["vote_id"]

    # Re-submitting with the same winner is idempotent.
    second = backend_client.post(
        f"/api/v1/battles/{battle_id}/vote",
        headers=auth_headers,
        json={"winner": "A"},
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["vote_id"] == first_vote_id
    assert second_payload["winner"] == "A"
    assert second_payload["reveal"] is None

    # Changing winner BEFORE reveal is allowed (vote update).
    updated = backend_client.post(
        f"/api/v1/battles/{battle_id}/vote",
        headers=auth_headers,
        json={"winner": "B"},
    )
    assert updated.status_code == 200
    assert updated.json()["vote_id"] == first_vote_id
    assert updated.json()["winner"] == "B"
    assert updated.json()["reveal"] is None

    # Reveal the vote — locks it and returns model identities.
    reveal = backend_client.post(
        f"/api/v1/battles/{battle_id}/vote/reveal",
        headers=auth_headers,
    )
    assert reveal.status_code == 200
    reveal_payload = reveal.json()
    assert reveal_payload["vote_id"] == first_vote_id
    assert reveal_payload["winner"] == "B"
    assert reveal_payload["reveal"]["A"]["model_id"] == str(model_a_id)
    assert reveal_payload["reveal"]["B"]["model_id"] == str(model_b_id)

    # After reveal, changing winner is rejected.
    conflicting = backend_client.post(
        f"/api/v1/battles/{battle_id}/vote",
        headers=auth_headers,
        json={"winner": "A"},
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
    backend_client,
    authentik_token: str,
) -> None:
    response = backend_client.get(
        "/api/v1/admin/models",
        headers={"Authorization": f"Bearer {authentik_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin group membership required"


def test_admin_routes_allow_access_when_configured_claim_matches_token(
    backend_client_with_token_claim_as_admin,
    authentik_token: str,
) -> None:
    response = backend_client_with_token_claim_as_admin.get(
        "/api/v1/admin/models",
        headers={"Authorization": f"Bearer {authentik_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("models"), list)


def test_battle_stream_vote_and_leaderboard_reflect_rating_updates(
    backend_client,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    authentik_token: str,
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

    auth_headers = {"Authorization": f"Bearer {authentik_token}"}

    create = backend_client.post(
        "/api/v1/battles",
        headers=auth_headers,
        json={"task_id": str(task.id)},
    )
    assert create.status_code == 201
    battle_id = create.json()["id"]

    with backend_client.stream(
        "GET", f"/api/v1/battles/{battle_id}/stream", headers=auth_headers
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

    before_a_rating, before_a_games = _rating_snapshot(db_session, run_a.model_id)
    before_b_rating, before_b_games = _rating_snapshot(db_session, run_b.model_id)

    vote = backend_client.post(
        f"/api/v1/battles/{battle_id}/vote",
        headers=auth_headers,
        json={"winner": "A"},
    )
    assert vote.status_code == 201
    vote_payload = vote.json()
    assert vote_payload["winner"] == "A"
    assert vote_payload["reveal"] is None

    # Reveal models — locks the vote and returns model identities.
    reveal = backend_client.post(
        f"/api/v1/battles/{battle_id}/vote/reveal",
        headers=auth_headers,
    )
    assert reveal.status_code == 200
    reveal_payload = reveal.json()
    assert reveal_payload["reveal"]["A"]["model_id"] == str(run_a.model_id)
    assert reveal_payload["reveal"]["B"]["model_id"] == str(run_b.model_id)

    # Elo leaderboard reads persisted ModelRating snapshots. In this e2e
    # harness the periodic refresher is disabled, so trigger one refresh cycle
    # explicitly before asserting the persisted leaderboard view.
    get_leaderboard_refresher().refresh_once()

    leaderboard = backend_client.get("/api/v1/leaderboard?method=elo")
    assert leaderboard.status_code == 200
    leaderboard_payload = leaderboard.json()

    rows_by_id = {row["model_id"]: row for row in leaderboard_payload["models"]}
    row_a = rows_by_id[str(run_a.model_id)]
    row_b = rows_by_id[str(run_b.model_id)]

    assert row_a["games_played"] == before_a_games + 1
    assert row_b["games_played"] == before_b_games + 1
    assert row_a["rating"] > before_a_rating
    assert row_b["rating"] < before_b_rating


def test_battle_create_requires_authentication_even_when_turnstile_is_enabled(
    backend_client_with_turnstile_enabled,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    authentik_token: str,
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
    missing_token = backend_client_with_turnstile_enabled.post(
        "/api/v1/battles",
        headers={"User-Agent": "arena-e2e-turnstile-missing"},
        json={},
    )
    assert missing_token.status_code == 401
    assert missing_token.json()["detail"] == "Authentication required"
    assert verification_calls == []

    # 2. Turnstile still does not rescue unauthenticated callers.
    valid_token = backend_client_with_turnstile_enabled.post(
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

    authed_battle = backend_client_with_turnstile_enabled.post(
        "/api/v1/battles",
        headers={"Authorization": f"Bearer {authentik_token}"},
        json={},
    )
    assert authed_battle.status_code == 201
