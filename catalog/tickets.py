from django.db import transaction
from django.utils import timezone

from .models import Order, Ticket, TicketType
from .ticket_email import send_after_commit


@transaction.atomic
def issue_tickets(order_id):
    from .orders import OrderConflict  # orders imports this module

    order = Order.objects.select_for_update().get(pk=order_id)
    if order.paid_at is not None:
        return list(order.tickets.all())

    items = list(order.items.all())
    ticket_types = {
        ticket.pk: ticket
        for ticket in TicketType.objects.select_for_update()
        .filter(pk__in=[item.ticket_type_id for item in items])
        .order_by("pk")
    }
    now = timezone.now()
    if order.expires_at <= now:
        # Our own reservation no longer counts, so check what other live orders hold.
        reserved = dict(
            TicketType.objects.filter(pk__in=ticket_types).with_reserved(now).values_list("pk", "reserved"),
        )
        for item in items:
            ticket = ticket_types[item.ticket_type_id]
            if ticket.capacity - ticket.sold - reserved[ticket.pk] < item.quantity:
                raise OrderConflict("insufficient_capacity")

    tickets = []
    for item in items:
        ticket_type = ticket_types[item.ticket_type_id]
        ticket_type.sold += item.quantity
        ticket_type.save(update_fields=["sold"])
        tickets.extend(
            Ticket(order=order, event_id=order.event_id, ticket_type=ticket_type)
            for _ in range(item.quantity)
        )
    order.paid_at = now
    order.save(update_fields=["paid_at"])
    created = Ticket.objects.bulk_create(tickets)
    # Se manda después del commit: si la emisión se deshace, no sale ningún mail.
    send_after_commit(order.pk)
    return created
