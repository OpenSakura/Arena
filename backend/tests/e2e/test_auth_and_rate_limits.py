from __future__ import annotations

import uuid

import pytest


pytestmark = pytest.mark.e2e


def test_authenticated_me_uses_authentik_jwt(
    backend_client,
    authentik_token: str,
) -> None:
    response = backend_client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {authentik_token}"},
    )

    assert response.status_code == 200
    payload = response.json()

    assert payload["authenticated"] is True
    assert payload["user"] is not None
    assert (
        payload["user"]["oidc_issuer"]
        == "http://localhost:19000/application/o/arena-e2e/"
    )
    assert payload["user"]["oidc_sub"]


def test_battle_create_requires_authentication(
    backend_client,
    db_session,
) -> None:
    from app.models.model_registry import Model
    from app.models.task import Task

    suffix = uuid.uuid4().hex[:8]

    task = Task(
        source_lang="ja",
        target_lang="zh",
        source_text=f"source-{suffix}",
    )
    model_a = Model(
        display_name=f"Model A {suffix}",
        provider_type="openai",
        model_name=f"model-a-{suffix}",
        base_url="http://example.invalid",
        enabled=True,
        visibility="public",
    )
    model_b = Model(
        display_name=f"Model B {suffix}",
        provider_type="openai",
        model_name=f"model-b-{suffix}",
        base_url="http://example.invalid",
        enabled=True,
        visibility="public",
    )

    db_session.add_all([task, model_a, model_b])
    db_session.commit()

    response = backend_client.post("/api/v1/battles", json={})
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


def test_authenticated_battle_rate_limit_is_enforced_with_redis(
    backend_client,
    db_session,
    authentik_token: str,
) -> None:
    from app.models.model_registry import Model
    from app.models.task import Task

    suffix = uuid.uuid4().hex[:8]

    task = Task(
        source_lang="ja",
        target_lang="zh",
        source_text=f"source-{suffix}",
    )
    model_a = Model(
        display_name=f"Model A {suffix}",
        provider_type="openai",
        model_name=f"model-a-{suffix}",
        base_url="http://example.invalid",
        enabled=True,
        visibility="public",
    )
    model_b = Model(
        display_name=f"Model B {suffix}",
        provider_type="openai",
        model_name=f"model-b-{suffix}",
        base_url="http://example.invalid",
        enabled=True,
        visibility="public",
    )

    db_session.add_all([task, model_a, model_b])
    db_session.commit()

    auth_headers = {"Authorization": f"Bearer {authentik_token}"}

    first = backend_client.post("/api/v1/battles", headers=auth_headers, json={})
    assert first.status_code == 201

    second = backend_client.post("/api/v1/battles", headers=auth_headers, json={})
    assert second.status_code == 429
    assert second.json()["detail"] == "Too many battle creation requests"
    assert second.headers.get("Retry-After") == "60"


def test_vote_submit_requires_authentication(
    backend_client,
    db_session,
) -> None:
    from app.models.battle import Battle, Run
    from app.models.model_registry import Model
    from app.models.task import Task

    suffix = uuid.uuid4().hex[:8]

    task = Task(
        source_lang="ja",
        target_lang="zh",
        source_text=f"vote-source-{suffix}",
    )
    model_a = Model(
        display_name=f"Vote Model A {suffix}",
        provider_type="openai",
        model_name=f"vote-model-a-{suffix}",
        base_url="http://example.invalid",
        enabled=True,
        visibility="public",
    )
    model_b = Model(
        display_name=f"Vote Model B {suffix}",
        provider_type="openai",
        model_name=f"vote-model-b-{suffix}",
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
                output_text="A output",
            ),
            Run(
                battle_id=battle.id,
                side="B",
                model_id=model_b.id,
                output_text="B output",
            ),
        ]
    )
    db_session.commit()

    response = backend_client.post(
        f"/api/v1/battles/{battle.id}/vote",
        json={"winner": "A"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


def test_vote_reveal_requires_authentication(
    backend_client,
    db_session,
) -> None:
    from app.models.battle import Battle, Run
    from app.models.model_registry import Model
    from app.models.task import Task

    suffix = uuid.uuid4().hex[:8]

    task = Task(
        source_lang="ja",
        target_lang="zh",
        source_text=f"vote-reveal-source-{suffix}",
    )
    model_a = Model(
        display_name=f"Vote Reveal Model A {suffix}",
        provider_type="openai",
        model_name=f"vote-reveal-model-a-{suffix}",
        base_url="http://example.invalid",
        enabled=True,
        visibility="public",
    )
    model_b = Model(
        display_name=f"Vote Reveal Model B {suffix}",
        provider_type="openai",
        model_name=f"vote-reveal-model-b-{suffix}",
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
                output_text="A output",
            ),
            Run(
                battle_id=battle.id,
                side="B",
                model_id=model_b.id,
                output_text="B output",
            ),
        ]
    )
    db_session.commit()

    response = backend_client.post(f"/api/v1/battles/{battle.id}/vote/reveal")
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


def test_authenticated_vote_rate_limit_is_enforced_with_redis(
    backend_client,
    db_session,
    authentik_token: str,
) -> None:
    from app.models.battle import Battle, Run
    from app.models.model_registry import Model
    from app.models.task import Task

    suffix = uuid.uuid4().hex[:8]

    task_a = Task(
        source_lang="ja",
        target_lang="zh",
        source_text=f"vote-source-a-{suffix}",
    )
    task_b = Task(
        source_lang="ja",
        target_lang="zh",
        source_text=f"vote-source-b-{suffix}",
    )
    model_a = Model(
        display_name=f"Vote Model A {suffix}",
        provider_type="openai",
        model_name=f"vote-model-a-{suffix}",
        base_url="http://example.invalid",
        enabled=True,
        visibility="public",
    )
    model_b = Model(
        display_name=f"Vote Model B {suffix}",
        provider_type="openai",
        model_name=f"vote-model-b-{suffix}",
        base_url="http://example.invalid",
        enabled=True,
        visibility="public",
    )

    db_session.add_all([task_a, task_b, model_a, model_b])
    db_session.flush()

    battle_a = Battle(task_id=task_a.id, mode="jp2zh_ab", status="completed")
    battle_b = Battle(task_id=task_b.id, mode="jp2zh_ab", status="completed")
    db_session.add_all([battle_a, battle_b])
    db_session.flush()

    db_session.add_all(
        [
            Run(
                battle_id=battle_a.id,
                side="A",
                model_id=model_a.id,
                output_text="A output",
            ),
            Run(
                battle_id=battle_a.id,
                side="B",
                model_id=model_b.id,
                output_text="B output",
            ),
            Run(
                battle_id=battle_b.id,
                side="A",
                model_id=model_a.id,
                output_text="A output",
            ),
            Run(
                battle_id=battle_b.id,
                side="B",
                model_id=model_b.id,
                output_text="B output",
            ),
        ]
    )
    db_session.commit()

    auth_headers = {"Authorization": f"Bearer {authentik_token}"}

    first = backend_client.post(
        f"/api/v1/battles/{battle_a.id}/vote",
        headers=auth_headers,
        json={"winner": "A"},
    )
    assert first.status_code == 201

    second = backend_client.post(
        f"/api/v1/battles/{battle_b.id}/vote",
        headers=auth_headers,
        json={"winner": "B"},
    )
    assert second.status_code == 429
    assert second.json()["detail"] == "Too many vote submissions"
    assert second.headers.get("Retry-After") == "60"
