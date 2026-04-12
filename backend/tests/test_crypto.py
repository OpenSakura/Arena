from __future__ import annotations

from cryptography.fernet import Fernet
import pytest

from app.core import crypto


class _Settings:
    def __init__(self, arena_master_key: str, arena_master_key_old: str = "") -> None:
        self.arena_master_key = arena_master_key
        self.arena_master_key_old = arena_master_key_old


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
