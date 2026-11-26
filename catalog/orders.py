from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Event, Order, OrderItem, Ticket, TicketType
from .tickets import issue_tickets

MAX_TICKETS_PER_ORDER = 10
RESERVATION_DURATION = timedelta(minutes=10)


class OrderConflict(Exception):
    pass


def check_pending_limits(event, email, ip, now):
    """Pedidos vivos sin pagar por mail (y por IP si hay) en el evento. Con el evento bloqueado."""
    live = event.orders.filter(paid_at__isnull=True, expires_at__gt=now)
    if live.filter(buyer_email__iexact=email).count() >= settings.MAX_PENDING_ORDERS_PER_EMAIL:
        raise OrderConflict("too_many_reservations")
    if ip and live.filter(buyer_ip=ip).count() >= settings.MAX_PENDING_ORDERS_PER_IP:
        raise OrderConflict("too_many_reservations")


def check_free_limits(event, email, ip, new_tickets):
    """Entradas gratis ya emitidas por el endpoint público (sin las de listas de RRPP) más las nuevas.

    El pedido nuevo ya está creado pero todavía no emitió, así que no cuenta dos veces. Si se pasa,
    el `OrderConflict` deshace la transacción entera (pedido incluido).
    """
    issued = (
        Ticket.objects.filter(event=event)
        .exclude(ticket_type__kind=TicketType.Kind.LIST)
        .exclude(order__items__unit_price__gt=0)
        .exclude(order__items__unit_service_fee__gt=0)
    )
    if issued.filter(order__buyer_email__iexact=email).count() + new_tickets > settings.MAX_FREE_TICKETS_PER_EMAIL:
        raise OrderConflict("free_limit_reached")
    if ip and issued.filter(order__buyer_ip=ip).count() + new_tickets > settings.MAX_FREE_TICKETS_PER_IP:
        raise OrderConflict("free_limit_reached")


@transaction.atomic
def reserve_order(venue_slug, event_slug, buyer, quantities, idempotency_key, buyer_ip=""):
    """buyer: dict con email, name y phone ya validados. buyer_ip: la que informó el front, o \"\"."""
    # The event lock serializes reservations (including retries with the same key).
    event = Event.objects.select_related("venue").select_for_update(of=("self",)).get(
        venue__slug=venue_slug, venue__is_active=True, slug=event_slug, is_published=True,
    )
    existing = event.orders.filter(idempotency_key=idempotency_key).first()
    if existing:
        items = list(existing.items.all())
        existing_quantities = {item.ticket_slug: item.quantity for item in items}
        same_buyer = (existing.buyer_email, existing.buyer_name, existing.buyer_phone) == (
            buyer["email"], buyer["name"], buyer["phone"],
        )
        if not same_buyer or quantities != existing_quantities:
            raise OrderConflict("idempotency_key_reused")
        return existing, items, False

    # La venta pausada no afecta los pedidos ya reservados (el reintento de arriba sigue devolviéndolos).
    if not event.sales_open:
        raise OrderConflict("sales_paused")

    ticket_types = list(
        TicketType.objects.select_for_update()
        .filter(event=event, slug__in=quantities).exclude(kind=TicketType.Kind.LIST).order_by("pk"),
    )
    if len(ticket_types) != len(quantities) or any(not ticket.is_active for ticket in ticket_types):
        raise OrderConflict("ticket_type_unavailable")

    now = timezone.now()
    check_pending_limits(event, buyer["email"], buyer_ip, now)
    reserved = dict(
        TicketType.objects.filter(pk__in=[ticket.pk for ticket in ticket_types])
        .with_reserved(now)
        .values_list("pk", "reserved"),
    )
    for ticket in ticket_types:
        if quantities[ticket.slug] > ticket.capacity - ticket.sold - reserved[ticket.pk]:
            raise OrderConflict("insufficient_capacity")

    order = Order.objects.create(
        event=event, buyer_email=buyer["email"], buyer_name=buyer["name"], buyer_phone=buyer["phone"],
        idempotency_key=idempotency_key, buyer_ip=buyer_ip or None,
        expires_at=now + RESERVATION_DURATION,
    )
    fee_rate = Decimal(event.venue.service_fee_rate)
    platform_rate = event.venue.platform_fee_rate
    items = [
        OrderItem(
            order=order,
            ticket_type=ticket,
            ticket_slug=ticket.slug,
            quantity=quantities[ticket.slug],
            unit_price=ticket.price,
            unit_service_fee=(ticket.price * fee_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP),
            unit_platform_fee=(ticket.price * platform_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        )
        for ticket in ticket_types
    ]
    OrderItem.objects.bulk_create(items)
    if all(item.unit_price + item.unit_service_fee == 0 for item in items):
        check_free_limits(event, buyer["email"], buyer_ip, sum(quantities.values()))
    if all(item.unit_price + item.unit_service_fee == 0 for item in items):
        issue_tickets(order.pk)
        order.refresh_from_db()
    return order, items, True
