"""Listas de RRPP: alta y edición desde la gestión del boliche, y carga de invitados por el RRPP.

Una lista es un `TicketType` de tipo `list` (cupo = capacity, emitido = sold) más el RRPP y la hora
límite. Cada invitado es un pedido gratis pagado con una entrada, así que el mail con el QR sale por
el mismo camino que cualquier compra.
"""

from datetime import timedelta
from functools import wraps
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt

from django.contrib.auth import get_user_model

from .manage import venue_admin_required
from .models import Event, GuestList, Membership, Order, OrderItem, Ticket, TicketType
from .staff_auth import error, json_body, user_from_request
from .tickets import issue_tickets

MAX_QUOTA = 5000
# Misma ventana que usa el panel para mostrar fechas vigentes: la fecha sigue "viva" 24 h después de empezar.
EVENT_WINDOW = timedelta(hours=24)
PROMOTER_ROLES = (Membership.Role.RRPP, Membership.Role.ADMIN)


class ListConflict(Exception):
    pass


# ---------- serialización ----------

def promoter_name(user):
    return user.get_full_name() or user.get_username()


def promoter_data(user):
    return {"username": user.get_username(), "name": promoter_name(user)}


def with_stats(queryset):
    return queryset.select_related("event__venue", "promoter", "ticket_type").annotate(
        admitted_count=Count("ticket_type__tickets", filter=Q(ticket_type__tickets__used_at__isnull=False)),
    )


def list_data(guest_list):
    event = guest_list.event
    return {
        "id": guest_list.pk,
        "name": guest_list.name,
        "promoter": promoter_data(guest_list.promoter),
        "quota": guest_list.ticket_type.capacity,
        "issued": guest_list.ticket_type.sold,
        "admitted": guest_list.admitted_count,
        "validUntil": guest_list.valid_until.isoformat(),
        "isActive": guest_list.is_active,
        "event": {
            "slug": event.slug,
            "title": event.title,
            "startsAt": event.starts_at.isoformat(),
            "venueSlug": event.venue.slug,
            "venueName": event.venue.name,
        },
    }


def guest_data(ticket):
    return {
        "name": ticket.order.buyer_name,
        "email": ticket.order.buyer_email,
        "addedAt": ticket.created_at.isoformat(),
        "usedAt": ticket.used_at.isoformat() if ticket.used_at else None,
    }


def fresh(list_id):
    return list_data(with_stats(GuestList.objects.filter(pk=list_id)).get())


# ---------- validación ----------

def list_name(data):
    name = data.get("name")
    name = " ".join(name.split()) if isinstance(name, str) else ""
    if not 1 <= len(name) <= 120:
        raise ValidationError("invalid_name")
    return name


def list_quota(data):
    quota = data.get("quota")
    if type(quota) is not int or not 1 <= quota <= MAX_QUOTA:
        raise ValidationError("invalid_quota")
    return quota


def list_valid_until(data, must_be_future):
    value = data.get("validUntil")
    try:
        parsed = parse_datetime(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or timezone.is_naive(parsed):
        raise ValidationError("invalid_valid_until")
    if must_be_future and parsed <= timezone.now():
        raise ValidationError("invalid_valid_until")
    return parsed


def validation_response(exc):
    code = str(exc.message) if exc.code is None else "invalid_request"
    return error(code, 400)


# ---------- gestión del boliche (admin) ----------

def promoter_users(venue, roles=PROMOTER_ROLES):
    return get_user_model().objects.filter(
        is_active=True, membership__venue=venue, membership__role__in=roles,
    ).distinct()


@never_cache
@venue_admin_required
def promoter_list(request, venue):
    if request.method != "GET":
        return error("method_not_allowed", 405)
    users = promoter_users(venue, (Membership.Role.RRPP,)).order_by("username")
    return JsonResponse({"promoters": [promoter_data(user) for user in users]})


@never_cache
@venue_admin_required
def list_collection(request, venue, event_slug):
    event = Event.objects.filter(venue=venue, slug=event_slug).first()
    if event is None:
        return error("event_unavailable", 404)
    if request.method == "GET":
        lists = with_stats(GuestList.objects.filter(event=event)).order_by("pk")
        return JsonResponse({"lists": [list_data(guest_list) for guest_list in lists]})
    if request.method == "POST":
        return list_create(request, venue, event)
    return error("method_not_allowed", 405)


def list_create(request, venue, event):
    data, failure = json_body(request)
    if failure:
        return failure
    try:
        name = list_name(data)
        quota = list_quota(data)
        valid_until = list_valid_until(data, must_be_future=True)
        username = data.get("promoterUsername")
        promoter = (
            promoter_users(venue).filter(username=username).first() if isinstance(username, str) else None
        )
        if promoter is None:
            raise ValidationError("invalid_promoter")
        guest_list = create_list(event, promoter, name, quota, valid_until)
    except ValidationError as exc:
        return validation_response(exc)
    except ListConflict as exc:
        return error(str(exc), 409)
    return JsonResponse({"list": fresh(guest_list.pk)}, status=201)


@transaction.atomic
def create_list(event, promoter, name, quota, valid_until):
    # El bloqueo de la fecha ordena las altas simultáneas (slug del tipo y una lista por RRPP).
    event = Event.objects.select_for_update().get(pk=event.pk)
    if GuestList.objects.filter(event=event, promoter=promoter).exists():
        raise ListConflict("list_exists")
    n = 0
    while True:
        n += 1
        slug = f"lista-{n}"
        if not event.ticket_types.filter(slug=slug).exists():
            break
    ticket_type = TicketType.objects.create(
        event=event, slug=slug, name=name, kind=TicketType.Kind.LIST, price=0, capacity=quota,
        description="Entrada de lista.",
    )
    return GuestList.objects.create(
        event=event, promoter=promoter, name=name, ticket_type=ticket_type, valid_until=valid_until,
    )


@never_cache
@venue_admin_required
def list_resource(request, venue, event_slug, list_id):
    guest_list = GuestList.objects.filter(pk=list_id, event__venue=venue, event__slug=event_slug).first()
    if guest_list is None:
        return error("list_unavailable", 404)
    if request.method != "PATCH":
        return error("method_not_allowed", 405)
    data, failure = json_body(request)
    if failure:
        return failure
    try:
        fields = {}
        if "name" in data:
            fields["name"] = list_name(data)
        if "quota" in data:
            fields["quota"] = list_quota(data)
        if "validUntil" in data:
            fields["valid_until"] = list_valid_until(data, must_be_future=False)
        if "isActive" in data:
            if type(data["isActive"]) is not bool:
                raise ValidationError("invalid_is_active")
            fields["is_active"] = data["isActive"]
        update_list(guest_list.pk, fields)
    except ValidationError as exc:
        return validation_response(exc)
    except ListConflict as exc:
        return error(str(exc), 409)
    return JsonResponse({"list": fresh(guest_list.pk)})


@transaction.atomic
def update_list(list_id, fields):
    guest_list = GuestList.objects.select_related("ticket_type").get(pk=list_id)
    # Mismo orden de bloqueo que al agregar invitados: el cupo no puede bajar de lo emitido en paralelo.
    ticket_type = TicketType.objects.select_for_update().get(pk=guest_list.ticket_type_id)
    if "quota" in fields:
        if fields["quota"] < ticket_type.sold:
            raise ListConflict("quota_below_issued")
        ticket_type.capacity = fields["quota"]
    if "name" in fields:
        ticket_type.name = fields["name"]
        guest_list.name = fields["name"]
    ticket_type.save()
    for attr in ("valid_until", "is_active"):
        if attr in fields:
            setattr(guest_list, attr, fields[attr])
    guest_list.save()


# ---------- RRPP ----------

def rrpp_view(view):
    """Autentica con el token de staff. La vista recibe (request, user, *args)."""

    @csrf_exempt
    @never_cache
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        user = user_from_request(request)
        if user is None:
            return error("unauthorized", 401)
        return view(request, user, *args, **kwargs)

    return wrapper


def own_lists(user):
    """Listas del usuario como RRPP, mientras conserve su rol (rrpp o admin) en ese boliche activo."""
    still_member = Membership.objects.filter(
        user=user, venue=OuterRef("event__venue"), role__in=PROMOTER_ROLES, venue__is_active=True,
    )
    return GuestList.objects.filter(promoter=user).filter(Exists(still_member))


def operable_lists(user):
    """Las propias más todas las del boliche donde el usuario es admin."""
    admin_of = Membership.objects.filter(
        user=user, venue=OuterRef("event__venue"), role=Membership.Role.ADMIN, venue__is_active=True,
    )
    mine = Q(pk__in=own_lists(user).values("pk"))
    return GuestList.objects.filter(mine | Q(Exists(admin_of)))


@rrpp_view
def rrpp_lists(request, user):
    if request.method != "GET":
        return error("method_not_allowed", 405)
    since = timezone.now() - EVENT_WINDOW
    lists = with_stats(own_lists(user).filter(event__starts_at__gte=since)).order_by("event__starts_at", "pk")
    return JsonResponse({"lists": [list_data(guest_list) for guest_list in lists]})


@rrpp_view
def rrpp_list_detail(request, user, list_id):
    if request.method != "GET":
        return error("method_not_allowed", 405)
    guest_list = with_stats(operable_lists(user).filter(pk=list_id)).first()
    if guest_list is None:
        return error("not_found", 404)
    tickets = (
        Ticket.objects.filter(ticket_type_id=guest_list.ticket_type_id)
        .select_related("order").order_by("-created_at", "-pk")
    )
    return JsonResponse({"list": list_data(guest_list), "guests": [guest_data(ticket) for ticket in tickets]})


@rrpp_view
def rrpp_guest_create(request, user, list_id):
    if request.method != "POST":
        return error("method_not_allowed", 405)
    list_pk = operable_lists(user).filter(pk=list_id).values_list("pk", flat=True).first()
    if list_pk is None:
        return error("not_found", 404)
    data, failure = json_body(request)
    if failure:
        return failure
    name = data.get("name")
    name = " ".join(name.split()) if isinstance(name, str) else ""
    if not 1 <= len(name) <= 150:
        return error("invalid_name", 400)
    email = data.get("email")
    email = email.strip() if isinstance(email, str) else ""
    try:
        if len(email) > 254:
            raise ValidationError("invalid_email")
        validate_email(email)
    except ValidationError:
        return error("invalid_email", 400)
    try:
        ticket = add_guest(list_pk, name, email)
    except ListConflict as exc:
        return error(str(exc), 409)
    return JsonResponse({"guest": guest_data(ticket), "list": fresh(list_pk)}, status=201)


@transaction.atomic
def add_guest(list_id, name, email):
    guest_list = GuestList.objects.select_related("event").get(pk=list_id)
    # Primero la fecha (serializa el chequeo de mail repetido entre listas), después el tipo de la lista (cupo).
    event = Event.objects.select_for_update().get(pk=guest_list.event_id)
    ticket_type = TicketType.objects.select_for_update().get(pk=guest_list.ticket_type_id)
    guest_list = GuestList.objects.get(pk=list_id)
    now = timezone.now()
    if not guest_list.is_active or now > guest_list.valid_until:
        raise ListConflict("list_closed")
    if Ticket.objects.filter(
        event=event, ticket_type__kind=TicketType.Kind.LIST, order__buyer_email__iexact=email,
    ).exists():
        raise ListConflict("already_on_list")
    if ticket_type.sold >= ticket_type.capacity:
        raise ListConflict("quota_full")
    order = Order.objects.create(
        event=event, buyer_email=email, buyer_name=name, buyer_phone="",
        idempotency_key=uuid4(), expires_at=now,
    )
    OrderItem.objects.create(
        order=order, ticket_type=ticket_type, ticket_slug=ticket_type.slug,
        quantity=1, unit_price=0, unit_service_fee=0,
    )
    (ticket,) = issue_tickets(order.pk)
    ticket.order = order
    return ticket
