"""Seguridad, bloque 2: anulación de entradas, payment_ref, clave de QR, antigüedad del webhook y tope de login."""

import hashlib
import hmac
import time
from datetime import timedelta
from unittest import mock
from uuid import uuid4

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db.models import QuerySet
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import mp, qr
from .client import proxied_client_ip
from .gateways import base as gateways_base
from .gateways.base import save_account, settle
from .models import (
    DoorLoginFailure, GatewayCharge, Membership, Payment, Ticket, VoidReason,
    new_payment_ref,
)
from .payment_confirm import order_total, process_order
from .qr import InvalidTicketToken, sign_ticket, verify_ticket
from .test_door import PASSWORD, DoorTestCase, make_ticket
from .test_payment_confirm import Base, mp_order
from .test_payments import make_order, make_venue
from .tickets import issue_tickets
from .voiding import void_tickets

User = get_user_model()
MACRO = {"comercio": "COM-123", "secret": "frase-secreta-del-comercio", "sucursal": ""}


# ---------- 1. anular entradas ----------

class VoidTicketsTests(DoorTestCase):
    def test_void_is_conditional_idempotent_and_keeps_sold(self):
        sold = self.ticket_a.ticket_type.sold
        result = void_tickets(Ticket.objects.filter(pk=self.ticket_a.pk), VoidReason.REFUND)
        self.assertEqual(tuple(result), (1, 0))
        self.ticket_a.refresh_from_db()
        first = self.ticket_a.voided_at
        self.assertEqual(self.ticket_a.void_reason, "refund")
        # Segunda anulación: no pisa ni el motivo ni la fecha.
        again = void_tickets(Ticket.objects.filter(pk=self.ticket_a.pk), VoidReason.MANUAL, by=self.door)
        self.assertEqual(tuple(again), (0, 0))
        self.ticket_a.refresh_from_db()
        self.assertEqual((self.ticket_a.voided_at, self.ticket_a.void_reason, self.ticket_a.voided_by), (first, "refund", None))
        self.ticket_a.ticket_type.refresh_from_db()
        self.assertEqual(self.ticket_a.ticket_type.sold, sold)  # anular no libera cupo

    def test_used_ticket_can_be_voided_and_is_counted(self):
        Ticket.objects.filter(pk=self.ticket_a.pk).update(used_at=timezone.now(), used_by=self.door)
        with self.assertLogs("catalog.voiding", "WARNING"):
            result = void_tickets(Ticket.objects.filter(pk=self.ticket_a.pk), VoidReason.CHARGEBACK, by=self.door)
        self.assertEqual(tuple(result), (1, 1))
        self.ticket_a.refresh_from_db()
        self.assertIsNotNone(self.ticket_a.used_at)
        self.assertEqual(self.ticket_a.voided_by, self.door)

    def test_invalid_reason(self):
        with self.assertRaises(ValueError):
            void_tickets(Ticket.objects.all(), "porque si")


class VoidDoorTests(DoorTestCase):
    def void(self, ticket, reason=VoidReason.MANUAL):
        void_tickets(Ticket.objects.filter(pk=ticket.pk), reason)
        ticket.refresh_from_db()

    def test_voided_ticket_is_409_void_and_not_burned(self):
        self.void(self.ticket_a)
        response = self.scan(self.ticket_a, self.token())
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["result"], "void")
        self.assertEqual(body["ticket"], {
            "id": str(self.ticket_a.id), "ticketType": "General", "holderName": "Juan Pérez",
            "voidedAt": self.ticket_a.voided_at.isoformat(),
        })
        self.ticket_a.refresh_from_db()
        self.assertIsNone(self.ticket_a.used_at)
        self.assertIsNone(self.ticket_a.used_by)

    def test_voided_after_admission_is_void_not_already_used(self):
        token = self.token()
        self.assertEqual(self.scan(self.ticket_a, token).json()["result"], "admitted")
        self.void(self.ticket_a, VoidReason.CHARGEBACK)
        self.assertEqual(self.scan(self.ticket_a, token).json()["result"], "void")

    def test_voided_between_check_and_burn_does_not_admit(self):
        token = self.token()
        original = QuerySet.first

        def stale_first(queryset):
            found = original(queryset)
            if isinstance(found, Ticket) and found.voided_at is None:  # se anula justo después de leerla
                Ticket.objects.filter(pk=found.pk).update(voided_at=timezone.now(), void_reason="refund")
            return found

        with mock.patch.object(QuerySet, "first", stale_first):
            response = self.scan(self.ticket_a, token)
        self.assertEqual((response.status_code, response.json()["result"]), (409, "void"))
        self.ticket_a.refresh_from_db()
        self.assertIsNone(self.ticket_a.used_at)

    def test_other_tickets_unaffected(self):
        self.void(self.ticket_a)
        self.assertEqual(self.scan(self.ticket_a2, self.token(), event="otra").json()["result"], "admitted")

    def test_attendance_excludes_voided(self):
        second = make_ticket(self.event_a, buyer_name="Otro Comprador", type_name="Extra")
        token = self.token()
        before = self.client.get("/api/v1/door/venues/a/events/fecha/attendance/", HTTP_AUTHORIZATION=f"Bearer {token}").json()
        self.assertEqual(before["issued"], 2)
        self.void(second)
        self.scan(self.ticket_a, token)
        self.void(self.ticket_a)  # entró y después se anuló: tampoco cuenta
        after = self.client.get("/api/v1/door/venues/a/events/fecha/attendance/", HTTP_AUTHORIZATION=f"Bearer {token}").json()
        self.assertEqual((after["issued"], after["admitted"]), (0, 0))
        self.assertEqual(after["tickets"], [])
        self.assertEqual([row["issued"] for row in after["ticketTypes"]], [])

    def test_public_order_shows_voided_at(self):
        order = self.ticket_a.order
        url = f"/api/v1/venues/a/events/fecha/orders/{order.pk}/"
        self.assertIsNone(self.client.get(url).json()["order"]["tickets"][0]["voidedAt"])
        self.void(self.ticket_a)
        shown = self.client.get(url).json()["order"]["tickets"][0]
        self.assertEqual(shown["voidedAt"], self.ticket_a.voided_at.isoformat())


class VoidAdminTests(TestCase):
    def setUp(self):
        self.venue_a, self.event_a, self.general_a, self.vip_a = make_venue("a")
        self.venue_b, self.event_b, self.general_b, _ = make_venue("b")
        self.order_a = make_order(self.event_a, {"general": 2})
        self.order_b = make_order(self.event_b, {"general": 1})
        self.unpaid_a = make_order(self.event_a, {"general": 1})
        issue_tickets(self.order_a.pk)
        issue_tickets(self.order_b.pk)
        self.user = User.objects.create_user(username="a-admin", is_staff=True)
        Membership.objects.create(venue=self.venue_a, user=self.user, role=Membership.Role.ADMIN)
        self.user.user_permissions.add(Permission.objects.get(codename="change_order"))
        self.client.force_login(self.user)
        self.url = reverse("admin:catalog_order_changelist")

    def act(self, orders, confirm=True):
        data = {"action": "void_order_tickets", "_selected_action": [str(o.pk) for o in orders]}
        if confirm:
            data["post"] = "yes"
        return self.client.post(self.url, data)

    def test_asks_for_confirmation_first(self):
        response = self.act([self.order_a], confirm=False)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sí, anular")
        self.assertFalse(Ticket.objects.filter(voided_at__isnull=False).exists())

    def test_voids_all_tickets_of_the_order_as_manual(self):
        response = self.act([self.order_a])
        self.assertEqual(response.status_code, 302)
        tickets = Ticket.objects.filter(order=self.order_a)
        self.assertEqual(tickets.count(), 2)
        for ticket in tickets:
            self.assertIsNotNone(ticket.voided_at)
            self.assertEqual((ticket.void_reason, ticket.voided_by), ("manual", self.user))
        self.general_a.refresh_from_db()
        self.assertEqual(self.general_a.sold, 2 + 0)  # el cupo vendido no se toca

    def test_other_venue_orders_are_out_of_reach(self):
        self.act([self.order_a, self.order_b])
        self.assertFalse(Ticket.objects.filter(order=self.order_b, voided_at__isnull=False).exists())
        self.assertTrue(Ticket.objects.filter(order=self.order_a, voided_at__isnull=False).exists())

    def test_unpaid_order_has_nothing_to_void(self):
        response = self.act([self.unpaid_a])
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Ticket.objects.filter(order=self.unpaid_a).exists())

    def test_requires_change_permission(self):
        self.user.user_permissions.clear()
        self.client.force_login(User.objects.get(pk=self.user.pk))
        self.act([self.order_a])
        self.assertFalse(Ticket.objects.filter(voided_at__isnull=False).exists())


class AutoVoidMercadoPagoTests(Base):
    def issued_order(self):
        order = make_order(self.event_a)
        process_order(self.acc_a, mp_order(order))
        return order

    def voided(self, order):
        return list(Ticket.objects.filter(order=order, voided_at__isnull=False).values_list("void_reason", flat=True))

    def test_refund_voids_tickets_and_keeps_sold(self):
        order = self.issued_order()
        self.general_a.refresh_from_db()
        sold = self.general_a.sold
        with self.assertLogs("catalog.payment_confirm", "WARNING"):
            record = process_order(self.acc_a, mp_order(order, status="refunded"))
        self.assertEqual(record.outcome, Payment.Outcome.ISSUED)
        self.assertEqual(self.voided(order), ["refund"] * 3)
        self.general_a.refresh_from_db()
        self.assertEqual(self.general_a.sold, sold)

    def test_chargeback_voids_with_chargeback_reason(self):
        order = self.issued_order()
        process_order(self.acc_a, mp_order(order, status="charged_back"))
        self.assertEqual(self.voided(order), ["chargeback"] * 3)

    def test_repeated_notice_does_not_change_the_void(self):
        order = self.issued_order()
        process_order(self.acc_a, mp_order(order, status="refunded"))
        stamp = Ticket.objects.filter(order=order).first().voided_at
        process_order(self.acc_a, mp_order(order, status="refunded"))
        self.assertEqual(Ticket.objects.filter(order=order).first().voided_at, stamp)

    def test_partial_refund_does_not_void(self):
        order = self.issued_order()
        process_order(self.acc_a, mp_order(order, status="processed", status_detail="partially_refunded"))
        self.assertEqual(self.voided(order), [])

    def test_refund_of_a_duplicate_payment_does_not_void_the_good_one(self):
        order = self.issued_order()
        process_order(self.acc_a, mp_order(order, order_id="ORDTST02"))  # duplicado: no emite
        process_order(self.acc_a, mp_order(order, order_id="ORDTST02", status="refunded"))
        self.assertEqual(self.voided(order), [])

    def test_refund_of_a_payment_that_never_issued(self):
        order = make_order(self.event_a)
        process_order(self.acc_a, mp_order(order, status="refunded"))
        self.assertEqual(Ticket.objects.count(), 0)


@override_settings(PAYMENTS_ALLOW_TEST_MODE=True, MP_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode())
class AutoVoidGatewayTests(TestCase):
    """Macro y Nave no tienen estados de devolución documentados: el mecanismo existe y está vacío."""

    def setUp(self):
        self.venue, self.event, *_ = make_venue("a")
        save_account(self.venue, "macro", MACRO, live_mode=True)
        self.order = make_order(self.event)
        self.charge = GatewayCharge.objects.create(order=self.order, provider="macro", reference=self.order.payment_ref)
        settle(self.charge.pk, status="3", approved=True, amount=order_total(self.order))

    def test_no_known_states_means_no_void(self):
        self.assertEqual(gateways_base.REVERSAL_STATES, {})
        settle(self.charge.pk, status="9", approved=False, amount=order_total(self.order))
        self.assertFalse(Ticket.objects.filter(order=self.order, voided_at__isnull=False).exists())

    def test_known_reversal_state_voids(self):
        with mock.patch.dict(gateways_base.REVERSAL_STATES, {("macro", "9"): VoidReason.REFUND}):
            settle(self.charge.pk, status="9", approved=False, amount=order_total(self.order))
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.outcome, Payment.Outcome.ISSUED)
        self.assertEqual(Ticket.objects.filter(order=self.order, void_reason="refund").count(), 3)


# ---------- 2. payment_ref ----------

@override_settings(MP_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode())
class PaymentRefTests(Base):
    def test_new_orders_get_distinct_random_refs_different_from_the_id(self):
        a, b = make_order(self.event_a), make_order(self.event_a)
        self.assertNotEqual(a.payment_ref, b.payment_ref)
        self.assertGreaterEqual(len(a.payment_ref), 32)
        self.assertNotIn(str(a.pk), a.payment_ref)
        self.assertNotEqual(new_payment_ref(), new_payment_ref())

    def test_mp_order_body_sends_payment_ref_not_the_id(self):
        from .payments import order_body
        order = make_order(self.event_a)
        body = order_body(order, list(order.items.select_related("ticket_type")), 0, order_total(order))
        self.assertEqual(body["external_reference"], order.payment_ref)
        self.assertNotIn(str(order.pk), str(body["external_reference"]))

    def test_confirms_by_payment_ref(self):
        order = make_order(self.event_a)
        record = process_order(self.acc_a, mp_order(order, external_reference=order.payment_ref))
        self.assertEqual(record.outcome, Payment.Outcome.ISSUED)

    def test_fallback_to_order_uuid_for_charges_started_before_the_deploy(self):
        order = make_order(self.event_a)
        record = process_order(self.acc_a, mp_order(order, external_reference=str(order.pk)))
        self.assertEqual(record.outcome, Payment.Outcome.ISSUED)
        self.assertEqual(record.order, order)

    def test_ref_of_another_venue_is_not_resolved(self):
        order = make_order(self.event_a)
        self.assertIsNone(process_order(self.acc_b, mp_order(order, user_id=self.acc_b.mp_user_id)))

    def test_old_macro_charge_keeps_working(self):
        # Cobro iniciado antes: la referencia guardada es el UUID en hexadecimal.
        save_account(self.venue_a, "macro", MACRO, live_mode=True)
        order = make_order(self.event_a)
        charge = GatewayCharge.objects.create(order=order, provider="macro", reference=order.pk.hex)
        row = {"transaccionComercioId": order.pk.hex, "estado": 3, "montoBruto": float(order_total(order))}
        session = mock.Mock(status_code=200, json=mock.Mock(return_value={"data": "t"}))
        listed = mock.Mock(status_code=200, json=mock.Mock(return_value={"data": {"transacciones": [row]}}))
        with mock.patch("catalog.gateways.macro.requests.post", return_value=session), \
                mock.patch("catalog.gateways.macro.requests.get", return_value=listed):
            response = self.client.post(
                "/api/v1/pagos/macro/aviso/", data='{"TransaccionComercioId": "%s"}' % order.pk.hex,
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        charge.refresh_from_db()
        self.assertEqual(charge.outcome, Payment.Outcome.ISSUED)

    def test_old_nave_notice_url_and_external_id_keep_working(self):
        from .gateways import nave
        account = save_account(self.venue_a, "nave", {"client_id": "c", "client_secret": "s", "pos_id": "P"}, True)
        order = make_order(self.event_a)
        charge = GatewayCharge.objects.create(
            order=order, provider="nave", reference="PR-1", notify_secret="s3cret", checkout_url="https://x",
        )
        for external in (str(order.pk), order.payment_ref):
            data = {"external_payment_id": external, "status": {"name": "SUCCESS_PROCESSED"},
                    "transactions": [{"amount": {"currency": "ARS", "value": "1.00"}}]}
            with mock.patch.object(nave, "_request", return_value=data):
                self.assertIsNotNone(nave.fetch(account, charge))
        data["external_payment_id"] = "otro"
        with mock.patch.object(nave, "_request", return_value=data):
            self.assertIsNone(nave.fetch(account, charge))
        with mock.patch("catalog.gateways.views.sync_charge", return_value="pending") as sync:
            for ref in (str(order.pk), order.payment_ref):
                response = self.client.post(f"/api/v1/pagos/nave/{ref}/aviso/?secret=s3cret")
                self.assertEqual(response.status_code, 200)
            self.assertEqual(sync.call_count, 2)
            self.assertEqual(self.client.post(f"/api/v1/pagos/nave/{order.payment_ref}/aviso/?secret=mal").status_code, 401)


# ---------- 3. clave propia de los QR ----------

class QrKeyTests(TestCase):
    def setUp(self):
        self.ticket_id = uuid4()

    def legacy(self):
        with override_settings(TICKET_QR_KEY=""):
            return sign_ticket(self.ticket_id)

    @override_settings(TICKET_QR_KEY="clave-propia-de-qr")
    def test_new_tokens_use_the_new_key_and_old_ones_still_enter(self):
        old = self.legacy()
        new = sign_ticket(self.ticket_id)
        self.assertNotEqual(old, new)
        self.assertEqual(verify_ticket(new), self.ticket_id)
        self.assertEqual(verify_ticket(old), self.ticket_id)  # el mail ya enviado sigue valiendo

    @override_settings(TICKET_QR_KEY="clave-propia-de-qr", TICKET_QR_ACCEPT_LEGACY=False)
    def test_without_legacy_the_old_token_is_invalid(self):
        old = self.legacy()
        with self.assertRaises(InvalidTicketToken):
            verify_ticket(old)
        self.assertEqual(verify_ticket(sign_ticket(self.ticket_id)), self.ticket_id)

    @override_settings(TICKET_QR_KEY="")
    def test_without_key_everything_is_as_before(self):
        self.assertEqual(verify_ticket(sign_ticket(self.ticket_id)), self.ticket_id)

    @override_settings(TICKET_QR_KEY="")
    def test_accept_legacy_flag_is_irrelevant_without_key(self):
        with override_settings(TICKET_QR_ACCEPT_LEGACY=False):
            self.assertEqual(verify_ticket(sign_ticket(self.ticket_id)), self.ticket_id)

    @override_settings(TICKET_QR_KEY="clave-propia-de-qr")
    def test_token_signed_with_another_key_is_invalid(self):
        with override_settings(TICKET_QR_KEY="otra-clave"):
            other = sign_ticket(self.ticket_id)
        with self.assertRaises(InvalidTicketToken):
            verify_ticket(other)


class QrKeyDoorTests(DoorTestCase):
    def test_door_accepts_old_and_new_and_rejects_old_without_legacy(self):
        token = self.token()
        old = sign_ticket(self.ticket_a.id)
        with override_settings(TICKET_QR_KEY="clave-propia-de-qr"):
            self.assertEqual(self.post(self.scan_url(), {"token": old}, token).json()["result"], "admitted")
            new = sign_ticket(self.ticket_a2.id)
            self.assertEqual(self.post(self.scan_url(event="otra"), {"token": new}, token).json()["result"], "admitted")
        with override_settings(TICKET_QR_KEY="clave-propia-de-qr", TICKET_QR_ACCEPT_LEGACY=False):
            response = self.post(self.scan_url(), {"token": old}, token)
            self.assertEqual((response.status_code, response.json()), (404, {"result": "invalid"}))


# ---------- 4. antigüedad del webhook de Mercado Pago ----------

SECRET = "secreto-del-webhook"


@override_settings(MP_WEBHOOK_SECRET=SECRET)
class WebhookAgeTests(TestCase):
    def sign(self, ts, data_id="abc123", request_id="req-1"):
        manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
        digest = hmac.new(SECRET.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        return f"ts={ts},v1={digest}"

    def valid(self, ts):
        return mp.webhook_signature_is_valid(self.sign(ts), "req-1", "abc123")

    def test_fresh_signature_in_seconds_or_milliseconds(self):
        now = time.time()
        self.assertTrue(self.valid(int(now)))
        self.assertTrue(self.valid(int(now * 1000)))

    def test_old_signature_is_invalid(self):
        now = time.time()
        for age in (301, 3600, 86400 * 400):
            self.assertFalse(self.valid(int(now - age)), age)
            self.assertFalse(self.valid(int((now - age) * 1000)), age)
        self.assertTrue(self.valid(int(now - 240)))

    def test_future_signature_is_invalid(self):
        now = time.time()
        self.assertFalse(self.valid(int(now + 600)))
        self.assertFalse(self.valid(int((now + 600) * 1000)))
        self.assertTrue(self.valid(int(now + 120)))

    def test_old_valid_signature_is_401_at_the_endpoint(self):
        headers = {"HTTP_X_SIGNATURE": self.sign(int(time.time()) - 3600), "HTTP_X_REQUEST_ID": "req-1"}
        response = self.client.post(
            "/api/v1/mercadopago/webhook/?data.id=abc123", data='{"type": "order", "user_id": 1}',
            content_type="application/json", **headers,
        )
        self.assertEqual((response.status_code, response.json()), (401, {"error": "invalid_signature"}))


# ---------- 5. tope de intentos en /admin/ y /plataforma/ ----------

class ClientIpTests(TestCase):
    def ip(self, forwarded=None, remote="10.0.0.1", proxy=True):
        extra = {"HTTP_X_FORWARDED_FOR": forwarded} if forwarded is not None else {}
        request = RequestFactory().get("/", REMOTE_ADDR=remote, **extra)
        with override_settings(SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https") if proxy else None):
            return proxied_client_ip(request)

    def test_behind_proxy_uses_the_last_forwarded_value(self):
        self.assertEqual(self.ip("6.6.6.6, 203.0.113.9"), "203.0.113.9")
        self.assertEqual(self.ip("203.0.113.9"), "203.0.113.9")
        self.assertEqual(self.ip("2001:db8::1"), "2001:db8::1")

    def test_invalid_forwarded_value_gives_empty(self):
        self.assertEqual(self.ip("1.1.1.1, no-es-ip"), "")

    def test_without_forwarded_header_uses_remote_addr(self):
        self.assertEqual(self.ip(), "10.0.0.1")

    def test_without_proxy_forwarded_is_ignored(self):
        self.assertEqual(self.ip("6.6.6.6", remote="198.51.100.4", proxy=False), "198.51.100.4")


class WebLoginLimitMixin:
    path = ""
    ip = "198.51.100.10"

    def setUp(self):
        self.root = User.objects.create_superuser("root", "root@example.com", PASSWORD)
        self.root.is_staff = True
        self.root.save()

    def attempt(self, username="root", password="mala", ip=None):
        return self.client.post(self.path, {"username": username, "password": password}, REMOTE_ADDR=ip or self.ip)

    def failures(self, **filters):
        return DoorLoginFailure.objects.filter(scope="admin", **filters).count()

    def assertNotLocked(self, response):
        self.assertNotIn("Demasiados intentos", response.content.decode())

    def assertLocked(self, response):
        self.assertEqual(response.status_code, 200)  # mismo formulario, no un error aparte
        self.assertContains(response, "Demasiados intentos. Probá en unos minutos.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_five_failures_lock_even_the_right_password(self):
        for _ in range(5):
            self.assertNotLocked(self.attempt())
        self.assertLocked(self.attempt())
        self.assertLocked(self.attempt(password=PASSWORD))

    def test_lock_message_is_the_same_for_unknown_users(self):
        for _ in range(5):
            self.attempt("fantasma")
        self.assertLocked(self.attempt("fantasma"))

    def test_lock_is_per_user_and_ip_but_the_ip_cap_covers_everyone(self):
        for _ in range(5):
            self.attempt()
        self.assertNotLocked(self.attempt(ip="198.51.100.77"))  # otra IP
        self.assertNotLocked(self.attempt("otro-usuario"))  # otro usuario, misma IP
        for i in range(20):
            self.attempt(f"user{i}", ip="198.51.100.99")
        self.assertLocked(self.attempt("nuevo", ip="198.51.100.99"))

    def test_lock_expires_after_15_minutes(self):
        for _ in range(5):
            self.attempt()
        DoorLoginFailure.objects.update(created_at=timezone.now() - timedelta(minutes=16))
        self.assertNotLocked(self.attempt(password=PASSWORD))
        self.assertIn("_auth_user_id", self.client.session)

    def test_success_clears_that_users_failures(self):
        for _ in range(4):
            self.attempt()
        self.assertEqual(self.failures(username="root"), 4)
        self.attempt(password=PASSWORD)
        self.assertEqual(self.failures(username="root"), 0)

    def test_door_failures_are_a_separate_scope(self):
        for _ in range(10):
            DoorLoginFailure.objects.create(username="root", ip=self.ip, scope="door")
        self.assertNotLocked(self.attempt(password=PASSWORD))

    def test_forwarded_ip_comes_from_the_proxy(self):
        with override_settings(SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https")):
            for i in range(5):  # el cliente inventa el primer valor; Caddy agrega el último
                self.client.post(
                    self.path, {"username": "root", "password": "mala"},
                    REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR=f"9.9.9.{i}, 203.0.113.50",
                )
            self.assertEqual(self.failures(ip="203.0.113.50"), 5)
            response = self.client.post(
                self.path, {"username": "root", "password": "mala"},
                REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="1.2.3.4, 203.0.113.50",
            )
        self.assertLocked(response)


class AdminLoginLimitTests(WebLoginLimitMixin, TestCase):
    path = "/admin/login/"


class PlatformLoginLimitTests(WebLoginLimitMixin, TestCase):
    path = "/plataforma/entrar/"
