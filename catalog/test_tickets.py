from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Event, Membership, Order, OrderItem, Ticket, TicketType, Venue
from .orders import OrderConflict, reserve_order
from .tickets import issue_tickets

BUYER = {"email": "buyer@example.com", "name": "Juan Pérez", "phone": "+5493884123456"}


def make_event(slug="moon"):
    venue = Venue.objects.create(name=slug, slug=slug, is_active=True)
    return Event.objects.create(
        venue=venue, title="Fecha", slug="fecha", starts_at=timezone.now(), is_published=True,
    )


def make_type(event, slug, price="100.00", capacity=10):
    return TicketType.objects.create(
        event=event, slug=slug, name=slug, price=price, capacity=capacity,
    )


def make_order(event, items, expires_in=timedelta(minutes=10)):
    order = Order.objects.create(
        event=event, buyer_email="b@example.com", idempotency_key=uuid4(),
        expires_at=timezone.now() + expires_in,
    )
    for ticket_type, quantity in items:
        OrderItem.objects.create(
            order=order, ticket_type=ticket_type, ticket_slug=ticket_type.slug,
            quantity=quantity, unit_price=ticket_type.price, unit_service_fee=0,
        )
    return order


class IssueTicketsTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.general = make_type(self.event, "general")
        self.vip = make_type(self.event, "vip")

    def test_issues_one_ticket_per_unit(self):
        order = make_order(self.event, [(self.general, 2), (self.vip, 1)])
        tickets = issue_tickets(order.pk)
        self.assertEqual(len(tickets), 3)
        self.assertEqual(
            sorted(t.ticket_type_id for t in tickets),
            sorted([self.general.pk] * 2 + [self.vip.pk]),
        )
        self.assertTrue(all(t.event_id == self.event.pk and t.order_id == order.pk for t in tickets))
        order.refresh_from_db()
        self.assertIsNotNone(order.paid_at)
        self.general.refresh_from_db()
        self.vip.refresh_from_db()
        self.assertEqual((self.general.sold, self.vip.sold), (2, 1))

    def test_issue_twice_is_idempotent(self):
        order = make_order(self.event, [(self.general, 2), (self.vip, 1)])
        first = issue_tickets(order.pk)
        second = issue_tickets(order.pk)
        self.assertEqual(len(second), 3)
        self.assertEqual({t.pk for t in first}, {t.pk for t in second})
        self.assertEqual(Ticket.objects.count(), 3)
        self.general.refresh_from_db()
        self.assertEqual(self.general.sold, 2)

    def test_availability_drops_once_not_twice(self):
        order = make_order(self.event, [(self.general, 3)])
        issue_tickets(order.pk)
        stock = TicketType.objects.with_reserved(timezone.now()).get(pk=self.general.pk)
        self.assertEqual(stock.capacity - stock.sold - stock.reserved, 7)

    def test_expired_order_without_capacity_conflicts(self):
        tight = make_type(self.event, "tight", capacity=2)
        expired = make_order(self.event, [(tight, 2)], expires_in=timedelta(minutes=-1))
        make_order(self.event, [(tight, 2)])
        with self.assertRaises(OrderConflict) as ctx:
            issue_tickets(expired.pk)
        self.assertEqual(str(ctx.exception), "insufficient_capacity")
        expired.refresh_from_db()
        tight.refresh_from_db()
        self.assertIsNone(expired.paid_at)
        self.assertEqual(tight.sold, 0)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_expired_order_with_capacity_issues(self):
        expired = make_order(self.event, [(self.general, 2)], expires_in=timedelta(minutes=-1))
        tickets = issue_tickets(expired.pk)
        self.assertEqual(len(tickets), 2)
        expired.refresh_from_db()
        self.assertIsNotNone(expired.paid_at)


class ReserveOrderTicketTests(TestCase):
    def setUp(self):
        self.event = make_event()

    def test_free_order_is_issued_and_retry_does_not_duplicate(self):
        free = make_type(self.event, "free", price="0")
        key = uuid4()
        order, _, created = reserve_order("moon", "fecha", BUYER, {"free": 2}, key)
        self.assertTrue(created)
        order.refresh_from_db()
        self.assertIsNotNone(order.paid_at)
        self.assertEqual(order.tickets.count(), 2)
        reserve_order("moon", "fecha", BUYER, {"free": 2}, key)
        self.assertEqual(Ticket.objects.count(), 2)
        free.refresh_from_db()
        self.assertEqual(free.sold, 2)

    def test_paid_order_has_no_tickets(self):
        make_type(self.event, "general")
        order, _, _ = reserve_order("moon", "fecha", BUYER, {"general": 1}, uuid4())
        order.refresh_from_db()
        self.assertIsNone(order.paid_at)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_order_detail_status_paid(self):
        make_type(self.event, "free", price="0")
        order, _, _ = reserve_order("moon", "fecha", BUYER, {"free": 1}, uuid4())
        response = self.client.get(reverse("order-detail", args=["moon", "fecha", order.pk]))
        self.assertEqual(response.json()["order"]["status"], "paid")


class OrderAdminTests(TestCase):
    def setUp(self):
        self.event_a = make_event("a")
        self.event_b = make_event("b")
        self.type_a = make_type(self.event_a, "general")
        self.type_b = make_type(self.event_b, "general")
        self.order_a = make_order(self.event_a, [(self.type_a, 2)])
        self.order_b = make_order(self.event_b, [(self.type_b, 1)])
        self.user = get_user_model().objects.create_user(username="a-admin", is_staff=True)
        Membership.objects.create(venue=self.event_a.venue, user=self.user, role=Membership.Role.ADMIN)
        self.client.force_login(self.user)

    def test_changelist_scoped_to_managed_venue(self):
        response = self.client.get(reverse("admin:catalog_order_changelist"))
        self.assertEqual(response.status_code, 200)
        orders = list(response.context["cl"].result_list)
        self.assertEqual([o.pk for o in orders], [self.order_a.pk])

    def test_mark_paid_action_issues_tickets(self):
        response = self.client.post(
            reverse("admin:catalog_order_changelist"),
            {"action": "mark_paid_and_issue", "_selected_action": [str(self.order_a.pk), str(self.order_b.pk)]},
        )
        self.assertEqual(response.status_code, 302)
        self.order_a.refresh_from_db()
        self.order_b.refresh_from_db()
        self.assertIsNotNone(self.order_a.paid_at)
        self.assertEqual(self.order_a.tickets.count(), 2)
        self.assertIsNone(self.order_b.paid_at)
