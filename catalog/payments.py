"""Link de pago (order de Checkout Pro) de un pedido, creado con el token del boliche."""

import json
import math
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import crypto, gateways, mp, mp_connect
from .models import GatewayAccount, MercadoPagoAccount, Order

CENT = Decimal("0.01")
EXCLUDED_PAYMENT_TYPES = ("ticket", "atm")
MERCADOPAGO = "mercadopago"


def _error(code, status):
    return JsonResponse({"error": code}, status=status)


def _locked_order(venue_slug, event_slug, order_id):
    """El pedido bloqueado y listo para pagar, o la respuesta de error."""
    order = (
        Order.objects.select_for_update(of=("self",))
        .select_related("event__venue")
        .filter(pk=order_id, event__slug=event_slug, event__venue__slug=venue_slug)
        .first()
    )
    if order is None:
        return None, _error("not_found", 404)
    if order.paid_at is not None:
        return None, _error("order_already_paid", 409)
    if order.expires_at <= timezone.now():
        return None, _error("order_expired", 409)
    return order, None


def _gateway_payment(venue_slug, event_slug, order_id, provider):
    if not (settings.FRONT_BASE_URL and settings.PUBLIC_API_URL and getattr(settings, "MP_TOKEN_ENCRYPTION_KEY", "")):
        return _error("gateway_not_configured", 503)
    with transaction.atomic():
        order, error = _locked_order(venue_slug, event_slug, order_id)
        if error is not None:
            return error
        try:
            init_point = gateways.start(order, provider)
        except GatewayAccount.DoesNotExist:
            return _error("venue_not_connected", 409)
        except (gateways.GatewayError, crypto.TokenCryptoError, KeyError):
            return _error("gateway_unavailable", 502)
    return JsonResponse({"initPoint": init_point, "provider": provider})


def _money(value):
    """Monto como texto con 2 decimales, el formato que usa la API de Orders."""
    return str(Decimal(value).quantize(CENT))


def idempotency_key(order):
    """Misma clave en cada intento sobre el mismo pedido: un reintento no crea dos orders."""
    return str(uuid5(NAMESPACE_URL, f"ticketera:mp-order:{order.pk}"))


def platform_fee(items):
    """Lo que retiene la plataforma: cargo de servicio más comisión, con los importes guardados
    en cada ítem al reservar (no se recalculan con las tasas actuales del boliche)."""
    return sum(
        (item.quantity * (item.unit_service_fee + item.unit_platform_fee) for item in items),
        Decimal(0),
    )


def expiration_time(order, now=None):
    """Lo que le queda a la reserva como duración ISO 8601 en minutos (hacia arriba, mínimo 1)."""
    remaining = (order.expires_at - (now or timezone.now())).total_seconds()
    return f"PT{max(1, math.ceil(remaining / 60))}M"


def order_body(order, items, fee, total, direct=False):
    event = order.event
    back_url = f"{settings.FRONT_BASE_URL}/{event.venue.slug}/{event.slug}/pedido/{order.pk}"
    body = {
        "type": "online",
        "processing_mode": "manual",
        "capture_mode": "automatic",
        "total_amount": _money(total),
        "external_reference": str(order.pk),
        "expiration_time": expiration_time(order),
        "payer": {"email": order.buyer_email},
        "items": [
            {
                "title": f"{item.ticket_type.name} · {event.title}",
                "quantity": item.quantity,
                "unit_price": _money(item.unit_price + item.unit_service_fee),
            }
            for item in items
        ],
        "config": {
            "online": {
                "success_url": back_url,
                "failure_url": back_url,
                "pending_url": back_url,
                "auto_return": "approved",
            },
            "payment_method": {"not_allowed_types": list(EXCLUDED_PAYMENT_TYPES)},
        },
    }
    if fee > 0 and not direct:  # cobro directo: sin reparto, la comisión se liquida por fuera
        body["marketplace_fee"] = _money(fee)
    return body


def _provider(request):
    """El medio de pago elegido, en un cuerpo JSON. Sin él (como manda el front hasta ahora), Mercado Pago."""
    if request.content_type != "application/json" or not request.body:
        return MERCADOPAGO
    try:
        data = json.loads(request.body)
    except (ValueError, RecursionError):
        return None
    provider = data.get("provider", MERCADOPAGO) if isinstance(data, dict) else None
    return provider if provider == MERCADOPAGO or provider in gateways.ADAPTERS else None


@csrf_exempt
@require_POST
@never_cache
def order_payment(request, venue_slug, event_slug, order_id):
    provider = _provider(request)
    if provider is None:
        return _error("invalid_provider", 400)
    if provider != MERCADOPAGO:
        return _gateway_payment(venue_slug, event_slug, order_id, provider)
    if not (settings.FRONT_BASE_URL and mp_connect.is_configured()):
        return mp_connect.not_configured()
    with transaction.atomic():
        order, error = _locked_order(venue_slug, event_slug, order_id)
        if error is not None:
            return error
        if order.mp_init_point:
            return JsonResponse({"initPoint": order.mp_init_point})
        venue = order.event.venue
        if not settings.PAYMENTS_ALLOW_TEST_MODE and MercadoPagoAccount.objects.filter(venue=venue, live_mode=False).exists():
            return _error("venue_not_connected", 409)  # cuenta en modo prueba con el modo prueba apagado
        try:
            access_token = mp_connect.get_access_token(venue)
        except mp_connect.MercadoPagoNotConnected:
            return _error("venue_not_connected", 409)
        items = list(order.items.select_related("ticket_type"))
        total = sum((item.quantity * (item.unit_price + item.unit_service_fee) for item in items), Decimal(0))
        fee = min(platform_fee(items), total)
        direct = MercadoPagoAccount.objects.filter(venue=venue, direct=True).exists()
        try:
            mp_order = mp.create_order(
                access_token, order_body(order, items, fee, total, direct), idempotency_key(order),
            )
            order_id, init_point = str(mp_order["id"]), mp_order["checkout_url"]
            if not mp_order["id"] or not init_point or Decimal(str(mp_order["total_amount"])) != total.quantize(CENT):
                raise KeyError("order")
        except (mp.MercadoPagoError, KeyError, TypeError, ArithmeticError, ValueError):
            return _error("mp_unavailable", 502)
        order.platform_fee = fee
        order.mp_order_id = order_id
        order.mp_init_point = init_point
        order.save(update_fields=["platform_fee", "mp_order_id", "mp_init_point"])
    return JsonResponse({"initPoint": init_point})
