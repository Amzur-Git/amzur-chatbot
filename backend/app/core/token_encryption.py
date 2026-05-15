import base64
import hashlib
from functools import lru_cache

from app.core.config import settings


try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "The 'cryptography' package is required for OAuth token encryption. "
        "Install it in the backend environment."
    ) from exc


def _derive_fallback_key(secret: str) -> bytes:
    # Fernet keys are URL-safe base64 encoded 32-byte values.
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    raw_key = (settings.TOKEN_ENCRYPTION_KEY or "").strip()
    if raw_key:
        key_bytes = raw_key.encode("utf-8")
    else:
        key_bytes = _derive_fallback_key(settings.SECRET_KEY)

    try:
        return Fernet(key_bytes)
    except Exception as exc:
        raise RuntimeError(
            "Invalid TOKEN_ENCRYPTION_KEY. Provide a valid Fernet key generated with Fernet.generate_key()."
        ) from exc


def encrypt_token(plain_text: str) -> str:
    if not plain_text:
        return ""
    return _get_fernet().encrypt(plain_text.encode("utf-8")).decode("utf-8")


def decrypt_token(cipher_text: str) -> str:
    if not cipher_text:
        return ""
    try:
        return _get_fernet().decrypt(cipher_text.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Failed to decrypt stored OAuth token with current encryption key.") from exc
