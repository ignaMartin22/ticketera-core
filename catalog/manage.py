import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps

from django.core.exceptions import RequestDataTooBig, ValidationError
from django.db import transaction
from django.db.models import Prefetch, ProtectedError
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt

from .covers import MAX_UPLOAD_BYTES, cover_url, delete_cover_file, process_cover, save_cover
from .models import Event, Membership, TicketType, Venue
from .staff_auth import bearer_user

MAX_BODY_SIZE = 8192

DEFAULT_DESCRIPTIONS = {
    TicketType.Kind.COMMON: "Ingreso general.",
    TicketType.Kind.VIP: "Fila VIP en la puerta y acceso al sector VIP.",
}


class ManageConflict(Exception):
    pass


def venue_admin_required(view):
    """Exige un token Bearer de un usuario que administra el boliche de la URL (o superusuario)."""

    @csrf_exempt
    @wraps(view)
    def wrapper(request, venue_slug, *args, **kwargs):
        user = bearer_user(request)
        if user is None:
            return JsonResponse({"error": "unauthorized"}, status=401)
        venue = Venue.objects.filter(slug=venue_slug).first()
        if venue is None:
            return JsonResponse({"error": "venue_unavailable"}, status=404)
        manages = user.is_superuser or Membership.objects.filter(
            venue=venue, user=user, role=Membership.Role.ADMIN
        ).exists()
        if not manages:
            return JsonResponse({"error": "forbidden"}, status=403)
        request.user = user
        return view(request, venue, *args, **kwargs)

    return wrapper


def managed_events(venue):
    return Event.objects.filter(venue=venue).prefetch_related(
        Prefetch(
            "ticket_types",
            queryset=TicketType.objects.with_reserved(timezone.now()).order_by("pk"),
            to_attr="managed_ticket_types",
        )
    )


def managed_ticket_data(ticket):
    return {
        "id": ticket.slug,
        "name": ticket.name,
        "kind": ticket.kind,
        "description": ticket.description,
        "price": float(ticket.price),
        "capacity": ticket.capacity,
        "sold": ticket.sold,
        "reserved": ticket.reserved,
        "isActive": ticket.is_active,
    }


def managed_event_data(event):
    tickets = event.managed_ticket_types
    sold = sum(ticket.sold for ticket in tickets)
    capacity = sum(ticket.capacity for ticket in tickets)
    reserved = sum(ticket.reserved for ticket in tickets)
    if event.starts_at <= timezone.now():
        status = "finished"
    elif not event.sales_open:
        status = "paused"
    elif capacity - sold - reserved <= 0:
        status = "sold_out"
    else:
        status = "on_sale"
    return {
        "slug": event.slug,
        "title": event.title,
        "startsAt": event.starts_at.isoformat(),
        "isPublished": event.is_published,
        "salesOpen": event.sales_open,
        "sold": sold,
        "capacity": capacity,
        "reserved": reserved,
        "status": status,
    }


def managed_event_detail(event):
    return {
        **managed_event_data(event),
        "lineup": [artist.strip() for artist in event.lineup.splitlines() if artist.strip()],
        "description": event.description,
        "doorsNote": event.doors_note,
        "ageLimit": event.age_limit,
        "dressCode": event.dress_code or None,
        "cover": cover_url(event.cover),
        "ticketTypes": [managed_ticket_data(ticket) for ticket in event.managed_ticket_types],
    }


def json_input(request):
    try:
        body = request.body
        if len(body) > MAX_BODY_SIZE:
            raise RequestDataTooBig
        data = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise ValidationError("invalid_request") from exc
    if not isinstance(data, dict):
        raise ValidationError("invalid_request")
    return data


def optional_text(data, key, max_length, code):
    value = data[key]
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > max_length:
        raise ValidationError(code)
    return value


def price_value(item):
    value = item.get("price")
    if type(value) is bool:
        raise ValidationError("invalid_price")
    try:
        price = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValidationError("invalid_price") from exc
    if not price.is_finite() or not 0 < price <= Decimal("9999999999.99"):
        raise ValidationError("invalid_price")
    return price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def capacity_value(item):
    capacity = item.get("capacity")
    if type(capacity) is not int or capacity <= 0:
        raise ValidationError("invalid_capacity")
    return capacity


def event_fields(data, creating):
    if "slug" in data:
        raise ValidationError("invalid_slug")
    fields = {}
    if creating or "title" in data:
        title = data.get("title")
        title = " ".join(title.split()) if isinstance(title, str) else ""
        if not 1 <= len(title) <= 200:
            raise ValidationError("invalid_title")
        fields["title"] = title
    if creating or "startsAt" in data:
        starts_at = data.get("startsAt")
        try:
            parsed = parse_datetime(starts_at) if isinstance(starts_at, str) else None
        except ValueError:
            parsed = None
        if parsed is None:
            raise ValidationError("invalid_starts_at")
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed)
        if creating and parsed <= timezone.now():
            raise ValidationError("invalid_starts_at")
        fields["starts_at"] = parsed
    if "lineup" in data:
        lineup = data["lineup"]
        if lineup is None:
            fields["lineup"] = ""
        elif isinstance(lineup, str):
            fields["lineup"] = lineup
        elif isinstance(lineup, list) and all(isinstance(artist, str) for artist in lineup):
            fields["lineup"] = "\n".join(lineup)
        else:
            raise ValidationError("invalid_lineup")
    if "description" in data:
        fields["description"] = optional_text(data, "description", 20000, "invalid_description")
    if "doorsNote" in data:
        fields["doors_note"] = optional_text(data, "doorsNote", 200, "invalid_doors_note")
    if "dressCode" in data:
        fields["dress_code"] = optional_text(data, "dressCode", 120, "invalid_dress_code")
    if "ageLimit" in data:
        age_limit = data["ageLimit"]
        if type(age_limit) is not int or not 0 <= age_limit <= 99:
            raise ValidationError("invalid_age_limit")
        fields["age_limit"] = age_limit
    elif creating:
        fields["age_limit"] = 18
    for key, attr in (("isPublished", "is_published"), ("salesOpen", "sales_open")):
        if key in data:
            value = data[key]
            if type(value) is not bool:
                raise ValidationError(f"invalid_{attr}")
            fields[attr] = value
        elif creating:
            fields[attr] = attr == "sales_open"
    return fields


def ticket_items_input(data, creating):
    items = data.get("ticketTypes")
    if items is None:
        if creating:
            raise ValidationError("invalid_ticket_types")
        return None
    if not isinstance(items, list) or (creating and not items):
        raise ValidationError("invalid_ticket_types")
    return items


def ticket_type_fields(item, creating):
    if not isinstance(item, dict):
        raise ValidationError("invalid_ticket_types")
    if "sold" in item:
        raise ValidationError("invalid_sold")
    if creating and "slug" in item:
        raise ValidationError("invalid_slug")
    fields = {}
    if creating or "name" in item:
        name = item.get("name")
        name = " ".join(name.split()) if isinstance(name, str) else ""
        if not 1 <= len(name) <= 150:
            raise ValidationError("invalid_name")
        fields["name"] = name
    if creating or "kind" in item:
        kind = item.get("kind", TicketType.Kind.COMMON)
        if kind not in TicketType.Kind.values:
            raise ValidationError("invalid_kind")
        fields["kind"] = kind
    if creating or "price" in item:
        fields["price"] = price_value(item)
    if creating or "capacity" in item:
        fields["capacity"] = capacity_value(item)
    if "description" in item:
        fields["description"] = optional_text(item, "description", 20000, "invalid_description")
    elif creating:
        fields["description"] = DEFAULT_DESCRIPTIONS[fields["kind"]]
    if "isActive" in item:
        is_active = item["isActive"]
        if type(is_active) is not bool:
            raise ValidationError("invalid_is_active")
        fields["is_active"] = is_active
    elif creating:
        fields["is_active"] = True
    return fields


def new_event_slug(venue, title):
    base = slugify(title)[:60] or "fecha"
    slug, n = base, 1
    while venue.events.filter(slug=slug).exists():
        n += 1
        slug = f"{base}-{n}"
    return slug


def new_ticket_slug(event, kind):
    prefix = "vip" if kind == TicketType.Kind.VIP else "general"
    n = 0
    while True:
        n += 1
        slug = f"{prefix}-{n}"
        if not event.ticket_types.filter(slug=slug).exists():
            return slug


@never_cache
@venue_admin_required
def event_collection(request, venue):
    if request.method == "GET":
        return JsonResponse({"events": [managed_event_data(event) for event in managed_events(venue)]})
    if request.method == "POST":
        return event_create(request, venue)
    return JsonResponse({"error": "method_not_allowed"}, status=405)


@never_cache
@venue_admin_required
def event_resource(request, venue, event_slug):
    event = managed_events(venue).filter(slug=event_slug).first()
    if event is None:
        return JsonResponse({"error": "event_unavailable"}, status=404)
    if request.method == "GET":
        return JsonResponse({"event": managed_event_detail(event)})
    if request.method == "PATCH":
        return event_update(request, venue, event)
    if request.method == "DELETE":
        return event_delete(request, event)
    return JsonResponse({"error": "method_not_allowed"}, status=405)


def event_create(request, venue):
    if request.content_type != "application/json":
        return JsonResponse({"error": "expected_json"}, status=415)
    try:
        data = json_input(request)
        fields = event_fields(data, creating=True)
        items = [ticket_type_fields(item, creating=True) for item in ticket_items_input(data, creating=True)]
        event = create_event(venue, fields, items)
    except RequestDataTooBig:
        return JsonResponse({"error": "request_too_large"}, status=413)
    except ValidationError as exc:
        error = str(exc.message) if exc.code is None else "invalid_request"
        return JsonResponse({"error": error}, status=400)
    return JsonResponse({"event": managed_event_detail(managed_events(venue).get(pk=event.pk))}, status=201)


@transaction.atomic
def create_event(venue, fields, items):
    # El bloqueo del boliche ordena altas simultáneas con el mismo título (mismo slug base).
    venue = Venue.objects.select_for_update().get(pk=venue.pk)
    event = Event.objects.create(venue=venue, slug=new_event_slug(venue, fields["title"]), **fields)
    for ticket_fields in items:
        TicketType.objects.create(
            event=event, slug=new_ticket_slug(event, ticket_fields["kind"]), **ticket_fields
        )
    return event


def event_update(request, venue, event):
    if request.content_type != "application/json":
        return JsonResponse({"error": "expected_json"}, status=415)
    try:
        data = json_input(request)
        fields = event_fields(data, creating=False)
        items = ticket_items_input(data, creating=False)
        update_event(event, fields, items)
    except RequestDataTooBig:
        return JsonResponse({"error": "request_too_large"}, status=413)
    except ValidationError as exc:
        error = str(exc.message) if exc.code is None else "invalid_request"
        return JsonResponse({"error": error}, status=400)
    except ManageConflict as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    return JsonResponse({"event": managed_event_detail(managed_events(venue).get(pk=event.pk))})


@transaction.atomic
def update_event(event, fields, items):
    # El bloqueo de la fecha ordena la edición contra las reservas (reserve_order toma la misma fila).
    event = Event.objects.select_for_update().get(pk=event.pk)
    for attr, value in fields.items():
        setattr(event, attr, value)
    if fields:
        event.save()
    if items is not None:
        update_ticket_types(event, items)


def update_ticket_types(event, items):
    # Las reservas bloquean las mismas filas: la edición espera a cualquier compra en curso.
    existing = {
        ticket.slug: ticket
        for ticket in TicketType.objects.select_for_update().filter(event=event)
    }
    reserved = dict(
        TicketType.objects.filter(event=event)
        .with_reserved(timezone.now())
        .values_list("slug", "reserved")
    )
    seen = set()
    for item in items:
        slug = item.get("slug") if isinstance(item, dict) else None
        if slug is None:
            ticket_fields = ticket_type_fields(item, creating=True)
            TicketType.objects.create(
                event=event, slug=new_ticket_slug(event, ticket_fields["kind"]), **ticket_fields
            )
            continue
        if not isinstance(slug, str) or slug in seen or slug not in existing:
            raise ValidationError("invalid_ticket_types")
        seen.add(slug)
        ticket_fields = ticket_type_fields(item, creating=False)
        ticket = existing[slug]
        if "capacity" in ticket_fields and ticket_fields["capacity"] < ticket.sold + reserved[slug]:
            raise ManageConflict("capacity_below_sales")
        for attr, value in ticket_fields.items():
            setattr(ticket, attr, value)
        if ticket_fields:
            ticket.save()


def event_delete(request, event):
    if sum(ticket.sold for ticket in event.managed_ticket_types):
        return JsonResponse({"error": "has_sales"}, status=409)
    cover = event.cover
    try:
        event.delete()
    except ProtectedError:
        return JsonResponse({"error": "has_orders"}, status=409)
    delete_cover_file(cover)
    return HttpResponse(status=204)


@never_cache
@venue_admin_required
def event_cover(request, venue, event_slug):
    """POST multipart con el archivo en `file`: reemplaza la portada. DELETE: la saca."""
    event = Event.objects.filter(venue=venue, slug=event_slug).select_related("venue").first()
    if event is None:
        return JsonResponse({"error": "event_unavailable"}, status=404)
    if request.method == "DELETE":
        old = event.cover
        Event.objects.filter(pk=event.pk).update(cover="")
        delete_cover_file(old)
        return JsonResponse({"cover": None})
    if request.method != "POST":
        return JsonResponse({"error": "method_not_allowed"}, status=405)
    # Un archivo enorme se rechaza antes de leerlo (el margen es para el resto del multipart).
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length > MAX_UPLOAD_BYTES + 64 * 1024:
        return JsonResponse({"error": "cover_too_large"}, status=413)
    upload = request.FILES.get("file")
    if upload is None:
        return JsonResponse({"error": "invalid_cover"}, status=400)
    try:
        data = process_cover(upload)
    except ValidationError as exc:
        error = str(exc.message)
        return JsonResponse({"error": error}, status=413 if error == "cover_too_large" else 400)
    path = save_cover(event, data)
    with transaction.atomic():
        locked = Event.objects.select_for_update().get(pk=event.pk)
        old = locked.cover
        locked.cover = path
        locked.save(update_fields=["cover"])
    delete_cover_file(old)
    return JsonResponse({"cover": cover_url(path)})
