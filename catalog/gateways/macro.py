"""Macro Click de Pago (botón integrado, plataforma PlusPagos).

El comprador no va a una URL que devuelve una API: hay que mandarle desde el navegador un
formulario con los datos cifrados (AES-256-CBC con la clave secreta del comercio) y una firma
SHA-256 que incluye la IP del comprador. Por eso el link de pago apunta a una página del core
(`pay_page`) que arma ese formulario con la IP de quien la abre y lo envía sola.

Para confirmar no se usa el aviso (no viene firmado): se consulta la transacción en la API de
PlusPagos con las credenciales del boliche.

⚠️ El formato sale de integraciones de terceros (plugin de WooCommerce y scripts de la API de
PlusPagos). Hay que validarlo contra la documentación técnica que entrega Macro.
"""

import base64
import hashlib
import os
from datetime import timedelta

import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from django.conf import settings
from django.utils import timezone

from .base import GatewayError, credentials, money

PROVIDER = "macro"
TIMEOUT_SECONDS = 10.0
# Campos de las credenciales. La API de consulta usa el ID de comercio como "guid" y la clave
# secreta como "frase" (a confirmar con el banco).
FIELDS = ("comercio", "secret", "sucursal")
# EstadoId / estado de PlusPagos: 3 es "transacción realizada".
APPROVED_STATE = 3


def _key(phrase):
    """La clave AES: la frase repetida o cortada a 32 bytes, como hace PlusPagos."""
    raw = phrase.encode()
    if not raw:
        raise ValueError("clave vacía")
    while len(raw) < 32:
        raw += raw
    return raw[:32]


def encrypt(text, phrase):
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    data = padder.update(text.encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_key(phrase)), modes.CBC(iv)).encryptor()
    return base64.b64encode(iv + encryptor.update(data) + encryptor.finalize()).decode()


def decrypt(encoded, phrase):
    raw = base64.b64decode(encoded)
    decryptor = Cipher(algorithms.AES(_key(phrase)), modes.CBC(raw[:16])).decryptor()
    data = decryptor.update(raw[16:]) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return (unpadder.update(data) + unpadder.finalize()).decode()


def amount_text(total):
    """El monto en centavos, sin separadores: $11.000,00 es "1100000"."""
    return str(int(money(total) * 100))


def signature(ip, comercio, sucursal, amount, secret):
    return hashlib.sha256(f"{ip}*{comercio}*{sucursal}*{amount}*{secret}".encode()).hexdigest()


def button_url(account):
    return settings.MACRO_CLICK_URL if account.live_mode else settings.MACRO_CLICK_TEST_URL


def form_fields(account, charge, total, ip, return_url, description):
    """Los campos del formulario que el navegador manda al botón de Macro."""
    data = credentials(account)
    comercio, secret, sucursal = data["comercio"], data["secret"], data.get("sucursal", "")
    amount = amount_text(total)
    return {
        "CallbackSuccess": encrypt(return_url, secret),
        "CallbackCancel": encrypt(return_url, secret),
        "Comercio": comercio,
        "SucursalComercio": encrypt(sucursal, secret),
        "Hash": signature(ip, comercio, sucursal, amount, secret),
        "TransaccionComercioId": charge.reference,
        "Monto": encrypt(amount, secret),
        "Producto[0]": description[:100],
    }


def _api_url(account):
    return (settings.MACRO_API_URL if account.live_mode else settings.MACRO_API_TEST_URL).rstrip("/")


def _json(response):
    try:
        data = response.json()
    except ValueError as exc:
        raise GatewayError(f"Macro respondió {response.status_code} sin JSON") from exc
    if response.status_code != 200 or not isinstance(data, dict):
        raise GatewayError(f"Macro respondió {response.status_code}")
    return data


def fetch(account, charge):
    """Consulta el cobro en PlusPagos. Devuelve (estado, aprobado, monto), o None si no aparece."""
    data = credentials(account)
    base = _api_url(account)
    try:
        session = _json(requests.post(
            f"{base}/sesion", json={"guid": data["comercio"], "frase": data["secret"]}, timeout=TIMEOUT_SECONDS,
        ))
        token = session.get("data")
        if not isinstance(token, str) or not token:
            raise GatewayError("Macro no devolvió un token de sesión")
        day = timedelta(days=1)
        since, until = timezone.localdate(charge.created_at) - day, timezone.localdate() + day
        params = {"FechaDesde": since.strftime("%Y/%m/%d"), "FechaHasta": until.strftime("%Y/%m/%d")}
        if data.get("sucursal"):
            params["NumeroSucursal"] = data["sucursal"]
        found = _json(requests.get(
            f"{base}/transactions", params=params, headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT_SECONDS,
        ))
    except requests.RequestException as exc:
        raise GatewayError("Macro no respondió") from exc
    body = found.get("data")
    rows = body.get("transacciones") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise GatewayError("Macro devolvió una lista de transacciones inesperada")
    mine = [r for r in rows if isinstance(r, dict) and str(r.get("transaccionComercioId")) == charge.reference]
    if not mine:
        return None
    # Si hubo varios intentos, cuenta el aprobado.
    row = next((r for r in mine if _state(r) == APPROVED_STATE), mine[0])
    gross = row.get("montoBruto")
    return str(row.get("estado")), _state(row) == APPROVED_STATE, money(gross if gross is not None else row.get("monto"))


def _state(row):
    try:
        return int(row.get("estado"))
    except (TypeError, ValueError):
        return None
