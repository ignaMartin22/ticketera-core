"""Confirmación del pago: consulta a Mercado Pago, emisión de entradas y avisos (webhook).

Reglas: nunca se confía en el contenido del aviso (siempre se consulta el pago con el token
del boliche), un pago emite las entradas una sola vez y no hay reembolsos: lo que no se
puede emitir queda registrado como "a revisar" para que el boliche lo resuelva a mano.
"""

import json
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import mp, mp_connect
from .models import MercadoPagoAccount, Order, Payment
from .orders import OrderConflict
from .tickets import issue_tickets
from .views import order_data

CENT = Decimal("0.01")
MAX_BODY = 64 * 1024
Outcome = Payment.Outcome


def _money(value):
    """Decimal a 2 decimales, o None si no es un monto válido."""
    try:
        amount = Decimal(str(value)).quantize(CENT)
    except (InvalidOperation, ValueError, TypeError):
        return None
    return amount if amount.is_finite() and abs(amount) < Decimal(10) ** 10 else None


def _int(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def process_payment(account, payment):
    """Registra un pago de Mercado Pago y, si corresponde, emite las entradas del pedido.

    `account` es la cuenta con cuyo token se consultó el pago y `payment` lo que devolvió
    Mercado Pago. Devuelve el `Payment` guardado o None si el pago no es de un pedido de
    ese boliche (en ese caso no guarda nada).
    """
    mp_payment_id = _int(payment.get("id"))
    if mp_payment_id is None or _int(payment.get("collector_id")) != account.mp_user_id:
        return None
    try:
        order_id = UUID(str(payment.get("external_reference")))
    except ValueError:
        return None
    with transaction.atomic():
        order = (
            Order.objects.select_for_update(of=("self",))
            .filter(pk=order_id, event__venue_id=account.venue_id)
            .first()
        )
        if order is None:
            return None
        record = Payment.objects.filter(mp_payment_id=mp_payment_id).first()
        if record is not None and record.order_id != order.pk:
            return None
        amount = _money(payment.get("transaction_amount"))
        if record is None:
            record = Payment(order=order, mp_payment_id=mp_payment_id, mp_user_id=account.mp_user_id)
        record.status = str(payment.get("status") or "")[:30]
        record.status_detail = str(payment.get("status_detail") or "")[:100]
        record.amount = amount if amount is not None else Decimal(0)
        if record.status != "approved":
            if record.outcome != Outcome.ISSUED:
                record.outcome = Outcome.NOT_APPROVED
        elif record.outcome != Outcome.ISSUED:
            record.outcome = _approved_outcome(order, record, amount, payment)
        record.save()
        return record


def _approved_outcome(order, record, amount, payment):
    total = sum(
        (item.quantity * (item.unit_price + item.unit_service_fee) for item in order.items.all()),
        Decimal(0),
    ).quantize(CENT)
    if payment.get("currency_id") != "ARS" or amount != total:
        return Outcome.MISMATCH
    if (
        order.paid_at is not None
        and order.payments.filter(outcome=Outcome.ISSUED).exclude(mp_payment_id=record.mp_payment_id).exists()
    ):
        return Outcome.DUPLICATE
    try:
        issue_tickets(order.pk)
    except OrderConflict:
        return Outcome.NO_CAPACITY
    return Outcome.ISSUED


def sync_order(order):
    """Busca en Mercado Pago los pagos de un pedido sin pagar y los confirma.

    Devuelve "skipped" (nada que consultar), "confirmed" (el pedido quedó pagado),
    "pending" (consultado, sigue sin pagar) o "failed" (Mercado Pago o el token fallaron).
    """
    if order.paid_at is not None or not order.mp_preference_id:
        return "skipped"
    venue = order.event.venue
    account = MercadoPagoAccount.objects.filter(venue=venue).first()
    if account is None:
        return "failed"
    try:
        access_token = mp_connect.get_access_token(venue)
        payments = mp.search_payments(access_token, str(order.pk))
    except (mp_connect.MercadoPagoNotConnected, mp.MercadoPagoError):
        return "failed"
    for payment in payments:
        process_payment(account, payment)
    return "confirmed" if Order.objects.filter(pk=order.pk, paid_at__isnull=False).exists() else "pending"


def _json_error(code, status):
    return JsonResponse({"error": code}, status=status)


def _body(request):
    if len(request.body) > MAX_BODY:
        return None
    try:
        data = json.loads(request.body)
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _confirm_payment(payment_id, user_id):
    """True si todo salió bien (o el pago no es nuestro); False si hay que reintentar."""
    accounts = MercadoPagoAccount.objects.filter(mp_user_id=user_id).select_related("venue").order_by("pk")
    failed = False
    for account in accounts:
        try:
            access_token = mp_connect.get_access_token(account.venue)
            payment = mp.get_payment(access_token, payment_id)
        except (mp_connect.MercadoPagoNotConnected, mp.MercadoPagoError):
            failed = True
            continue
        if process_payment(account, payment) is not None:
            return True
    return not failed


@csrf_exempt
@require_POST
@never_cache
def webhook(request):
    if not settings.MP_WEBHOOK_SECRET:
        return _json_error("mp_not_configured", 503)
    data_id = request.GET.get("data.id")
    if data_id is not None and not data_id.isdigit():
        data_id = data_id.lower()
    if not mp.webhook_signature_is_valid(
        request.headers.get("x-signature"), request.headers.get("x-request-id"), data_id,
    ):
        return _json_error("invalid_signature", 401)
    body = _body(request)
    if body is None:
        return _json_error("invalid_request", 400)
    kind = body.get("type")
    if kind == "payment":
        user_id = _int(body.get("user_id"))
        if not data_id or not data_id.isdigit() or user_id is None:
            return _json_error("invalid_request", 400)
        if not _confirm_payment(data_id, user_id):
            return _json_error("mp_unavailable", 500)
    elif kind == "mp-connect" and body.get("action") == "application.deauthorized":
        user_id = _int(body.get("user_id"))
        if user_id is None:
            return _json_error("invalid_request", 400)
        now = timezone.now()
        MercadoPagoAccount.objects.filter(mp_user_id=user_id).update(
            needs_reconnect=True, expires_at=now, updated_at=now,
        )
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
@never_cache
def order_payment_sync(request, venue_slug, event_slug, order_id):
    order = get_object_or_404(
        Order.objects.select_related("event__venue"),
        pk=order_id, event__slug=event_slug, event__venue__slug=venue_slug,
    )
    sync_order(order)
    order = Order.objects.get(pk=order_id)
    return JsonResponse({"order": order_data(order, order.items.all())})
