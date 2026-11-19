from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from .models import Event, Order, OrderItem, TicketType, Venue
from .tickets import issue_tickets

MAX_TICKETS_PER_ORDER = 10
RESERVATION_DURATION = timedelta(minutes=10)


class OrderConflict(Exception):
    pass


@transaction.atomic
def reserve_order(venue_slug, event_slug, buyer, quantities, idempotency_key):
    """buyer: dict con email, name y phone ya validados."""
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

    ticket_types = list(
        TicketType.objects.select_for_update().filter(event=event, slug__in=quantities).order_by("pk"),
    )
    if len(ticket_types) != len(quantities) or any(not ticket.is_active for ticket in ticket_types):
        raise OrderConflict("ticket_type_unavailable")

    now = timezone.now()
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
        idempotency_key=idempotency_key,
        expires_at=now + RESERVATION_DURATION,
    )
    fee_rate = event.venue.buyer_fee_rate if event.venue.fee_mode == Venue.FeeMode.BUYER else Decimal(0)
    items = [
        OrderItem(
            order=order,
            ticket_type=ticket,
            ticket_slug=ticket.slug,
            quantity=quantities[ticket.slug],
            unit_price=ticket.price,
            unit_service_fee=(ticket.price * fee_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP),
        )
        for ticket in ticket_types
    ]
    OrderItem.objects.bulk_create(items)
    if all(item.unit_price + item.unit_service_fee == 0 for item in items):
        issue_tickets(order.pk)
        order.refresh_from_db()
    return order, items, True
