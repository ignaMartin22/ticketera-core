import hashlib
import hmac
import io
import json
from datetime import timedelta
from threading import Barrier, Thread
from unittest import mock
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import MercadoPagoAccount, Membership, Order, Payment, Ticket, TicketType, VenueIntegration
from .mp import MercadoPagoError
from .payment_confirm import process_order
from .test_payments import MP_ORDER_ID, make_order, make_venue
from .views import order_data

SECRET = "secreto-del-webhook"
WEBHOOK = "/api/v1/mercadopago/webhook/"
USER_A, USER_B = 1001, 2002
NO_CAPACITY, ISSUED = Payment.Outcome.NO_CAPACITY, Payment.Outcome.ISSUED


def make_account(venue, mp_user_id):
    return MercadoPagoAccount.objects.create(
        venue=venue, mp_user_id=mp_user_id, access_token="x", refresh_token="x",
        public_key="pk", live_mode=False, expires_at=timezone.now() + timedelta(days=100),
    )


def total_of(order):
    return sum(i.quantity * (i.unit_price + i.unit_service_fee) for i in order.items.all())


def mp_order(order, user_id=USER_A, order_id=MP_ORDER_ID, status="processed", amount=None, **extra):
    amount = f"{(total_of(order) if amount is None else amount):.2f}"
    return {
        "id": order_id, "status": status, "status_detail": "accredited",
        "external_reference": str(order.pk), "total_amount": amount, "total_paid_amount": amount,
        "currency": "ARS", "user_id": str(user_id),
        "transactions": {"payments": [{
            "id": "PAY01", "amount": amount, "paid_amount": amount, "status": status,
            "status_detail": "accredited", "payment_method": {"id": "master", "type": "credit_card"},
            "reference": {"id": "183961436800"},
        }]},
        **extra,
    }


class Base(TestCase):
    def setUp(self):
        self.venue_a, self.event_a, self.general_a, self.vip_a = make_venue("a")
        self.venue_b, self.event_b, self.general_b, _ = make_venue("b")
        self.acc_a = make_account(self.venue_a, USER_A)
        self.acc_b = make_account(self.venue_b, USER_B)

    def tickets(self, order):
        return set(Ticket.objects.filter(order=order).values_list("pk", flat=True))


class ProcessOrderTests(Base):
    def test_01_approved_issues_tickets(self):
        order = make_order(self.event_a)
        record = process_order(self.acc_a, mp_order(order))
        self.assertEqual(record.outcome, ISSUED)
        order.refresh_from_db()
        self.assertIsNotNone(order.paid_at)
        self.assertEqual(Ticket.objects.filter(order=order).count(), 3)
        self.assertEqual(record.order, order)

    def test_02_same_payment_twice(self):
        order = make_order(self.event_a)
        process_order(self.acc_a, mp_order(order))
        first = self.tickets(order)
        self.general_a.refresh_from_db()
        sold = self.general_a.sold
        process_order(self.acc_a, mp_order(order))
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(self.tickets(order), first)
        self.general_a.refresh_from_db()
        self.assertEqual(self.general_a.sold, sold)

    def test_03_rejected(self):
        order = make_order(self.event_a)
        record = process_order(self.acc_a, mp_order(order, status="created"))
        self.assertEqual(record.outcome, Payment.Outcome.NOT_APPROVED)
        order.refresh_from_db()
        self.assertIsNone(order.paid_at)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_04_mismatch_amount_and_currency(self):
        order = make_order(self.event_a)
        record = process_order(self.acc_a, mp_order(order, amount=1))
        self.assertEqual(record.outcome, Payment.Outcome.MISMATCH)
        record = process_order(self.acc_a, mp_order(order, order_id="ORDTST02", currency="USD"))
        self.assertEqual(record.outcome, Payment.Outcome.MISMATCH)
        order.refresh_from_db()
        self.assertIsNone(order.paid_at)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_05_expired_reservation_with_capacity(self):
        order = make_order(self.event_a)
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        record = process_order(self.acc_a, mp_order(order))
        self.assertEqual(record.outcome, ISSUED)
        self.assertEqual(Ticket.objects.filter(order=order).count(), 3)

    def test_06_expired_reservation_without_capacity(self):
        TicketType.objects.filter(pk=self.general_a.pk).update(capacity=2)
        order = make_order(self.event_a, {"general": 2})
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        make_order(self.event_a, {"general": 2})
        record = process_order(self.acc_a, mp_order(order))
        self.assertEqual(record.outcome, NO_CAPACITY)
        order.refresh_from_db()
        self.assertIsNone(order.paid_at)
        self.assertEqual(Ticket.objects.count(), 0)
        self.general_a.refresh_from_db()
        self.assertEqual(self.general_a.sold, 0)

    def test_07_second_payment_is_duplicate(self):
        order = make_order(self.event_a)
        process_order(self.acc_a, mp_order(order))
        first = self.tickets(order)
        record = process_order(self.acc_a, mp_order(order, order_id="ORDTST02"))
        self.assertEqual(record.outcome, Payment.Outcome.DUPLICATE)
        self.assertEqual(self.tickets(order), first)

    def test_08_isolation_between_venues(self):
        order = make_order(self.event_a)
        self.assertIsNone(process_order(self.acc_b, mp_order(order, user_id=USER_B)))
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_09_wrong_collector(self):
        order = make_order(self.event_a)
        self.assertIsNone(process_order(self.acc_a, mp_order(order, user_id=USER_B)))
        self.assertEqual(Payment.objects.count(), 0)

    def test_10_bad_external_reference(self):
        order = make_order(self.event_a)
        payment = mp_order(order)
        self.assertIsNone(process_order(self.acc_a, {**payment, "external_reference": "no-es-uuid"}))
        self.assertIsNone(process_order(self.acc_a, {**payment, "external_reference": str(uuid4())}))
        self.assertIsNone(process_order(self.acc_a, {**payment, "external_reference": None}))
        self.assertEqual(Payment.objects.count(), 0)

    def test_11_refund_keeps_issued(self):
        order = make_order(self.event_a)
        process_order(self.acc_a, mp_order(order))
        first = self.tickets(order)
        record = process_order(self.acc_a, mp_order(order, status="refunded"))
        self.assertEqual((record.status, record.outcome), ("refunded", ISSUED))
        self.assertEqual(self.tickets(order), first)

    def test_01b_user_id_text_matches_integer_mp_user_id(self):  # (f)
        order = make_order(self.event_a)
        self.assertIsInstance(self.acc_a.mp_user_id, int)
        data = mp_order(order)
        self.assertEqual(data["user_id"], "1001")
        self.assertEqual(process_order(self.acc_a, data).outcome, ISSUED)

    def test_01c_processed_accredited_saves_reference(self):  # (g)
        order = make_order(self.event_a)
        record = process_order(self.acc_a, mp_order(order))
        record.refresh_from_db()
        self.assertEqual(
            (record.outcome, record.status, record.status_detail, record.mp_reference, record.mp_order_id),
            (ISSUED, "processed", "accredited", "183961436800", MP_ORDER_ID),
        )
        self.assertEqual(record.amount, total_of(order))

    def test_03b_created_or_other_detail_is_not_approved(self):  # (h)
        order = make_order(self.event_a)
        record = process_order(self.acc_a, mp_order(order, status="created", status_detail="waiting_payment"))
        self.assertEqual((record.outcome, record.status), (Payment.Outcome.NOT_APPROVED, "created"))
        other = mp_order(order, order_id="ORDTST02", status_detail="pending_review_manual")
        record = process_order(self.acc_a, other)
        self.assertEqual(record.outcome, Payment.Outcome.NOT_APPROVED)
        order.refresh_from_db()
        self.assertIsNone(order.paid_at)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_03c_falls_back_to_total_amount(self):
        order = make_order(self.event_a)
        data = mp_order(order)
        del data["total_paid_amount"]
        self.assertEqual(process_order(self.acc_a, data).outcome, ISSUED)


class ConcurrencyTests(TransactionTestCase):
    def test_simultaneous_same_payment_issues_once(self):
        venue, event, general, _ = make_venue("a")
        account = make_account(venue, USER_A)
        order = make_order(event)
        payment = mp_order(order)
        barrier = Barrier(2)
        errors = []

        def run():
            try:
                barrier.wait()
                process_order(account, payment)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
            finally:
                connection.close()

        threads = [Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(Ticket.objects.filter(order=order).count(), 3)
        general.refresh_from_db()
        self.assertEqual(general.sold, 2)


def token_for(venue):
    return f"tok-{venue.slug}"


@override_settings(MP_WEBHOOK_SECRET=SECRET)
class WebhookTests(Base):
    def setUp(self):
        super().setUp()
        self.get = mock.patch("catalog.mp.get_order").start()
        mock.patch("catalog.mp_connect.get_access_token", side_effect=token_for).start()
        self.addCleanup(mock.patch.stopall)

    def sign(self, data_id, request_id="req-1", ts="1700000000", secret=SECRET):
        data_id = str(data_id).lower()  # Mercado Pago firma los ids alfanuméricos en minúsculas
        manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
        digest = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        return {"HTTP_X_SIGNATURE": f"ts={ts},v1={digest}", "HTTP_X_REQUEST_ID": request_id}

    def notify(self, user_id=USER_A, order_id=MP_ORDER_ID, type_="order", headers=None, body=None, **extra):
        body = body if body is not None else {
            "action": "order.processed", "type": type_, "data": {"id": order_id}, "user_id": user_id, **extra,
        }
        headers = self.sign(order_id) if headers is None else headers
        return self.client.post(
            f"{WEBHOOK}?data.id={order_id}&type={type_}", data=json.dumps(body),
            content_type="application/json", **headers,
        )

    def test_12_valid_notification_issues(self):
        order = make_order(self.event_a)
        self.get.return_value = mp_order(order)
        response = self.notify()
        self.assertEqual((response.status_code, response.json()), (200, {"ok": True}))
        self.assertEqual(self.get.call_args.args, ("tok-a", MP_ORDER_ID))
        self.assertEqual(Payment.objects.get().outcome, ISSUED)
        self.assertEqual(Ticket.objects.filter(order=order).count(), 3)

    def test_13_bad_or_missing_signature(self):
        headers = self.sign(MP_ORDER_ID)
        headers["HTTP_X_SIGNATURE"] = headers["HTTP_X_SIGNATURE"][:-2] + "00"
        response = self.notify(headers=headers)
        self.assertEqual((response.status_code, response.json()), (401, {"error": "invalid_signature"}))
        self.assertEqual(self.notify(headers={"HTTP_X_REQUEST_ID": "req-1"}).status_code, 401)
        self.assertEqual(self.notify(headers=self.sign("ORDOTRO")).status_code, 401)
        self.get.assert_not_called()

    @override_settings(MP_WEBHOOK_SECRET="")
    def test_14_not_configured(self):
        response = self.notify()
        self.assertEqual((response.status_code, response.json()), (503, {"error": "mp_not_configured"}))

    def test_15_unknown_account(self):
        self.assertEqual(self.notify(user_id=424242).status_code, 200)
        self.get.assert_not_called()

    def test_16_mp_error_is_500(self):
        self.get.side_effect = MercadoPagoError(500)
        self.assertEqual(self.notify().status_code, 500)

    def test_17_same_owner_two_venues(self):
        MercadoPagoAccount.objects.filter(pk=self.acc_b.pk).update(mp_user_id=USER_A)
        order = make_order(self.event_b)
        self.get.return_value = mp_order(order, user_id=USER_A)
        self.assertEqual(self.notify().status_code, 200)
        record = Payment.objects.get()
        self.assertEqual(record.order, order)
        self.assertEqual(Ticket.objects.filter(order=order).count(), 3)
        self.assertEqual({call.args[0] for call in self.get.call_args_list}, {"tok-a", "tok-b"})

    def test_18_deauthorized(self):
        other = make_venue("c")[0]
        acc_c = make_account(other, USER_B)
        response = self.notify(
            user_id=USER_B, type_="mp-connect", headers=self.sign(MP_ORDER_ID), action="application.deauthorized",
        )
        self.assertEqual(response.status_code, 200)
        for account in (self.acc_b, acc_c):
            account.refresh_from_db()
            self.assertTrue(account.needs_reconnect)
            self.assertLessEqual(account.expires_at, timezone.now())
        self.acc_a.refresh_from_db()
        self.assertFalse(self.acc_a.needs_reconnect)
        self.assertGreater(self.acc_a.expires_at, timezone.now())

    def test_18b_deauthorized_expires_the_venue_integration(self):
        provider = VenueIntegration.Provider.MERCADOPAGO
        for venue in (self.venue_a, self.venue_b):
            VenueIntegration.objects.create(
                venue=venue, provider=provider, status=VenueIntegration.Status.CONNECTED, account_id="1",
            )
        response = self.notify(
            user_id=USER_B, type_="mp-connect", headers=self.sign(MP_ORDER_ID), action="application.deauthorized",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            VenueIntegration.objects.get(venue=self.venue_b, provider=provider).status,
            VenueIntegration.Status.EXPIRED,
        )
        self.assertEqual(
            VenueIntegration.objects.get(venue=self.venue_a, provider=provider).status,
            VenueIntegration.Status.CONNECTED,
        )

    def test_19_other_type_does_nothing(self):
        response = self.notify(type_="merchant_order")
        self.assertEqual(response.status_code, 200)
        self.get.assert_not_called()
        self.assertEqual(Payment.objects.count(), 0)

    def test_20_same_notification_twice(self):
        order = make_order(self.event_a)
        self.get.return_value = mp_order(order)
        self.assertEqual(self.notify().status_code, 200)
        self.assertEqual(self.notify().status_code, 200)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(Ticket.objects.filter(order=order).count(), 3)

    def test_19b_payment_type_does_nothing(self):  # (j)
        order = make_order(self.event_a)
        self.get.return_value = mp_order(order)
        response = self.notify(type_="payment", order_id="9001")
        self.assertEqual((response.status_code, response.json()), (200, {"ok": True}))
        self.get.assert_not_called()
        self.assertEqual(Payment.objects.count(), 0)

    def test_12b_uppercase_id_signed_lowercase_and_fetched_as_is(self):  # (i)
        order = make_order(self.event_a)
        self.get.return_value = mp_order(order)
        self.assertEqual(MP_ORDER_ID, MP_ORDER_ID.upper())
        headers = self.sign(MP_ORDER_ID.lower())
        response = self.notify(order_id=MP_ORDER_ID, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.get.call_args.args, ("tok-a", MP_ORDER_ID))
        self.assertEqual(Payment.objects.get().mp_order_id, MP_ORDER_ID)
        self.get.reset_mock()
        # firmado con el id en mayúsculas: no es la firma de Mercado Pago
        manifest = f"id:{MP_ORDER_ID};request-id:req-1;ts:1700000000;"
        digest = hmac.new(SECRET.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        bad = {"HTTP_X_SIGNATURE": f"ts=1700000000,v1={digest}", "HTTP_X_REQUEST_ID": "req-1"}
        self.assertEqual(self.notify(order_id=MP_ORDER_ID, headers=bad).status_code, 401)
        self.get.assert_not_called()

    def test_invalid_body_is_400(self):
        response = self.client.post(
            f"{WEBHOOK}?data.id={MP_ORDER_ID}&type=order", data="[1]", content_type="application/json",
            **self.sign(MP_ORDER_ID),
        )
        self.assertEqual(response.status_code, 400)

    def test_no_token_is_500(self):
        from .mp_connect import MercadoPagoNotConnected
        with mock.patch("catalog.mp_connect.get_access_token", side_effect=MercadoPagoNotConnected("a")):
            self.assertEqual(self.notify().status_code, 500)


def sync_url(order, venue="a", event="fecha"):
    return f"/api/v1/venues/{venue}/events/{event}/orders/{order.pk}/payment/sync/"


class SyncTests(Base):
    def setUp(self):
        super().setUp()
        self.get = mock.patch("catalog.mp.get_order").start()
        mock.patch("catalog.mp_connect.get_access_token", side_effect=token_for).start()
        self.addCleanup(mock.patch.stopall)

    def with_link(self, order):
        Order.objects.filter(pk=order.pk).update(mp_order_id=MP_ORDER_ID)
        return order

    def test_21_sync_confirms(self):
        order = self.with_link(make_order(self.event_a))
        self.get.return_value = mp_order(order)
        response = self.client.post(sync_url(order))
        self.assertEqual(response.status_code, 200)
        data = response.json()["order"]
        self.assertEqual(data["status"], "paid")
        self.assertEqual(len(data["tickets"]), 3)
        self.assertEqual(self.get.call_args.args, ("tok-a", MP_ORDER_ID))

    def test_22_sync_mp_failure(self):
        order = self.with_link(make_order(self.event_a))
        self.get.side_effect = MercadoPagoError(500)
        response = self.client.post(sync_url(order))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["order"]["status"], "pending_payment")

    def test_23_sync_without_query(self):
        no_link = make_order(self.event_a)
        paid = self.with_link(make_order(self.event_a))
        process_order(self.acc_a, mp_order(paid))
        for order in (no_link, paid):
            self.assertEqual(self.client.post(sync_url(order)).status_code, 200)
        self.get.assert_not_called()

    def test_24_sync_wrong_venue(self):
        order = self.with_link(make_order(self.event_a))
        self.assertEqual(self.client.post(sync_url(order, venue="b")).status_code, 404)
        self.get.assert_not_called()


class OrderDataTests(Base):
    def test_25_payment_review_flag_and_queries(self):
        normal = make_order(self.event_a)
        self.assertIs(order_data(normal, normal.items.all())["paymentReview"], False)
        flagged = make_order(self.event_a)

        def queries(order):
            with CaptureQueriesContext(connection) as ctx:
                data = order_data(order, order.items.all())
            return data, len(ctx)

        def add(n, start):
            for i in range(n):
                Payment.objects.create(
                    order=flagged, mp_order_id=f"ORD{start + i}", mp_user_id=USER_A, status="processed",
                    amount=1, outcome=NO_CAPACITY,
                )

        add(1, 100)
        data, one = queries(flagged)
        self.assertIs(data["paymentReview"], True)
        add(4, 200)
        _, five = queries(flagged)
        self.assertEqual(one, five)
        self.assertEqual(
            set(data), {"id", "status", "expiresAt", "currency", "total", "items", "tickets", "paymentReview"},
        )


class ReconcileCommandTests(Base):
    def test_26_command_continues_after_failure(self):
        ok = make_order(self.event_a, {"general": 1})
        empty = make_order(self.event_a, {"general": 1})
        broken = make_order(self.event_a, {"general": 1})
        Order.objects.filter(pk__in=[ok.pk, empty.pk, broken.pk]).update(mp_order_id="ORDTSTX")

        Order.objects.filter(pk=ok.pk).update(mp_order_id="ORDOK")
        Order.objects.filter(pk=empty.pk).update(mp_order_id="ORDEMPTY")
        Order.objects.filter(pk=broken.pk).update(mp_order_id="ORDBROKEN")

        def get_order(token, order_id):
            if order_id == "ORDBROKEN":
                raise MercadoPagoError(500)
            if order_id == "ORDOK":
                return mp_order(ok, order_id="ORDOK")
            return mp_order(empty, order_id="ORDEMPTY", status="created")

        out = io.StringIO()
        with mock.patch("catalog.mp.get_order", side_effect=get_order), mock.patch(
            "catalog.mp_connect.get_access_token", side_effect=token_for,
        ):
            call_command("reconcile_mp_payments", stdout=out, stderr=io.StringIO())
        for order in (ok, empty, broken):
            order.refresh_from_db()
        self.assertIsNotNone(ok.paid_at)
        self.assertIsNone(empty.paid_at)
        self.assertIsNone(broken.paid_at)
        self.assertIn("Revisados: 3", out.getvalue())
        self.assertIn("Confirmados: 1", out.getvalue())
        self.assertIn("Fallidos: 1", out.getvalue())
        self.assertNotIn("Nombre Secreto", out.getvalue())


class PaymentAdminTests(Base):
    def test_27_admin_isolated_and_read_only(self):
        order_a, order_b = make_order(self.event_a), make_order(self.event_b)
        process_order(self.acc_a, mp_order(order_a, order_id="ORD1"))
        process_order(self.acc_b, mp_order(order_b, user_id=USER_B, order_id="ORD2"))
        user = get_user_model().objects.create_user(username="a-admin", is_staff=True)
        Membership.objects.create(venue=self.venue_a, user=user, role=Membership.Role.ADMIN)
        client = Client()
        client.force_login(user)
        response = client.get(reverse("admin:catalog_payment_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([p.mp_order_id for p in response.context["cl"].result_list], ["ORD1"])
        self.assertEqual(client.get(reverse("admin:catalog_payment_add")).status_code, 403)
