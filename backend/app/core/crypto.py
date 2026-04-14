"""app.core.crypto

Small helper for encrypting/decrypting secrets stored in Postgres.

Notes:
- The MVP stores encrypted provider API keys in Postgres.
- The master key comes from env var `ARENA_MASTER_KEY`.
- Set `ARENA_MASTER_KEY_OLD` during key rotation so existing secrets
  (encrypted under the old key) can still be decrypted while new secrets
  are always encrypted under the current key.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings


class SecretDecryptionError(RuntimeError):
    """Raised when a stored secret cannot be decrypted."""


@lru_cache(maxsize=1)
def _get_fernet() -> MultiFernet:
    settings = get_settings()
    if not settings.arena_master_key:
        raise RuntimeError("ARENA_MASTER_KEY is not set")
    try:
        current = Fernet(settings.arena_master_key)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("ARENA_MASTER_KEY is invalid Fernet key") from exc

    if not settings.arena_master_key_old:
        return MultiFernet([current])

    try:
        old = Fernet(settings.arena_master_key_old)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("ARENA_MASTER_KEY_OLD is invalid Fernet key") from exc

    return MultiFernet([current, old])


def reset_fernet() -> None:
    """Clear the cached Fernet instance (e.g. after key rotation or in tests)."""
    _get_fernet.cache_clear()


def encrypt_secret(plaintext: str) -> str:
    f = _get_fernet()
    token = f.encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_secret(token: str) -> str:
    f = _get_fernet()
    try:
        plaintext = f.decrypt(token.encode("ascii"))
    except InvalidToken as exc:
        raise SecretDecryptionError(
            "Failed to decrypt secret — the master key may have changed"
        ) from exc
    return plaintext.decode("utf-8")
