"""Confirmación del pago: consulta a Mercado Pago, emisión de entradas y avisos (webhook).

Reglas: nunca se confía en el contenido del aviso (siempre se consulta la order con el token
del boliche), una order emite las entradas una sola vez y no hay reembolsos: lo que no se
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


def process_order(account, mp_order):
    """Registra una order de Mercado Pago y, si corresponde, emite las entradas del pedido.

    `account` es la cuenta con cuyo token se consultó la order y `mp_order` lo que devolvió
    Mercado Pago. Devuelve el `Payment` guardado o None si la order no es de un pedido de
    ese boliche (en ese caso no guarda nada).
    """
    mp_order_id = str(mp_order.get("id") or "")[:64]
    if not mp_order_id or str(mp_order.get("user_id")) != str(account.mp_user_id):
        return None
    try:
        order_id = UUID(str(mp_order.get("external_reference")))
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
        record = Payment.objects.filter(mp_order_id=mp_order_id).first()
        if record is not None and record.order_id != order.pk:
            return None
        paid_amount = mp_order.get("total_paid_amount")
        amount = _money(paid_amount if paid_amount not in (None, "") else mp_order.get("total_amount"))
        if record is None:
            record = Payment(order=order, mp_order_id=mp_order_id, mp_user_id=account.mp_user_id)
        record.status = str(mp_order.get("status") or "")[:30]
        record.status_detail = str(mp_order.get("status_detail") or "")[:100]
        record.amount = amount if amount is not None else Decimal(0)
        record.mp_reference = _reference(mp_order)
        approved = record.status == "processed" and record.status_detail == "accredited"
        if not approved:
            if record.outcome != Outcome.ISSUED:
                record.outcome = Outcome.NOT_APPROVED
        elif record.outcome != Outcome.ISSUED:
            record.outcome = _approved_outcome(order, record, amount, mp_order)
        record.save()
        return record


def _reference(mp_order):
    transactions = mp_order.get("transactions")
    payments = transactions.get("payments") if isinstance(transactions, dict) else None
    first = payments[0] if isinstance(payments, list) and payments else None
    reference = first.get("reference") if isinstance(first, dict) else None
    value = reference.get("id") if isinstance(reference, dict) else None
    return str(value)[:100] if value not in (None, "") else ""


def _approved_outcome(order, record, amount, mp_order):
    total = sum(
        (item.quantity * (item.unit_price + item.unit_service_fee) for item in order.items.all()),
        Decimal(0),
    ).quantize(CENT)
    if mp_order.get("currency") != "ARS" or amount != total:
        return Outcome.MISMATCH
    if (
        order.paid_at is not None
        and order.payments.filter(outcome=Outcome.ISSUED).exclude(mp_order_id=record.mp_order_id).exists()
    ):
        return Outcome.DUPLICATE
    try:
        issue_tickets(order.pk)
    except OrderConflict:
        return Outcome.NO_CAPACITY
    return Outcome.ISSUED


def sync_order(order):
    """Consulta en Mercado Pago la order de un pedido sin pagar y la confirma.

    Devuelve "skipped" (nada que consultar), "confirmed" (el pedido quedó pagado),
    "pending" (consultado, sigue sin pagar) o "failed" (Mercado Pago o el token fallaron).
    """
    if order.paid_at is not None or not order.mp_order_id:
        return "skipped"
    venue = order.event.venue
    account = MercadoPagoAccount.objects.filter(venue=venue).first()
    if account is None:
        return "failed"
    try:
        access_token = mp_connect.get_access_token(venue)
        mp_order = mp.get_order(access_token, order.mp_order_id)
    except (mp_connect.MercadoPagoNotConnected, mp.MercadoPagoError):
        return "failed"
    process_order(account, mp_order)
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


def _confirm_order(order_id, user_id):
    """True si todo salió bien (o la order no es nuestra); False si hay que reintentar."""
    accounts = MercadoPagoAccount.objects.filter(mp_user_id=user_id).select_related("venue").order_by("pk")
    failed = False
    for account in accounts:
        try:
            access_token = mp_connect.get_access_token(account.venue)
            mp_order = mp.get_order(access_token, order_id)
        except (mp_connect.MercadoPagoNotConnected, mp.MercadoPagoError):
            failed = True
            continue
        if process_order(account, mp_order) is not None:
            return True
    return not failed


@csrf_exempt
@require_POST
@never_cache
def webhook(request):
    if not settings.MP_WEBHOOK_SECRET:
        return _json_error("mp_not_configured", 503)
    data_id = request.GET.get("data.id")
    signed_id = data_id.lower() if data_id is not None else None  # MP firma los ids alfanuméricos en minúsculas
    if not mp.webhook_signature_is_valid(
        request.headers.get("x-signature"), request.headers.get("x-request-id"), signed_id,
    ):
        return _json_error("invalid_signature", 401)
    body = _body(request)
    if body is None:
        return _json_error("invalid_request", 400)
    kind = body.get("type")
    if kind == "order":
        user_id = _int(body.get("user_id"))
        if not data_id or user_id is None:
            return _json_error("invalid_request", 400)
        if not _confirm_order(data_id, user_id):
            return _json_error("mp_unavailable", 500)
    elif kind == "mp-connect" and body.get("action") == "application.deauthorized":
        user_id = _int(body.get("user_id"))
        if user_id is None:
            return _json_error("invalid_request", 400)
        now = timezone.now()
        accounts = MercadoPagoAccount.objects.filter(mp_user_id=user_id, direct=False)  # el aviso es del flujo OAuth
        venue_ids = list(accounts.values_list("venue_id", flat=True))
        accounts.update(needs_reconnect=True, expires_at=now, updated_at=now)
        mp_connect.mark_expired(venue_ids)
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
