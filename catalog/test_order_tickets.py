from uuid import uuid4

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import Ticket
from .qr import verify_ticket
from .test_orders import create_order
from .test_tickets import BUYER, make_event, make_order, make_type
from .tickets import issue_tickets


def detail(client, order, venue="moon"):
    return client.get(reverse("order-detail", args=[venue, "fecha", order.pk]))


class OrderTicketsTests(TestCase):
    def setUp(self):
        self.event = make_event("moon")
        self.general = make_type(self.event, "general")
        self.vip = make_type(self.event, "vip", price="200.00")

    def paid_order(self):
        order = make_order(self.event, [(self.general, 2), (self.vip, 1)])
        issue_tickets(order.pk)
        return order

    def test_unpaid_order_has_empty_tickets(self):
        order = make_order(self.event, [(self.general, 1)])
        self.assertEqual(detail(self.client, order).json()["order"]["tickets"], [])

    def test_paid_order_lists_one_ticket_per_unit(self):
        order = self.paid_order()
        tickets = detail(self.client, order).json()["order"]["tickets"]
        self.assertEqual(len(tickets), 3)
        expected = [
            (str(t.id), t.ticket_type.slug, t.ticket_type.name)
            for t in order.tickets.select_related("ticket_type")
        ]
        self.assertEqual([(t["id"], t["ticketTypeId"], t["ticketTypeName"]) for t in tickets], expected)
        self.assertEqual(sorted(t["ticketTypeId"] for t in tickets), ["general", "general", "vip"])
        self.assertTrue(all(t["usedAt"] is None for t in tickets))

    def test_qr_verifies_to_ticket_id(self):
        tickets = detail(self.client, self.paid_order()).json()["order"]["tickets"]
        for ticket in tickets:
            self.assertEqual(str(verify_ticket(ticket["qr"])), ticket["id"])

    def test_free_order_post_includes_tickets_and_retry_matches(self):
        make_type(self.event, "gratis", price="0.00")
        key = uuid4()
        first = create_order(self.client, key, ticket_type="gratis", quantity=2)
        self.assertEqual(first.status_code, 201)
        tickets = first.json()["order"]["tickets"]
        self.assertEqual(len(tickets), 2)
        self.assertEqual({t["ticketTypeId"] for t in tickets}, {"gratis"})
        retry = create_order(self.client, key, ticket_type="gratis", quantity=2)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["order"]["tickets"], tickets)

    def test_used_ticket_shows_iso_used_at(self):
        order = self.paid_order()
        ticket = order.tickets.first()
        used = timezone.now()
        Ticket.objects.filter(pk=ticket.pk).update(used_at=used)
        tickets = detail(self.client, order).json()["order"]["tickets"]
        by_id = {t["id"]: t for t in tickets}
        self.assertEqual(by_id[str(ticket.pk)]["usedAt"], used.isoformat())

    def test_other_venue_slug_gives_404(self):
        order = self.paid_order()
        make_event("other")
        self.assertEqual(detail(self.client, order, venue="other").status_code, 404)

    def test_response_hides_buyer_data(self):
        order = self.paid_order()
        body = detail(self.client, order).content.decode()
        for value in (BUYER["email"], BUYER["name"], BUYER["phone"], "b@example.com"):
            self.assertNotIn(value, body)

    def test_query_count_does_not_grow_with_tickets(self):
        small = make_order(self.event, [(self.general, 1)])
        issue_tickets(small.pk)
        big = make_order(self.event, [(self.general, 5), (self.vip, 5)])
        issue_tickets(big.pk)
        with CaptureQueriesContext(connection) as one:
            detail(self.client, small)
        with CaptureQueriesContext(connection) as ten:
            response = detail(self.client, big)
        self.assertEqual(len(response.json()["order"]["tickets"]), 10)
        self.assertEqual(len(ten), len(one))
