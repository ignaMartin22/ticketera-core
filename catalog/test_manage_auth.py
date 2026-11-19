"""Autenticación de la API de gestión (dos formas de token), rutas de manage/ y venta pausada."""

import hashlib
import secrets
from datetime import timedelta
from unittest import mock
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from .models import ApiToken, Event, Membership, Order, TicketType, Venue
from .orders import OrderConflict, reserve_order
from .tickets import issue_tickets

PASSWORD = "clave-segura-123"
BUYER = {"email": "comprador@example.com", "name": "Comprador", "phone": "+5493884123456"}


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


class ManageAuthTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.moon = Venue.objects.create(slug="moon", name="Moon", is_active=True)
        cls.other = Venue.objects.create(slug="other", name="Otro", is_active=True)
        User = get_user_model()
        cls.admin = User.objects.create_user("admin@moon.com", "admin@moon.com", PASSWORD)
        cls.door = User.objects.create_user("door@moon.com", "door@moon.com", PASSWORD)
        cls.other_admin = User.objects.create_user("admin@other.com", "admin@other.com", PASSWORD)
        Membership.objects.create(venue=cls.moon, user=cls.admin, role=Membership.Role.ADMIN)
        Membership.objects.create(venue=cls.moon, user=cls.door, role=Membership.Role.DOOR)
        Membership.objects.create(venue=cls.other, user=cls.other_admin, role=Membership.Role.ADMIN)

    def login_token(self, username):
        response = self.client.post(
            reverse("auth-login"), {"username": username, "password": PASSWORD}, content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["token"]

    def api_key(self, user):
        key = secrets.token_urlsafe(32)
        ApiToken.objects.create(key_hash=hashlib.sha256(key.encode()).hexdigest(), hint=key[-6:], user=user)
        return key

    def list_url(self, venue="moon"):
        return reverse("manage-event-list", args=[venue])

    def test_a_route_accepts_an_api_token_and_a_login_token_of_the_venue_admin(self):
        for key in (self.api_key(self.admin), self.login_token("admin@moon.com")):
            response = self.client.get(self.list_url(), headers=bearer(key))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"events": []})

    def test_b_login_token_of_door_or_of_another_venue_admin_is_rejected(self):
        door = self.client.get(self.list_url(), headers=bearer(self.login_token("door@moon.com")))
        self.assertEqual((door.status_code, door.json()["error"]), (403, "forbidden"))
        foreign = self.client.get(self.list_url(), headers=bearer(self.login_token("admin@other.com")))
        self.assertEqual((foreign.status_code, foreign.json()["error"]), (403, "forbidden"))
        # Lo mismo vale para la clave de larga duración.
        foreign_key = self.client.get(self.list_url(), headers=bearer(self.api_key(self.other_admin)))
        self.assertEqual(foreign_key.status_code, 403)

    def test_c_invalid_tokens_of_either_kind_are_401(self):
        valid_login = self.login_token("admin@moon.com")
        inactive = self.api_key(self.admin)
        ApiToken.objects.update(is_active=False)
        headers = [
            bearer("no-es-una-clave"),
            bearer(valid_login + "x"),
            bearer(signing.dumps({"uid": self.admin.pk}, salt="otra.sal")),
            bearer(inactive),
            {"Authorization": "Basic abc"},
            {},
        ]
        responses = [self.client.get(self.list_url(), headers=h) for h in headers]
        # Un token del login vencido (más de 12 horas).
        future = timezone.now().timestamp() + 13 * 3600
        with mock.patch("django.core.signing.time.time", return_value=future):
            responses.append(self.client.get(self.list_url(), headers=bearer(valid_login)))
        for response in responses:
            self.assertEqual((response.status_code, response.json()["error"]), (401, "unauthorized"))

    def test_token_of_an_inactive_user_is_rejected(self):
        login = self.login_token("admin@moon.com")
        api_key = self.api_key(self.admin)
        get_user_model().objects.filter(pk=self.admin.pk).update(is_active=False)
        for key in (login, api_key):
            self.assertEqual(self.client.get(self.list_url(), headers=bearer(key)).status_code, 401)

    def test_e_mercadopago_and_events_routes_resolve_to_their_views(self):
        mp_status = reverse("mp-status", args=["moon"])
        mp_connect_url = reverse("mp-connect", args=["moon"])
        events = self.list_url()
        detail = reverse("manage-event-detail", args=["moon", "noche"])
        self.assertEqual(mp_status, "/api/v1/manage/venues/moon/mercadopago/")
        self.assertEqual(mp_connect_url, "/api/v1/manage/venues/moon/mercadopago/connect/")
        self.assertEqual(events, "/api/v1/manage/venues/moon/events/")
        self.assertEqual(detail, "/api/v1/manage/venues/moon/events/noche/")
        self.assertEqual(resolve(events).url_name, "manage-event-list")
        self.assertEqual(resolve(detail).url_name, "manage-event-detail")
        self.assertEqual(resolve(mp_status).url_name, "mp-status")
        self.assertEqual(resolve(mp_connect_url).url_name, "mp-connect")
        # Una petición a cada familia llega a su vista.
        key = self.login_token("admin@moon.com")
        self.assertEqual(self.client.get(events, headers=bearer(key)).status_code, 200)
        missing = self.client.get(detail, headers=bearer(key))
        self.assertEqual((missing.status_code, missing.json()["error"]), (404, "event_unavailable"))
        self.assertIn(self.client.get(mp_status, headers=bearer(key)).status_code, (200, 204))
        self.assertEqual(self.client.get(mp_status).status_code, 401)


@override_settings(FRONT_BASE_URL="https://front.example.com", PUBLIC_API_URL="https://api.example.com")
class PausedSalesTests(TestCase):
    def setUp(self):
        venue = Venue.objects.create(name="moon", slug="moon", is_active=True)
        self.event = Event.objects.create(
            venue=venue, title="Fiesta", slug="fecha", starts_at=timezone.now() + timedelta(days=3),
            is_published=True,
        )
        self.ticket = TicketType.objects.create(
            event=self.event, slug="general", name="General", price="1000", capacity=10,
        )
        mock.patch("catalog.mp_connect.get_access_token", return_value="APP_USR-token").start()
        mock.patch("catalog.mp_connect.is_configured", return_value=True).start()
        mock.patch(
            "catalog.mp.create_order",
            side_effect=lambda token, body, key: {
                "id": "ORD1", "checkout_url": "https://mp.example/checkout", "total_amount": body["total_amount"],
            },
        ).start()
        self.addCleanup(mock.patch.stopall)

    def reserve(self, key=None, quantity=1):
        return reserve_order("moon", "fecha", BUYER, {"general": quantity}, key or uuid4())

    def test_d_paused_event_rejects_new_reservations_but_keeps_existing_orders_payable(self):
        key = uuid4()
        order, _items, _created = self.reserve(key)
        Event.objects.filter(pk=self.event.pk).update(sales_open=False)

        with self.assertRaises(OrderConflict) as raised:
            self.reserve(quantity=1)
        self.assertEqual(str(raised.exception), "sales_paused")
        # El reintento de la reserva hecha antes de pausar sigue devolviendo el pedido.
        again, _items, created = self.reserve(key)
        self.assertEqual((again.pk, created), (order.pk, False))

        # El link de pago de un pedido ya reservado sigue funcionando.
        response = self.client.post(f"/api/v1/venues/moon/events/fecha/orders/{order.pk}/payment/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"initPoint": "https://mp.example/checkout"})

        # Y el pago confirmado emite las entradas y suma a las vendidas.
        tickets = issue_tickets(order.pk)
        self.assertEqual(len(tickets), 1)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.sold, 1)
        self.assertIsNotNone(Order.objects.get(pk=order.pk).paid_at)
