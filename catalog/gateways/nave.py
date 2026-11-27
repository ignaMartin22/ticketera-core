"""Nave, la plataforma de cobros de Naranja X y Galicia (checkout online).

Flujo: token OAuth entre servidores con las credenciales del boliche, una "intención de pago"
(`payment_request`) que devuelve el `checkout_url` al que va el comprador, y un aviso a nuestra
URL cuando hay un pago. El aviso no viene firmado: la URL lleva un secreto por cobro y, además,
siempre se consulta la intención de pago con las credenciales del boliche.

⚠️ El formato sale del plugin oficial de Nave para WooCommerce y de un SDK no oficial. Hay que
validarlo con las credenciales de prueba que entrega Nave.
"""

from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.cache import cache

from .base import GatewayError, credentials, money

PROVIDER = "nave"
TIMEOUT_SECONDS = 15.0
FIELDS = ("client_id", "client_secret", "pos_id")
APPROVED_STATUS = "SUCCESS_PROCESSED"


def _auth_url(account):
    return settings.NAVE_AUTH_URL if account.live_mode else settings.NAVE_AUTH_TEST_URL


def _api_url(account):
    return (settings.NAVE_API_URL if account.live_mode else settings.NAVE_API_TEST_URL).rstrip("/")


def _json(response):
    try:
        data = response.json()
    except ValueError as exc:
        raise GatewayError(f"Nave respondió {response.status_code} sin JSON") from exc
    if response.status_code not in (200, 201) or not isinstance(data, dict):
        raise GatewayError(f"Nave respondió {response.status_code}")
    return data


def _token(account):
    # La clave cambia si se recargan las credenciales: un token viejo no se reusa.
    key = f"nave-token:{account.pk}:{account.updated_at.timestamp()}"
    token = cache.get(key)
    if token:
        return token
    data = credentials(account)
    try:
        response = _json(requests.post(_auth_url(account), json={
            "client_id": data["client_id"], "client_secret": data["client_secret"], "audience": settings.NAVE_AUDIENCE,
        }, timeout=TIMEOUT_SECONDS))
    except requests.RequestException as exc:
        raise GatewayError("Nave no respondió") from exc
    token = response.get("access_token")
    if not isinstance(token, str) or not token:
        raise GatewayError("Nave no devolvió un token")
    try:
        expires_in = int(response.get("expires_in") or 3600)
    except (TypeError, ValueError):
        expires_in = 3600
    cache.set(key, token, max(expires_in - 60, 60))
    return token


def _request(account, method, path, body=None):
    try:
        response = requests.request(
            method, f"{_api_url(account)}{path}", json=body, timeout=TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {_token(account)}", "Accept": "application/json"},
        )
    except requests.RequestException as exc:
        raise GatewayError("Nave no respondió") from exc
    return _json(response)


def checkout_host_allowed(url):
    parts = urlparse(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and any(host == h or host.endswith(f".{h}") for h in settings.NAVE_CHECKOUT_HOSTS)


def create(account, order, items, total, return_url, notify_url, seconds_left):
    """Crea la intención de pago. Devuelve (id, checkout_url)."""
    event = order.event
    body = {
        "external_payment_id": order.payment_ref,
        "seller": {"pos_id": credentials(account)["pos_id"]},
        "transactions": [{
            "amount": {"currency": "ARS", "value": format(money(total), ".2f")},
            "products": [
                {
                    "name": f"{item.ticket_type.name} · {event.title}"[:100],
                    "description": event.title[:100],
                    "quantity": item.quantity,
                    "unit_price": {"currency": "ARS", "value": format(item.unit_price + item.unit_service_fee, ".2f")},
                }
                for item in items
            ],
        }],
        "buyer": {
            "user_id": f"guest_{order.payment_ref}",
            "name": order.buyer_name,
            "user_email": order.buyer_email,
            "phone": order.buyer_phone,
        },
        "additional_info": {"callback_url": return_url, "notification_url": notify_url},
        "duration_time": max(60, int(seconds_left)),
    }
    response = _request(account, "POST", "/payment_request/ecommerce", body)
    # El plugin oficial lee id y checkout_url arriba; el SDK los lee dentro de "data".
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    request_id = data.get("payment_request_id") or data.get("id")
    checkout_url = data.get("checkout_url")
    if not request_id or not isinstance(checkout_url, str) or not checkout_host_allowed(checkout_url):
        raise GatewayError("Nave devolvió una intención de pago incompleta")
    return str(request_id)[:64], checkout_url


def fetch(account, charge):
    """Consulta la intención de pago. Devuelve (estado, aprobado, monto), o None si no es de este pedido."""
    data = _request(account, "GET", f"/payment_requests/{charge.reference}")
    # Los cobros iniciados antes de `payment_ref` mandaron el UUID del pedido como referencia.
    if str(data.get("external_payment_id")) not in (charge.order.payment_ref, str(charge.order_id)):
        return None
    status = data.get("status")
    name = status.get("name") if isinstance(status, dict) else status
    transactions = data.get("transactions")
    first = transactions[0] if isinstance(transactions, list) and transactions else {}
    amount = first.get("amount") if isinstance(first, dict) else None
    value = money(amount.get("value")) if isinstance(amount, dict) else None
    currency = amount.get("currency") if isinstance(amount, dict) else None
    approved = name == APPROVED_STATUS and currency == "ARS"
    return str(name or ""), approved, value
