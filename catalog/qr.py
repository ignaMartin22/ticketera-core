"""Token firmado que va dentro del QR de una entrada.

Formato: ``<uuid en base64url sin padding>.<firma>``. La firma es un
HMAC-SHA256 derivado de ``settings.SECRET_KEY`` con un salt propio, truncado
a 128 bits. El token es determinista y no vence.
"""

import base64
import binascii
from uuid import UUID

from django.utils.crypto import constant_time_compare, salted_hmac

SALT = "catalog.ticket-qr"
SIGNATURE_BYTES = 16  # 128 bits
MAX_TOKEN_LENGTH = 200
_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


class InvalidTicketToken(Exception):
    pass


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _signature(id_part: str) -> str:
    digest = salted_hmac(SALT, id_part, algorithm="sha256").digest()
    return _b64encode(digest[:SIGNATURE_BYTES])


def sign_ticket(ticket_id: UUID) -> str:
    id_part = _b64encode(ticket_id.bytes)
    return f"{id_part}.{_signature(id_part)}"


def verify_ticket(token: str) -> UUID:
    try:
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
            raise InvalidTicketToken("Token inválido.")
        id_part, sep, signature = token.partition(".")
        if (
            not sep
            or not id_part
            or not signature
            or not set(id_part) <= _ALPHABET
            or not set(signature) <= _ALPHABET
        ):
            raise InvalidTicketToken("Token inválido.")
        if not constant_time_compare(signature, _signature(id_part)):
            raise InvalidTicketToken("Token inválido.")
        return UUID(bytes=_b64decode(id_part))
    except InvalidTicketToken:
        raise
    except (ValueError, TypeError, binascii.Error) as exc:
        raise InvalidTicketToken("Token inválido.") from exc
