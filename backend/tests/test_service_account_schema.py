from __future__ import annotations

from collections.abc import Iterator
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Battle, ServiceAccount, ServiceAccountToken, Task, User, Vote


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type: JSONB, _compiler: object, **_kw: object) -> str:
    return "JSON"


@pytest.fixture()
def db_session(tmp_path) -> Iterator[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'schema.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _human_user(*, issuer: str = "https://issuer.example", sub: str | None = None) -> User:
    return User(oidc_issuer=issuer, oidc_sub=sub or f"human-{uuid.uuid4()}")


def _create_service_account(
    db: Session,
    *,
    token_hash: str = "sha256:token-a",
) -> tuple[ServiceAccount, ServiceAccountToken, User]:
    service_account_id = uuid.uuid4()
    bot_user = User(
        oidc_issuer="system:service-account",
        oidc_sub=f"service-account:{service_account_id}",
        actor_type="bot",
    )
    db.add(bot_user)
    db.flush()

    service_account = ServiceAccount(
        id=service_account_id,
        name=f"bot-{service_account_id}",
        bot_user_id=bot_user.id,
    )
    token = ServiceAccountToken(
        service_account_id=service_account_id,
        token_prefix="osa_bot_test",
        token_hash=token_hash,
        scopes=["vote:create"],
    )
    db.add_all([service_account, token])
    db.commit()
    db.refresh(service_account)
    db.refresh(token)
    db.refresh(bot_user)
    return service_account, token, bot_user


def _create_task(db: Session) -> Task:
    task = Task(source_text="原文")
    db.add(task)
    db.flush()
    return task


def test_human_user_actor_type_defaults_to_human(db_session: Session) -> None:
    user = _human_user()
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    assert user.actor_type == "human"


def test_user_actor_type_has_deterministic_index() -> None:
    index = next(
        item for item in User.__table__.indexes if item.name == "ix_users_actor_type"
    )

    assert [column.name for column in index.columns] == ["actor_type"]


def test_bot_user_service_account_and_token_rows_use_hashed_metadata_only(
    db_session: Session,
) -> None:
    service_account, token, bot_user = _create_service_account(db_session)

    assert bot_user.actor_type == "bot"
    assert bot_user.oidc_issuer == "system:service-account"
    assert bot_user.oidc_sub == f"service-account:{service_account.id}"
    assert service_account.bot_user_id == bot_user.id
    assert token.service_account_id == service_account.id
    assert token.token_prefix == "osa_bot_test"
    assert token.token_hash == "sha256:token-a"
    assert token.scopes == ["vote:create"]
    token_columns = set(ServiceAccountToken.__table__.columns.keys())
    assert {"token_prefix", "token_hash"} <= token_columns
    assert not {"plaintext_token", "token_plaintext", "encrypted_token"} & token_columns


def test_bot_vote_stores_service_account_token_and_metadata(
    db_session: Session,
) -> None:
    service_account, token, bot_user = _create_service_account(db_session)
    task = _create_task(db_session)
    battle = Battle(
        task_id=task.id,
        requester_service_account_id=service_account.id,
        idempotency_key="vote-metadata-case",
    )
    db_session.add(battle)
    db_session.flush()

    vote = Vote(
        battle_id=battle.id,
        winner="A",
        voter_user_id=bot_user.id,
        service_account_id=service_account.id,
        service_account_token_id=token.id,
        bot_metadata={"judge": "auto", "score": 0.97},
    )
    db_session.add(vote)
    db_session.commit()
    db_session.refresh(vote)

    assert vote.service_account_id == service_account.id
    assert vote.service_account_token_id == token.id
    assert vote.bot_metadata == {"judge": "auto", "score": 0.97}


def test_duplicate_token_hash_is_rejected(db_session: Session) -> None:
    service_account, token, _bot_user = _create_service_account(
        db_session, token_hash="sha256:duplicate"
    )
    duplicate = ServiceAccountToken(
        service_account_id=service_account.id,
        token_prefix="osa_bot_dupe",
        token_hash=token.token_hash,
        scopes=["vote:create"],
    )

    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_duplicate_non_null_service_account_idempotency_is_rejected(
    db_session: Session,
) -> None:
    service_account, _token, _bot_user = _create_service_account(db_session)
    task = _create_task(db_session)
    first_battle = Battle(
        task_id=task.id,
        requester_service_account_id=service_account.id,
        idempotency_key="create-battle-1",
    )
    db_session.add(first_battle)
    db_session.commit()

    duplicate_battle = Battle(
        task_id=task.id,
        requester_service_account_id=service_account.id,
        idempotency_key="create-battle-1",
    )
    db_session.add(duplicate_battle)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    index = next(
        item
        for item in Battle.__table__.indexes
        if item.name == "ix_battles_service_account_idempotency_unique"
    )
    assert index.unique is True
    assert str(index.dialect_options["postgresql"]["where"]) == (
        "requester_service_account_id IS NOT NULL AND idempotency_key IS NOT NULL"
    )
