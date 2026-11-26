"""Pasarelas de pago sin reparto: Macro Click de Pago y Nave (Naranja X y Galicia).

Toda la venta entra a la cuenta del boliche; la comisión de la plataforma queda guardada en
`Order.platform_fee` para liquidarla por fuera. Ver docs/specs/pasarelas.md.
"""

import secrets

from django.conf import settings
from django.utils import timezone

from ..models import GatewayAccount, GatewayCharge, MercadoPagoAccount, Order
from ..payment_confirm import order_total
from . import macro, nave
from .base import GatewayError, settle

ADAPTERS = {macro.PROVIDER: macro, nave.PROVIDER: nave}
MERCADOPAGO = "mercadopago"


def payment_methods(venue):
    """Con qué se puede pagar en este boliche, en el orden en que se muestran."""
    methods = [MERCADOPAGO] if MercadoPagoAccount.objects.filter(venue=venue, needs_reconnect=False).exists() else []
    connected = set(GatewayAccount.objects.filter(venue=venue).values_list("provider", flat=True))
    return methods + [p for p in ADAPTERS if p in connected]


def return_url(order):
    event = order.event
    return f"{settings.FRONT_BASE_URL}/{event.venue.slug}/{event.slug}/pedido/{order.pk}"


def start(order, provider):
    """Crea (o devuelve) el cobro del pedido en la pasarela y el link al que va el comprador.

    Se llama con el pedido bloqueado y ya validado (sin pagar y sin vencer). Levanta
    `GatewayAccount.DoesNotExist` si el boliche no tiene la pasarela y `GatewayError` si falla.
    """
    from ..payments import platform_fee  # payments importa este módulo

    venue = order.event.venue
    account = GatewayAccount.objects.get(venue=venue, provider=provider)
    existing = GatewayCharge.objects.filter(order=order, provider=provider).first()
    if existing is not None and existing.checkout_url:
        return existing.checkout_url
    items = list(order.items.select_related("ticket_type"))
    total = order_total(order)
    order.platform_fee = min(platform_fee(items), total)
    order.save(update_fields=["platform_fee"])
    if provider == macro.PROVIDER:
        # La referencia la elegimos nosotros; el link es una página del core que arma el formulario.
        charge = existing or GatewayCharge.objects.create(order=order, provider=provider, reference=order.pk.hex)
        charge.checkout_url = f"{settings.PUBLIC_API_URL}/api/v1/pagos/macro/{order.pk}/"
        charge.save(update_fields=["checkout_url", "updated_at"])
        return charge.checkout_url
    secret = secrets.token_hex(32)
    notify_url = f"{settings.PUBLIC_API_URL}/api/v1/pagos/nave/{order.pk}/aviso/?secret={secret}"
    seconds_left = (order.expires_at - timezone.now()).total_seconds()
    reference, checkout_url = nave.create(account, order, items, total, return_url(order), notify_url, seconds_left)
    GatewayCharge.objects.create(
        order=order, provider=provider, reference=reference, checkout_url=checkout_url, notify_secret=secret,
    )
    return checkout_url


def sync_charge(charge):
    """Consulta el cobro en la pasarela y lo confirma. Devuelve "confirmed", "pending" o "failed"."""
    adapter = ADAPTERS.get(charge.provider)
    account = GatewayAccount.objects.filter(venue_id=charge.order.event.venue_id, provider=charge.provider).first()
    if adapter is None or account is None:
        return "failed"
    try:
        found = adapter.fetch(account, charge)
    except GatewayError:
        return "failed"
    if found is None:
        return "pending"
    status, approved, amount = found
    settle(charge.pk, status=status, approved=approved, amount=amount)
    return "confirmed" if Order.objects.filter(pk=charge.order_id, paid_at__isnull=False).exists() else "pending"


def sync_order(order):
    """Consulta los cobros de pasarela de un pedido sin pagar."""
    if order.paid_at is not None:
        return "skipped"
    charges = list(order.gateway_charges.select_related("order__event"))
    if not charges:
        return "skipped"
    results = [sync_charge(charge) for charge in charges]
    if "confirmed" in results:
        return "confirmed"
    return "failed" if all(r == "failed" for r in results) else "pending"

