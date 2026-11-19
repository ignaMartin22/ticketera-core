from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from .models import Event, Order, TicketType, Venue


def create_catalog(slug):
    venue = Venue.objects.create(
        name=slug, slug=slug, is_active=True, fee_mode=Venue.FeeMode.BUYER, buyer_fee_rate="0.1",
    )
    event = Event.objects.create(
        venue=venue, title="Fecha", slug="fecha", starts_at=timezone.now(), is_published=True,
    )
    return TicketType.objects.create(
        event=event, slug="general", name="General", price="105.50", capacity=2,
    )


def create_order(client, key, venue="moon", ticket_type="general", quantity=2, **buyer):
    return client.post(
        reverse("order-create", args=[venue, "fecha"]),
        {
            "email": "buyer@example.com",
            "name": "Juan Pérez",
            "phone": "+5493884123456",
            **buyer,
            "items": [{"ticketTypeId": ticket_type, "quantity": quantity}],
        },
        content_type="application/json",
        headers={"Idempotency-Key": str(key)},
    )


class OrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ticket = create_catalog("moon")
        other = create_catalog("other")
        TicketType.objects.filter(pk=other.pk).update(slug="other-general")

    def available(self):
        event = self.client.get(reverse("event-detail", args=["moon", "fecha"])).json()["event"]
        return event["ticketTypes"][0]["available"]

    def test_reservation_changes_availability_and_expires(self):
        key = uuid4()
        response = create_order(self.client, key)
        self.assertEqual(response.status_code, 201)
        order = response.json()["order"]
        self.assertEqual(order["status"], "pending_payment")
        self.assertEqual(order["total"], "233.00")
        self.assertNotIn("email", order)
        self.assertEqual(self.available(), 0)
        self.assertEqual(create_order(self.client, uuid4()).status_code, 409)

        order_url = reverse("order-detail", args=["moon", "fecha", order["id"]])
        self.assertEqual(self.client.get(order_url).json()["order"], order)
        expiry = Order.objects.get(pk=order["id"]).expires_at
        with patch("catalog.views.timezone.now", return_value=expiry):
            self.assertEqual(self.available(), 2)
            self.assertEqual(self.client.get(order_url).json()["order"]["status"], "expired")
            self.assertEqual(create_order(self.client, key).json()["order"]["status"], "expired")
            self.assertEqual(create_order(self.client, uuid4()).status_code, 201)

    def test_retries_keep_original_snapshot_and_reject_different_cart(self):
        key = uuid4()
        first = create_order(self.client, key, quantity=1).json()["order"]
        TicketType.objects.filter(pk=self.ticket.pk).update(price=200, slug="renamed")
        replay = create_order(self.client, key, quantity=1)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["order"], first)
        self.assertEqual(create_order(self.client, key, quantity=2).status_code, 409)
        self.assertEqual(Order.objects.count(), 1)

    def test_buyer_contact_is_validated_stored_and_private(self):
        self.assertEqual(create_order(self.client, uuid4(), email="sin-arroba").json()["error"], "invalid_email")
        self.assertEqual(create_order(self.client, uuid4(), name="  ").json()["error"], "invalid_name")
        self.assertEqual(create_order(self.client, uuid4(), phone="3884123456").json()["error"], "invalid_phone")
        key = uuid4()
        order = create_order(self.client, key, name="  Juan   Pérez ").json()["order"]
        self.assertNotIn("phone", order)
        stored = Order.objects.get(pk=order["id"])
        self.assertEqual((stored.buyer_name, stored.buyer_phone), ("Juan Pérez", "+5493884123456"))
        self.assertEqual(create_order(self.client, key, name="Juan Pérez").status_code, 200)
        self.assertEqual(create_order(self.client, key, phone="+5493884000000").status_code, 409)

    def test_orders_are_scoped_to_venue_and_event(self):
        key = uuid4()
        self.assertEqual(create_order(self.client, key, ticket_type="other-general").status_code, 409)
        self.assertEqual(create_order(self.client, key, quantity=True).status_code, 400)
        order = create_order(self.client, key).json()["order"]
        other_url = reverse("order-detail", args=["other", "fecha", order["id"]])
        self.assertEqual(self.client.get(other_url).status_code, 404)

    def test_admin_cannot_lower_capacity_below_active_reservations(self):
        create_order(self.client, uuid4())
        from django.contrib.auth import get_user_model

        self.client.force_login(get_user_model().objects.create_superuser(username="admin"))
        response = self.client.post(
            reverse("admin:catalog_tickettype_change", args=[self.ticket.pk]),
            {"slug": "general", "name": "General", "price": "105.50", "capacity": 1, "is_active": "on"},
        )
        self.assertIn("capacity", response.context["adminform"].form.errors)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.capacity, 2)


class ConcurrentOrderTests(TransactionTestCase):
    def test_two_requests_cannot_both_reserve_the_last_tickets(self):
        create_catalog("moon")
        barrier = Barrier(2, timeout=5)
        keys = [uuid4(), uuid4()]

        def request_order(key):
            try:
                barrier.wait()
                return create_order(Client(), key).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(request_order, keys))
        self.assertCountEqual(statuses, [201, 409])
        self.assertEqual(Order.objects.count(), 1)
