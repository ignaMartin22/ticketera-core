import io
from datetime import timedelta
from unittest import mock

from cryptography.fernet import Fernet
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from . import crypto, mp
from .models import MercadoPagoAccount, VenueIntegration
from .mp_connect import get_access_token
from .test_door import DoorTestCase

TOKEN = "TEST-1234567890-secreto-del-vendedor"
USER = {"id": 2468, "nickname": "TESTVENDEDOR", "site_id": "MLA", "tags": ["test_user", "normal"]}


class MpSetTestAccountTests(DoorTestCase):
    def setUp(self):
        super().setUp()
        config = override_settings(MP_TEST_TOKENS=True, MP_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode())
        config.enable()
        self.addCleanup(config.disable)

    def invoke(self, slug="a", token=TOKEN, user=USER, get_user=None):
        """Corre el comando con stdin simulado y mp.get_user simulado. Devuelve la salida."""
        out = io.StringIO()
        fake = get_user or mock.Mock(return_value=user)
        with mock.patch("catalog.mp.get_user", fake), mock.patch("sys.stdin", io.StringIO(token)):
            call_command("mp_set_test_account", slug, stdout=out, stderr=out)
        self.get_user = fake
        return out.getvalue()

    def assert_error(self, **kwargs):
        out = io.StringIO()
        fake = kwargs.pop("get_user", None) or mock.Mock(return_value=USER)
        self.get_user = fake
        with self.assertRaises(CommandError) as ctx:
            with mock.patch("catalog.mp.get_user", fake), mock.patch(
                "sys.stdin", io.StringIO(kwargs.pop("token", TOKEN)),
            ):
                call_command("mp_set_test_account", kwargs.pop("slug", "a"), stdout=out, stderr=out)
        self.assertNotIn(TOKEN, str(ctx.exception))
        self.assertNotIn(TOKEN, out.getvalue())
        return ctx.exception

    def test_camino_feliz(self):
        self.invoke()
        account = MercadoPagoAccount.objects.get(venue=self.venue_a)
        self.assertEqual(account.mp_user_id, 2468)
        self.assertFalse(account.live_mode)
        self.assertTrue(account.direct)  # 14: es un cobro directo en una cuenta de prueba
        self.assertFalse(account.needs_reconnect)
        self.assertGreater(account.expires_at, timezone.now() + timedelta(days=100))
        with mock.patch("catalog.mp.refresh_tokens") as refresh:
            self.assertEqual(get_access_token(self.venue_a), TOKEN)
        refresh.assert_not_called()
        self.get_user.assert_called_once_with(TOKEN)

    def test_token_cifrado(self):
        self.invoke()
        with connection.cursor() as cursor:
            cursor.execute("SELECT access_token, refresh_token FROM catalog_mercadopagoaccount")
            raw = cursor.fetchone()
        for value in raw:
            self.assertNotIn(TOKEN, value)
        self.assertEqual(crypto.decrypt(raw[0]), TOKEN)

    def test_integracion_conectada(self):
        self.invoke()
        integration = VenueIntegration.objects.get(venue=self.venue_a, provider="mercadopago")
        self.assertEqual(integration.status, VenueIntegration.Status.CONNECTED)
        self.assertEqual(integration.account_id, "2468")
        self.assertEqual(integration.account_label, "TESTVENDEDOR (cobro directo, cuenta de prueba)")
        self.assertEqual(integration.last_error, "")
        self.assertIsNotNone(integration.connected_at)
        self.assertEqual(integration.expires_at, MercadoPagoAccount.objects.get(venue=self.venue_a).expires_at)

    def test_cuenta_real_rechazada(self):
        self.assert_error(get_user=mock.Mock(return_value={**USER, "tags": ["normal"]}))
        self.assertFalse(MercadoPagoAccount.objects.exists())
        self.assertFalse(VenueIntegration.objects.exists())

    def test_sin_tags_rechazada(self):
        self.assert_error(get_user=mock.Mock(return_value={"id": 1, "nickname": "x"}))
        self.assertFalse(MercadoPagoAccount.objects.exists())

    @override_settings(MP_TEST_TOKENS=False)
    def test_sin_mp_test_tokens(self):
        self.assert_error()
        self.get_user.assert_not_called()
        self.assertFalse(MercadoPagoAccount.objects.exists())

    @override_settings(MP_TOKEN_ENCRYPTION_KEY="")
    def test_sin_clave_de_cifrado(self):
        self.assert_error()
        self.get_user.assert_not_called()

    def test_mercado_pago_rechaza_el_token(self):
        error = self.assert_error(get_user=mock.Mock(side_effect=mp.MercadoPagoError(401, {"echo": TOKEN})))
        self.assertIn("Mercado Pago rechazó el token", str(error))
        self.assertFalse(MercadoPagoAccount.objects.exists())

    def test_entrada_vacia(self):
        self.assert_error(token="  \n")
        self.get_user.assert_not_called()

    def test_boliche_inexistente(self):
        self.assert_error(slug="no-existe")
        self.assertFalse(MercadoPagoAccount.objects.exists())

    def test_aislamiento(self):
        self.invoke()
        self.assertFalse(MercadoPagoAccount.objects.filter(venue=self.venue_b).exists())
        self.assertFalse(VenueIntegration.objects.filter(venue=self.venue_b).exists())

    def test_idempotente(self):
        self.invoke()
        self.invoke()
        self.assertEqual(MercadoPagoAccount.objects.filter(venue=self.venue_a).count(), 1)
        self.assertEqual(VenueIntegration.objects.filter(venue=self.venue_a).count(), 1)

    def test_salida(self):
        out = self.invoke()
        self.assertNotIn(TOKEN, out)
        self.assertNotIn(TOKEN[:12], out)
        self.assertIn("a:", out)
        self.assertIn("2468", out)
        self.assertIn("TESTVENDEDOR", out)
        self.assertEqual(len(out.strip().splitlines()), 1)

    def test_reemplaza_cuenta_oauth(self):
        MercadoPagoAccount.objects.create(
            venue=self.venue_a, mp_user_id=999, access_token=crypto.encrypt("APP_USR-viejo"),
            refresh_token=crypto.encrypt("TG-viejo"), public_key="pk", live_mode=True,
            expires_at=timezone.now() + timedelta(days=5), needs_reconnect=True,
        )
        self.invoke()
        account = MercadoPagoAccount.objects.get(venue=self.venue_a)
        self.assertEqual(account.mp_user_id, 2468)
        self.assertFalse(account.needs_reconnect)
        self.assertFalse(account.live_mode)
        self.assertEqual(get_access_token(self.venue_a), TOKEN)


class GetUserTests(DoorTestCase):
    def test_devuelve_el_perfil(self):
        with mock.patch("mercadopago.resources.user.User.get", return_value={"status": 200, "response": USER}):
            self.assertEqual(mp.get_user(TOKEN), USER)

    def test_rechazo_levanta_error(self):
        with mock.patch("mercadopago.resources.user.User.get", return_value={"status": 401, "response": {}}):
            with self.assertRaises(mp.MercadoPagoError):
                mp.get_user(TOKEN)
