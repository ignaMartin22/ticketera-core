"""API de portería: login del personal de puerta y escaneo de entradas."""

from functools import wraps

from django.db.models import Count, Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe

from .models import Event, Membership, Ticket, Venue
from .qr import InvalidTicketToken, verify_ticket
from .staff_auth import (  # noqa: F401  (LOGIN_MAX_FAILURES se reexporta: lo importan los tests)
    LOGIN_MAX_FAILURES, TOKEN_MAX_AGE, error, issue_token, json_body, login_user,
    venue_staff_view,
)

DOOR_ROLES = (Membership.Role.DOOR, Membership.Role.ADMIN)


def door_venue_view(view):
    """Autentica con el token y exige rol door/admin en el boliche de la URL."""

    @wraps(view)
    def adapted(request, venue, user, membership, *args, **kwargs):
        return view(request, venue, user, *args, **kwargs)

    return venue_staff_view(*DOOR_ROLES)(adapted)


def event_summary(event):
    return {"slug": event.slug, "title": event.title, "startsAt": event.starts_at.isoformat()}


@csrf_exempt
@require_POST
@never_cache
def login(request):
    user, failure = login_user(request)
    if failure:
        return failure
    venues = Venue.objects.filter(
        is_active=True, memberships__user=user, memberships__role__in=DOOR_ROLES,
    ).distinct().order_by("slug")
    if not venues:
        return error("not_door_staff", 403)
    return JsonResponse({
        "token": issue_token(user),
        "expiresIn": TOKEN_MAX_AGE,
        "venues": [{"slug": venue.slug, "name": venue.name} for venue in venues],
    })


@require_safe
@never_cache
@door_venue_view
def event_list(request, venue, user):
    events = Event.objects.filter(venue=venue, is_published=True).order_by("starts_at", "pk")
    return JsonResponse({"events": [event_summary(event) for event in events]})


def ticket_data(ticket):
    return {
        "id": str(ticket.pk),
        "ticketType": ticket.ticket_type.name,
        "holderName": ticket.order.buyer_name,
        "usedAt": ticket.used_at.isoformat(),
    }


def void_response(ticket):
    """Entrada anulada (devolución, contracargo o a mano): no entra y no se quema."""
    return JsonResponse({
        "result": "void",
        "ticket": {
            "id": str(ticket.pk),
            "ticketType": ticket.ticket_type.name,
            "holderName": ticket.order.buyer_name,
            "voidedAt": ticket.voided_at.isoformat(),
        },
    }, status=409)


@csrf_exempt
@require_POST
@never_cache
@door_venue_view
def scan(request, venue, user, event_slug):
    event = Event.objects.filter(venue=venue, slug=event_slug, is_published=True).first()
    if event is None:
        return error("not_found", 404)
    data, failure = json_body(request)
    if failure:
        return failure
    invalid = JsonResponse({"result": "invalid"}, status=404)
    try:
        ticket_id = verify_ticket(data.get("token"))
    except InvalidTicketToken:
        return invalid
    ticket = (
        Ticket.objects.select_related("event", "ticket_type__guest_list", "order", "used_by")
        .filter(pk=ticket_id, event__venue=venue)
        .first()
    )
    if ticket is None:
        return invalid
    if ticket.event_id != event.pk:
        return JsonResponse({"result": "wrong_event", "event": event_summary(ticket.event)}, status=409)

    if ticket.voided_at is not None:
        return void_response(ticket)

    now = timezone.now()
    # La hora límite se evalúa con el mismo `now` con el que se quema: no hay ventana entre el chequeo y el UPDATE.
    guest_list = getattr(ticket.ticket_type, "guest_list", None)
    if guest_list is not None and ticket.used_at is None and now > guest_list.valid_until:
        return JsonResponse({
            "result": "list_closed",
            "ticket": {
                "id": str(ticket.pk),
                "ticketType": ticket.ticket_type.name,
                "holderName": ticket.order.buyer_name,
                "validUntil": guest_list.valid_until.isoformat(),
            },
        }, status=409)
    # `voided_at IS NULL` en el mismo UPDATE: no hay ventana entre anular y quemar.
    burned = Ticket.objects.filter(pk=ticket.pk, used_at__isnull=True, voided_at__isnull=True).update(
        used_at=now, used_by=user,
    )
    if burned:
        ticket.used_at = now
        return JsonResponse({"result": "admitted", "ticket": ticket_data(ticket)})
    ticket = Ticket.objects.select_related("ticket_type", "order", "used_by").get(pk=ticket.pk)
    if ticket.voided_at is not None:  # se anuló entre el chequeo y el quemado
        return void_response(ticket)
    return JsonResponse(
        {"result": "already_used", "ticket": {**ticket_data(ticket), "usedBy": ticket.used_by.get_username() if ticket.used_by else None}},
        status=409,
    )


@require_safe
@never_cache
@door_venue_view
def attendance(request, venue, user, event_slug):
    event = Event.objects.filter(venue=venue, slug=event_slug, is_published=True).first()
    if event is None:
        return error("not_found", 404)
    tickets = Ticket.objects.filter(event=event, voided_at__isnull=True)  # las anuladas no cuentan ni se listan
    admitted_count = Count("pk", filter=Q(used_at__isnull=False))
    totals = tickets.aggregate(issued=Count("pk"), admitted=admitted_count)
    types = (
        tickets.values("ticket_type__pk", "ticket_type__slug", "ticket_type__name")
        .annotate(issued=Count("pk"), admitted=admitted_count)
        .order_by("ticket_type__pk")
    )
    admitted = (
        tickets.filter(used_at__isnull=False)
        .select_related("ticket_type", "order", "used_by")
        .order_by("-used_at", "pk")
    )
    pending = (
        tickets.filter(used_at__isnull=True)
        .select_related("ticket_type", "order")
        .order_by("created_at", "pk")
    )
    return JsonResponse({
        "event": event_summary(event),
        "issued": totals["issued"],
        "admitted": totals["admitted"],
        "ticketTypes": [
            {
                "id": row["ticket_type__slug"], "name": row["ticket_type__name"],
                "issued": row["issued"], "admitted": row["admitted"],
            }
            for row in types
        ],
        "tickets": [
            {
                "id": str(ticket.pk),
                "holderName": ticket.order.buyer_name,
                "ticketType": ticket.ticket_type.name,
                "usedAt": ticket.used_at.isoformat() if ticket.used_at else None,
                "usedBy": ticket.used_by.get_username() if ticket.used_by else None,
            }
            for ticket in [*admitted, *pending]
        ],
    })
