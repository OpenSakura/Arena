"""app.core.crypto

Small helper for encrypting/decrypting secrets stored in Postgres.

Notes:
- The MVP stores encrypted provider API keys in Postgres.
- The master key comes from env var `ARENA_MASTER_KEY`.
- Supports key rotation via MultiFernet: set `ARENA_MASTER_KEY` to the new key
  and `ARENA_MASTER_KEY_OLD` (optional) to the previous key.  New encryptions
  use the new key; decryptions try new key first, then fall back to old.
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
        keys = [Fernet(settings.arena_master_key)]
    except (TypeError, ValueError) as exc:
        raise RuntimeError("ARENA_MASTER_KEY is invalid Fernet key") from exc

    # Support key rotation: if an old key is configured, add it as a fallback
    # for decrypting secrets encrypted with the previous key.
    old_key = settings.arena_master_key_old or ""
    if old_key:
        try:
            keys.append(Fernet(old_key))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("ARENA_MASTER_KEY_OLD is invalid Fernet key") from exc

    return MultiFernet(keys)


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
            "Failed to decrypt secret — the master key may have been rotated "
            "without configuring the old key as a fallback"
        ) from exc
    return plaintext.decode("utf-8")
