from __future__ import annotations

import uuid

import httpx
import pytest


pytestmark = pytest.mark.e2e


def test_backend_e2e_confidential_oidc_settings(configured_backend_env: None) -> None:
    del configured_backend_env

    from app.core.config import get_settings

    settings = get_settings()

    assert settings.oidc_client_id == "arena-e2e-client"
    assert settings.oidc_client_secret == "arena-e2e-confidential-client-secret"
    assert settings.public_base_url == "http://localhost:13000"
    assert settings.oidc_redirect_path == "/api/v1/auth/callback"
    assert settings.auth_session_hash_secret == "arena-e2e-auth-session-hash-secret"
    assert settings.oidc_issuer == "http://localhost:19000/application/o/arena-e2e/"
    assert settings.oidc_admin_group_claim == "groups"
    assert settings.oidc_admin_group_name == "arena_admin"


def test_authentik_provider_is_confidential_for_e2e(
    authentik_provider_config: dict[str, object],
) -> None:
    assert authentik_provider_config["client_type"] == "confidential"
    assert authentik_provider_config["client_id"] == "arena-e2e-client"
    assert authentik_provider_config["client_secret_configured"] is True
    assert authentik_provider_config["client_secret_matches_expected"] is True
    assert authentik_provider_config["redirect_uris"] == [
        {
            "matching_mode": "strict",
            "url": "http://localhost:13000/api/v1/auth/callback",
        }
    ]


def test_authentik_token_endpoint_requires_confidential_secret(
    e2e_stack,
    authentik_token: str,
) -> None:
    del e2e_stack

    assert authentik_token.count(".") == 2

    response = httpx.post(
        "http://localhost:19000/application/o/token/",
        data={"grant_type": "client_credentials", "scope": "openid"},
        auth=("arena-e2e-client", "wrong-arena-e2e-secret"),
        timeout=10.0,
    )

    assert response.status_code in {400, 401}


def test_authenticated_me_uses_backend_session(
    authenticated_backend_client,
) -> None:
    response = authenticated_backend_client.client.get("/api/v1/me")

    assert response.status_code == 200
    payload = response.json()

    assert payload["authenticated"] is True
    assert payload["user"] is not None
    assert (
        payload["user"]["oidc_issuer"]
        == "http://localhost:19000/application/o/arena-e2e"
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
        model_name=f"model-a-{suffix}",
        base_url="http://example.invalid",
        enabled=True,
        visibility="public",
    )
    model_b = Model(
        display_name=f"Model B {suffix}",
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
    authenticated_backend_client,
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
        model_name=f"model-a-{suffix}",
        base_url="http://example.invalid",
        enabled=True,
        visibility="public",
    )
    model_b = Model(
        display_name=f"Model B {suffix}",
        model_name=f"model-b-{suffix}",
        base_url="http://example.invalid",
        enabled=True,
        visibility="public",
    )

    db_session.add_all([task, model_a, model_b])
    db_session.commit()

    first = authenticated_backend_client.client.post(
        "/api/v1/battles",
        headers=authenticated_backend_client.headers,
        json={},
    )
    assert first.status_code == 201

    second = authenticated_backend_client.client.post(
        "/api/v1/battles",
        headers=authenticated_backend_client.headers,
        json={},
    )
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
        model_name=f"vote-model-a-{suffix}",
        base_url="http://example.invalid",
        enabled=True,
        visibility="public",
    )
    model_b = Model(
        display_name=f"Vote Model B {suffix}",
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


def test_private_battle_read_requires_authentication(
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
        source_text=f"private-battle-source-{suffix}",
    )
    model_a = Model(
        display_name=f"Private Battle Model A {suffix}",
        model_name=f"private-battle-model-a-{suffix}",
        base_url="http://example.invalid",
        enabled=True,
        visibility="public",
    )
    model_b = Model(
        display_name=f"Private Battle Model B {suffix}",
        model_name=f"private-battle-model-b-{suffix}",
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

    response = backend_client.get(f"/api/v1/battles/{battle.id}")
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


def test_removed_vote_reveal_route_returns_404(
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
        source_text=f"vote-reveal-removed-source-{suffix}",
    )
    model_a = Model(
        display_name=f"Vote Reveal Removed Model A {suffix}",
        model_name=f"vote-reveal-removed-model-a-{suffix}",
        base_url="http://example.invalid",
        enabled=True,
        visibility="public",
    )
    model_b = Model(
        display_name=f"Vote Reveal Removed Model B {suffix}",
        model_name=f"vote-reveal-removed-model-b-{suffix}",
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
    assert response.status_code == 404


def test_authenticated_vote_rate_limit_is_enforced_with_redis(
    authenticated_backend_client,
    db_session,
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
        model_name=f"vote-model-a-{suffix}",
        base_url="http://example.invalid",
        enabled=True,
        visibility="public",
    )
    model_b = Model(
        display_name=f"Vote Model B {suffix}",
        model_name=f"vote-model-b-{suffix}",
        base_url="http://example.invalid",
        enabled=True,
        visibility="public",
    )

    db_session.add_all([task_a, task_b, model_a, model_b])
    db_session.flush()

    requester_user_id = authenticated_backend_client.user_id

    battle_a = Battle(
        task_id=task_a.id,
        mode="jp2zh_ab",
        status="completed",
        metadata_json={"requester_user_id": requester_user_id},
    )
    battle_b = Battle(
        task_id=task_b.id,
        mode="jp2zh_ab",
        status="completed",
        metadata_json={"requester_user_id": requester_user_id},
    )
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

    first = authenticated_backend_client.client.post(
        f"/api/v1/battles/{battle_a.id}/vote",
        headers=authenticated_backend_client.headers,
        json={"winner": "A"},
    )
    assert first.status_code == 201

    second = authenticated_backend_client.client.post(
        f"/api/v1/battles/{battle_b.id}/vote",
        headers=authenticated_backend_client.headers,
        json={"winner": "B"},
    )
    assert second.status_code == 429
    assert second.json()["detail"] == "Too many vote submissions"
    assert second.headers.get("Retry-After") == "60"
