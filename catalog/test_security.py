"""Endurecimiento de seguridad: modo prueba, clave del front, topes, freno de sync, token y login."""

import json
from datetime import timedelta
from unittest import mock
from uuid import uuid4

from cryptography.fernet import Fernet
from django.core import signing
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import gateways
from .client import buyer_ip, front_trusted
from .gateways.base import save_account, settle
from .models import (
    DoorLoginFailure, GatewayAccount, GatewayCharge, Membership, MercadoPagoAccount, Order, OrderItem, Payment,
    Ticket, TicketType, Venue,
)
from .payment_confirm import _may_query_gateway, order_total, process_order
from .staff_auth import TOKEN_SALT, issue_token
from .test_guest_lists import GuestListTestCase, make_user
from .test_orders import create_catalog, create_order
from .test_payment_confirm import USER_A, make_account, mp_order
from .test_payments import API, FRONT, TOKEN_A, make_order, make_venue, url
from .tickets import issue_tickets

KEY = "clave-del-front-de-prueba"
OUTCOME = Payment.Outcome
MACRO = {"comercio": "COM-123", "secret": "frase-secreta-del-comercio", "sucursal": ""}
NAVE = {"client_id": "cid", "client_secret": "csecret", "pos_id": "POS-9"}


def pay(client, order, provider=None, venue="a"):
    body = json.dumps({"provider": provider}) if provider else ""
    return client.post(url(order, venue=venue), data=body, content_type="application/json")


def order_with_key(client, idem, email="buyer@example.com", ip=None, key=KEY, ticket_type="general", quantity=1):
    headers = {"Idempotency-Key": str(idem)}
    if key:
        headers["X-Front-Key"] = key
    if ip:
        headers["X-Buyer-Ip"] = ip
    return client.post(
        reverse("order-create", args=["moon", "fecha"]),
        {"email": email, "name": "Juan Pérez", "items": [{"ticketTypeId": ticket_type, "quantity": quantity}]},
        content_type="application/json", headers=headers,
    )


# ---------- 1. cuentas en modo prueba ----------

@override_settings(FRONT_BASE_URL=FRONT, PUBLIC_API_URL=API)
class TestModeTests(TestCase):
    def setUp(self):
        config = override_settings(MP_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode())
        config.enable()
        self.addCleanup(config.disable)
        self.venue, self.event, *_ = make_venue("a")
        self.other, self.other_event, *_ = make_venue("b")
        mock.patch("catalog.mp_connect.get_access_token", return_value=TOKEN_A).start()
        mock.patch("catalog.mp_connect.is_configured", return_value=True).start()
        self.create = mock.patch(
            "catalog.mp.create_order",
            side_effect=lambda token, body, key: {
                "id": "ORD1", "checkout_url": "https://mp.example/c", "total_amount": body["total_amount"],
            },
        ).start()
        self.addCleanup(mock.patch.stopall)

    def methods(self, venue=None):
        return gateways.payment_methods(venue or self.venue)

    def test_mercadopago_test_account_is_off_by_default(self):
        make_account(self.venue, USER_A)  # live_mode=False
        self.assertEqual(self.methods(), [])
        response = pay(self.client, make_order(self.event))
        self.assertEqual((response.status_code, response.json()), (409, {"error": "venue_not_connected"}))
        self.create.assert_not_called()

    @override_settings(PAYMENTS_ALLOW_TEST_MODE=True)
    def test_mercadopago_test_account_works_when_allowed(self):
        make_account(self.venue, USER_A)
        self.assertEqual(self.methods(), ["mercadopago"])
        self.assertEqual(pay(self.client, make_order(self.event)).status_code, 200)

    def test_mercadopago_live_account_works_and_is_isolated_by_venue(self):
        MercadoPagoAccount.objects.filter(pk=make_account(self.venue, USER_A).pk).update(live_mode=True)
        make_account(self.other, 2002)  # el otro boliche sigue en prueba
        self.assertEqual(self.methods(), ["mercadopago"])
        self.assertEqual(self.methods(self.other), [])
        self.assertEqual(pay(self.client, make_order(self.event)).status_code, 200)
        self.assertEqual(pay(self.client, make_order(self.other_event), venue="b").status_code, 409)

    def test_mercadopago_approved_payment_from_test_account_does_not_issue(self):
        account = make_account(self.venue, USER_A)
        order = make_order(self.event)
        record = process_order(account, mp_order(order))
        self.assertEqual(record.outcome, OUTCOME.NOT_APPROVED)
        order.refresh_from_db()
        self.assertIsNone(order.paid_at)
        self.assertFalse(Ticket.objects.filter(order=order).exists())
        with override_settings(PAYMENTS_ALLOW_TEST_MODE=True):
            self.assertEqual(process_order(account, mp_order(order)).outcome, OUTCOME.ISSUED)

    def test_mercadopago_approved_payment_from_live_account_issues(self):
        account = make_account(self.venue, USER_A)
        MercadoPagoAccount.objects.filter(pk=account.pk).update(live_mode=True)
        account.refresh_from_db()
        order = make_order(self.event)
        self.assertEqual(process_order(account, mp_order(order)).outcome, OUTCOME.ISSUED)

    def test_gateways_test_account_is_off_by_default(self):
        save_account(self.venue, "macro", MACRO, live_mode=False)
        save_account(self.venue, "nave", NAVE, live_mode=False)
        self.assertEqual(self.methods(), [])
        for provider in ("macro", "nave"):
            response = pay(self.client, make_order(self.event), provider)
            self.assertEqual((response.status_code, response.json()), (409, {"error": "venue_not_connected"}))
        self.assertFalse(GatewayCharge.objects.exists())

    def test_gateways_live_account_works_only_in_its_own_venue(self):
        save_account(self.venue, "macro", MACRO, live_mode=True)
        save_account(self.other, "macro", MACRO, live_mode=False)
        self.assertEqual(self.methods(), ["macro"])
        self.assertEqual(self.methods(self.other), [])
        self.assertEqual(pay(self.client, make_order(self.event), "macro").status_code, 200)
        self.assertEqual(pay(self.client, make_order(self.other_event), "macro", venue="b").status_code, 409)

    @override_settings(PAYMENTS_ALLOW_TEST_MODE=True)
    def test_gateways_test_account_works_when_allowed(self):
        save_account(self.venue, "macro", MACRO, live_mode=False)
        self.assertEqual(self.methods(), ["macro"])
        self.assertEqual(pay(self.client, make_order(self.event), "macro").status_code, 200)

    def test_gateways_approved_charge_from_test_account_does_not_issue(self):
        account = save_account(self.venue, "macro", MACRO, live_mode=False)
        order = make_order(self.event)
        charge = GatewayCharge.objects.create(order=order, provider="macro", reference=order.pk.hex)
        settle(charge.pk, status="APROBADA", approved=True, amount=order_total(order))
        charge.refresh_from_db()
        self.assertEqual(charge.outcome, OUTCOME.NOT_APPROVED)
        self.assertFalse(Ticket.objects.filter(order=order).exists())
        GatewayAccount.objects.filter(pk=account.pk).update(live_mode=True)
        settle(charge.pk, status="APROBADA", approved=True, amount=order_total(order))
        charge.refresh_from_db()
        self.assertEqual(charge.outcome, OUTCOME.ISSUED)
        self.assertTrue(Ticket.objects.filter(order=order).exists())


# ---------- 2. clave del front e IP ----------

class FrontKeyTests(TestCase):
    def setUp(self):
        create_catalog("moon")

    @staticmethod
    def request(**headers):
        return mock.Mock(headers=headers)

    @override_settings(FRONT_API_KEY="")
    def test_without_configured_key_nothing_is_trusted_and_orders_stay_open(self):
        self.assertFalse(front_trusted(self.request(**{"X-Front-Key": ""})))
        self.assertEqual(buyer_ip(self.request(**{"X-Buyer-Ip": "1.2.3.4"})), "")
        self.assertEqual(create_order(self.client, uuid4(), quantity=1).status_code, 201)

    @override_settings(FRONT_API_KEY=KEY)
    def test_helpers(self):
        ok = {"X-Front-Key": KEY}
        self.assertTrue(front_trusted(self.request(**ok)))
        self.assertFalse(front_trusted(self.request(**{"X-Front-Key": "otra"})))
        self.assertFalse(front_trusted(self.request()))
        self.assertEqual(buyer_ip(self.request(**ok, **{"X-Buyer-Ip": "203.0.113.7"})), "203.0.113.7")
        self.assertEqual(buyer_ip(self.request(**ok, **{"X-Buyer-Ip": "2001:db8::1"})), "2001:db8::1")
        self.assertEqual(buyer_ip(self.request(**ok, **{"X-Buyer-Ip": "no-es-ip"})), "")
        self.assertEqual(buyer_ip(self.request(**ok)), "")
        self.assertEqual(buyer_ip(self.request(**{"X-Buyer-Ip": "203.0.113.7"})), "")  # sin clave

    @override_settings(FRONT_API_KEY=KEY)
    def test_order_create_requires_the_key(self):
        for key in (None, "otra"):
            response = order_with_key(self.client, uuid4(), key=key)
            self.assertEqual((response.status_code, response.json()), (403, {"error": "forbidden"}))
        self.assertFalse(Order.objects.exists())
        self.assertEqual(order_with_key(self.client, uuid4()).status_code, 201)

    @override_settings(FRONT_API_KEY=KEY)
    def test_public_reads_do_not_require_the_key(self):
        self.assertEqual(self.client.get(reverse("event-detail", args=["moon", "fecha"])).status_code, 200)
        order_id = order_with_key(self.client, uuid4()).json()["order"]["id"]
        self.assertEqual(self.client.get(f"/api/v1/venues/moon/events/fecha/orders/{order_id}/").status_code, 200)

    @override_settings(FRONT_API_KEY=KEY)
    def test_buyer_ip_is_saved(self):
        order_with_key(self.client, uuid4(), "a@example.com", "203.0.113.7")
        order_with_key(self.client, uuid4(), "b@example.com")
        self.assertEqual(Order.objects.get(buyer_email="a@example.com").buyer_ip, "203.0.113.7")
        self.assertIsNone(Order.objects.get(buyer_email="b@example.com").buyer_ip)


# ---------- 3. topes ----------

class LimitsTests(TestCase):
    def setUp(self):
        for slug in ("moon", "other"):
            ticket = create_catalog(slug)
            TicketType.objects.filter(pk=ticket.pk).update(capacity=100)
            TicketType.objects.create(event=ticket.event, slug="gratis", name="Gratis", price=0, capacity=100)
        self.event = Venue.objects.get(slug="moon").events.get()

    def order(self, email="a@example.com", venue="moon", ticket_type="general", quantity=1, idem=None):
        return create_order(
            self.client, idem or uuid4(), venue=venue, ticket_type=ticket_type, quantity=quantity, email=email,
        )

    def test_pending_per_email_is_case_insensitive(self):
        self.assertEqual(self.order("Ana@example.com").status_code, 201)
        self.assertEqual(self.order("ana@EXAMPLE.com").status_code, 201)
        response = self.order("ANA@example.com")
        self.assertEqual((response.status_code, response.json()), (429, {"error": "too_many_reservations"}))
        self.assertEqual(Order.objects.count(), 2)

    def test_pending_per_email_is_per_event_and_ignores_paid_and_expired(self):
        self.assertEqual(self.order().status_code, 201)
        self.assertEqual(self.order().status_code, 201)
        self.assertEqual(self.order(venue="other").status_code, 201)  # otro boliche
        first = Order.objects.filter(event=self.event).order_by("created_at").first()
        Order.objects.filter(pk=first.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.order().status_code, 201)  # el vencido ya no cuenta
        self.assertEqual(self.order().status_code, 429)
        Order.objects.filter(pk=first.pk).update(expires_at=timezone.now() + timedelta(minutes=5), paid_at=timezone.now())
        Order.objects.filter(event=self.event, paid_at__isnull=True).update(paid_at=timezone.now())
        self.assertEqual(self.order().status_code, 201)  # los pagados no cuentan

    def test_idempotent_retry_does_not_count(self):
        key = uuid4()
        self.assertEqual(self.order(idem=key).status_code, 201)
        self.assertEqual(self.order().status_code, 201)
        self.assertEqual(self.order(idem=key).status_code, 200)  # con el tope ya lleno
        self.assertEqual(Order.objects.count(), 2)

    @override_settings(FRONT_API_KEY=KEY, MAX_PENDING_ORDERS_PER_IP=5)
    def test_pending_per_ip_only_with_trusted_ip(self):
        for n in range(5):
            self.assertEqual(order_with_key(self.client, uuid4(), f"u{n}@example.com", "203.0.113.9").status_code, 201)
        response = order_with_key(self.client, uuid4(), "u9@example.com", "203.0.113.9")
        self.assertEqual((response.status_code, response.json()), (429, {"error": "too_many_reservations"}))
        self.assertEqual(order_with_key(self.client, uuid4(), "u9@example.com", "203.0.113.10").status_code, 201)
        self.assertEqual(order_with_key(self.client, uuid4(), "u10@example.com").status_code, 201)  # sin IP, sin tope

    @override_settings(FRONT_API_KEY="")
    def test_ip_header_is_ignored_without_key(self):
        for n in range(6):
            response = self.client.post(
                reverse("order-create", args=["moon", "fecha"]),
                {"email": f"u{n}@example.com", "name": "Ana", "items": [{"ticketTypeId": "general", "quantity": 1}]},
                content_type="application/json",
                headers={"Idempotency-Key": str(uuid4()), "X-Buyer-Ip": "203.0.113.9"},
            )
            self.assertEqual(response.status_code, 201)
        self.assertFalse(Order.objects.exclude(buyer_ip=None).exists())

    def test_free_limit_per_email(self):
        self.assertEqual(self.order(ticket_type="gratis", quantity=3).status_code, 201)
        response = self.order(ticket_type="gratis", quantity=2)
        self.assertEqual((response.status_code, response.json()), (409, {"error": "free_limit_reached"}))
        self.assertEqual(Order.objects.count(), 1)  # el pedido rechazado no queda
        self.assertEqual(self.order(ticket_type="gratis", quantity=1).status_code, 201)  # justo en el tope
        self.assertEqual(self.order("otra@example.com", ticket_type="gratis", quantity=4).status_code, 201)
        self.assertEqual(self.order(ticket_type="general").status_code, 201)  # lo pago no cuenta
        self.assertEqual(self.order(venue="other", ticket_type="gratis", quantity=4).status_code, 201)  # otro boliche

    def test_list_tickets_do_not_count_as_free(self):
        guest_list = TicketType.objects.create(
            event=self.event, slug="lista-1", name="Lista", kind=TicketType.Kind.LIST, price=0, capacity=10,
        )
        order = Order.objects.create(
            event=self.event, buyer_email="a@example.com", buyer_name="Ana", idempotency_key=uuid4(),
            expires_at=timezone.now(),
        )
        OrderItem.objects.create(
            order=order, ticket_type=guest_list, ticket_slug="lista-1", quantity=3, unit_price=0, unit_service_fee=0,
        )
        issue_tickets(order.pk)
        self.assertEqual(self.order(ticket_type="gratis", quantity=4).status_code, 201)

    @override_settings(FRONT_API_KEY=KEY, MAX_FREE_TICKETS_PER_IP=8)
    def test_free_limit_per_ip(self):
        for n in range(2):
            response = order_with_key(
                self.client, uuid4(), f"u{n}@example.com", "203.0.113.9", ticket_type="gratis", quantity=4,
            )
            self.assertEqual(response.status_code, 201)
        response = order_with_key(self.client, uuid4(), "u3@example.com", "203.0.113.9", ticket_type="gratis")
        self.assertEqual((response.status_code, response.json()), (409, {"error": "free_limit_reached"}))
        response = order_with_key(self.client, uuid4(), "u3@example.com", "203.0.113.10", ticket_type="gratis")
        self.assertEqual(response.status_code, 201)

    def test_free_retry_returns_the_original_order(self):
        key = uuid4()
        self.assertEqual(self.order(ticket_type="gratis", quantity=4, idem=key).status_code, 201)
        self.assertEqual(self.order(ticket_type="gratis", quantity=4, idem=key).status_code, 200)


# ---------- 4. freno de payment/sync/ ----------

@override_settings(FRONT_BASE_URL=FRONT, PUBLIC_API_URL=API)
class SyncThrottleTests(TestCase):
    def setUp(self):
        self.venue, self.event, *_ = make_venue("a")
        self.mp = mock.patch("catalog.payment_confirm.sync_order", return_value="pending").start()
        self.gw = mock.patch("catalog.gateways.sync_order", return_value="pending").start()
        self.addCleanup(mock.patch.stopall)

    def sync(self, order):
        return self.client.post(f"/api/v1/venues/a/events/fecha/orders/{order.pk}/payment/sync/")

    def test_second_call_within_window_skips_the_gateways(self):
        order = make_order(self.event)
        first, second = self.sync(order), self.sync(order)
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(first.json(), second.json())
        self.assertEqual((self.mp.call_count, self.gw.call_count), (1, 1))
        Order.objects.filter(pk=order.pk).update(payment_synced_at=timezone.now() - timedelta(seconds=16))
        self.sync(order)
        self.assertEqual((self.mp.call_count, self.gw.call_count), (2, 2))

    def test_throttle_is_per_order(self):
        self.sync(make_order(self.event))
        self.sync(make_order(self.event))
        self.assertEqual(self.mp.call_count, 2)

    def test_long_expired_order_does_not_query(self):
        order = make_order(self.event)
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(hours=25))
        self.assertEqual(self.sync(order).json()["order"]["status"], "expired")
        self.assertEqual((self.mp.call_count, self.gw.call_count), (0, 0))
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(hours=23))
        self.sync(order)
        self.assertEqual(self.mp.call_count, 1)

    def test_paid_order_does_not_query(self):
        order = make_order(self.event)
        Order.objects.filter(pk=order.pk).update(paid_at=timezone.now())
        self.assertEqual(self.sync(order).status_code, 200)
        self.assertEqual(self.mp.call_count, 0)

    def test_claim_is_conditional(self):
        order = make_order(self.event)
        self.assertTrue(_may_query_gateway(order))
        self.assertFalse(_may_query_gateway(Order.objects.get(pk=order.pk)))
        self.assertFalse(_may_query_gateway(order))  # la copia vieja en memoria tampoco pasa


# ---------- 5. token de staff ----------

class StaffTokenTests(TestCase):
    def setUp(self):
        self.venue = Venue.objects.create(name="A", slug="a", is_active=True)
        self.user = make_user("portero", (self.venue, Membership.Role.DOOR))

    def me(self, token):
        return self.client.get("/api/v1/auth/me/", HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_password_change_revokes_the_token(self):
        token = issue_token(self.user)
        self.assertEqual(self.me(token).status_code, 200)
        self.user.set_password("otra-clave-segura-456")
        self.user.save()
        self.assertEqual(self.me(token).status_code, 401)
        self.assertEqual(self.me(issue_token(self.user)).status_code, 200)

    def test_each_token_belongs_to_its_user(self):
        other = make_user("otro")
        self.assertEqual(self.me(issue_token(other)).json()["user"]["username"], "otro")
        forged = signing.dumps({"uid": other.pk, "h": issue_token(self.user)[:10]}, salt=TOKEN_SALT)
        self.assertEqual(self.me(forged).status_code, 401)

    def test_old_token_without_stamp_is_invalid(self):
        self.assertEqual(self.me(signing.dumps({"uid": self.user.pk}, salt=TOKEN_SALT)).status_code, 401)


# ---------- 6. límite de login por usuario e IP ----------

@override_settings(FRONT_API_KEY=KEY)
class LoginIpLimitTests(TestCase):
    PATH = "/api/v1/door/login/"

    def setUp(self):
        self.venue = Venue.objects.create(name="A", slug="a", is_active=True)
        make_user("portero", (self.venue, Membership.Role.DOOR))
        make_user("otro", (self.venue, Membership.Role.DOOR))

    def login(self, username="portero", password="mala", ip=None, key=KEY):
        extra = {"HTTP_X_FRONT_KEY": key} if key else {}
        if ip:
            extra["HTTP_X_BUYER_IP"] = ip
        return self.client.post(
            self.PATH, json.dumps({"username": username, "password": password}),
            content_type="application/json", **extra,
        )

    def test_user_is_blocked_only_on_the_ip_that_failed(self):
        for _ in range(10):
            self.assertEqual(self.login(ip="203.0.113.1").status_code, 401)
        self.assertEqual(self.login(ip="203.0.113.1").status_code, 429)
        self.assertEqual(self.login(password="clave-segura-123", ip="203.0.113.1").status_code, 429)
        # Otra IP: el usuario legítimo entra aunque le hayan fallado 10 desde afuera.
        self.assertEqual(self.login(password="clave-segura-123", ip="203.0.113.2").status_code, 200)
        self.assertEqual(self.login(username="otro", ip="203.0.113.1").status_code, 401)  # otro usuario, misma IP

    def test_ip_wide_cap(self):
        DoorLoginFailure.objects.bulk_create(
            [DoorLoginFailure(username=f"u{n}", ip="203.0.113.5") for n in range(30)],
        )
        self.assertEqual(self.login(username="nuevo", ip="203.0.113.5").status_code, 429)
        self.assertEqual(self.login(password="clave-segura-123", ip="203.0.113.5").status_code, 429)
        self.assertEqual(self.login(password="clave-segura-123", ip="203.0.113.6").status_code, 200)
        DoorLoginFailure.objects.update(created_at=timezone.now() - timedelta(minutes=16))
        self.assertEqual(self.login(password="clave-segura-123", ip="203.0.113.5").status_code, 200)

    def test_without_trusted_ip_it_is_per_user(self):
        for _ in range(10):
            self.login(ip="203.0.113.1", key="falsa")  # clave mala: la IP se ignora
        self.assertEqual(DoorLoginFailure.objects.filter(username="portero", ip__isnull=True).count(), 10)
        self.assertEqual(self.login(ip="203.0.113.2", key="falsa").status_code, 429)
        self.assertEqual(self.login(key=None).status_code, 429)

    def test_unknown_user_and_wrong_password_look_the_same(self):
        a = self.login(username="fantasma", ip="203.0.113.1")
        b = self.login(ip="203.0.113.1")
        self.assertEqual((a.status_code, a.json()), (b.status_code, b.json()))

    def test_success_clears_only_that_ip(self):
        self.login(ip="203.0.113.1")
        self.login(ip="203.0.113.2")
        self.assertEqual(self.login(password="clave-segura-123", ip="203.0.113.1").status_code, 200)
        self.assertEqual(list(DoorLoginFailure.objects.values_list("ip", flat=True)), ["203.0.113.2"])


# ---------- 7. nombres e invitados ----------

class NameTests(GuestListTestCase):
    def test_order_name_validation(self):
        TicketType.objects.filter(pk=create_catalog("moon").pk).update(capacity=20)
        for bad in ("Ver http://malo.com", "www.malo.com", "a/b", "a\\b", "<b>x</b>", "ana@x.com", "Ana\x00", "Ana\x07"):
            response = create_order(self.client, uuid4(), quantity=1, name=bad)
            self.assertEqual((response.status_code, response.json()), (400, {"error": "invalid_name"}), bad)
        for n, good in enumerate(("María José O'Brien-Núñez", "Ana", "J. P. 2da", "Ñandú Pérez")):
            response = create_order(self.client, uuid4(), quantity=1, name=good, email=f"u{n}@example.com")
            self.assertEqual(response.status_code, 201, good)

    def test_guest_name_validation(self):
        guest_list = self.make_list("rrpp1")
        for bad in ("Ver http://malo.com", "www.malo.com", "a/b", "a\\b", "<b>", "ana@x.com", "Ana\x00", ""):
            response = self.add_guest(guest_list["id"], self.rrpp1, name=bad)
            self.assertEqual((response.status_code, response.json()), (400, {"error": "invalid_name"}), bad)
        self.assertEqual(self.add_guest(guest_list["id"], self.rrpp1, name="María O'Brien-Núñez").status_code, 201)

    @override_settings(MAX_GUESTS_PER_PROMOTER_PER_HOUR=2)
    def test_guest_cap_per_promoter_across_lists(self):
        l1 = self.make_list("rrpp1")
        l2 = self.make_list("rrpp1", event=self.event_a2)
        lb = self.make_list("rrpp-b", event=self.event_b)
        self.assertEqual(self.add_guest(l1["id"], self.rrpp1, email="a1@example.com").status_code, 201)
        self.assertEqual(self.add_guest(l2["id"], self.rrpp1, email="a2@example.com").status_code, 201)
        response = self.add_guest(l1["id"], self.rrpp1, email="a3@example.com")
        self.assertEqual((response.status_code, response.json()), (429, {"error": "too_many_guests"}))
        # El tope es por RRPP: el de otro boliche sigue pudiendo.
        self.assertEqual(self.add_guest(lb["id"], self.rrpp_b, email="b1@example.com").status_code, 201)
        # Pasada la hora, se libera.
        Ticket.objects.update(created_at=timezone.now() - timedelta(hours=2))
        self.assertEqual(self.add_guest(l1["id"], self.rrpp1, email="a3@example.com").status_code, 201)
