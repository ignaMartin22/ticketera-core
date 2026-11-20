"""Mail al comprador con sus entradas: un QR por entrada y el link al pedido."""

import io
import logging
from datetime import timedelta
from email.mime.image import MIMEImage

import segno
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from .models import Order
from .qr import sign_ticket

logger = logging.getLogger(__name__)

# El reintento deja tranquilos los pedidos recién pagados: su envío sale con la confirmación.
RETRY_AFTER = timedelta(minutes=5)
# Pasado este plazo deja de reintentar (por ejemplo, un email mal escrito o un remitente sin
# verificar): esos pedidos se reenvían a mano desde el admin.
RETRY_WINDOW = timedelta(days=2)


def order_url(order):
    if not settings.FRONT_BASE_URL:
        return ""
    event = order.event
    return f"{settings.FRONT_BASE_URL}/{event.venue.slug}/{event.slug}/pedido/{order.pk}"


def qr_png(text):
    buffer = io.BytesIO()
    segno.make(text, error="m").save(buffer, kind="png", scale=8, border=4)
    return buffer.getvalue()


def build_message(order):
    event = order.event
    tickets = list(order.tickets.select_related("ticket_type").order_by("created_at", "pk"))
    entries = [
        {
            "number": number,
            "type": ticket.ticket_type.name,
            "code": sign_ticket(ticket.pk),
            "cid": f"entrada-{number}@ticketera",
        }
        for number, ticket in enumerate(tickets, start=1)
    ]
    context = {
        "order": order, "event": event, "venue": event.venue, "entries": entries,
        "order_url": order_url(order), "short_id": str(order.pk)[:8],
    }
    message = EmailMultiAlternatives(
        subject=f"Tus entradas para {event.title}",
        body=render_to_string("catalog/email/entradas.txt", context),
        to=[order.buyer_email],
        reply_to=[settings.TICKETS_REPLY_TO] if settings.TICKETS_REPLY_TO else None,
    )
    message.attach_alternative(render_to_string("catalog/email/entradas.html", context), "text/html")
    # Las imágenes van dentro del mail (cid:) para que se vean sin descargar nada.
    message.mixed_subtype = "related"
    for entry in entries:
        image = MIMEImage(qr_png(entry["code"]), "png")
        image.add_header("Content-ID", f"<{entry['cid']}>")
        image.add_header("Content-Disposition", "inline", filename=f"entrada-{entry['number']}.png")
        message.attach(image)
    return message


def send_ticket_email(order_id, force=False):
    """Manda las entradas de un pedido pagado. Devuelve True si salió el mail.

    Sin ``force`` no reenvía a quien ya lo recibió. Un error se registra y deja el pedido
    pendiente para ``send_ticket_emails``: nunca afecta la confirmación del pago.
    """
    order = Order.objects.select_related("event__venue").filter(pk=order_id).first()
    if order is None or order.paid_at is None or not order.tickets.exists():
        return False
    if order.tickets_emailed_at is not None and not force:
        return False
    try:
        build_message(order).send()
    except Exception:
        logger.exception("No se pudo mandar el mail de entradas del pedido %s", order.pk)
        return False
    Order.objects.filter(pk=order.pk).update(tickets_emailed_at=timezone.now())
    return True


def send_after_commit(order_id):
    transaction.on_commit(lambda: send_ticket_email(order_id))


def pending_orders(now=None):
    now = now or timezone.now()
    return Order.objects.filter(
        paid_at__isnull=False, paid_at__lte=now - RETRY_AFTER, paid_at__gt=now - RETRY_WINDOW,
        tickets_emailed_at__isnull=True, tickets__isnull=False,
    ).distinct()
