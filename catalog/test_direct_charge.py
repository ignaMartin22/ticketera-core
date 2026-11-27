"""Cobro directo: toda la venta entra a una cuenta cargada a mano, sin marketplace_fee."""

import hashlib
import hmac
import io
import json
from datetime import timedelta
from unittest import mock

from cryptography.fernet import Fernet
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from . import crypto, mp, mp_connect
from .models import MercadoPagoAccount, Payment, Ticket, VenueIntegration
from .payment_confirm import process_order
from .test_mp_connect import MpTestCase, tokens
from .test_payment_confirm import mp_order
from .test_payments import FRONT, make_order, make_venue, url

TOKEN = "APP_USR-1234567890-token-propio-secreto"
TEST_USER = {"id": 2468, "nickname": "TESTVENDEDOR", "site_id": "MLA", "tags": ["test_user", "normal"]}
REAL_USER = {"id": 9753, "nickname": "DUENIO", "site_id": "MLA", "tags": ["normal"]}
PROVIDER = VenueIntegration.Provider.MERCADOPAGO


def make_account(venue, direct, days=100, user_id=1001, access="tok", **extra):
    return MercadoPagoAccount.objects.create(
        venue=venue, mp_user_id=user_id, access_token=crypto.encrypt(access),
        refresh_token=crypto.encrypt("ref"), public_key="pk", live_mode=False, direct=direct,
        expires_at=timezone.now() + timedelta(days=days), **extra,
    )


class KeyedCase(TestCase):
    def setUp(self):
        config = override_settings(MP_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode())
        config.enable()
        self.addCleanup(config.disable)


@override_settings(FRONT_BASE_URL=FRONT, PUBLIC_API_URL="https://api.example.com", PAYMENTS_ALLOW_TEST_MODE=True)
class PaymentBodyTests(KeyedCase):
    def setUp(self):
        super().setUp()
        self.venue_a, self.event_a, *_ = make_venue("a", rate="0.10", platform_rate="0")
        self.venue_b, self.event_b, *_ = make_venue("b", rate="0.10", platform_rate="0")
        mock.patch("catalog.mp_connect.is_configured", return_value=True).start()
        mock.patch("catalog.mp_connect.get_access_token", return_value="tok").start()
        self.addCleanup(mock.patch.stopall)

    def pay(self, venue, event):
        order = make_order(event, {"general": 1})
        order.items.update(unit_price="7000", unit_service_fee="700", unit_platform_fee="0")
        body = {}

        def fake(token, payload, key):
            body.update(payload)
            return {"id": "ORD1", "checkout_url": "https://mp/x", "total_amount": payload["total_amount"]}

        with mock.patch("catalog.mp.create_order", side_effect=fake):
            response = self.client.post(url(order, venue.slug))
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        return body, order

    def test_01_direct_account_sends_no_marketplace_fee(self):
        make_account(self.venue_a, direct=True)
        body, order = self.pay(self.venue_a, self.event_a)
        self.assertNotIn("marketplace_fee", body)
        self.assertEqual(body["total_amount"], "7700.00")
        self.assertEqual(order.platform_fee, 700)

    def test_02_oauth_account_still_sends_marketplace_fee(self):
        make_account(self.venue_b, direct=False)
        body, order = self.pay(self.venue_b, self.event_b)
        self.assertEqual(body["marketplace_fee"], "700.00")
        self.assertEqual(body["total_amount"], "7700.00")
        self.assertEqual(order.platform_fee, 700)


class TokenTests(MpTestCase):
    def test_03_direct_token_ignores_expiry_and_reconnect(self):
        make_account(self.venue_a, direct=True, days=-5, access=TOKEN, needs_reconnect=True)
        with mock.patch("catalog.mp.refresh_tokens") as refresh:
            self.assertEqual(mp_connect.get_access_token(self.venue_a), TOKEN)
        refresh.assert_not_called()

    def test_04_refresh_command_skips_direct_accounts(self):
        direct = make_account(self.venue_a, direct=True, days=5, access="d1")
        oauth = make_account(self.venue_b, direct=False, days=5, access="o1")
        out = io.StringIO()
        with mock.patch("catalog.mp.refresh_tokens", return_value=tokens(access="nuevo")) as refresh:
            call_command("refresh_mp_tokens", stdout=out)
        self.assertEqual(refresh.call_count, 1)
        self.assertIn("Renovadas: 1. Fallidas: 0.", out.getvalue())
        direct.refresh_from_db()
        oauth.refresh_from_db()
        self.assertEqual(crypto.decrypt(direct.access_token), "d1")
        self.assertFalse(direct.needs_reconnect)
        self.assertEqual(crypto.decrypt(oauth.access_token), "nuevo")

    def test_04b_renew_account_skips_direct(self):
        direct = make_account(self.venue_a, direct=True, days=5)
        with mock.patch("catalog.mp.refresh_tokens") as refresh:
            _, ok = mp_connect.renew_account(direct.pk)
        refresh.assert_not_called()
        self.assertTrue(ok)

    def test_05_oauth_connect_replaces_direct_account(self):
        make_account(self.venue_a, direct=True, access="viejo")
        response, _ = self.run_callback(self.get_state(), mock.Mock(return_value=tokens(access="nuevo-oauth")))
        self.assertEqual(response.status_code, 302)
        account = MercadoPagoAccount.objects.get(venue=self.venue_a)
        self.assertFalse(account.direct)
        self.assertEqual(crypto.decrypt(account.access_token), "nuevo-oauth")
        self.assertEqual(account.mp_user_id, 555)


SECRET = "secreto-del-webhook"


@override_settings(MP_WEBHOOK_SECRET=SECRET)
class DeauthorizedTests(KeyedCase):
    def test_06_deauthorized_does_not_touch_direct_account(self):
        venue_a = make_venue("a")[0]
        venue_b = make_venue("b")[0]
        direct = make_account(venue_a, direct=True, user_id=777)
        oauth = make_account(venue_b, direct=False, user_id=777)
        for venue in (venue_a, venue_b):
            VenueIntegration.objects.create(
                venue=venue, provider=PROVIDER, status=VenueIntegration.Status.CONNECTED, account_id="777",
            )
        order_id = "ORDTST01"
        manifest = f"id:{order_id.lower()};request-id:req-1;ts:1700000000;"
        digest = hmac.new(SECRET.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        body = {"action": "application.deauthorized", "type": "mp-connect", "data": {"id": order_id}, "user_id": 777}
        response = self.client.post(
            f"/api/v1/mercadopago/webhook/?data.id={order_id}&type=mp-connect", data=json.dumps(body),
            content_type="application/json", HTTP_X_SIGNATURE=f"ts=1700000000,v1={digest}", HTTP_X_REQUEST_ID="req-1",
        )
        self.assertEqual(response.status_code, 200)
        direct.refresh_from_db()
        oauth.refresh_from_db()
        self.assertFalse(direct.needs_reconnect)
        self.assertGreater(direct.expires_at, timezone.now())
        self.assertTrue(oauth.needs_reconnect)
        self.assertLessEqual(oauth.expires_at, timezone.now())
        self.assertEqual(VenueIntegration.objects.get(venue=venue_a, provider=PROVIDER).status, "connected")
        self.assertEqual(VenueIntegration.objects.get(venue=venue_b, provider=PROVIDER).status, "expired")


@override_settings(PAYMENTS_ALLOW_TEST_MODE=True)
class SetOwnAccountTests(KeyedCase):
    def setUp(self):
        super().setUp()
        self.venue_a = make_venue("a")[0]
        self.venue_b = make_venue("b")[0]

    def run_command(self, slug="a", token=TOKEN, user=TEST_USER, get_user=None, args=()):
        out = io.StringIO()
        fake = get_user or mock.Mock(return_value=user)
        try:
            with mock.patch("catalog.mp.get_user", fake), mock.patch("sys.stdin", io.StringIO(token)):
                call_command("mp_set_own_account", slug, *args, stdout=out, stderr=out)
        except CommandError as exc:
            self.assertNotIn(TOKEN, str(exc))
            self.assertNotIn(TOKEN, out.getvalue())
            raise
        self.assertNotIn(TOKEN, out.getvalue())
        return out.getvalue()

    def test_07_test_account_without_flag(self):
        with override_settings(MP_TEST_TOKENS=False):
            out = self.run_command()
        account = MercadoPagoAccount.objects.get(venue=self.venue_a)
        self.assertTrue(account.direct)
        self.assertFalse(account.live_mode)
        self.assertFalse(account.needs_reconnect)
        self.assertEqual(account.mp_user_id, 2468)
        self.assertGreater(account.expires_at, timezone.now() + timedelta(days=3000))
        self.assertEqual(crypto.decrypt(account.access_token), TOKEN)
        with connection.cursor() as cursor:
            cursor.execute("SELECT access_token, refresh_token FROM catalog_mercadopagoaccount")
            for value in cursor.fetchone():
                self.assertNotIn(TOKEN, value)
        integration = VenueIntegration.objects.get(venue=self.venue_a, provider=PROVIDER)
        self.assertEqual(integration.status, "connected")
        self.assertEqual(integration.account_id, "2468")
        self.assertEqual(integration.account_label, "TESTVENDEDOR (cobro directo, cuenta de prueba)")
        self.assertIn("2468", out)

    def test_08_real_account_needs_produccion(self):
        with self.assertRaises(CommandError) as ctx:
            self.run_command(user=REAL_USER)
        self.assertIn("Esa es una cuenta real de Mercado Pago. Para cargarla usá --produccion.", str(ctx.exception))
        self.assertFalse(MercadoPagoAccount.objects.exists())
        self.assertFalse(VenueIntegration.objects.exists())
        self.run_command(user=REAL_USER, args=("--produccion",))
        account = MercadoPagoAccount.objects.get(venue=self.venue_a)
        self.assertTrue(account.direct)
        self.assertTrue(account.live_mode)
        integration = VenueIntegration.objects.get(venue=self.venue_a, provider=PROVIDER)
        self.assertEqual(integration.account_label, "DUENIO (cobro directo)")

    def test_09_test_account_with_produccion_fails(self):
        with self.assertRaises(CommandError) as ctx:
            self.run_command(args=("--produccion",))
        self.assertIn("Esa cuenta es de prueba; sacá --produccion.", str(ctx.exception))
        self.assertFalse(MercadoPagoAccount.objects.exists())

    def test_10_works_without_mp_test_tokens(self):
        with override_settings(MP_TEST_TOKENS=False):
            self.run_command()
        self.assertTrue(MercadoPagoAccount.objects.filter(venue=self.venue_a, direct=True).exists())

    def test_11_errors_never_leak_the_token(self):
        with override_settings(MP_TOKEN_ENCRYPTION_KEY=""):
            with self.assertRaises(CommandError):
                self.run_command()
        with self.assertRaises(CommandError):
            self.run_command(token="\n")
        with self.assertRaises(CommandError):
            self.run_command(slug="no-existe")
        failing = mock.Mock(side_effect=mp.MercadoPagoError(401, {"message": f"bad {TOKEN}"}))
        with self.assertRaises(CommandError) as ctx:
            self.run_command(get_user=failing)
        self.assertIn("Mercado Pago rechazó el token", str(ctx.exception))
        self.assertFalse(MercadoPagoAccount.objects.exists())

    def test_12_isolation_and_idempotence(self):
        self.run_command()
        self.run_command()
        self.assertEqual(MercadoPagoAccount.objects.count(), 1)
        self.assertFalse(MercadoPagoAccount.objects.filter(venue=self.venue_b).exists())
        self.assertFalse(VenueIntegration.objects.filter(venue=self.venue_b).exists())

    def test_13_confirmation_works_for_direct_account(self):
        venue, event, *_ = make_venue("c")
        self.run_command(slug="c")
        account = MercadoPagoAccount.objects.get(venue=venue)
        order = make_order(event)
        record = process_order(account, mp_order(order, user_id=account.mp_user_id))
        self.assertEqual(record.outcome, Payment.Outcome.ISSUED)
        self.assertEqual(Ticket.objects.filter(order=order).count(), 3)
