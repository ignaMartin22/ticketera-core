import io
import time
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from . import crypto, mp
from .models import MercadoPagoAccount, Membership, Venue
from .mp_connect import MercadoPagoNotConnected, get_access_token
from .test_door import DoorTestCase, make_user

ADMIN, DOOR = Membership.Role.ADMIN, Membership.Role.DOOR
RETURN_URL = "https://app.example.com/panel"
CONNECT = "/api/v1/manage/venues/{}/mercadopago/connect/"
STATUS = "/api/v1/manage/venues/{}/mercadopago/"
CALLBACK = "/api/v1/mercadopago/oauth/callback/"
EXPIRES_IN = 15552000


def tokens(access="APP_USR-acceso-nuevo", refresh="TG-refresco-nuevo", user_id=555, **extra):
    return {
        "access_token": access, "refresh_token": refresh, "public_key": "APP_USR-pk-secreta",
        "user_id": user_id, "live_mode": True, "expires_in": EXPIRES_IN, "scope": "offline_access", **extra,
    }


class MpTestCase(DoorTestCase):
    def setUp(self):
        super().setUp()
        self.config = override_settings(
            MP_APP_ID="123", MP_CLIENT_SECRET="secreto", MP_REDIRECT_URI="https://api.example.com/cb",
            MP_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(), MP_CONNECT_RETURN_URL=RETURN_URL,
        )
        self.config.enable()
        self.addCleanup(self.config.disable)
        self.admin_a = make_user("jefe_a", self.venue_a, ADMIN)
        self.admin_token = self.token("jefe_a")

    def auth(self, token):
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def connect(self, venue="a", token=None, state_out=None):
        with mock.patch("catalog.mp.authorization_url", return_value="https://auth.mp/x") as url:
            response = self.client.post(CONNECT.format(venue), **self.auth(token or self.admin_token))
        if state_out is not None and url.call_args:
            state_out.append(url.call_args.args[0])
        return response

    def get_state(self, venue="a", token=None):
        out = []
        self.assertEqual(self.connect(venue, token, out).status_code, 200)
        return out[0]

    def run_callback(self, state, exchange=None, **params):
        exchange = exchange or mock.Mock(return_value=tokens())
        with mock.patch("catalog.mp.exchange_code", exchange):
            return self.client.get(CALLBACK, {"state": state, "code": "abc", **params}), exchange

    def assert_redirect(self, response, **expected):
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(RETURN_URL + "?"))
        query = {k: v[0] for k, v in parse_qs(urlparse(response["Location"]).query).items()}
        self.assertEqual(query, expected)

    def make_account(self, venue, days_left, access="acc-viejo", refresh="ref-viejo", **extra):
        return MercadoPagoAccount.objects.create(
            venue=venue, mp_user_id=1, access_token=crypto.encrypt(access),
            refresh_token=crypto.encrypt(refresh), public_key="pk", live_mode=True,
            expires_at=timezone.now() + timedelta(days=days_left), **extra,
        )


class ConnectTests(MpTestCase):
    def test_01_admin_gets_authorization_url(self):  # 1
        out = []
        response = self.connect(state_out=out)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"authorizationUrl": "https://auth.mp/x"})
        self.assertTrue(out[0])

    def test_02_permissions(self):  # 2
        make_user("portero_a", self.venue_a, DOOR)
        self.assertEqual(self.connect(token=self.token("portero_a")).status_code, 404)
        self.assertEqual(self.connect(venue="b").status_code, 404)
        self.assertEqual(self.client.post(CONNECT.format("a")).status_code, 401)

    def test_19_not_configured(self):  # 19
        with override_settings(MP_APP_ID=""):
            response = self.connect()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "mp_not_configured"})


class CallbackTests(MpTestCase):
    def test_03_happy_path(self):  # 3
        state = self.get_state()
        before = timezone.now()
        response, exchange = self.run_callback(state)
        self.assert_redirect(response, mp="connected", venue="a")
        exchange.assert_called_once_with("abc")
        account = MercadoPagoAccount.objects.get(venue=self.venue_a)
        self.assertEqual(account.mp_user_id, 555)
        self.assertTrue(account.live_mode)
        self.assertFalse(account.needs_reconnect)
        delta = account.expires_at - (before + timedelta(seconds=EXPIRES_IN))
        self.assertLess(abs(delta.total_seconds()), 60)

    def test_04_tokens_stored_encrypted(self):  # 4
        self.run_callback(self.get_state())
        raw = MercadoPagoAccount.objects.filter(venue=self.venue_a).values("access_token", "refresh_token")[0]
        self.assertNotIn("APP_USR-acceso-nuevo", raw["access_token"])
        self.assertNotIn("TG-refresco-nuevo", raw["refresh_token"])
        self.assertEqual(crypto.decrypt(raw["access_token"]), "APP_USR-acceso-nuevo")
        self.assertEqual(crypto.decrypt(raw["refresh_token"]), "TG-refresco-nuevo")

    def test_05_tampered_state(self):  # 5
        state = self.get_state()
        tampered = state[:-2] + ("AA" if state[-2:] != "AA" else "BB")
        response, exchange = self.run_callback(tampered)
        self.assert_redirect(response, mp="error", reason="invalid_state")
        exchange.assert_not_called()
        self.assertFalse(MercadoPagoAccount.objects.exists())

    def test_06_expired_state(self):  # 6
        state = self.get_state()
        with mock.patch("django.core.signing.time.time", return_value=time.time() + 11 * 60):
            response, exchange = self.run_callback(state)
        self.assert_redirect(response, mp="error", reason="invalid_state")
        exchange.assert_not_called()
        self.assertFalse(MercadoPagoAccount.objects.exists())

    def test_07_role_removed_after_link(self):  # 7
        state = self.get_state()
        Membership.objects.filter(user=self.admin_a).delete()
        response, exchange = self.run_callback(state)
        self.assert_redirect(response, mp="error", reason="invalid_state")
        exchange.assert_not_called()
        self.assertFalse(MercadoPagoAccount.objects.exists())

    def test_08_denied(self):  # 8
        response = self.client.get(CALLBACK, {"error": "access_denied", "state": self.get_state()})
        self.assert_redirect(response, mp="error", reason="denied")
        self.assertFalse(MercadoPagoAccount.objects.exists())

    def test_09_exchange_failed(self):  # 9
        exchange = mock.Mock(side_effect=mp.MercadoPagoError(400, {"message": "invalid_grant"}))
        response, _ = self.run_callback(self.get_state(), exchange)
        self.assert_redirect(response, mp="error", reason="exchange_failed")
        self.assertFalse(MercadoPagoAccount.objects.exists())

    def test_callback_not_configured(self):
        state = self.get_state()
        with override_settings(MP_CLIENT_SECRET=""):
            response, exchange = self.run_callback(state)
        self.assert_redirect(response, mp="error", reason="not_configured")
        exchange.assert_not_called()

    def test_10_reconnect_replaces_account(self):  # 10
        self.make_account(self.venue_a, 5, needs_reconnect=True)
        self.run_callback(self.get_state(), mock.Mock(return_value=tokens(user_id=999)))
        self.assertEqual(MercadoPagoAccount.objects.filter(venue=self.venue_a).count(), 1)
        account = MercadoPagoAccount.objects.get(venue=self.venue_a)
        self.assertEqual(account.mp_user_id, 999)
        self.assertFalse(account.needs_reconnect)
        self.assertEqual(crypto.decrypt(account.access_token), "APP_USR-acceso-nuevo")

    def test_11_isolation(self):  # 11
        self.run_callback(self.get_state())
        self.assertFalse(MercadoPagoAccount.objects.filter(venue=self.venue_b).exists())
        make_user("jefe_b", self.venue_b, ADMIN)
        body = self.client.get(STATUS.format("b"), **self.auth(self.token("jefe_b"))).json()
        self.assertFalse(body["connected"])


class StatusTests(MpTestCase):
    def test_12_status(self):  # 12
        self.run_callback(self.get_state())
        response = self.client.get(STATUS.format("a"), **self.auth(self.admin_token))
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(body), {"connected", "needsReconnect", "mpUserId", "liveMode", "connectedAt", "expiresAt"})
        self.assertTrue(body["connected"])
        self.assertFalse(body["needsReconnect"])
        self.assertEqual(body["mpUserId"], 555)
        self.assertTrue(body["liveMode"])
        raw = response.content.decode()
        for secret in ("APP_USR-acceso-nuevo", "TG-refresco-nuevo", "APP_USR-pk-secreta"):
            self.assertNotIn(secret, raw)

    def test_12_status_without_account(self):  # 12
        body = self.client.get(STATUS.format("a"), **self.auth(self.admin_token)).json()
        self.assertEqual(body, {
            "connected": False, "needsReconnect": False, "mpUserId": None,
            "liveMode": None, "connectedAt": None, "expiresAt": None,
        })

    def test_13_delete(self):  # 13
        self.make_account(self.venue_a, 100)
        self.make_account(self.venue_b, 100)
        make_user("portero_a", self.venue_a, DOOR)
        self.assertEqual(self.client.delete(STATUS.format("a"), **self.auth(self.token("portero_a"))).status_code, 404)
        self.assertTrue(MercadoPagoAccount.objects.filter(venue=self.venue_a).exists())
        response = self.client.delete(STATUS.format("a"), **self.auth(self.admin_token))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(MercadoPagoAccount.objects.filter(venue=self.venue_a).exists())
        self.assertTrue(MercadoPagoAccount.objects.filter(venue=self.venue_b).exists())
        self.assertEqual(self.client.delete(STATUS.format("a"), **self.auth(self.admin_token)).status_code, 204)


class AccessTokenTests(MpTestCase):
    def test_14_fresh_token_no_refresh(self):  # 14
        self.make_account(self.venue_a, 100)
        with mock.patch("catalog.mp.refresh_tokens") as refresh:
            self.assertEqual(get_access_token(self.venue_a), "acc-viejo")
        refresh.assert_not_called()

    def test_15_refreshes_when_close_to_expiry(self):  # 15
        self.make_account(self.venue_a, 10)
        new = tokens(access="acc-2", refresh="ref-2")
        with mock.patch("catalog.mp.refresh_tokens", return_value=new) as refresh:
            self.assertEqual(get_access_token(self.venue_a), "acc-2")
        refresh.assert_called_once_with("ref-viejo")
        account = MercadoPagoAccount.objects.get(venue=self.venue_a)
        self.assertNotIn("acc-2", account.access_token)
        self.assertEqual(crypto.decrypt(account.access_token), "acc-2")
        self.assertEqual(crypto.decrypt(account.refresh_token), "ref-2")
        self.assertLess(abs((account.expires_at - timezone.now()).total_seconds() - EXPIRES_IN), 60)
        self.assertFalse(account.needs_reconnect)

    def test_16_failed_refresh_still_valid(self):  # 16
        self.make_account(self.venue_a, 10)
        with mock.patch("catalog.mp.refresh_tokens", side_effect=mp.MercadoPagoError(400, {})):
            self.assertEqual(get_access_token(self.venue_a), "acc-viejo")
        self.assertTrue(MercadoPagoAccount.objects.get(venue=self.venue_a).needs_reconnect)

    def test_17_failed_refresh_expired(self):  # 17
        self.make_account(self.venue_a, -1)
        with mock.patch("catalog.mp.refresh_tokens", side_effect=mp.MercadoPagoError(400, {})):
            with self.assertRaises(MercadoPagoNotConnected):
                get_access_token(self.venue_a)
        self.assertTrue(MercadoPagoAccount.objects.get(venue=self.venue_a).needs_reconnect)

    def test_18_not_connected(self):  # 18
        with self.assertRaises(MercadoPagoNotConnected):
            get_access_token(self.venue_a)
        self.make_account(self.venue_b, 100, needs_reconnect=True)
        with self.assertRaises(MercadoPagoNotConnected):
            get_access_token(self.venue_b)


class RefreshCommandTests(MpTestCase):
    def test_20_command(self):  # 20
        near = self.make_account(self.venue_a, 10, access="a1", refresh="r1")
        far = self.make_account(self.venue_b, 100, access="b1", refresh="r2")
        venue_d = Venue.objects.create(name="D", slug="d", is_active=True)
        failing = self.make_account(venue_d, 10, access="d1", refresh="r-falla")

        def fake(refresh_token):
            if refresh_token == "r-falla":
                raise mp.MercadoPagoError(400, {})
            return tokens(access="a-nuevo", refresh="r-nuevo")

        out = io.StringIO()
        with mock.patch("catalog.mp.refresh_tokens", side_effect=fake) as refresh:
            call_command("refresh_mp_tokens", stdout=out)
        self.assertEqual(refresh.call_count, 2)
        for account in (near, far, failing):
            account.refresh_from_db()
        self.assertEqual(crypto.decrypt(near.access_token), "a-nuevo")
        self.assertEqual(crypto.decrypt(far.access_token), "b1")
        self.assertFalse(far.needs_reconnect)
        self.assertTrue(failing.needs_reconnect)
        self.assertNotIn("a-nuevo", out.getvalue())
        self.assertNotIn("r-nuevo", out.getvalue())


class CryptoTests(MpTestCase):
    def test_21_tampered_text_raises(self):  # 21
        encrypted = crypto.encrypt("secreto")
        with self.assertRaises(crypto.TokenCryptoError):
            crypto.decrypt(encrypted[:-4] + "AAAA")
        with self.assertRaises(crypto.TokenCryptoError):
            crypto.decrypt("basura")
        with override_settings(MP_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            with self.assertRaises(crypto.TokenCryptoError):
                crypto.decrypt(encrypted)
        with override_settings(MP_TOKEN_ENCRYPTION_KEY=""):
            with self.assertRaises(crypto.TokenCryptoError):
                crypto.encrypt("x")
