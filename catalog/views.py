from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_safe

from .models import Event, TicketType, Venue


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
            }
            for ticket_type in event.public_ticket_types
        ],
    }


def published_events(venue):
    return Event.objects.filter(venue=venue, is_published=True).select_related("venue").prefetch_related(
        Prefetch(
            "ticket_types",
            queryset=TicketType.objects.filter(is_active=True),
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
