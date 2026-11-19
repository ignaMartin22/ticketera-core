"""Link de pago (preferencia de Checkout Pro) de un pedido, creado con el token del boliche."""

from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import mp, mp_connect
from .models import Order

WEBHOOK_PATH = "/api/v1/mercadopago/webhook/"
EXCLUDED_PAYMENT_TYPES = ("ticket", "atm")


def _error(code, status):
    return JsonResponse({"error": code}, status=status)


def _number(value):
    value = Decimal(value)
    return int(value) if value == value.to_integral_value() else float(value)


def platform_fee(venue, items):
    """Comisión de la plataforma con los precios guardados en el pedido, en pesos enteros."""
    return sum(
        (
            item.quantity * (item.unit_price * venue.buyer_fee_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            for item in items
        ),
        Decimal(0),
    )


def preference_body(order, items, fee):
    event = order.event
    back_url = f"{settings.FRONT_BASE_URL}/{event.venue.slug}/{event.slug}/pedido/{order.pk}"
    body = {
        "items": [
            {
                "title": f"{item.ticket_type.name} · {event.title}",
                "quantity": item.quantity,
                "unit_price": _number(item.unit_price + item.unit_service_fee),
                "currency_id": "ARS",
            }
            for item in items
        ],
        "payer": {"email": order.buyer_email},
        "external_reference": str(order.pk),
        "back_urls": {"success": back_url, "failure": back_url, "pending": back_url},
        "auto_return": "approved",
        "notification_url": settings.PUBLIC_API_URL + WEBHOOK_PATH,
        "expires": True,
        "expiration_date_to": order.expires_at.isoformat(),
        "payment_methods": {"excluded_payment_types": [{"id": kind} for kind in EXCLUDED_PAYMENT_TYPES]},
        "binary_mode": True,
    }
    if fee > 0:
        body["marketplace_fee"] = _number(fee)
    return body


@csrf_exempt
@require_POST
@never_cache
def order_payment(request, venue_slug, event_slug, order_id):
    if not (settings.FRONT_BASE_URL and settings.PUBLIC_API_URL and mp_connect.is_configured()):
        return mp_connect.not_configured()
    with transaction.atomic():
        order = (
            Order.objects.select_for_update(of=("self",))
            .select_related("event__venue")
            .filter(pk=order_id, event__slug=event_slug, event__venue__slug=venue_slug)
            .first()
        )
        if order is None:
            return _error("not_found", 404)
        if order.paid_at is not None:
            return _error("order_already_paid", 409)
        if order.expires_at <= timezone.now():
            return _error("order_expired", 409)
        if order.mp_init_point:
            return JsonResponse({"initPoint": order.mp_init_point})
        venue = order.event.venue
        try:
            access_token = mp_connect.get_access_token(venue)
        except mp_connect.MercadoPagoNotConnected:
            return _error("venue_not_connected", 409)
        items = list(order.items.select_related("ticket_type"))
        total = sum((item.quantity * (item.unit_price + item.unit_service_fee) for item in items), Decimal(0))
        fee = min(platform_fee(venue, items), total)
        try:
            preference = mp.create_preference(access_token, preference_body(order, items, fee))
            preference_id, init_point = str(preference["id"]), preference["init_point"]
            if not preference_id or not init_point:
                raise KeyError("init_point")
        except (mp.MercadoPagoError, KeyError, TypeError):
            return _error("mp_unavailable", 502)
        order.platform_fee = fee
        order.mp_preference_id = preference_id
        order.mp_init_point = init_point
        order.save(update_fields=["platform_fee", "mp_preference_id", "mp_init_point"])
    return JsonResponse({"initPoint": init_point})
