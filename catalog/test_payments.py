import json
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock
from uuid import uuid4

from django.test import TestCase, override_settings
from django.utils import timezone

from . import mp_connect
from .models import Event, Order, TicketType, Venue
from .mp import MercadoPagoError
from .orders import reserve_order

REAL_GET_ACCESS_TOKEN = mp_connect.get_access_token
FRONT = "https://front.example.com"
API = "https://api.example.com"
INIT_POINT = "https://www.mercadopago.com.ar/checkout/v1/redirect?pref_id=pref-1"
TOKEN_A = "APP_USR-token-del-boliche-a"
BUYER = {"email": "comprador@example.com", "name": "Nombre Secreto Comprador", "phone": "+5493884123456"}


def make_venue(slug, fee_mode=Venue.FeeMode.BUYER, rate="0.10"):
    venue = Venue.objects.create(
        name=slug, slug=slug, is_active=True, fee_mode=fee_mode, buyer_fee_rate=rate,
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
        self.create = mock.patch(
            "catalog.mp.create_preference", return_value={"id": "pref-1", "init_point": INIT_POINT},
        ).start()
        self.addCleanup(mock.patch.stopall)

    def pay(self, order, **kwargs):
        return self.client.post(url(order, **kwargs))

    def body(self):
        return self.create.call_args.args[1]

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
        self.assertEqual((general["quantity"], general["unit_price"]), (2, 11000))
        self.assertEqual((vip["quantity"], vip["unit_price"]), (1, 19800))
        self.assertTrue(all(item["currency_id"] == "ARS" for item in body["items"]))
        total = sum(Decimal(str(i["quantity"])) * Decimal(str(i["unit_price"])) for i in body["items"])
        self.assertEqual(total, Decimal(2 * 11000 + 19800))
        self.assertEqual(body["external_reference"], str(order.pk))
        self.assertEqual(body["payer"], {"email": "comprador@example.com"})

    def test_03_body_has_no_buyer_name_or_phone(self):
        self.pay(make_order(self.event_a))
        text = json.dumps(self.body())
        self.assertNotIn("Nombre Secreto", text)
        self.assertNotIn("5493884123456", text)

    def test_04_marketplace_fee_buyer_mode(self):
        self.pay(make_order(self.event_a))
        self.assertEqual(self.body()["marketplace_fee"], 3800)

    def test_05_marketplace_fee_venue_mode(self):
        _venue, event, *_ = make_venue("c", fee_mode=Venue.FeeMode.VENUE)
        self.pay(make_order(event), venue="c")
        body = self.body()
        self.assertEqual(body["marketplace_fee"], 3800)
        self.assertEqual(sorted(i["unit_price"] for i in body["items"]), [10000, 18000])

    def test_06_zero_rate_has_no_marketplace_fee(self):
        _venue, event, *_ = make_venue("c", rate="0")
        self.pay(make_order(event), venue="c")
        self.assertNotIn("marketplace_fee", self.body())

    def test_07_uses_stored_prices(self):
        order = make_order(self.event_a)
        TicketType.objects.filter(pk=self.general_a.pk).update(price="99999")
        self.pay(order)
        body = self.body()
        self.assertEqual(body["marketplace_fee"], 3800)
        self.assertEqual(sorted(i["unit_price"] for i in body["items"]), [11000, 19800])

    def test_08_urls(self):
        order = make_order(self.event_a)
        self.pay(order)
        body = self.body()
        expected = f"{FRONT}/a/fecha/pedido/{order.pk}"
        self.assertEqual(body["back_urls"], {"success": expected, "failure": expected, "pending": expected})
        self.assertEqual(body["auto_return"], "approved")
        self.assertEqual(body["notification_url"], f"{API}/api/v1/mercadopago/webhook/")

    def test_09_expiration_binary_and_payment_methods(self):
        order = make_order(self.event_a)
        self.pay(order)
        body = self.body()
        self.assertIs(body["expires"], True)
        expires = datetime.fromisoformat(body["expiration_date_to"])
        self.assertIsNotNone(expires.tzinfo)
        self.assertEqual(expires, order.expires_at)
        self.assertIs(body["binary_mode"], True)
        excluded = {t["id"] for t in body["payment_methods"]["excluded_payment_types"]}
        self.assertEqual(excluded, {"ticket", "atm"})

    def test_10_saves_preference_data(self):
        order = make_order(self.event_a)
        self.pay(order)
        order.refresh_from_db()
        self.assertEqual(order.platform_fee, Decimal("3800"))
        self.assertEqual(order.mp_preference_id, "pref-1")
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
        self.assertEqual((order.mp_preference_id, order.mp_init_point, order.platform_fee), ("", "", None))

    def test_15b_malformed_response_is_unavailable(self):
        order = make_order(self.event_a)
        self.create.return_value = {"id": "x"}
        self.assertEqual(self.pay(order).status_code, 502)
        order.refresh_from_db()
        self.assertEqual(order.mp_preference_id, "")

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
        for setting in ("FRONT_BASE_URL", "PUBLIC_API_URL"):
            with self.subTest(setting), override_settings(**{setting: ""}):
                response = self.pay(order)
                self.assertEqual((response.status_code, response.json()), (503, {"error": "mp_not_configured"}))
        with mock.patch("catalog.mp_connect.is_configured", return_value=False):
            self.assertEqual(self.pay(order).status_code, 503)
        self.create.assert_not_called()

    def test_18_get_not_allowed(self):
        order = make_order(self.event_a)
        self.assertEqual(self.client.get(url(order)).status_code, 405)
