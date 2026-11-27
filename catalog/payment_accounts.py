"""Cuentas de cobro del boliche, para que su administración las vincule desde el panel.

- Mercado Pago se conecta por OAuth (`mp_connect`): acá solo se informa el estado.
- Macro y Nave usan las credenciales que entrega el banco: se cargan, reemplazan o quitan acá.

Las claves secretas nunca vuelven en una respuesta: para cambiarlas hay que cargarlas de nuevo.
"""

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_safe

from . import crypto, mp_connect
from .gateways import CREDENTIAL_FIELDS
from .gateways.base import save_account
from .models import GatewayAccount, MercadoPagoAccount, Membership, VenueIntegration
from .staff_auth import error, json_body, venue_staff_view

ADMIN = Membership.Role.ADMIN
MAX_FIELD = 200


def gateways_ready():
    """Sin la URL pública del back o la clave de cifrado no se pueden guardar credenciales."""
    return bool(settings.PUBLIC_API_URL and settings.MP_TOKEN_ENCRYPTION_KEY)


def _iso(value):
    return value.isoformat() if value else None


def _mercadopago(venue):
    account = MercadoPagoAccount.objects.filter(venue=venue).first()
    return {
        "available": mp_connect.is_configured(),
        "connected": account is not None,
        "needsReconnect": account.needs_reconnect if account else False,
        "direct": account.direct if account else False,
        "liveMode": account.live_mode if account else None,
        "connectedAt": _iso(account.connected_at) if account else None,
    }


def _gateway(venue, provider, accounts, integrations):
    account = accounts.get(provider)
    integration = integrations.get(provider)
    return {
        "available": gateways_ready(),
        "connected": account is not None,
        "liveMode": account.live_mode if account else None,
        # El ID de comercio o el POS ID: no es secreto y ayuda a saber qué cuenta quedó.
        "accountId": integration.account_id if account and integration else "",
        "connectedAt": _iso(account.connected_at) if account else None,
        "fields": [
            {"name": name, "label": label, "secret": secret, "required": required}
            for name, label, secret, required in CREDENTIAL_FIELDS[provider]
        ],
    }


def status_data(venue):
    accounts = {a.provider: a for a in GatewayAccount.objects.filter(venue=venue)}
    integrations = {i.provider: i for i in VenueIntegration.objects.filter(venue=venue, provider__in=CREDENTIAL_FIELDS)}
    return {
        "mercadopago": _mercadopago(venue),
        **{p: _gateway(venue, p, accounts, integrations) for p in CREDENTIAL_FIELDS},
    }


@csrf_exempt
@require_safe
@never_cache
@venue_staff_view(ADMIN)
def account_list(request, venue, user, membership):
    return JsonResponse(status_data(venue))


def _clean(provider, data):
    """Las credenciales limpias y si son de producción. Levanta ValueError con el código de error."""
    credentials = {}
    for name, _label, _secret, required in CREDENTIAL_FIELDS[provider]:
        value = data.get(name, "")
        if value is None:
            value = ""
        if not isinstance(value, str) or len(value) > MAX_FIELD:
            raise ValueError("invalid_credentials")
        value = value.strip()
        if required and not value:
            raise ValueError("missing_credentials")
        credentials[name] = value
    live = data.get("liveMode", False)
    if not isinstance(live, bool):
        raise ValueError("invalid_credentials")
    return credentials, live


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
@never_cache
@venue_staff_view(ADMIN)
def account_resource(request, venue, user, membership, provider):
    if provider not in CREDENTIAL_FIELDS:
        return error("not_found", 404)
    if request.method == "DELETE":
        with transaction.atomic():
            GatewayAccount.objects.filter(venue=venue, provider=provider).delete()
            VenueIntegration.objects.filter(venue=venue, provider=provider).update(
                status=VenueIntegration.Status.DISCONNECTED, account_id="", account_label="", connected_at=None,
            )
        return HttpResponse(status=204)

    if not gateways_ready():
        return error("gateway_not_configured", 503)
    data, problem = json_body(request)
    if problem:
        return problem
    try:
        credentials, live = _clean(provider, data)
    except ValueError as exc:
        return error(str(exc), 400)
    account_id = credentials["comercio" if provider == "macro" else "pos_id"]
    try:
        with transaction.atomic():
            save_account(venue, provider, credentials, live_mode=live)
            VenueIntegration.objects.update_or_create(venue=venue, provider=provider, defaults={
                "status": VenueIntegration.Status.CONNECTED, "account_id": account_id[:100],
                "account_label": "Producción" if live else "Entorno de prueba",
                "connected_at": timezone.now(), "expires_at": None, "last_error": "",
            })
    except crypto.TokenCryptoError:
        return error("gateway_not_configured", 503)
    return JsonResponse(status_data(venue))
