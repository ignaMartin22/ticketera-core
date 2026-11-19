"""API de portería: login del personal de puerta y escaneo de entradas."""

import json
from datetime import timedelta
from functools import wraps

from django.contrib.auth import authenticate, get_user_model
from django.core import signing
from django.core.exceptions import RequestDataTooBig
from django.db.models import Count, Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe

from .models import DoorLoginFailure, Event, Membership, Ticket, Venue
from .qr import InvalidTicketToken, verify_ticket

TOKEN_SALT = "catalog.door-token"
TOKEN_MAX_AGE = 12 * 60 * 60
MAX_BODY_BYTES = 8192
LOGIN_MAX_FAILURES = 10
LOGIN_FAILURE_WINDOW = timedelta(minutes=15)
LOGIN_FAILURE_RETENTION = timedelta(hours=24)
DOOR_ROLES = (Membership.Role.DOOR, Membership.Role.ADMIN)


def error(code, status):
    return JsonResponse({"error": code}, status=status)


def json_body(request):
    """Devuelve (datos, respuesta_de_error); exactamente uno de los dos es None."""
    if request.content_type != "application/json":
        return None, error("expected_json", 415)
    try:
        body = request.body
        if len(body) > MAX_BODY_BYTES:
            raise RequestDataTooBig
        data = json.loads(body)
    except RequestDataTooBig:
        return None, error("request_too_large", 413)
    except (ValueError, RecursionError):
        return None, error("invalid_request", 400)
    if not isinstance(data, dict):
        return None, error("invalid_request", 400)
    return data, None


def user_from_request(request):
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme != "Bearer" or not token:
        return None
    try:
        payload = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
        uid = payload["uid"]
        return get_user_model().objects.filter(pk=uid, is_active=True).first()
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None


def door_venue_view(view):
    """Autentica con el token y exige rol door/admin en el boliche de la URL."""

    @wraps(view)
    def wrapper(request, venue_slug, *args, **kwargs):
        user = user_from_request(request)
        if user is None:
            return error("unauthorized", 401)
        venue = Venue.objects.filter(
            slug=venue_slug, is_active=True,
            memberships__user=user, memberships__role__in=DOOR_ROLES,
        ).first()
        if venue is None:
            return error("not_found", 404)
        return view(request, venue, user, *args, **kwargs)

    return wrapper


def event_summary(event):
    return {"slug": event.slug, "title": event.title, "startsAt": event.starts_at.isoformat()}


def login_retry_after(key):
    """Segundos de espera si el usuario agotó sus intentos; 0 si puede intentar."""
    now = timezone.now()
    recent = list(
        DoorLoginFailure.objects.filter(username=key, created_at__gt=now - LOGIN_FAILURE_WINDOW)
        .order_by("-created_at").values_list("created_at", flat=True)[:LOGIN_MAX_FAILURES]
    )
    if len(recent) < LOGIN_MAX_FAILURES:
        return 0
    wait = recent[-1] + LOGIN_FAILURE_WINDOW - now
    return max(1, int(wait.total_seconds()) + 1)


def record_login_failure(key):
    DoorLoginFailure.objects.create(username=key)
    DoorLoginFailure.objects.filter(created_at__lt=timezone.now() - LOGIN_FAILURE_RETENTION).delete()


@csrf_exempt
@require_POST
@never_cache
def login(request):
    data, failure = json_body(request)
    if failure:
        return failure
    username, password = data.get("username"), data.get("password")
    user = None
    key = None
    if isinstance(username, str) and isinstance(password, str):
        key = username.strip().lower()[:150]
        retry_after = login_retry_after(key)
        if retry_after:
            response = JsonResponse({"error": "too_many_attempts", "retryAfter": retry_after}, status=429)
            response["Retry-After"] = str(retry_after)
            return response
        user = authenticate(request, username=username, password=password)
    if user is None or not user.is_active:
        if key is not None:
            record_login_failure(key)
        return error("invalid_credentials", 401)
    DoorLoginFailure.objects.filter(username=key).delete()
    venues = Venue.objects.filter(
        is_active=True, memberships__user=user, memberships__role__in=DOOR_ROLES,
    ).distinct().order_by("slug")
    if not venues:
        return error("not_door_staff", 403)
    return JsonResponse({
        "token": signing.dumps({"uid": user.pk}, salt=TOKEN_SALT),
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
        Ticket.objects.select_related("event", "ticket_type", "order", "used_by")
        .filter(pk=ticket_id, event__venue=venue)
        .first()
    )
    if ticket is None:
        return invalid
    if ticket.event_id != event.pk:
        return JsonResponse({"result": "wrong_event", "event": event_summary(ticket.event)}, status=409)

    now = timezone.now()
    burned = Ticket.objects.filter(pk=ticket.pk, used_at__isnull=True).update(used_at=now, used_by=user)
    if burned:
        ticket.used_at = now
        return JsonResponse({"result": "admitted", "ticket": ticket_data(ticket)})
    ticket = Ticket.objects.select_related("ticket_type", "order", "used_by").get(pk=ticket.pk)
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
    tickets = Ticket.objects.filter(event=event)
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
