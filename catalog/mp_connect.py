"""Conexión del boliche con su cuenta de Mercado Pago (OAuth) y renovación de tokens.

Para cobrar en nombre de un boliche, el código de pagos debe usar `get_access_token(venue)`:
devuelve el access token en claro y lo renueva solo cuando faltan menos de 30 días.
"""

import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from . import crypto, mp
from .models import MercadoPagoAccount, Membership, VenueIntegration
from .staff_auth import error, venue_staff_view

STATE_SALT = "catalog.mp-connect-state"
STATE_MAX_AGE = 10 * 60
RENEW_BEFORE = timedelta(days=30)
ADMIN = Membership.Role.ADMIN


class MercadoPagoNotConnected(Exception):
    """El boliche no tiene una cuenta de Mercado Pago utilizable."""


def is_configured():
    return all(
        getattr(settings, name, "")
        for name in ("MP_APP_ID", "MP_CLIENT_SECRET", "MP_REDIRECT_URI", "MP_TOKEN_ENCRYPTION_KEY")
    )


Status = VenueIntegration.Status
MERCADOPAGO = VenueIntegration.Provider.MERCADOPAGO


def _integrations(venue_ids):
    return VenueIntegration.objects.filter(venue_id__in=venue_ids, provider=MERCADOPAGO)


def mark_connected(account):
    """Refleja la conexión en el estado del boliche. Nunca recibe ni guarda tokens."""
    VenueIntegration.objects.update_or_create(
        venue_id=account.venue_id, provider=MERCADOPAGO,
        defaults={
            "status": Status.CONNECTED,
            "account_id": str(account.mp_user_id),
            "connected_at": timezone.now(),
            "expires_at": account.expires_at,
            "last_error": "",
        },
    )


def mark_renewed(account):
    _integrations([account.venue_id]).update(expires_at=account.expires_at, updated_at=timezone.now())


def mark_expired(venue_ids):
    """Renovación rechazada de forma definitiva o aviso de desautorización: hay que reconectar."""
    _integrations(venue_ids).update(status=Status.EXPIRED, updated_at=timezone.now())


def mark_error(venue, reason):
    VenueIntegration.objects.update_or_create(
        venue=venue, provider=MERCADOPAGO,
        defaults={"status": Status.ERROR, "last_error": reason[:500]},
    )


def mark_disconnected(venue):
    _integrations([venue.pk]).update(
        status=Status.DISCONNECTED, account_id="", connected_at=None, expires_at=None, last_error="",
        updated_at=timezone.now(),
    )


def _due(account):
    return account.expires_at - timezone.now() < RENEW_BEFORE


def _apply_tokens(account, data):
    """Copia a la cuenta lo que devolvió Mercado Pago, con los tokens cifrados."""
    account.access_token = crypto.encrypt(data["access_token"])
    account.refresh_token = crypto.encrypt(data["refresh_token"])
    account.expires_at = timezone.now() + timedelta(seconds=int(data["expires_in"]))
    account.needs_reconnect = False
    if data.get("public_key"):
        account.public_key = data["public_key"]
    if "live_mode" in data:
        account.live_mode = bool(data["live_mode"])


def _transient(error):
    """Red, 429 o 5xx: no dice nada sobre la validez del refresh token."""
    return error.status in (0, None, 429) or (isinstance(error.status, int) and error.status >= 500)


def renew_account(account_id):
    """Renueva los tokens de una cuenta si les quedan menos de 30 días.

    Bloquea la fila: cada renovación rota el refresh_token y dos simultáneas romperían la
    conexión. Devuelve (cuenta, ok). Un rechazo definitivo (400/401, refresh token ilegible
    o respuesta incompleta) marca `needs_reconnect`; un fallo transitorio (red, 429, 5xx)
    deja la cuenta igual para reintentar en el próximo uso o corrida.
    """
    with transaction.atomic():
        account = MercadoPagoAccount.objects.select_for_update().get(pk=account_id)
        if account.needs_reconnect:
            return account, False
        if not _due(account):
            return account, True  # otra renovación se adelantó
        try:
            _apply_tokens(account, mp.refresh_tokens(crypto.decrypt(account.refresh_token)))
        except mp.MercadoPagoError as exc:
            account = MercadoPagoAccount.objects.get(pk=account_id)
            if not _transient(exc):
                account.needs_reconnect = True
                account.save(update_fields=["needs_reconnect", "updated_at"])
                mark_expired([account.venue_id])
            return account, False
        except (crypto.TokenCryptoError, KeyError, TypeError, ValueError):
            account = MercadoPagoAccount.objects.get(pk=account_id)
            account.needs_reconnect = True
            account.save(update_fields=["needs_reconnect", "updated_at"])
            mark_expired([account.venue_id])
            return account, False
        account.save()
        mark_renewed(account)
        return account, True


def get_access_token(venue):
    """Access token en claro del boliche, renovado si le quedan menos de 30 días.

    Mientras el token no haya vencido lo devuelve siempre: `needs_reconnect` es un aviso
    para el panel, no un corte. Si le quedan menos de 30 días y no hay que reconectar,
    intenta renovar antes (un fallo de renovación no impide devolver el token vigente).
    Levanta MercadoPagoNotConnected si no hay cuenta o si el token ya venció y no pudo
    renovarse.
    """
    account = MercadoPagoAccount.objects.filter(venue=venue).first()
    if account is None:
        raise MercadoPagoNotConnected(venue.slug)
    if _due(account) and not account.needs_reconnect:
        account, _ = renew_account(account.pk)
    if account.expires_at <= timezone.now():
        raise MercadoPagoNotConnected(venue.slug)
    try:
        return crypto.decrypt(account.access_token)
    except crypto.TokenCryptoError as exc:
        raise MercadoPagoNotConnected(venue.slug) from exc


def not_configured():
    return error("mp_not_configured", 503)


@csrf_exempt
@require_POST
@never_cache
@venue_staff_view(ADMIN)
def connect(request, venue, user, membership):
    if not is_configured():
        return not_configured()
    state = signing.dumps(
        {"v": venue.pk, "u": user.pk, "n": secrets.token_urlsafe(16)}, salt=STATE_SALT,
    )
    return JsonResponse({"authorizationUrl": mp.authorization_url(state)})


def _return(**params):
    separator = "&" if "?" in settings.MP_CONNECT_RETURN_URL else "?"
    return HttpResponseRedirect(settings.MP_CONNECT_RETURN_URL + separator + urlencode(params))


def _fail(reason):
    return _return(mp="error", reason=reason)


@require_safe
@never_cache
def oauth_callback(request):
    if not is_configured():
        return _fail("not_configured")
    code = request.GET.get("code")
    if not code:
        return _fail("denied" if request.GET.get("error") else "invalid_state")
    try:
        payload = signing.loads(request.GET.get("state", ""), salt=STATE_SALT, max_age=STATE_MAX_AGE)
        venue_id, user_id = payload["v"], payload["u"]
    except (signing.BadSignature, KeyError, TypeError):
        return _fail("invalid_state")
    membership = (
        Membership.objects.select_related("venue")
        .filter(
            venue_id=venue_id, user_id=user_id, role=ADMIN,
            venue__is_active=True, user__is_active=True,
        )
        .first()
    )
    if membership is None:
        return _fail("invalid_state")
    venue = membership.venue
    try:
        data = mp.exchange_code(code)
        account = MercadoPagoAccount.objects.filter(venue=venue).first() or MercadoPagoAccount(venue=venue)
        _apply_tokens(account, data)
        account.mp_user_id = int(data["user_id"])
        account.public_key = data.get("public_key") or ""
        account.live_mode = bool(data.get("live_mode", False))
        with transaction.atomic():
            account.save()
            account.refresh_from_db()
            mark_connected(account)
    except (mp.MercadoPagoError, crypto.TokenCryptoError, KeyError, TypeError, ValueError):
        mark_error(venue, "exchange_failed")
        return _fail("exchange_failed")
    return _return(mp="connected", venue=venue.slug)


def _iso(value):
    return value.isoformat() if value else None


@csrf_exempt
@require_http_methods(["GET", "DELETE"])
@never_cache
@venue_staff_view(ADMIN)
def account_status(request, venue, user, membership):
    account = MercadoPagoAccount.objects.filter(venue=venue).first()
    if request.method == "DELETE":
        if account is not None:
            with transaction.atomic():
                account.delete()
                mark_disconnected(venue)
        return HttpResponse(status=204)
    return JsonResponse({
        "connected": account is not None,
        "needsReconnect": account.needs_reconnect if account else False,
        "mpUserId": account.mp_user_id if account else None,
        "liveMode": account.live_mode if account else None,
        "connectedAt": _iso(account.connected_at) if account else None,
        "expiresAt": _iso(account.expires_at) if account else None,
    })

