from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
from types import SimpleNamespace

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from app.core import crypto
from app.db.base import Base
from app.models.auth_session import AuthSession, OAuthLoginState
from app.models.user import User
from app.services import auth_session


_HASH_SECRET = "test-auth-session-hash-secret"


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type: JSONB, _compiler: object, **_kw: object) -> str:
    return "JSON"


@pytest.fixture(autouse=True)
def _crypto_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    settings = SimpleNamespace(arena_master_key=Fernet.generate_key().decode("ascii"), arena_master_key_old="")
    monkeypatch.setattr(crypto, "get_settings", lambda: settings)
    crypto.reset_fernet()
    yield
    crypto.reset_fernet()


@pytest.fixture()
def settings() -> SimpleNamespace:
    return SimpleNamespace(
        auth_session_hash_secret=_HASH_SECRET,
        auth_session_max_age_seconds=3600,
        oidc_login_state_max_age_seconds=600,
    )


@pytest.fixture()
def db_session(tmp_path) -> Iterator[Session]:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'auth-session.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _user(db: Session) -> User:
    user = User(oidc_issuer="https://issuer.example", oidc_sub="user-1")
    db.add(user)
    db.flush()
    return user


def _assert_hmac(raw_token: str, stored_hash: str) -> None:
    expected_hash = hmac.new(
        _HASH_SECRET.encode("utf-8"),
        raw_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert stored_hash == expected_hash
    assert len(stored_hash) == 64
    assert stored_hash != raw_token


def _serialized_row(row: object) -> str:
    return json.dumps(
        {column.name: str(getattr(row, column.name)) for column in row.__table__.columns},
        sort_keys=True,
    )


def test_generate_urlsafe_token_uses_at_least_32_random_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_nbytes: list[int] = []

    def fake_token_urlsafe(nbytes: int) -> str:
        requested_nbytes.append(nbytes)
        return "abc-DEF_123"

    monkeypatch.setattr(auth_session.secrets, "token_urlsafe", fake_token_urlsafe)

    token = auth_session.generate_urlsafe_token()

    assert token == "abc-DEF_123"
    assert requested_nbytes == [32]
    assert re.fullmatch(r"[A-Za-z0-9_-]+", token)


def test_hash_auth_token_returns_hmac_digest_only(settings: SimpleNamespace) -> None:
    stored_hash = auth_session.hash_auth_token("raw-session-token", settings=settings)

    _assert_hmac("raw-session-token", stored_hash)


def test_hash_auth_token_requires_configured_secret() -> None:
    settings = SimpleNamespace(
        auth_session_hash_secret=" ",
        auth_session_max_age_seconds=3600,
        oidc_login_state_max_age_seconds=600,
    )

    with pytest.raises(RuntimeError, match="AUTH_SESSION_HASH_SECRET"):
        auth_session.hash_auth_token("raw-token", settings=settings)


def test_constant_time_verify_uses_compare_digest(
    monkeypatch: pytest.MonkeyPatch,
    settings: SimpleNamespace,
) -> None:
    stored_hash = auth_session.hash_auth_token("raw-token", settings=settings)
    original_compare_digest = hmac.compare_digest
    calls: list[tuple[str, str]] = []

    def fake_compare_digest(candidate_hash: str, stored_value: str) -> bool:
        calls.append((candidate_hash, stored_value))
        return original_compare_digest(candidate_hash, stored_value)

    monkeypatch.setattr(auth_session.hmac, "compare_digest", fake_compare_digest)

    assert auth_session.constant_time_verify_auth_token(
        "raw-token", stored_hash, settings=settings
    )
    assert calls == [(stored_hash, stored_hash)]


def test_create_and_load_session_stores_hashes_and_updates_last_seen(
    db_session: Session,
    settings: SimpleNamespace,
) -> None:
    user = _user(db_session)
    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)

    created = auth_session.create_auth_session(
        db_session,
        user=user,
        claims={"groups": ["arena_admin"], "email": "user@example.test"},
        settings=settings,
        now=now,
    )
    db_session.commit()
    loaded = auth_session.load_auth_session(
        db_session,
        session_token=created.session_token,
        settings=settings,
        now=now + timedelta(seconds=5),
    )

    assert loaded is not None
    assert loaded.user_id == user.id
    assert loaded.oidc_issuer == user.oidc_issuer
    assert loaded.oidc_sub == user.oidc_sub
    assert loaded.claims == {"groups": ["arena_admin"], "email": "user@example.test"}
    assert auth_session.load_user_for_auth_session(
        db_session, auth_session=loaded
    ) == user
    _assert_hmac(created.session_token, loaded.session_token_hash)
    assert len(loaded.csrf_token_hash) == 64
    assert loaded.csrf_token_hash != loaded.session_token_hash
    assert created.session_token not in _serialized_row(loaded)

    auth_session.refresh_auth_session_last_seen(
        db_session,
        auth_session=loaded,
        now=now + timedelta(minutes=2),
    )

    assert loaded.last_seen_at == now + timedelta(minutes=2)


def test_load_session_rejects_expired_and_revoked_rows(
    db_session: Session,
    settings: SimpleNamespace,
) -> None:
    user = _user(db_session)
    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    expired = auth_session.create_auth_session(
        db_session,
        user=user,
        expires_at=now - timedelta(seconds=1),
        settings=settings,
        now=now - timedelta(hours=1),
    )
    active = auth_session.create_auth_session(db_session, user=user, settings=settings, now=now)
    db_session.commit()

    assert auth_session.load_auth_session(
        db_session, session_token=expired.session_token, settings=settings, now=now
    ) is None
    assert auth_session.revoke_auth_session(
        db_session,
        session_token=active.session_token,
        settings=settings,
        now=now + timedelta(seconds=10),
    )
    db_session.commit()

    assert auth_session.load_auth_session(
        db_session,
        session_token=active.session_token,
        settings=settings,
        now=now + timedelta(seconds=11),
    ) is None


def test_expire_auth_session_marks_session_unloadable(
    db_session: Session,
    settings: SimpleNamespace,
) -> None:
    user = _user(db_session)
    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    created = auth_session.create_auth_session(db_session, user=user, settings=settings, now=now)
    db_session.commit()

    assert auth_session.expire_auth_session(
        db_session,
        session_token=created.session_token,
        settings=settings,
        now=now + timedelta(seconds=1),
    )
    db_session.commit()

    assert auth_session.load_auth_session(
        db_session,
        session_token=created.session_token,
        settings=settings,
        now=now + timedelta(seconds=2),
    ) is None


def test_delete_auth_session_rows(
    db_session: Session,
    settings: SimpleNamespace,
) -> None:
    user = _user(db_session)
    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    expired = auth_session.create_auth_session(
        db_session,
        user=user,
        expires_at=now - timedelta(seconds=1),
        settings=settings,
        now=now - timedelta(hours=1),
    )
    active = auth_session.create_auth_session(db_session, user=user, settings=settings, now=now)
    expired_id = expired.row.id
    active_id = active.row.id
    db_session.commit()

    assert auth_session.delete_expired_auth_sessions(db_session, now=now) == 1
    assert db_session.get(AuthSession, expired_id) is None
    assert db_session.get(AuthSession, active_id) is not None
    assert auth_session.delete_auth_session(
        db_session, session_token=active.session_token, settings=settings
    )
    assert db_session.get(AuthSession, active_id) is None


def test_auth_session_token_helpers_fail_closed(
    db_session: Session,
    settings: SimpleNamespace,
) -> None:
    assert auth_session.load_auth_session(
        db_session, session_token=None, settings=settings
    ) is None
    assert not auth_session.revoke_auth_session(
        db_session, session_token=None, settings=settings
    )
    assert not auth_session.expire_auth_session(
        db_session, session_token=None, settings=settings
    )
    assert not auth_session.delete_auth_session(
        db_session, session_token=None, settings=settings
    )


def test_create_login_state_encrypts_callback_secrets_and_sanitizes_return_to(
    db_session: Session,
    settings: SimpleNamespace,
) -> None:
    created = auth_session.create_oauth_login_state(
        db_session,
        nonce="raw-nonce",
        code_verifier="raw-code-verifier",
        return_to="https://evil.example/path",
        settings=settings,
        now=datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc),
    )
    db_session.commit()

    row = db_session.execute(select(OAuthLoginState)).scalar_one()
    assert row.return_to == "/"
    _assert_hmac(created.state, row.state_hash)
    _assert_hmac(created.login_binding_token, row.login_binding_hash)
    assert crypto.decrypt_secret(row.nonce) == "raw-nonce"
    assert crypto.decrypt_secret(row.code_verifier) == "raw-code-verifier"
    serialized = _serialized_row(row)
    assert created.state not in serialized
    assert created.login_binding_token not in serialized
    assert "raw-nonce" not in serialized
    assert "raw-code-verifier" not in serialized


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "/"),
        ("", "/"),
        ("/battles?x=1#vote", "/battles?x=1#vote"),
        ("leaderboard", "/leaderboard"),
        ("//evil.example/path", "/"),
        ("https://evil.example/path", "/"),
    ],
)
def test_sanitize_return_to(value: str | None, expected: str) -> None:
    assert auth_session.sanitize_return_to(value) == expected


def test_login_binding_mismatch_fails_closed(
    db_session: Session,
    settings: SimpleNamespace,
) -> None:
    created = auth_session.create_oauth_login_state(
        db_session,
        nonce="nonce",
        code_verifier="verifier",
        settings=settings,
    )
    db_session.commit()

    assert auth_session.consume_oauth_login_state(
        db_session,
        state=created.state,
        login_binding_token=None,
        settings=settings,
    ) is None
    assert auth_session.consume_oauth_login_state(
        db_session,
        state=created.state,
        login_binding_token="wrong-binding-token",
        settings=settings,
    ) is None
    assert auth_session.load_oauth_login_state(
        db_session, state=created.state, settings=settings
    ).consumed_at is None


def test_expired_login_state_cannot_be_consumed(
    db_session: Session,
    settings: SimpleNamespace,
) -> None:
    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    created = auth_session.create_oauth_login_state(
        db_session,
        nonce="nonce",
        code_verifier="verifier",
        expires_at=now - timedelta(seconds=1),
        settings=settings,
        now=now - timedelta(minutes=5),
    )
    db_session.commit()

    consumed = auth_session.consume_oauth_login_state(
        db_session,
        state=created.state,
        login_binding_token=created.login_binding_token,
        settings=settings,
        now=now,
    )

    assert consumed is None


def test_expire_login_state_marks_state_unusable(
    db_session: Session,
    settings: SimpleNamespace,
) -> None:
    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    created = auth_session.create_oauth_login_state(
        db_session,
        nonce="nonce",
        code_verifier="verifier",
        settings=settings,
        now=now,
    )
    db_session.commit()

    assert auth_session.expire_oauth_login_state(
        db_session,
        state=created.state,
        settings=settings,
        now=now + timedelta(seconds=1),
    )
    db_session.commit()

    assert auth_session.consume_oauth_login_state(
        db_session,
        state=created.state,
        login_binding_token=created.login_binding_token,
        settings=settings,
        now=now + timedelta(seconds=2),
    ) is None


def test_replayed_login_state_cannot_be_consumed_twice(
    db_session: Session,
    settings: SimpleNamespace,
) -> None:
    created = auth_session.create_oauth_login_state(
        db_session,
        nonce="nonce",
        code_verifier="verifier",
        return_to="/after-login",
        settings=settings,
    )
    db_session.commit()

    first = auth_session.consume_oauth_login_state(
        db_session,
        state=created.state,
        login_binding_token=created.login_binding_token,
        settings=settings,
    )
    second = auth_session.consume_oauth_login_state(
        db_session,
        state=created.state,
        login_binding_token=created.login_binding_token,
        settings=settings,
    )

    assert first is not None
    assert first.nonce == "nonce"
    assert first.code_verifier == "verifier"
    assert first.return_to == "/after-login"
    assert second is None


def test_atomic_consumption_allows_exactly_one_callback_success(
    tmp_path,
    settings: SimpleNamespace,
) -> None:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'atomic-auth-session.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as setup_db:
        created = auth_session.create_oauth_login_state(
            setup_db,
            nonce="nonce",
            code_verifier="verifier",
            settings=settings,
        )
        setup_db.commit()

    with session_factory() as first_db, session_factory() as second_db:
        first_row = auth_session.load_oauth_login_state(
            first_db, state=created.state, settings=settings
        )
        second_row = auth_session.load_oauth_login_state(
            second_db, state=created.state, settings=settings
        )
        assert first_row is not None
        assert second_row is not None
        first = auth_session.consume_oauth_login_state(
            first_db,
            state=created.state,
            login_binding_token=created.login_binding_token,
            settings=settings,
        )
        first_db.commit()
        second = auth_session.consume_oauth_login_state(
            second_db,
            state=created.state,
            login_binding_token=created.login_binding_token,
            settings=settings,
        )
        second_db.commit()

    assert [first is not None, second is not None].count(True) == 1
    with session_factory() as verify_db:
        row = verify_db.execute(select(OAuthLoginState)).scalar_one()
        assert row.consumed_at is not None
        _assert_hmac(created.state, row.state_hash)
        _assert_hmac(created.login_binding_token, row.login_binding_hash)
    engine.dispose()


def test_delete_login_state_rows(
    db_session: Session,
    settings: SimpleNamespace,
) -> None:
    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    expired = auth_session.create_oauth_login_state(
        db_session,
        nonce="old",
        code_verifier="old-verifier",
        expires_at=now - timedelta(seconds=1),
        settings=settings,
        now=now - timedelta(minutes=5),
    )
    active = auth_session.create_oauth_login_state(
        db_session,
        nonce="new",
        code_verifier="new-verifier",
        expires_at=now + timedelta(minutes=5),
        settings=settings,
        now=now,
    )
    expired_id = expired.row.id
    active_id = active.row.id
    db_session.commit()

    assert auth_session.delete_expired_oauth_login_states(db_session, now=now) == 1
    assert db_session.get(OAuthLoginState, expired_id) is None
    assert db_session.get(OAuthLoginState, active_id) is not None
    assert auth_session.delete_oauth_login_state(
        db_session, login_state_id=active_id
    )
    assert db_session.get(OAuthLoginState, active_id) is None


def test_model_metadata_contains_auth_tables() -> None:
    assert "auth_sessions" in Base.metadata.tables
    assert "oauth_login_states" in Base.metadata.tables
    assert AuthSession.__table__.c.claims.default is not None
