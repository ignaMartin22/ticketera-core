import json
import re
from uuid import UUID

from django.core.exceptions import RequestDataTooBig, ValidationError
from django.core.validators import validate_email, validate_slug
from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe

from .models import Event, Order, TicketType, Venue
from .orders import MAX_TICKETS_PER_ORDER, OrderConflict, reserve_order

# E.164: + y entre 8 y 15 dígitos. El front manda los celulares argentinos como +549XXXXXXXXXX.
PHONE_RE = re.compile(r"\+[1-9]\d{7,14}")


def venue_data(venue):
    return {
        "slug": venue.slug,
        "name": venue.name,
        "city": venue.city,
        "address": venue.address,
        "instagram": venue.instagram,
        "tagline": venue.tagline or None,
        "logo": venue.logo or None,
        "cover": venue.cover or None,
        "flyerBackdrop": venue.flyer_backdrop or None,
        "mapsUrl": venue.maps_url or None,
        "accent": venue.accent,
        "feeMode": venue.fee_mode,
        "buyerFeeRate": float(venue.buyer_fee_rate),
    }


def event_data(event):
    return {
        "slug": event.slug,
        "venueSlug": event.venue.slug,
        "title": event.title,
        "lineup": [artist.strip() for artist in event.lineup.splitlines() if artist.strip()],
        "description": event.description,
        "startsAt": event.starts_at.isoformat(),
        "doorsNote": event.doors_note,
        "ageLimit": event.age_limit,
        "dressCode": event.dress_code or None,
        "ticketTypes": [
            {
                "id": ticket_type.slug,
                "name": ticket_type.name,
                "description": ticket_type.description,
                "price": float(ticket_type.price),
                "capacity": ticket_type.capacity,
                "sold": ticket_type.sold,
                "available": max(0, ticket_type.capacity - ticket_type.sold - ticket_type.reserved),
            }
            for ticket_type in event.public_ticket_types
        ],
    }


def published_events(venue):
    return Event.objects.filter(venue=venue, is_published=True).select_related("venue").prefetch_related(
        Prefetch(
            "ticket_types",
            queryset=TicketType.objects.filter(is_active=True).with_reserved(timezone.now()).order_by("pk"),
            to_attr="public_ticket_types",
        ),
    )


@require_safe
def venue_list(request):
    return JsonResponse({"venues": [venue_data(venue) for venue in Venue.objects.filter(is_active=True)]})


@require_safe
def event_list(request, venue_slug):
    venue = get_object_or_404(Venue, slug=venue_slug, is_active=True)
    return JsonResponse({"venue": venue_data(venue), "events": [event_data(event) for event in published_events(venue)]})


@require_safe
def event_detail(request, venue_slug, event_slug):
    venue = get_object_or_404(Venue, slug=venue_slug, is_active=True)
    event = get_object_or_404(published_events(venue), slug=event_slug)
    return JsonResponse({"venue": venue_data(venue), "event": event_data(event)})


def order_data(order, items):
    return {
        "id": str(order.pk),
        "status": "pending_payment" if order.expires_at > timezone.now() else "expired",
        "expiresAt": order.expires_at.isoformat(),
        "currency": "ARS",
        "total": format(sum(item.quantity * (item.unit_price + item.unit_service_fee) for item in items), ".2f"),
        "items": [
            {
                "ticketTypeId": item.ticket_slug,
                "quantity": item.quantity,
                "unitPrice": format(item.unit_price, ".2f"),
                "unitServiceFee": format(item.unit_service_fee, ".2f"),
            }
            for item in items
        ],
    }


def order_input(request):
    try:
        key = UUID(request.headers.get("Idempotency-Key", ""))
        body = request.body
        if len(body) > 8192:
            raise RequestDataTooBig
        data = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise ValidationError("invalid_request") from exc
    if not isinstance(data, dict):
        raise ValidationError("invalid_request")
    email = data.get("email")
    if not isinstance(email, str) or len(email) > 254:
        raise ValidationError("invalid_email")
    email = email.strip()
    validate_email(email)
    name = data.get("name")
    if not isinstance(name, str) or not 1 <= len(" ".join(name.split())) <= 150:
        raise ValidationError("invalid_name")
    name = " ".join(name.split())
    phone = data.get("phone")
    if not isinstance(phone, str) or not PHONE_RE.fullmatch(phone):
        raise ValidationError("invalid_phone")
    items = data.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_TICKETS_PER_ORDER:
        raise ValidationError("invalid_items")
    quantities = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValidationError("invalid_items")
        slug, quantity = item.get("ticketTypeId"), item.get("quantity")
        if not isinstance(slug, str) or len(slug) > 50 or slug in quantities:
            raise ValidationError("invalid_items")
        validate_slug(slug)
        if type(quantity) is not int or not 1 <= quantity <= MAX_TICKETS_PER_ORDER:
            raise ValidationError("invalid_items")
        quantities[slug] = quantity
    if sum(quantities.values()) > MAX_TICKETS_PER_ORDER:
        raise ValidationError("order_limit_exceeded")
    return {"email": email, "name": name, "phone": phone}, quantities, key


@csrf_exempt  # The endpoint accepts only JSON and uses no session credentials.
@require_POST
@never_cache
def order_create(request, venue_slug, event_slug):
    if request.content_type != "application/json":
        return JsonResponse({"error": "expected_json"}, status=415)
    try:
        buyer, quantities, key = order_input(request)
        order, items, created = reserve_order(venue_slug, event_slug, buyer, quantities, key)
    except RequestDataTooBig:
        return JsonResponse({"error": "request_too_large"}, status=413)
    except ValidationError as exc:
        error = str(exc.message) if exc.code is None else "invalid_request"
        return JsonResponse({"error": error}, status=400)
    except Event.DoesNotExist:
        return JsonResponse({"error": "event_unavailable"}, status=404)
    except OrderConflict as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    return JsonResponse({"order": order_data(order, items)}, status=201 if created else 200)


@require_safe
@never_cache
def order_detail(request, venue_slug, event_slug, order_id):
    order = get_object_or_404(Order, pk=order_id, event__slug=event_slug, event__venue__slug=venue_slug)
    return JsonResponse({"order": order_data(order, order.items.all())})
