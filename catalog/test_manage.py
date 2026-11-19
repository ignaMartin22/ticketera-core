import hashlib
import secrets
from datetime import timedelta
from io import StringIO
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import ApiToken, Event, Membership, Order, OrderItem, TicketType, Venue


def auth(key):
    return {"Authorization": f"Bearer {key}"}


def create_token(user, active=True):
    key = secrets.token_urlsafe(32)
    ApiToken.objects.create(
        key_hash=hashlib.sha256(key.encode()).hexdigest(), hint=key[-6:], user=user, is_active=active,
    )
    return key


def event_payload(**overrides):
    payload = {
        "title": "Noche de prueba",
        "startsAt": (timezone.now() + timedelta(days=7)).isoformat(),
        "ticketTypes": [{"name": "General", "price": 5000, "capacity": 100}],
    }
    payload.update(overrides)
    return payload


class ManageApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.moon = Venue.objects.create(slug="moon", name="Moon", is_active=True)
        cls.other = Venue.objects.create(slug="other", name="Otro", is_active=True)
        User = get_user_model()
        cls.admin = User.objects.create_user(username="dueno@moon.com", email="dueno@moon.com")
        cls.door = User.objects.create_user(username="puerta@moon.com", email="puerta@moon.com")
        cls.platform = User.objects.create_superuser(username="root", email="root@ticketera.com")
        Membership.objects.create(venue=cls.moon, user=cls.admin, role=Membership.Role.ADMIN)
        Membership.objects.create(venue=cls.moon, user=cls.door, role=Membership.Role.DOOR)
        cls.key = create_token(cls.admin)
        cls.door_key = create_token(cls.door)
        cls.platform_key = create_token(cls.platform)
        cls.event = cls.make_event(cls.moon, "noche", "Noche", days=5)
        cls.ticket = TicketType.objects.create(
            event=cls.event, slug="general-1", name="General", price=5000, capacity=100,
        )
        cls.draft = cls.make_event(cls.moon, "borrador", "Borrador", days=8, published=False)
        cls.past = cls.make_event(cls.moon, "vieja", "Vieja", days=-2)
        TicketType.objects.create(
            event=cls.past, slug="general-1", name="General", price=1000, capacity=50, sold=50,
        )
        cls.foreign = cls.make_event(cls.other, "ajena", "Ajena", days=5)
        TicketType.objects.create(
            event=cls.foreign, slug="general-1", name="General", price=1000, capacity=50,
        )

    @staticmethod
    def make_event(venue, slug, title, days, published=True):
        return Event.objects.create(
            venue=venue, slug=slug, title=title,
            starts_at=timezone.now() + timedelta(days=days), is_published=published,
        )

    def url(self, venue="moon", event=None):
        name, args = ("manage-event-list", [venue]) if event is None else (
            "manage-event-detail", [venue, event],
        )
        return reverse(name, args=args)

    def reserve(self, ticket, quantity, expires_at):
        order = Order.objects.create(
            event=ticket.event, buyer_email="comprador@example.com",
            idempotency_key=uuid4(), expires_at=expires_at,
        )
        OrderItem.objects.create(
            order=order, ticket_type=ticket, ticket_slug=ticket.slug, quantity=quantity,
            unit_price=ticket.price, unit_service_fee=0,
        )
        return order

    def test_requests_require_a_valid_token(self):
        self.assertEqual(self.client.get(self.url()).status_code, 401)
        for header in ["Bearer nope", "nope", "Bearer", f"bearer {self.key}"]:
            response = self.client.get(self.url(), headers={"Authorization": header})
            self.assertEqual((response.status_code, response.json()["error"]), (401, "unauthorized"))
        dead = create_token(self.admin, active=False)
        self.assertEqual(self.client.get(self.url(), headers=auth(dead)).status_code, 401)

    def test_token_maps_to_venue_admin_membership(self):
        response = self.client.get(self.url(), headers=auth(self.key))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(self.url(), headers=auth(self.door_key)).status_code, 403)
        self.assertEqual(
            self.client.get(self.url(), headers=auth(self.door_key)).json()["error"], "forbidden",
        )
        self.assertEqual(self.client.get(self.url("other"), headers=auth(self.key)).status_code, 403)
        self.assertEqual(self.client.get(self.url("other"), headers=auth(self.platform_key)).status_code, 200)

    def test_nonexistent_venue_is_404(self):
        for key in [self.key, self.door_key, self.platform_key]:
            response = self.client.get(self.url("fantasma"), headers=auth(key))
            self.assertEqual((response.status_code, response.json()["error"]), (404, "venue_unavailable"))

    def test_create_event_with_ticket_types(self):
        response = self.client.post(
            self.url(), event_payload(), content_type="application/json", headers=auth(self.key),
        )
        self.assertEqual(response.status_code, 201)
        event = response.json()["event"]
        self.assertEqual(event["slug"], "noche-de-prueba")
        self.assertIs(event["isPublished"], False)
        self.assertIs(event["salesOpen"], True)
        self.assertEqual(event["ageLimit"], 18)
        self.assertEqual(event["status"], "on_sale")
        [ticket] = event["ticketTypes"]
        self.assertEqual(
            (ticket["id"], ticket["kind"], ticket["price"], ticket["capacity"], ticket["sold"]),
            ("general-1", "common", 5000.0, 100, 0),
        )
        self.assertEqual(ticket["description"], "Ingreso general.")

        again = self.client.post(
            self.url(),
            event_payload(
                lineup=["DJ Uno", "DJ Dos"],
                ticketTypes=[
                    {"name": "Anticipada", "price": 3000, "capacity": 50},
                    {"name": "Mesa VIP", "kind": "vip", "price": 15000, "capacity": 10},
                ],
            ),
            content_type="application/json",
            headers=auth(self.key),
        )
        self.assertEqual(again.status_code, 201)
        event = again.json()["event"]
        self.assertEqual(event["slug"], "noche-de-prueba-2")
        self.assertEqual(event["lineup"], ["DJ Uno", "DJ Dos"])
        self.assertEqual([t["id"] for t in event["ticketTypes"]], ["general-1", "vip-1"])
        self.assertEqual(
            event["ticketTypes"][1]["description"], "Fila VIP en la puerta y acceso al sector VIP.",
        )
        stored = Event.objects.get(venue=self.moon, slug="noche-de-prueba-2")
        self.assertEqual((stored.is_published, stored.sales_open), (False, True))

    def test_create_validates_fields(self):
        def post(payload):
            return self.client.post(
                self.url(), payload, content_type="application/json", headers=auth(self.key),
            ).json()["error"]

        cases = [
            (event_payload(title=""), "invalid_title"),
            (event_payload(title="x" * 201), "invalid_title"),
            (event_payload(title=None), "invalid_title"),
            (event_payload(startsAt="ayer"), "invalid_starts_at"),
            (event_payload(startsAt="2026-13-40T25:00:00-03:00"), "invalid_starts_at"),
            (event_payload(startsAt=(timezone.now() - timedelta(days=1)).isoformat()), "invalid_starts_at"),
            (event_payload(startsAt=123), "invalid_starts_at"),
            (event_payload(ageLimit=120), "invalid_age_limit"),
            (event_payload(ageLimit=True), "invalid_age_limit"),
            (event_payload(lineup=[{"dj": 1}]), "invalid_lineup"),
            (event_payload(doorsNote=5), "invalid_doors_note"),
            (event_payload(isPublished="si"), "invalid_is_published"),
            (event_payload(salesOpen=1), "invalid_sales_open"),
            (event_payload(slug="trucho"), "invalid_slug"),
            (event_payload(ticketTypes=None), "invalid_ticket_types"),
            (event_payload(ticketTypes=[]), "invalid_ticket_types"),
            (event_payload(ticketTypes=[{"name": "G", "capacity": 10}]), "invalid_price"),
            (event_payload(ticketTypes=[{"name": "G", "price": 0, "capacity": 10}]), "invalid_price"),
            (event_payload(ticketTypes=[{"name": "G", "price": "mil", "capacity": 10}]), "invalid_price"),
            (event_payload(ticketTypes=[{"name": "G", "price": 100, "capacity": 0}]), "invalid_capacity"),
            (event_payload(ticketTypes=[{"price": 100, "capacity": 10}]), "invalid_name"),
            (event_payload(ticketTypes=[{"name": "G", "kind": "oro", "price": 100, "capacity": 10}]), "invalid_kind"),
            (event_payload(ticketTypes=[{"name": "G", "price": 100, "capacity": 10, "sold": 5}]), "invalid_sold"),
            (event_payload(ticketTypes=[{"name": "G", "price": 100, "capacity": 10, "slug": "x"}]), "invalid_slug"),
            ({"title": "Sin entradas", "startsAt": (timezone.now() + timedelta(days=1)).isoformat()}, "invalid_ticket_types"),
        ]
        for payload, code in cases:
            with self.subTest(code=code):
                self.assertEqual(post(payload), code)
        self.assertEqual(self.moon.events.filter(title="Sin entradas").count(), 0)

        response = self.client.post(
            self.url(), "datos", content_type="text/plain", headers=auth(self.key),
        )
        self.assertEqual((response.status_code, response.json()["error"]), (415, "expected_json"))

    def test_list_shows_unpublished_finished_and_status(self):
        paused = self.make_event(self.moon, "pausada", "Pausada", days=3)
        TicketType.objects.create(event=paused, slug="general-1", name="G", price=1, capacity=10)
        Event.objects.filter(pk=paused.pk).update(sales_open=False)
        sold_out = self.make_event(self.moon, "agotada", "Agotada", days=4)
        TicketType.objects.create(
            event=sold_out, slug="general-1", name="G", price=1, capacity=10, sold=10,
        )
        response = self.client.get(self.url(), headers=auth(self.key))
        events = {event["slug"]: event for event in response.json()["events"]}
        self.assertEqual(set(events), {"noche", "borrador", "vieja", "pausada", "agotada"})
        self.assertEqual(events["noche"]["status"], "on_sale")
        self.assertEqual(events["pausada"]["status"], "paused")
        self.assertEqual(events["agotada"]["status"], "sold_out")
        self.assertEqual(events["vieja"]["status"], "finished")
        self.assertIs(events["borrador"]["isPublished"], False)
        self.assertEqual((events["vieja"]["sold"], events["vieja"]["capacity"]), (50, 50))
        self.assertNotIn("ticketTypes", events["noche"])

    def test_detail_counts_active_reservations(self):
        self.reserve(self.ticket, 4, timezone.now() + timedelta(minutes=10))
        self.reserve(self.ticket, 7, timezone.now() - timedelta(minutes=1))
        response = self.client.get(self.url(event="noche"), headers=auth(self.key))
        self.assertEqual(response.status_code, 200)
        event = response.json()["event"]
        self.assertEqual(event["reserved"], 4)
        [ticket] = event["ticketTypes"]
        self.assertEqual((ticket["reserved"], ticket["sold"], ticket["capacity"]), (4, 0, 100))

    def test_patch_edits_fields_but_not_slug(self):
        url = self.url(event="noche")
        response = self.client.patch(
            url,
            {
                "title": "Noche renovada",
                "lineup": ["DJ Uno", "DJ Dos"],
                "description": "Nueva descripción",
                "doorsNote": "Hasta las 2:00",
                "dressCode": "Elegante sport",
                "ageLimit": 21,
                "salesOpen": False,
                "isPublished": False,
            },
            content_type="application/json",
            headers=auth(self.key),
        )
        self.assertEqual(response.status_code, 200)
        event = response.json()["event"]
        self.assertEqual(event["slug"], "noche")
        self.assertEqual(event["title"], "Noche renovada")
        self.assertEqual(event["lineup"], ["DJ Uno", "DJ Dos"])
        self.assertEqual(event["status"], "paused")
        self.assertIs(event["isPublished"], False)
        self.event.refresh_from_db()
        self.assertEqual(
            (self.event.lineup, self.event.age_limit, self.event.sales_open, self.event.doors_note),
            ("DJ Uno\nDJ Dos", 21, False, "Hasta las 2:00"),
        )
        response = self.client.patch(
            url, {"slug": "otro"}, content_type="application/json", headers=auth(self.key),
        )
        self.assertEqual(response.json()["error"], "invalid_slug")
        self.assertEqual(response.status_code, 400)

    def test_patch_capacity_cannot_drop_below_sold_and_reserved(self):
        TicketType.objects.filter(pk=self.ticket.pk).update(sold=3)
        self.reserve(self.ticket, 2, timezone.now() + timedelta(minutes=10))

        def patch(capacity):
            return self.client.patch(
                self.url(event="noche"),
                {"ticketTypes": [{"slug": "general-1", "capacity": capacity}]},
                content_type="application/json",
                headers=auth(self.key),
            )

        response = patch(4)
        self.assertEqual((response.status_code, response.json()["error"]), (409, "capacity_below_sales"))
        self.assertEqual(patch(5).status_code, 200)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.capacity, 5)

    def test_patch_adds_and_deactivates_ticket_types(self):
        response = self.client.patch(
            self.url(event="noche"),
            {
                "ticketTypes": [
                    {"slug": "general-1", "isActive": False, "price": 6000},
                    {"name": "VIP mesa", "kind": "vip", "price": 9000, "capacity": 5},
                ]
            },
            content_type="application/json",
            headers=auth(self.key),
        )
        self.assertEqual(response.status_code, 200)
        tickets = {t["id"]: t for t in response.json()["event"]["ticketTypes"]}
        self.assertIs(tickets["general-1"]["isActive"], False)
        self.assertEqual(tickets["general-1"]["price"], 6000.0)
        self.assertEqual(tickets["vip-1"]["name"], "VIP mesa")
        self.assertEqual(
            tickets["vip-1"]["description"], "Fila VIP en la puerta y acceso al sector VIP.",
        )

        for items, code in [
            ([{"slug": "vip-9", "price": 1}], "invalid_ticket_types"),
            ([{"slug": "general-1", "sold": 0}], "invalid_sold"),
            ([{"slug": "general-1"}, {"slug": "general-1"}], "invalid_ticket_types"),
            ("no-es-lista", "invalid_ticket_types"),
        ]:
            with self.subTest(code=code):
                response = self.client.patch(
                    self.url(event="noche"), {"ticketTypes": items},
                    content_type="application/json", headers=auth(self.key),
                )
                self.assertEqual(response.json()["error"], code)

        deactivated = TicketType.objects.get(event=self.event, slug="general-1")
        self.assertFalse(deactivated.is_active)

    def test_delete_requires_no_sales(self):
        doomed = self.make_event(self.moon, "doomed", "Doomed", days=1)
        TicketType.objects.create(event=doomed, slug="general-1", name="G", price=1, capacity=5)
        response = self.client.delete(self.url(event="doomed"), headers=auth(self.key))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Event.objects.filter(pk=doomed.pk).exists())
        self.assertEqual(
            self.client.get(self.url(event="doomed"), headers=auth(self.key)).status_code, 404,
        )

        TicketType.objects.filter(pk=self.ticket.pk).update(sold=1)
        response = self.client.delete(self.url(event="noche"), headers=auth(self.key))
        self.assertEqual((response.status_code, response.json()["error"]), (409, "has_sales"))

    def test_delete_with_orders_is_protected(self):
        self.reserve(self.ticket, 1, timezone.now() - timedelta(minutes=5))
        response = self.client.delete(self.url(event="noche"), headers=auth(self.key))
        self.assertEqual((response.status_code, response.json()["error"]), (409, "has_orders"))
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

    def test_venues_are_isolated(self):
        url = self.url("other", "ajena")
        self.assertEqual(self.client.get(url, headers=auth(self.key)).status_code, 403)
        self.assertEqual(
            self.client.patch(
                url, {"title": "Robada"}, content_type="application/json", headers=auth(self.key),
            ).status_code,
            403,
        )
        self.assertEqual(self.client.delete(url, headers=auth(self.key)).status_code, 403)
        self.assertEqual(
            self.client.post(
                self.url("other"), event_payload(),
                content_type="application/json", headers=auth(self.key),
            ).status_code,
            403,
        )
        # Dentro de moon, un slug que solo existe en other da 404.
        response = self.client.get(self.url("moon", "ajena"), headers=auth(self.key))
        self.assertEqual((response.status_code, response.json()["error"]), (404, "event_unavailable"))

    def test_method_not_allowed(self):
        self.assertEqual(self.client.put(self.url(), headers=auth(self.key)).status_code, 405)
        self.assertEqual(
            self.client.put(self.url(event="noche"), headers=auth(self.key)).status_code, 405,
        )

    def test_create_api_token_command(self):
        out = StringIO()
        call_command("create_api_token", "dueno@moon.com", stdout=out)
        key = out.getvalue().strip().splitlines()[-1]
        token = ApiToken.objects.get(key_hash=hashlib.sha256(key.encode()).hexdigest())
        self.assertEqual((token.user, token.hint), (self.admin, key[-6:]))
        self.assertEqual(self.client.get(self.url(), headers=auth(key)).status_code, 200)
        with self.assertRaises(CommandError):
            call_command("create_api_token", "nadie@example.com", stdout=StringIO())
        get_user_model().objects.create_user(username="otro", email="dueno@moon.com")
        with self.assertRaises(CommandError):
            call_command("create_api_token", "dueno@moon.com", stdout=StringIO())
