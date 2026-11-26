from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .gateways.base import credentials, save_account
from .models import GatewayAccount, MercadoPagoAccount, Membership, Venue, VenueIntegration
from .staff_auth import issue_token
from .test_manage import auth

MACRO = {"comercio": "COM-123", "secret": "frase-secreta", "sucursal": ""}
KEY = Fernet.generate_key().decode()


@override_settings(PUBLIC_API_URL="https://api.example.com", MP_TOKEN_ENCRYPTION_KEY=KEY)
class PaymentAccountsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.moon = Venue.objects.create(slug="moon", name="Moon", is_active=True)
        cls.other = Venue.objects.create(slug="other", name="Otro", is_active=True)
        User = get_user_model()
        cls.admin = User.objects.create_user(username="dueno@moon.com")
        cls.door = User.objects.create_user(username="puerta@moon.com")
        Membership.objects.create(venue=cls.moon, user=cls.admin, role=Membership.Role.ADMIN)
        Membership.objects.create(venue=cls.moon, user=cls.door, role=Membership.Role.DOOR)
        cls.key = issue_token(cls.admin)
        cls.door_key = issue_token(cls.door)

    def list(self, key=None, venue="moon"):
        return self.client.get(reverse("payment-accounts", args=[venue]), headers=auth(key or self.key))

    def put(self, provider, data, key=None, venue="moon"):
        return self.client.put(
            reverse("payment-account", args=[venue, provider]), data,
            content_type="application/json", headers=auth(key or self.key),
        )

    def test_status_without_accounts(self):
        data = self.list().json()
        self.assertFalse(data["mercadopago"]["connected"])
        self.assertFalse(data["macro"]["connected"])
        self.assertTrue(data["macro"]["available"])
        self.assertEqual([f["name"] for f in data["macro"]["fields"]], ["comercio", "secret", "sucursal"])
        self.assertEqual([f["name"] for f in data["nave"]["fields"]], ["client_id", "client_secret", "pos_id"])

    def test_save_macro_encrypts_and_never_returns_the_secret(self):
        response = self.put("macro", {**MACRO, "liveMode": False})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("frase-secreta", response.content.decode())
        macro = response.json()["macro"]
        self.assertTrue(macro["connected"])
        self.assertEqual(macro["accountId"], "COM-123")
        self.assertFalse(macro["liveMode"])
        account = GatewayAccount.objects.get(venue=self.moon, provider="macro")
        self.assertNotIn("frase-secreta", account.credentials)
        self.assertEqual(credentials(account), MACRO)
        integration = VenueIntegration.objects.get(venue=self.moon, provider="macro")
        self.assertEqual(integration.status, VenueIntegration.Status.CONNECTED)
        self.assertEqual(integration.account_label, "Entorno de prueba")
        self.assertNotIn("frase-secreta", self.list().content.decode())

    def test_replace_and_disconnect(self):
        self.put("nave", {"client_id": "a", "client_secret": "b", "pos_id": "P1"})
        response = self.put("nave", {"client_id": "c", "client_secret": "d", "pos_id": "P2", "liveMode": True})
        self.assertEqual(response.json()["nave"]["accountId"], "P2")
        self.assertTrue(GatewayAccount.objects.get(venue=self.moon, provider="nave").live_mode)
        response = self.client.delete(reverse("payment-account", args=["moon", "nave"]), headers=auth(self.key))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(GatewayAccount.objects.filter(venue=self.moon).exists())
        self.assertEqual(
            VenueIntegration.objects.get(venue=self.moon, provider="nave").status, VenueIntegration.Status.DISCONNECTED
        )

    def test_validation(self):
        self.assertEqual(self.put("macro", {"comercio": "X"}).json(), {"error": "missing_credentials"})
        self.assertEqual(self.put("macro", {**MACRO, "secret": 5}).json(), {"error": "invalid_credentials"})
        self.assertEqual(self.put("macro", {**MACRO, "comercio": "x" * 201}).json(), {"error": "invalid_credentials"})
        self.assertEqual(self.put("macro", {**MACRO, "liveMode": "si"}).json(), {"error": "invalid_credentials"})
        self.assertEqual(self.put("macro", {**MACRO, "secret": "   "}).json(), {"error": "missing_credentials"})
        self.assertEqual(self.put("mercadopago", MACRO).status_code, 404)
        self.assertFalse(GatewayAccount.objects.exists())

    def test_needs_public_url_and_key(self):
        with override_settings(PUBLIC_API_URL=""):
            self.assertFalse(self.list().json()["macro"]["available"])
            response = self.put("macro", MACRO)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "gateway_not_configured"})

    def test_only_admin_of_that_venue(self):
        self.assertEqual(self.client.get(reverse("payment-accounts", args=["moon"])).status_code, 401)
        self.assertEqual(self.list(key=self.door_key).status_code, 404)
        self.assertEqual(self.put("macro", MACRO, key=self.door_key).status_code, 404)
        self.assertEqual(self.list(venue="other").status_code, 404)
        self.assertEqual(self.put("macro", MACRO, venue="other").status_code, 404)
        self.assertFalse(GatewayAccount.objects.exists())

    def test_cannot_touch_another_venue(self):
        save_account(self.other, "macro", MACRO, live_mode=False)
        response = self.client.delete(reverse("payment-account", args=["other", "macro"]), headers=auth(self.key))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(GatewayAccount.objects.filter(venue=self.other).exists())

    def test_mercadopago_status(self):
        MercadoPagoAccount.objects.create(
            venue=self.moon, mp_user_id=1, access_token="x", live_mode=True, direct=True, expires_at=timezone.now(),
        )
        mp = self.list().json()["mercadopago"]
        self.assertTrue(mp["connected"])
        self.assertTrue(mp["direct"])
        self.assertTrue(mp["liveMode"])
        self.assertNotIn("access_token", mp)

