"""Cifrado de los tokens de Mercado Pago en reposo (Fernet, clave MP_TOKEN_ENCRYPTION_KEY)."""

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class TokenCryptoError(Exception):
    """No se pudo cifrar o descifrar: sin clave, clave inválida o texto alterado."""


def _fernet():
    key = getattr(settings, "MP_TOKEN_ENCRYPTION_KEY", "")
    if not key:
        raise TokenCryptoError("MP_TOKEN_ENCRYPTION_KEY no está configurada")
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise TokenCryptoError("MP_TOKEN_ENCRYPTION_KEY no es una clave Fernet válida") from exc


def encrypt(text):
    return _fernet().encrypt(text.encode()).decode()


def decrypt(encrypted):
    try:
        return _fernet().decrypt(encrypted.encode()).decode()
    except (InvalidToken, ValueError, TypeError, AttributeError) as exc:
        raise TokenCryptoError("no se pudo descifrar el valor") from exc
