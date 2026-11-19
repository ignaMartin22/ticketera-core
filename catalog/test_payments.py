import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock
from uuid import uuid4

from django.test import TestCase, override_settings
from django.utils import timezone

from . import mp_connect
from .models import Event, Order, TicketType, Venue
from .mp import MercadoPagoError
from .orders import reserve_order
from .payments import expiration_time, idempotency_key

REAL_GET_ACCESS_TOKEN = mp_connect.get_access_token
FRONT = "https://front.example.com"
API = "https://api.example.com"
INIT_POINT = "https://www.mercadopago.com.ar/checkout/v1/redirect?order_id=ORDTST01&pref_id=pref-1"
MP_ORDER_ID = "ORDTST01M7TQ2F8WJ3P07MSVAJQJBRNP"
TOKEN_A = "APP_USR-token-del-boliche-a"
BUYER = {"email": "comprador@example.com", "name": "Nombre Secreto Comprador", "phone": "+5493884123456"}


def make_venue(slug, fee_mode=Venue.FeeMode.BUYER, rate="0.10", platform_rate="0.05"):
    venue = Venue.objects.create(
        name=slug, slug=slug, is_active=True, fee_mode=fee_mode, buyer_fee_rate=rate,
        platform_fee_rate=platform_rate,
    )
    event = Event.objects.create(
        venue=venue, title=f"Fiesta {slug}", slug="fecha", starts_at=timezone.now(), is_published=True,
    )
    general = TicketType.objects.create(event=event, slug="general", name="General", price="10000", capacity=50)
    vip = TicketType.objects.create(event=event, slug="vip", name="VIP", price="18000", capacity=50)
    return venue, event, general, vip


def make_order(event, quantities=None):
    order, _items, _created = reserve_order(
        event.venue.slug, event.slug, BUYER, quantities or {"general": 2, "vip": 1}, uuid4(),
    )
    return order


def url(order, venue="a", event="fecha"):
    return f"/api/v1/venues/{venue}/events/{event}/orders/{order.pk}/payment/"


@override_settings(FRONT_BASE_URL=FRONT, PUBLIC_API_URL=API)
class PaymentLinkTests(TestCase):
    def setUp(self):
        self.venue_a, self.event_a, self.general_a, self.vip_a = make_venue("a")
        self.venue_b, self.event_b, *_ = make_venue("b")
        self.token = mock.patch("catalog.mp_connect.get_access_token", return_value=TOKEN_A).start()
        mock.patch("catalog.mp_connect.is_configured", return_value=True).start()
        self.create = mock.patch("catalog.mp.create_order", side_effect=self.fake_create).start()
        self.addCleanup(mock.patch.stopall)

    @staticmethod
    def fake_create(access_token, body, key):
        return {"id": MP_ORDER_ID, "checkout_url": INIT_POINT, "total_amount": body["total_amount"], "status": "created"}

    def pay(self, order, **kwargs):
        return self.client.post(url(order, **kwargs))

    def body(self):
        return self.create.call_args.args[1]

    def key(self):
        return self.create.call_args.args[2]

    def test_01_happy_path_uses_the_venue_token(self):
        order = make_order(self.event_a)
        response = self.pay(order)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"initPoint": INIT_POINT})
        self.assertEqual(self.token.call_args.args[0], self.venue_a)
        self.assertEqual(self.create.call_args.args[0], TOKEN_A)

    def test_02_body_items_total_and_references(self):
        order = make_order(self.event_a)
        self.pay(order)
        body = self.body()
        self.assertEqual(len(body["items"]), 2)
        by_title = {item["title"]: item for item in body["items"]}
        general = by_title["General · Fiesta a"]
        vip = by_title["VIP · Fiesta a"]
        self.assertEqual((general["quantity"], general["unit_price"]), (2, "11000.00"))
        self.assertEqual((vip["quantity"], vip["unit_price"]), (1, "19800.00"))
        total = sum(Decimal(str(i["quantity"])) * Decimal(i["unit_price"]) for i in body["items"])
        self.assertEqual(total, Decimal(2 * 11000 + 19800))
        self.assertEqual(Decimal(body["total_amount"]), total)
        self.assertEqual(body["external_reference"], str(order.pk))
        self.assertEqual(body["payer"], {"email": "comprador@example.com"})

    def test_03_body_has_no_buyer_name_or_phone(self):
        self.pay(make_order(self.event_a))
        text = json.dumps(self.body())
        self.assertNotIn("Nombre Secreto", text)
        self.assertNotIn("5493884123456", text)

    def test_04_marketplace_fee_buyer_mode(self):
        # Cargo de servicio 3800 (10 %) + comisión de la plataforma 1900 (5 %).
        self.pay(make_order(self.event_a))
        self.assertEqual(self.body()["marketplace_fee"], "5700.00")

    def test_05_marketplace_fee_venue_mode(self):
        # Sin cargo de servicio queda solo la comisión de la plataforma, y el comprador paga el precio.
        _venue, event, *_ = make_venue("c", fee_mode=Venue.FeeMode.VENUE)
        self.pay(make_order(event), venue="c")
        body = self.body()
        self.assertEqual(body["marketplace_fee"], "1900.00")
        self.assertEqual(body["total_amount"], "38000.00")
        self.assertEqual(sorted(i["unit_price"] for i in body["items"]), ["10000.00", "18000.00"])

    def test_06_zero_rates_have_no_marketplace_fee(self):
        _venue, event, *_ = make_venue("c", rate="0", platform_rate="0")
        self.pay(make_order(event), venue="c")
        self.assertNotIn("marketplace_fee", self.body())

    def test_06b_only_platform_rate_sets_the_fee(self):
        _venue, event, *_ = make_venue("c", rate="0")
        self.pay(make_order(event), venue="c")
        self.assertEqual(self.body()["marketplace_fee"], "1900.00")

    def test_06c_rate_change_after_reserving_keeps_the_fee(self):
        order = make_order(self.event_a)
        Venue.objects.filter(pk=self.venue_a.pk).update(buyer_fee_rate="0.30", platform_fee_rate="0.20")
        self.pay(order)
        self.assertEqual(self.body()["marketplace_fee"], "5700.00")
        self.assertEqual(self.body()["total_amount"], "41800.00")
        order.refresh_from_db()
        self.assertEqual(order.platform_fee, Decimal("5700"))

    def test_07_uses_stored_prices(self):
        order = make_order(self.event_a)
        TicketType.objects.filter(pk=self.general_a.pk).update(price="99999")
        self.pay(order)
        body = self.body()
        self.assertEqual(body["marketplace_fee"], "5700.00")
        self.assertEqual(sorted(i["unit_price"] for i in body["items"]), ["11000.00", "19800.00"])

    def test_08_urls(self):
        order = make_order(self.event_a)
        self.pay(order)
        online = self.body()["config"]["online"]
        expected = f"{FRONT}/a/fecha/pedido/{order.pk}"
        self.assertEqual(online["success_url"], expected)
        self.assertEqual(online["failure_url"], expected)
        self.assertEqual(online["pending_url"], expected)
        self.assertEqual(online["auto_return"], "approved")
        self.assertNotIn("notification_url", self.body())

    def test_09_expiration_and_payment_methods(self):
        order = make_order(self.event_a)
        self.pay(order)
        body = self.body()
        self.assertRegex(body["expiration_time"], r"^PT\d+M$")
        self.assertEqual(body["expiration_time"], expiration_time(order, now=timezone.now()))
        self.assertEqual(body["config"]["payment_method"]["not_allowed_types"], ["ticket", "atm"])
        self.assertNotIn("binary_mode", body)

    def test_09b_body_type_modes_and_text_amounts(self):  # (a)
        self.pay(make_order(self.event_a))
        body = self.body()
        self.assertEqual(
            (body["type"], body["processing_mode"], body["capture_mode"]), ("online", "manual", "automatic"),
        )
        for value in (body["total_amount"], body["marketplace_fee"], *(i["unit_price"] for i in body["items"])):
            self.assertIsInstance(value, str)
            self.assertRegex(value, r"^\d+\.\d{2}$")
        self.assertEqual(body["total_amount"], "41800.00")

    def test_09c_expiration_time_rounds_up(self):  # (b)
        order = make_order(self.event_a)
        now = timezone.now()
        order.expires_at = now + timedelta(minutes=6, seconds=10)
        self.assertEqual(expiration_time(order, now), "PT7M")
        order.expires_at = now + timedelta(seconds=20)
        self.assertEqual(expiration_time(order, now), "PT1M")
        order.expires_at = now + timedelta(minutes=5)
        self.assertEqual(expiration_time(order, now), "PT5M")

    def test_09d_idempotency_key_stable_per_order(self):  # (c)
        order = make_order(self.event_a)
        other = make_order(self.event_a)
        self.create.side_effect = [MercadoPagoError(500, {}), self.fake_create("t", {"total_amount": "41800.00"}, "k")]
        self.assertEqual(self.pay(order).status_code, 502)
        first = self.key()
        self.assertEqual(self.pay(order).status_code, 200)
        self.assertEqual(self.key(), first)
        self.create.side_effect = self.fake_create
        self.pay(other)
        self.assertNotEqual(self.key(), first)
        self.assertEqual(first, idempotency_key(order))
        self.assertLessEqual(len(first), 64)

    def test_10_saves_order_data(self):
        order = make_order(self.event_a)
        self.pay(order)
        order.refresh_from_db()
        self.assertEqual(order.platform_fee, Decimal("5700"))
        self.assertEqual(order.mp_order_id, MP_ORDER_ID)
        self.assertEqual(order.mp_init_point, INIT_POINT)

    def test_11_idempotent(self):
        order = make_order(self.event_a)
        first = self.pay(order)
        second = self.pay(order)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(self.create.call_count, 1)

    def test_12_paid_orders_conflict(self):
        order = make_order(self.event_a)
        Order.objects.filter(pk=order.pk).update(paid_at=timezone.now())
        response = self.pay(order)
        self.assertEqual((response.status_code, response.json()), (409, {"error": "order_already_paid"}))
        TicketType.objects.filter(pk__in=[self.general_a.pk, self.vip_a.pk]).update(price="0")
        free = make_order(self.event_a)
        self.assertIsNotNone(free.paid_at)
        response = self.pay(free)
        self.assertEqual((response.status_code, response.json()), (409, {"error": "order_already_paid"}))
        self.create.assert_not_called()

    def test_13_expired_order(self):
        order = make_order(self.event_a)
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        response = self.pay(order)
        self.assertEqual((response.status_code, response.json()), (409, {"error": "order_expired"}))
        self.create.assert_not_called()

    def test_14_venue_without_connected_account(self):
        order = make_order(self.event_a)
        self.token.side_effect = REAL_GET_ACCESS_TOKEN  # el boliche A no tiene MercadoPagoAccount
        response = self.pay(order)
        self.assertEqual((response.status_code, response.json()), (409, {"error": "venue_not_connected"}))
        self.create.assert_not_called()

    def test_15_mp_failure(self):
        order = make_order(self.event_a)
        self.create.side_effect = MercadoPagoError(500, {})
        response = self.pay(order)
        self.assertEqual((response.status_code, response.json()), (502, {"error": "mp_unavailable"}))
        order.refresh_from_db()
        self.assertEqual((order.mp_order_id, order.mp_init_point, order.platform_fee), ("", "", None))

    def test_15b_malformed_response_is_unavailable(self):
        order = make_order(self.event_a)
        self.create.side_effect = None
        self.create.return_value = {"id": "x"}
        self.assertEqual(self.pay(order).status_code, 502)
        order.refresh_from_db()
        self.assertEqual(order.mp_order_id, "")

    def test_15c_missing_checkout_url_or_wrong_total(self):  # (d)
        order = make_order(self.event_a)
        good = self.fake_create("t", {"total_amount": "41800.00"}, "k")
        for bad in (
            {k: v for k, v in good.items() if k != "checkout_url"},
            {k: v for k, v in good.items() if k != "id"},
            {**good, "total_amount": "100.00"},
        ):
            with self.subTest(bad=bad):
                self.create.side_effect = None
                self.create.return_value = bad
                self.assertEqual(self.pay(order).status_code, 502)
                order.refresh_from_db()
                self.assertEqual((order.mp_order_id, order.mp_init_point, order.platform_fee), ("", "", None))

    def test_16_isolation(self):
        order = make_order(self.event_a)
        response = self.pay(order, venue="b")
        self.assertEqual((response.status_code, response.json()), (404, {"error": "not_found"}))
        Event.objects.create(
            venue=self.venue_a, title="Otra", slug="otra", starts_at=timezone.now(), is_published=True,
        )
        self.assertEqual(self.pay(order, event="otra").status_code, 404)
        self.assertEqual(self.client.post(f"/api/v1/venues/a/events/fecha/orders/{uuid4()}/payment/").status_code, 404)
        self.create.assert_not_called()

    def test_17_missing_configuration(self):
        order = make_order(self.event_a)
        with override_settings(FRONT_BASE_URL=""):
            response = self.pay(order)
            self.assertEqual((response.status_code, response.json()), (503, {"error": "mp_not_configured"}))
        with mock.patch("catalog.mp_connect.is_configured", return_value=False):
            self.assertEqual(self.pay(order).status_code, 503)
        self.create.assert_not_called()

    @override_settings(PUBLIC_API_URL="")
    def test_17b_works_without_public_api_url(self):  # (e)
        response = self.pay(make_order(self.event_a))
        self.assertEqual((response.status_code, response.json()), (200, {"initPoint": INIT_POINT}))
        self.assertNotIn("notification_url", self.body())

    def test_18_get_not_allowed(self):
        order = make_order(self.event_a)
        self.assertEqual(self.client.get(url(order)).status_code, 405)


class MpClientTests(TestCase):
    """El SDK real contra una sesión HTTP simulada: URL, token y X-Idempotency-Key."""

    def fake_response(self, status=201):
        response = mock.Mock(status_code=status, content=b"{}")
        response.json.return_value = {"id": MP_ORDER_ID}
        return response

    def test_create_order_sends_given_idempotency_key(self):
        from . import mp
        with mock.patch("requests.Session.request", return_value=self.fake_response()) as request:
            result = mp.create_order("tok", {"type": "online"}, "clave-fija")
        self.assertEqual(result, {"id": MP_ORDER_ID})
        method, url = request.call_args.args[:2]
        self.assertEqual((method, url), ("POST", "https://api.mercadopago.com/v1/orders"))
        headers = request.call_args.kwargs["headers"]
        self.assertEqual(headers["x-idempotency-key"], "clave-fija")
        self.assertEqual(headers["Authorization"], "Bearer tok")

    def test_get_order_url_and_errors(self):
        from . import mp
        with mock.patch("requests.Session.request", return_value=self.fake_response(200)) as request:
            mp.get_order("tok", MP_ORDER_ID)
        self.assertEqual(request.call_args.args[:2], ("GET", f"https://api.mercadopago.com/v1/orders/{MP_ORDER_ID}"))
        with mock.patch("requests.Session.request", return_value=self.fake_response(404)):
            with self.assertRaises(MercadoPagoError):
                mp.get_order("tok", MP_ORDER_ID)
