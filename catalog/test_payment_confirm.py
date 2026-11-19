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

from .models import MercadoPagoAccount, Membership, Order, Payment, Ticket, TicketType
from .mp import MercadoPagoError
from .payment_confirm import process_payment
from .test_payments import make_order, make_venue
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


def mp_payment(order, user_id=USER_A, payment_id=9001, status="approved", amount=None, **extra):
    amount = total_of(order) if amount is None else amount
    return {
        "id": payment_id, "status": status, "status_detail": "accredited",
        "external_reference": str(order.pk), "transaction_amount": float(amount),
        "currency_id": "ARS", "collector_id": user_id, **extra,
    }


class Base(TestCase):
    def setUp(self):
        self.venue_a, self.event_a, self.general_a, self.vip_a = make_venue("a")
        self.venue_b, self.event_b, self.general_b, _ = make_venue("b")
        self.acc_a = make_account(self.venue_a, USER_A)
        self.acc_b = make_account(self.venue_b, USER_B)

    def tickets(self, order):
        return set(Ticket.objects.filter(order=order).values_list("pk", flat=True))


class ProcessPaymentTests(Base):
    def test_01_approved_issues_tickets(self):
        order = make_order(self.event_a)
        record = process_payment(self.acc_a, mp_payment(order))
        self.assertEqual(record.outcome, ISSUED)
        order.refresh_from_db()
        self.assertIsNotNone(order.paid_at)
        self.assertEqual(Ticket.objects.filter(order=order).count(), 3)
        self.assertEqual(record.order, order)

    def test_02_same_payment_twice(self):
        order = make_order(self.event_a)
        process_payment(self.acc_a, mp_payment(order))
        first = self.tickets(order)
        self.general_a.refresh_from_db()
        sold = self.general_a.sold
        process_payment(self.acc_a, mp_payment(order))
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(self.tickets(order), first)
        self.general_a.refresh_from_db()
        self.assertEqual(self.general_a.sold, sold)

    def test_03_rejected(self):
        order = make_order(self.event_a)
        record = process_payment(self.acc_a, mp_payment(order, status="rejected"))
        self.assertEqual(record.outcome, Payment.Outcome.NOT_APPROVED)
        order.refresh_from_db()
        self.assertIsNone(order.paid_at)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_04_mismatch_amount_and_currency(self):
        order = make_order(self.event_a)
        record = process_payment(self.acc_a, mp_payment(order, amount=1))
        self.assertEqual(record.outcome, Payment.Outcome.MISMATCH)
        record = process_payment(self.acc_a, mp_payment(order, payment_id=9002, currency_id="USD"))
        self.assertEqual(record.outcome, Payment.Outcome.MISMATCH)
        order.refresh_from_db()
        self.assertIsNone(order.paid_at)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_05_expired_reservation_with_capacity(self):
        order = make_order(self.event_a)
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        record = process_payment(self.acc_a, mp_payment(order))
        self.assertEqual(record.outcome, ISSUED)
        self.assertEqual(Ticket.objects.filter(order=order).count(), 3)

    def test_06_expired_reservation_without_capacity(self):
        TicketType.objects.filter(pk=self.general_a.pk).update(capacity=2)
        order = make_order(self.event_a, {"general": 2})
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        make_order(self.event_a, {"general": 2})
        record = process_payment(self.acc_a, mp_payment(order))
        self.assertEqual(record.outcome, NO_CAPACITY)
        order.refresh_from_db()
        self.assertIsNone(order.paid_at)
        self.assertEqual(Ticket.objects.count(), 0)
        self.general_a.refresh_from_db()
        self.assertEqual(self.general_a.sold, 0)

    def test_07_second_payment_is_duplicate(self):
        order = make_order(self.event_a)
        process_payment(self.acc_a, mp_payment(order))
        first = self.tickets(order)
        record = process_payment(self.acc_a, mp_payment(order, payment_id=9002))
        self.assertEqual(record.outcome, Payment.Outcome.DUPLICATE)
        self.assertEqual(self.tickets(order), first)

    def test_08_isolation_between_venues(self):
        order = make_order(self.event_a)
        self.assertIsNone(process_payment(self.acc_b, mp_payment(order, user_id=USER_B)))
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_09_wrong_collector(self):
        order = make_order(self.event_a)
        self.assertIsNone(process_payment(self.acc_a, mp_payment(order, user_id=USER_B)))
        self.assertEqual(Payment.objects.count(), 0)

    def test_10_bad_external_reference(self):
        order = make_order(self.event_a)
        payment = mp_payment(order)
        self.assertIsNone(process_payment(self.acc_a, {**payment, "external_reference": "no-es-uuid"}))
        self.assertIsNone(process_payment(self.acc_a, {**payment, "external_reference": str(uuid4())}))
        self.assertIsNone(process_payment(self.acc_a, {**payment, "external_reference": None}))
        self.assertEqual(Payment.objects.count(), 0)

    def test_11_refund_keeps_issued(self):
        order = make_order(self.event_a)
        process_payment(self.acc_a, mp_payment(order))
        first = self.tickets(order)
        record = process_payment(self.acc_a, mp_payment(order, status="refunded"))
        self.assertEqual((record.status, record.outcome), ("refunded", ISSUED))
        self.assertEqual(self.tickets(order), first)


class ConcurrencyTests(TransactionTestCase):
    def test_simultaneous_same_payment_issues_once(self):
        venue, event, general, _ = make_venue("a")
        account = make_account(venue, USER_A)
        order = make_order(event)
        payment = mp_payment(order)
        barrier = Barrier(2)
        errors = []

        def run():
            try:
                barrier.wait()
                process_payment(account, payment)
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
        self.get = mock.patch("catalog.mp.get_payment").start()
        mock.patch("catalog.mp_connect.get_access_token", side_effect=token_for).start()
        self.addCleanup(mock.patch.stopall)

    def sign(self, data_id, request_id="req-1", ts="1700000000", secret=SECRET):
        manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
        digest = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        return {"HTTP_X_SIGNATURE": f"ts={ts},v1={digest}", "HTTP_X_REQUEST_ID": request_id}

    def notify(self, user_id=USER_A, payment_id=9001, type_="payment", headers=None, body=None, **extra):
        body = body if body is not None else {
            "action": "payment.updated", "type": type_, "data": {"id": str(payment_id)}, "user_id": user_id, **extra,
        }
        headers = self.sign(payment_id) if headers is None else headers
        return self.client.post(
            f"{WEBHOOK}?data.id={payment_id}&type={type_}", data=json.dumps(body),
            content_type="application/json", **headers,
        )

    def test_12_valid_notification_issues(self):
        order = make_order(self.event_a)
        self.get.return_value = mp_payment(order)
        response = self.notify()
        self.assertEqual((response.status_code, response.json()), (200, {"ok": True}))
        self.assertEqual(self.get.call_args.args, ("tok-a", "9001"))
        self.assertEqual(Payment.objects.get().outcome, ISSUED)
        self.assertEqual(Ticket.objects.filter(order=order).count(), 3)

    def test_13_bad_or_missing_signature(self):
        headers = self.sign(9001)
        headers["HTTP_X_SIGNATURE"] = headers["HTTP_X_SIGNATURE"][:-2] + "00"
        response = self.notify(headers=headers)
        self.assertEqual((response.status_code, response.json()), (401, {"error": "invalid_signature"}))
        self.assertEqual(self.notify(headers={"HTTP_X_REQUEST_ID": "req-1"}).status_code, 401)
        self.assertEqual(self.notify(headers=self.sign(9999)).status_code, 401)
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
        self.get.return_value = mp_payment(order, user_id=USER_A)
        self.assertEqual(self.notify().status_code, 200)
        record = Payment.objects.get()
        self.assertEqual(record.order, order)
        self.assertEqual(Ticket.objects.filter(order=order).count(), 3)
        self.assertEqual({call.args[0] for call in self.get.call_args_list}, {"tok-a", "tok-b"})

    def test_18_deauthorized(self):
        other = make_venue("c")[0]
        acc_c = make_account(other, USER_B)
        response = self.notify(
            user_id=USER_B, type_="mp-connect", headers=self.sign(9001), action="application.deauthorized",
        )
        self.assertEqual(response.status_code, 200)
        for account in (self.acc_b, acc_c):
            account.refresh_from_db()
            self.assertTrue(account.needs_reconnect)
            self.assertLessEqual(account.expires_at, timezone.now())
        self.acc_a.refresh_from_db()
        self.assertFalse(self.acc_a.needs_reconnect)
        self.assertGreater(self.acc_a.expires_at, timezone.now())

    def test_19_other_type_does_nothing(self):
        response = self.notify(type_="merchant_order")
        self.assertEqual(response.status_code, 200)
        self.get.assert_not_called()
        self.assertEqual(Payment.objects.count(), 0)

    def test_20_same_notification_twice(self):
        order = make_order(self.event_a)
        self.get.return_value = mp_payment(order)
        self.assertEqual(self.notify().status_code, 200)
        self.assertEqual(self.notify().status_code, 200)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(Ticket.objects.filter(order=order).count(), 3)

    def test_invalid_body_is_400(self):
        response = self.client.post(
            f"{WEBHOOK}?data.id=9001&type=payment", data="[1]", content_type="application/json", **self.sign(9001),
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
        self.search = mock.patch("catalog.mp.search_payments").start()
        mock.patch("catalog.mp_connect.get_access_token", side_effect=token_for).start()
        self.addCleanup(mock.patch.stopall)

    def with_link(self, order):
        Order.objects.filter(pk=order.pk).update(mp_preference_id="pref-1")
        return order

    def test_21_sync_confirms(self):
        order = self.with_link(make_order(self.event_a))
        self.search.return_value = [mp_payment(order)]
        response = self.client.post(sync_url(order))
        self.assertEqual(response.status_code, 200)
        data = response.json()["order"]
        self.assertEqual(data["status"], "paid")
        self.assertEqual(len(data["tickets"]), 3)
        self.assertEqual(self.search.call_args.args, ("tok-a", str(order.pk)))

    def test_22_sync_mp_failure(self):
        order = self.with_link(make_order(self.event_a))
        self.search.side_effect = MercadoPagoError(500)
        response = self.client.post(sync_url(order))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["order"]["status"], "pending_payment")

    def test_23_sync_without_query(self):
        no_link = make_order(self.event_a)
        paid = self.with_link(make_order(self.event_a))
        process_payment(self.acc_a, mp_payment(paid))
        for order in (no_link, paid):
            self.assertEqual(self.client.post(sync_url(order)).status_code, 200)
        self.search.assert_not_called()

    def test_24_sync_wrong_venue(self):
        order = self.with_link(make_order(self.event_a))
        self.assertEqual(self.client.post(sync_url(order, venue="b")).status_code, 404)
        self.search.assert_not_called()


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
                    order=flagged, mp_payment_id=start + i, mp_user_id=USER_A, status="approved",
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
        Order.objects.filter(pk__in=[ok.pk, empty.pk, broken.pk]).update(mp_preference_id="pref")

        def search(token, reference):
            if reference == str(broken.pk):
                raise MercadoPagoError(500)
            return [mp_payment(ok)] if reference == str(ok.pk) else []

        out = io.StringIO()
        with mock.patch("catalog.mp.search_payments", side_effect=search), mock.patch(
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
        process_payment(self.acc_a, mp_payment(order_a, payment_id=1))
        process_payment(self.acc_b, mp_payment(order_b, user_id=USER_B, payment_id=2))
        user = get_user_model().objects.create_user(username="a-admin", is_staff=True)
        Membership.objects.create(venue=self.venue_a, user=user, role=Membership.Role.ADMIN)
        client = Client()
        client.force_login(user)
        response = client.get(reverse("admin:catalog_payment_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([p.mp_payment_id for p in response.context["cl"].result_list], [1])
        self.assertEqual(client.get(reverse("admin:catalog_payment_add")).status_code, 403)
