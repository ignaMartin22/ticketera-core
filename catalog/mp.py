"""Única puerta de salida hacia Mercado Pago (SDK oficial). Sin lógica de negocio."""

import requests
from django.conf import settings
from mercadopago import SDK
from mercadopago.config.request_options import RequestOptions
from mercadopago.errors.exceptions import MercadoPagoError as SdkError

TIMEOUT_SECONDS = 10.0


class MercadoPagoError(Exception):
    """Mercado Pago respondió con un error (o no respondió). Lleva el status y el cuerpo."""

    def __init__(self, status, body=None):
        super().__init__(f"Mercado Pago respondió {status}")
        self.status = status
        self.body = body


def _oauth():
    # El SDK exige un access token para armar la cabecera Authorization; en /oauth/token
    # la autenticación real viaja en el cuerpo (client_id y client_secret).
    options = RequestOptions(connection_timeout=TIMEOUT_SECONDS, max_retries=0)
    return SDK(settings.MP_CLIENT_SECRET, request_options=options).oauth()


def _call(function, body):
    try:
        result = function(body)
    except (requests.RequestException, SdkError) as exc:
        raise MercadoPagoError(0, str(exc)) from exc
    status, response = result.get("status"), result.get("response")
    if status not in (200, 201) or not isinstance(response, dict):
        raise MercadoPagoError(status, response)
    return response


def authorization_url(state):
    return _oauth().get_authorization_url(settings.MP_APP_ID, settings.MP_REDIRECT_URI, state)


def exchange_code(code):
    oauth = _oauth()
    body = {
        "client_id": settings.MP_APP_ID,
        "client_secret": settings.MP_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.MP_REDIRECT_URI,
    }
    if settings.MP_TEST_TOKENS:
        body["test_token"] = True
    return _call(oauth.create, body)


def refresh_tokens(refresh_token):
    body = {
        "client_id": settings.MP_APP_ID,
        "client_secret": settings.MP_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    return _call(_oauth().refresh, body)
