from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from catalog.models import Event, FeeChange, TicketType, Venue

VENUE = {
    "slug": "infinity",
    "name": "Infinity",
    "city": "San Salvador de Jujuy",
    "address": "San Salvador de Jujuy",
    "instagram": "moon.nochesinfinitas",
    "tagline": "Noches infinitas",
    "logo": "/brand/infinity/infinity-logo.svg",
    "cover": "/venues/infinity/pista-luces.jpg",
    "flyer_backdrop": "/venues/infinity/hero-poster.jpg",
    "maps_url": "https://www.google.com/maps/search/?api=1&query=-24.1805835,-65.2718714",
    "accent": "#ee10b8",
    "fee_mode": Venue.FeeMode.BUYER,
    "buyer_fee_rate": Decimal("0.1"),
    "is_active": True,
}

# Mismas fechas que los datos de prueba de TicketJuy (src/lib/mock-data.ts).
# days: días desde hoy hasta el inicio (de madrugada, en la hora local del boliche).
EVENTS = [
    {
        "slug": "noche-de-luna-llena", "title": "Noche de Luna Llena", "days": 2, "start": time(0, 30),
        "lineup": "Vale Quispe\nResidentes Infinity",
        "description": "La fecha grande del mes. Pista principal con house y tech house hasta el cierre.",
        "doors_note": "Hasta las 3:00",
        "tickets": [
            ("anticipada-1", "Anticipada, 1ª tanda", 8000, 200, 200, "Ingreso general."),
            ("anticipada-2", "Anticipada, 2ª tanda", 10000, 300, 271, "Ingreso general."),
            ("vip", "VIP", 18000, 80, 12, "Fila VIP en la puerta y acceso al sector VIP."),
        ],
    },
    {
        "slug": "cumbia-hasta-el-alba", "title": "Cumbia hasta el alba", "days": 3, "start": time(0, 30),
        "lineup": "Los del Cerro\nDJ Tincho",
        "description": "Cumbia, cuarteto y algo de reggaetón. Banda en vivo a las 2:00.",
        "doors_note": "Hasta las 3:30",
        "tickets": [
            ("general", "General", 7000, 450, 188, "Ingreso general."),
            ("grupo-4", "Pack 4 amigos", 24000, 60, 41, "4 ingresos generales."),
        ],
    },
    {
        "slug": "fiesta-de-egresados", "title": "Fiesta de egresados", "days": 16, "start": time(0, 30),
        "lineup": "Residentes Infinity",
        "description": "Promo 2026. Fotógrafo en la entrada y cotillón incluido.",
        "doors_note": "Hasta las 2:30", "dress_code": "Elegante sport",
        "tickets": [("general", "General", 9000, 500, 104, "Ingreso general.")],
    },
    {
        "slug": "fin-de-ano", "title": "Año nuevo en Infinity", "days": 43, "start": time(1, 30),
        "lineup": "Vale Quispe\nDJ Tincho\nResidentes Infinity",
        "description": "Abrimos después del brindis. Barra libre de agua toda la noche.",
        "doors_note": "Hasta las 4:00",
        "tickets": [
            ("preventa", "Preventa", 15000, 300, 300, "Ingreso general."),
            ("general", "General", 20000, 400, 62, "Ingreso general."),
            ("vip", "VIP con mesa", 120000, 20, 7, "Mesa para 6 con una botella."),
        ],
    },
]


class Command(BaseCommand):
    help = "Crea o actualiza el boliche Infinity con fechas de demostración. Solo para desarrollo."

    @transaction.atomic
    def handle(self, *args, **options):
        slug = VENUE["slug"]
        venue, _ = Venue.objects.update_or_create(slug=slug, defaults={k: v for k, v in VENUE.items() if k != "slug"})
        if not venue.fee_changes.exists():
            FeeChange.objects.create(
                venue=venue, fee_mode=venue.fee_mode, buyer_fee_rate=venue.buyer_fee_rate,
                platform_fee_rate=venue.platform_fee_rate, note="Datos de demostración",
            )
        today = timezone.localdate()
        for data in EVENTS:
            data = dict(data)
            tickets = data.pop("tickets")
            day = today + timedelta(days=data.pop("days"))
            starts_at = timezone.make_aware(datetime.combine(day, data.pop("start")))
            event, _ = Event.objects.update_or_create(
                venue=venue, slug=data.pop("slug"),
                defaults={**data, "starts_at": starts_at, "is_published": True},
            )
            for ticket_slug, name, price, capacity, sold, description in tickets:
                TicketType.objects.update_or_create(
                    event=event, slug=ticket_slug,
                    defaults={
                        "name": name, "price": price, "capacity": capacity, "sold": sold,
                        "description": description, "is_active": True,
                    },
                )
        self.stdout.write(self.style.SUCCESS(f"Boliche {slug} listo con {len(EVENTS)} fechas."))
