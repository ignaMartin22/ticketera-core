from datetime import timedelta
from uuid import uuid4

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import Membership, Order, OrderItem, Ticket, TicketType
from .test_door import DoorTestCase, make_event, make_user, make_venue
from .tickets import issue_tickets


def issue(event, type_name, quantity=1, buyer_name="Juan Pérez", email="b@example.com", phone=""):
    ticket_type = TicketType.objects.filter(event=event, name=type_name).first() or TicketType.objects.create(
        event=event, slug=type_name.lower(), name=type_name, price="100.00", capacity=50,
    )
    order = Order.objects.create(
        event=event, buyer_email=email, buyer_name=buyer_name, buyer_phone=phone,
        idempotency_key=uuid4(), expires_at=timezone.now() + timedelta(minutes=10),
    )
    OrderItem.objects.create(
        order=order, ticket_type=ticket_type, ticket_slug=ticket_type.slug,
        quantity=quantity, unit_price=ticket_type.price, unit_service_fee=0,
    )
    return issue_tickets(order.pk)


class AttendanceTests(DoorTestCase):
    def setUp(self):
        super().setUp()
        self.event = make_event(self.venue_a, slug="noche", offset=2)
        self.generals = issue(self.event, "General", 3, buyer_name="Ana")
        self.vips = issue(self.event, "VIP", 2, buyer_name="Beto")
        self.tok = self.token()

    def url(self, venue="a", event="noche"):
        return f"/api/v1/door/venues/{venue}/events/{event}/attendance/"

    def get(self, token="default", **url):
        token = self.tok if token == "default" else token
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.get(self.url(**url), **headers)

    def scan_at(self, ticket, when=None):
        self.scan(ticket, self.tok, event="noche")
        if when:
            Ticket.objects.filter(pk=ticket.pk).update(used_at=when)

    def test_counts_issued_admitted_and_per_type(self):
        self.scan_at(self.generals[0])
        self.scan_at(self.vips[0])
        response = self.get()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["event"]["slug"], "noche")
        self.assertEqual((body["issued"], body["admitted"]), (5, 2))
        self.assertEqual(body["ticketTypes"], [
            {"id": "general", "name": "General", "issued": 3, "admitted": 1},
            {"id": "vip", "name": "VIP", "issued": 2, "admitted": 1},
        ])

    def test_lists_all_tickets_with_used_data_only_on_scanned(self):
        self.scan_at(self.generals[0])
        self.scan_at(self.vips[0])
        tickets = self.get().json()["tickets"]
        self.assertEqual(len(tickets), 5)
        by_id = {t["id"]: t for t in tickets}
        for ticket in [*self.generals, *self.vips]:
            row = by_id[str(ticket.pk)]
            is_general = ticket in self.generals
            self.assertEqual(row["holderName"], "Ana" if is_general else "Beto")
            self.assertEqual(row["ticketType"], "General" if is_general else "VIP")
            scanned = ticket in (self.generals[0], self.vips[0])
            self.assertEqual(row["usedAt"] is not None, scanned)
            self.assertEqual(row["usedBy"], "portero" if scanned else None)

    def test_ordering_admitted_first_most_recent_first(self):
        now = timezone.now()
        self.scan_at(self.generals[0], now - timedelta(minutes=10))
        self.scan_at(self.vips[1], now - timedelta(minutes=1))
        ids = [t["id"] for t in self.get().json()["tickets"]]
        self.assertEqual(ids[:2], [str(self.vips[1].pk), str(self.generals[0].pk)])
        rest = Ticket.objects.filter(event=self.event, used_at__isnull=True).order_by("created_at", "pk")
        self.assertEqual(ids[2:], [str(t.pk) for t in rest])

    def test_event_without_tickets(self):
        make_event(self.venue_a, slug="vacia", offset=3)
        body = self.get(event="vacia").json()
        self.assertEqual((body["issued"], body["admitted"]), (0, 0))
        self.assertEqual(body["ticketTypes"], [])
        self.assertEqual(body["tickets"], [])

    def test_other_venue_tickets_not_included(self):
        issue(self.event_b, "General", 4)
        body = self.get(event="fecha").json()
        self.assertEqual(body["issued"], 1)
        self.assertEqual([t["id"] for t in body["tickets"]], [str(self.ticket_a.pk)])
        self.assertEqual(self.get().json()["issued"], 5)

    def test_door_of_a_cannot_see_event_of_b(self):
        response = self.get(venue="b", event="fecha")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "not_found"})

    def test_requires_token(self):
        response = self.get(token=None)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "unauthorized"})

    def test_does_not_expose_buyer_email_or_phone(self):
        issue(self.event, "General", 1, email="secreto@example.org", phone="+5491155550000")
        text = self.get().content.decode()
        self.assertNotIn("secreto@example.org", text)
        self.assertNotIn("5491155550000", text)

    def test_query_count_does_not_grow_with_tickets(self):
        solo = make_event(self.venue_a, slug="solo", offset=4)
        muchas = make_event(self.venue_a, slug="muchas", offset=5)
        issue(solo, "General", 1)
        tickets = issue(muchas, "General", 5) + issue(muchas, "VIP", 5)
        self.scan(tickets[0], self.tok, event="muchas")
        self.scan(tickets[7], self.tok, event="muchas")
        self.assertEqual(self.queries("muchas"), self.queries("solo"))

    def queries(self, event):
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.get(event=event).status_code, 200)
        return len(ctx)


class TicketAdminTests(TestCase):
    def setUp(self):
        self.venue_a, self.venue_b = make_venue("a"), make_venue("b")
        self.event_a, self.event_b = make_event(self.venue_a), make_event(self.venue_b)
        issue(self.event_a, "General", 1, buyer_name="Titular A")
        issue(self.event_b, "General", 1, buyer_name="Titular B")
        self.url = reverse("admin:catalog_ticket_changelist")

    def login_staff(self, name, venue, role):
        user = make_user(name, venue, role)
        user.is_staff = True
        user.save()
        self.client.force_login(user)

    def test_admin_sees_only_own_venue_tickets(self):
        self.login_staff("duenio", self.venue_a, Membership.Role.ADMIN)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Titular A")
        self.assertNotContains(response, "Titular B")

    def test_no_add_and_door_role_has_no_access(self):
        self.login_staff("portero2", self.venue_a, Membership.Role.DOOR)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.login_staff("duenio", self.venue_a, Membership.Role.ADMIN)
        response = self.client.get(self.url)
        self.assertFalse(response.context["cl"].model_admin.has_add_permission(response.wsgi_request))
        self.assertFalse(response.context["has_add_permission"])
        self.assertEqual(self.client.get(reverse("admin:catalog_ticket_add")).status_code, 403)
