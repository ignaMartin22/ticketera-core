"""Única puerta de salida hacia Mercado Pago (SDK oficial). Sin lógica de negocio."""

import warnings

import requests
from django.conf import settings
from mercadopago import SDK
from mercadopago.config.request_options import RequestOptions
from mercadopago.errors.exceptions import MercadoPagoError as SdkError
from mercadopago.webhook.validator import InvalidWebhookSignatureError, WebhookSignatureValidator

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


def _payments(access_token):
    options = RequestOptions(connection_timeout=TIMEOUT_SECONDS, max_retries=0)
    return SDK(access_token, request_options=options).payment()


def get_payment(access_token, payment_id):
    """Consulta un pago con el access token del boliche que lo cobró."""
    payment = _payments(access_token)
    return _call(lambda _: payment.get(payment_id), None)


def search_payments(access_token, external_reference):
    """Pagos con ese external_reference, consultados con el access token del boliche."""
    payment = _payments(access_token)
    response = _call(lambda _: payment.search({"external_reference": external_reference}), None)
    results = response.get("results")
    if not isinstance(results, list):
        raise MercadoPagoError(200, response)
    return [result for result in results if isinstance(result, dict)]


def webhook_signature_is_valid(x_signature, x_request_id, data_id):
    """Valida la firma del aviso con la clave de la aplicación. Nunca levanta."""
    secret = settings.MP_WEBHOOK_SECRET
    if not secret or not x_signature:
        return False
    try:
        WebhookSignatureValidator.validate(x_signature, x_request_id, data_id, secret)
    except (InvalidWebhookSignatureError, ValueError, TypeError, AttributeError):
        return False
    return True


def create_preference(access_token, body):
    """Crea una preferencia de Checkout Pro con el access token del boliche."""
    options = RequestOptions(connection_timeout=TIMEOUT_SECONDS, max_retries=0)
    preference = SDK(access_token, request_options=options).preference()
    with warnings.catch_warnings():
        # El SDK avisa que notification_url está en desuso; lo usamos igual a propósito.
        warnings.simplefilter("ignore", DeprecationWarning)
        return _call(preference.create, body)
