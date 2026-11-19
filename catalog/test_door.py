import json
import time
from datetime import timedelta
from unittest import mock
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import Event, Membership, Order, OrderItem, TicketType, Venue
from .qr import sign_ticket
from .tickets import issue_tickets

User = get_user_model()
PASSWORD = "clave-segura-123"


def make_venue(slug):
    return Venue.objects.create(name=f"Boliche {slug}", slug=slug, is_active=True)


def make_event(venue, slug="fecha", published=True, offset=0):
    return Event.objects.create(
        venue=venue, title=f"Fecha {slug}", slug=slug,
        starts_at=timezone.now() + timedelta(days=offset), is_published=published,
    )


def make_ticket(event, buyer_name="Juan Pérez", type_name="General"):
    ticket_type = TicketType.objects.create(
        event=event, slug=type_name.lower(), name=type_name, price="100.00", capacity=10,
    )
    order = Order.objects.create(
        event=event, buyer_email="b@example.com", buyer_name=buyer_name,
        idempotency_key=uuid4(), expires_at=timezone.now() + timedelta(minutes=10),
    )
    OrderItem.objects.create(
        order=order, ticket_type=ticket_type, ticket_slug=ticket_type.slug,
        quantity=1, unit_price=ticket_type.price, unit_service_fee=0,
    )
    return issue_tickets(order.pk)[0]


def make_user(username, venue=None, role=None):
    user = User.objects.create_user(username, password=PASSWORD)
    if venue is not None:
        Membership.objects.create(venue=venue, user=user, role=role)
    return user


class DoorTestCase(TestCase):
    def setUp(self):
        self.venue_a = make_venue("a")
        self.venue_b = make_venue("b")
        self.event_a = make_event(self.venue_a)
        self.event_a2 = make_event(self.venue_a, slug="otra", offset=1)
        self.event_b = make_event(self.venue_b)
        self.ticket_a = make_ticket(self.event_a)
        self.ticket_a2 = make_ticket(self.event_a2, buyer_name="Otra Persona")
        self.ticket_b = make_ticket(self.event_b)
        self.door = make_user("portero", self.venue_a, Membership.Role.DOOR)

    def post(self, path, body, token=None, content_type="application/json"):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        data = json.dumps(body) if content_type == "application/json" and not isinstance(body, str) else body
        return self.client.post(path, data, content_type=content_type, **headers)

    def login(self, username="portero", password=PASSWORD):
        return self.post("/api/v1/door/login/", {"username": username, "password": password})

    def token(self, username="portero"):
        return self.login(username).json()["token"]

    def scan_url(self, venue="a", event="fecha"):
        return f"/api/v1/door/venues/{venue}/events/{event}/scan/"

    def scan(self, ticket, token, **url):
        return self.post(self.scan_url(**url), {"token": sign_ticket(ticket.id)}, token)


class LoginTests(DoorTestCase):
    def test_door_login_returns_token_and_venues(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["token"])
        self.assertEqual(body["expiresIn"], 43200)
        self.assertEqual(body["venues"], [{"slug": "a", "name": "Boliche a"}])

    def test_wrong_password_is_401(self):
        response = self.login(password="mala")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "invalid_credentials"})

    def test_cashier_only_is_403(self):
        make_user("cajero", self.venue_a, Membership.Role.CASHIER)
        response = self.login("cajero")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "not_door_staff"})

    def test_login_requires_json(self):
        response = self.post("/api/v1/door/login/", "x", content_type="text/plain")
        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json(), {"error": "expected_json"})


class ScanTests(DoorTestCase):
    def test_valid_scan_admits_and_burns(self):
        response = self.scan(self.ticket_a, self.token())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["result"], "admitted")
        self.assertEqual(body["ticket"]["id"], str(self.ticket_a.id))
        self.assertEqual(body["ticket"]["holderName"], "Juan Pérez")
        self.assertEqual(body["ticket"]["ticketType"], "General")
        self.ticket_a.refresh_from_db()
        self.assertIsNotNone(self.ticket_a.used_at)
        self.assertEqual(self.ticket_a.used_by, self.door)

    def test_second_scan_is_already_used(self):
        token = self.token()
        first = self.scan(self.ticket_a, token).json()
        self.ticket_a.refresh_from_db()
        used_at = self.ticket_a.used_at
        response = self.scan(self.ticket_a, token)
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["result"], "already_used")
        self.assertEqual(body["ticket"]["usedBy"], "portero")
        self.assertEqual(body["ticket"]["usedAt"], first["ticket"]["usedAt"])
        self.ticket_a.refresh_from_db()
        self.assertEqual(self.ticket_a.used_at, used_at)
        self.assertEqual(self.ticket_a.used_by, self.door)

    def test_other_event_same_venue_is_wrong_event(self):
        response = self.scan(self.ticket_a2, self.token())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {
            "result": "wrong_event",
            "event": {
                "slug": "otra", "title": "Fecha otra", "startsAt": self.event_a2.starts_at.isoformat(),
            },
        })
        self.ticket_a2.refresh_from_db()
        self.assertIsNone(self.ticket_a2.used_at)

    def test_tampered_signature_is_invalid(self):
        qr = sign_ticket(self.ticket_a.id)
        bad = qr[:-1] + ("A" if qr[-1] != "A" else "B")
        response = self.post(self.scan_url(), {"token": bad}, self.token())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"result": "invalid"})
        self.ticket_a.refresh_from_db()
        self.assertIsNone(self.ticket_a.used_at)

    def test_missing_or_non_string_token_is_invalid(self):
        token = self.token()
        for body in ({}, {"token": 5}):
            response = self.post(self.scan_url(), body, token)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json(), {"result": "invalid"})

    def test_ticket_of_other_venue_is_invalid(self):
        response = self.scan(self.ticket_b, self.token())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"result": "invalid"})
        self.ticket_b.refresh_from_db()
        self.assertIsNone(self.ticket_b.used_at)

    def test_door_of_a_cannot_scan_in_b(self):
        response = self.scan(self.ticket_b, self.token(), venue="b")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "not_found"})
        self.ticket_b.refresh_from_db()
        self.assertIsNone(self.ticket_b.used_at)

    def test_unpublished_event_is_not_found(self):
        make_event(self.venue_a, slug="borrador", published=False)
        response = self.scan(self.ticket_a, self.token(), event="borrador")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "not_found"})

    def test_admin_can_scan(self):
        make_user("jefe", self.venue_a, Membership.Role.ADMIN)
        response = self.scan(self.ticket_a, self.token("jefe"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], "admitted")

    def test_scan_body_validation(self):
        token = self.token()
        response = self.post(self.scan_url(), "x", token, content_type="text/plain")
        self.assertEqual(response.status_code, 415)
        response = self.post(self.scan_url(), "[1]", token)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "invalid_request"})
        response = self.post(self.scan_url(), {"token": "x" * 9000}, token)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json(), {"error": "request_too_large"})


class AuthTests(DoorTestCase):
    def test_missing_and_tampered_token_are_unauthorized(self):
        token = self.token()
        for headers in ({}, {"HTTP_AUTHORIZATION": f"Bearer {token}x"}, {"HTTP_AUTHORIZATION": "Basic abc"}):
            response = self.client.post(
                self.scan_url(), json.dumps({"token": sign_ticket(self.ticket_a.id)}),
                content_type="application/json", **headers,
            )
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json(), {"error": "unauthorized"})

    def test_expired_token_is_unauthorized(self):
        token = self.token()
        with mock.patch("time.time", return_value=time.time() + 43200 + 60):
            response = self.scan(self.ticket_a, token)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "unauthorized"})

    def test_deactivated_user_is_unauthorized(self):
        token = self.token()
        self.door.is_active = False
        self.door.save()
        response = self.scan(self.ticket_a, token)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "unauthorized"})
        self.ticket_a.refresh_from_db()
        self.assertIsNone(self.ticket_a.used_at)


class EventListTests(DoorTestCase):
    def test_lists_only_published_events_of_venue(self):
        make_event(self.venue_a, slug="borrador", published=False)
        response = self.client.get(
            "/api/v1/door/venues/a/events/", HTTP_AUTHORIZATION=f"Bearer {self.token()}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([e["slug"] for e in response.json()["events"]], ["fecha", "otra"])
        self.assertEqual(set(response.json()["events"][0]), {"slug", "title", "startsAt"})
