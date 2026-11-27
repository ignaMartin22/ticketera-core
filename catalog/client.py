"""Credencial del front y datos del comprador que solo él puede asegurar.

El front (servidor de Next) manda `X-Front-Key` y, con ella, `X-Buyer-Ip` con la IP real del
navegador. Sin la clave el header de IP se ignora: nunca se confía en X-Forwarded-For ni en
REMOTE_ADDR para esto, porque el cliente los puede falsear o llegan siempre con la IP del front.
"""

import hmac
import ipaddress
import unicodedata

from django.conf import settings
from django.core.exceptions import ValidationError

FORBIDDEN_NAME_PARTS = ("://", "www.", "/", "\\", "<", ">", "@")


def front_trusted(request):
    """True si hay `FRONT_API_KEY` configurada y el request trae la misma en `X-Front-Key`."""
    expected = settings.FRONT_API_KEY
    if not expected:
        return False
    sent = request.headers.get("X-Front-Key", "")
    return hmac.compare_digest(sent.encode(), expected.encode())


def buyer_ip(request):
    """La IP del comprador según `X-Buyer-Ip`, solo si el request es del front; si no, cadena vacía."""
    if not front_trusted(request):
        return ""
    try:
        return str(ipaddress.ip_address(request.headers.get("X-Buyer-Ip", "").strip()))
    except ValueError:
        return ""


def clean_person_name(value, max_length):
    """Nombre normalizado (espacios colapsados) o `ValidationError("invalid_name")`.

    Rechaza enlaces (`://`, `www.`), `/`, `\\`, `<`, `>`, `@` y caracteres de control.
    """
    if not isinstance(value, str) or any(unicodedata.category(char) == "Cc" for char in value):
        raise ValidationError("invalid_name")
    name = " ".join(value.split())
    if not 1 <= len(name) <= max_length or any(part in name.lower() for part in FORBIDDEN_NAME_PARTS):
        raise ValidationError("invalid_name")
    return name
