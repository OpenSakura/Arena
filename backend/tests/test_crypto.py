from __future__ import annotations

from cryptography.fernet import Fernet
import pytest

from app.core import crypto


class _Settings:
    def __init__(
        self,
        arena_master_key: str,
        arena_master_key_old: str = "",
    ) -> None:
        self.arena_master_key = arena_master_key
        self.arena_master_key_old = arena_master_key_old


@pytest.fixture(autouse=True)
def _clear_fernet_cache() -> None:
    crypto.reset_fernet()


def test_encrypt_secret_requires_master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto, "get_settings", lambda: _Settings(""))

    with pytest.raises(RuntimeError, match="ARENA_MASTER_KEY is not set"):
        crypto.encrypt_secret("hello")


def test_encrypt_secret_requires_valid_master_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(crypto, "get_settings", lambda: _Settings("invalid-key"))

    with pytest.raises(RuntimeError, match="ARENA_MASTER_KEY is invalid Fernet key"):
        crypto.encrypt_secret("hello")


def test_encrypt_and_decrypt_secret_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(crypto, "get_settings", lambda: _Settings(key))

    token = crypto.encrypt_secret("sensitive-value")
    plaintext = crypto.decrypt_secret(token)

    assert plaintext == "sensitive-value"
    assert token != "sensitive-value"


def test_encrypt_secret_is_nondeterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(crypto, "get_settings", lambda: _Settings(key))

    token_one = crypto.encrypt_secret("same-input")
    token_two = crypto.encrypt_secret("same-input")

    assert token_one != token_two


def test_decrypt_secret_rejects_tokens_from_other_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(crypto, "get_settings", lambda: _Settings(key))

    foreign_token = Fernet(Fernet.generate_key()).encrypt(b"payload").decode("ascii")

    with pytest.raises(crypto.SecretDecryptionError):
        crypto.decrypt_secret(foreign_token)


def test_old_key_required_valid_fernet_key(monkeypatch: pytest.MonkeyPatch) -> None:
    current_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(
        crypto,
        "get_settings",
        lambda: _Settings(current_key, arena_master_key_old="not-a-fernet-key"),
    )

    with pytest.raises(
        RuntimeError, match="ARENA_MASTER_KEY_OLD is invalid Fernet key"
    ):
        crypto.encrypt_secret("hello")


def test_decrypt_secret_falls_back_to_old_key(monkeypatch: pytest.MonkeyPatch) -> None:
    old_key = Fernet.generate_key().decode("ascii")
    current_key = Fernet.generate_key().decode("ascii")

    token_encrypted_with_old = Fernet(old_key).encrypt(b"legacy-value").decode("ascii")

    monkeypatch.setattr(
        crypto,
        "get_settings",
        lambda: _Settings(current_key, arena_master_key_old=old_key),
    )

    plaintext = crypto.decrypt_secret(token_encrypted_with_old)
    assert plaintext == "legacy-value"


def test_new_encryption_uses_current_key(monkeypatch: pytest.MonkeyPatch) -> None:
    old_key = Fernet.generate_key().decode("ascii")
    current_key = Fernet.generate_key().decode("ascii")

    monkeypatch.setattr(
        crypto,
        "get_settings",
        lambda: _Settings(current_key, arena_master_key_old=old_key),
    )

    token = crypto.encrypt_secret("new-value")

    assert Fernet(current_key).decrypt(token.encode("ascii")) == b"new-value"
    with pytest.raises(Exception):
        Fernet(old_key).decrypt(token.encode("ascii"))


def test_old_key_not_accepted_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    old_key = Fernet.generate_key().decode("ascii")
    current_key = Fernet.generate_key().decode("ascii")

    token_encrypted_with_old = Fernet(old_key).encrypt(b"legacy-value").decode("ascii")

    monkeypatch.setattr(
        crypto,
        "get_settings",
        lambda: _Settings(current_key),
    )

    with pytest.raises(crypto.SecretDecryptionError):
        crypto.decrypt_secret(token_encrypted_with_old)
