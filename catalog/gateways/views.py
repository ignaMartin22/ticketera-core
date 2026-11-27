"""Rutas públicas de las pasarelas: la página que manda al comprador a Macro y los avisos de pago."""

import hmac
import json

from django.conf import settings
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe

from ..models import GatewayAccount, GatewayCharge, Order
from ..payment_confirm import order_ref_filter, order_total
from . import macro, nave, return_url, sync_charge

MAX_BODY = 64 * 1024


def client_ip(request):
    """La IP del comprador. Detrás del proxy, la primera de X-Forwarded-For (la que agrega el proxy)."""
    if getattr(settings, "SECURE_PROXY_SSL_HEADER", None):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


@require_safe
@never_cache
def macro_pay_page(request, order_ref):
    """Arma el formulario cifrado con la IP de quien abre la página y lo envía solo al botón de Macro."""
    order = get_object_or_404(Order.objects.select_related("event__venue"), order_ref_filter(order_ref))
    charge = get_object_or_404(GatewayCharge, order=order, provider=macro.PROVIDER)
    if order.paid_at is not None or order.expires_at <= timezone.now():
        return HttpResponseRedirect(return_url(order))
    account = get_object_or_404(GatewayAccount, venue=order.event.venue, provider=macro.PROVIDER)
    fields = macro.form_fields(
        account, charge, order_total(order), client_ip(request), return_url(order),
        f"Entradas {order.event.title} · {order.event.venue.name}",
    )
    response = render(request, "gateways/macro_redirect.html", {"action": macro.button_url(account), "fields": fields})
    response["X-Robots-Tag"] = "noindex"
    response["Referrer-Policy"] = "no-referrer"
    return response


def _payload(request):
    if len(request.body) > MAX_BODY:
        return None
    try:
        data = json.loads(request.body)
    except (ValueError, RecursionError):
        data = request.POST.dict()  # por si el aviso llega como formulario
    return data if isinstance(data, dict) else None


def _answer(result):
    # Con "failed" respondemos 500 para que la pasarela reintente; el cron también lo cubre.
    return JsonResponse({"ok": result != "failed"}, status=500 if result == "failed" else 200)


@csrf_exempt
@require_POST
@never_cache
def macro_notify(request):
    """Aviso de Macro. No viene firmado: solo dispara la consulta de la transacción."""
    data = _payload(request)
    if data is None:
        return JsonResponse({"error": "invalid_request"}, status=400)
    reference = str(data.get("TransaccionComercioId") or "")[:64]
    charge = GatewayCharge.objects.select_related("order__event").filter(
        provider=macro.PROVIDER, reference=reference,
    ).first()
    if charge is None:
        return JsonResponse({"ok": True})  # no es nuestro: no se reintenta
    return _answer(sync_charge(charge))


@csrf_exempt
@require_POST
@never_cache
def nave_notify(request, order_ref):
    """Aviso de Nave. La URL lleva el secreto del cobro; igual se consulta la intención de pago."""
    charge = GatewayCharge.objects.select_related("order__event").filter(
        order_ref_filter(order_ref, prefix="order__"), provider=nave.PROVIDER,
    ).first()
    secret = request.GET.get("secret", "")
    if charge is None or not charge.notify_secret or not hmac.compare_digest(charge.notify_secret, secret):
        return JsonResponse({"error": "invalid_secret"}, status=401)
    return _answer(sync_charge(charge))
