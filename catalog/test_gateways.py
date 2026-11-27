"""Macro Click de Pago y Nave: link de pago, confirmación consultando la pasarela y avisos."""

import json
from decimal import Decimal
from unittest import mock

from cryptography.fernet import Fernet
from django.core.cache import cache
from django.test import TestCase, override_settings

from . import gateways
from .gateways import macro, nave
from .gateways.base import GatewayError, credentials, save_account
from .models import GatewayCharge, Order, Payment, Ticket
from .test_payments import API, FRONT, make_order, make_venue, url

Outcome = Payment.Outcome
MACRO = {"comercio": "COM-123", "secret": "frase-secreta-del-comercio", "sucursal": ""}
NAVE = {"client_id": "cid", "client_secret": "csecret", "pos_id": "POS-9"}
NAVE_CHECKOUT = "https://checkout.ranty.io/pago/abc"


def pay(client, order, provider, venue="a"):
    return client.post(url(order, venue=venue), data=json.dumps({"provider": provider}), content_type="application/json")


@override_settings(FRONT_BASE_URL=FRONT, PUBLIC_API_URL=API, PAYMENTS_ALLOW_TEST_MODE=True)
class GatewayCase(TestCase):
    def setUp(self):
        config = override_settings(MP_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode())
        config.enable()
        self.addCleanup(config.disable)
        cache.clear()
        self.venue, self.event, *_ = make_venue("a")
        self.other, self.other_event, *_ = make_venue("b")
        self.addCleanup(mock.patch.stopall)


class MacroTests(GatewayCase):
    def setUp(self):
        super().setUp()
        self.account = save_account(self.venue, "macro", MACRO, live_mode=False)

    def test_01_credentials_are_encrypted_at_rest(self):
        self.assertNotIn("frase-secreta", self.account.credentials)
        self.assertEqual(credentials(self.account), MACRO)

    def test_02_link_points_to_our_page_and_freezes_the_fee(self):
        order = make_order(self.event)
        response = pay(self.client, order, "macro")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"initPoint": f"{API}/api/v1/pagos/macro/{order.pk}/", "provider": "macro"})
        charge = GatewayCharge.objects.get(order=order)
        self.assertEqual(charge.reference, order.pk.hex)
        order.refresh_from_db()
        # Cargo de servicio (10 %) más comisión (5 %) sobre 2 × 10.000 + 18.000.
        self.assertEqual(order.platform_fee, Decimal("5700.00"))
        # Idempotente: el segundo pedido de link no crea otro cobro.
        self.assertEqual(pay(self.client, order, "macro").json()["initPoint"], charge.checkout_url)
        self.assertEqual(GatewayCharge.objects.filter(order=order).count(), 1)

    def test_03_venue_without_macro_is_not_connected(self):
        order = make_order(self.other_event)
        self.assertEqual(pay(self.client, order, "macro", venue="b").json(), {"error": "venue_not_connected"})

    def test_04_unknown_provider_is_rejected(self):
        order = make_order(self.event)
        self.assertEqual(pay(self.client, order, "bitcoin").status_code, 400)

    def test_05_page_posts_the_encrypted_form(self):
        order = make_order(self.event)
        pay(self.client, order, "macro")
        response = self.client.get(f"/api/v1/pagos/macro/{order.pk}/", REMOTE_ADDR="190.1.2.3")
        self.assertEqual(response.status_code, 200)
        fields = response.context["fields"]
        self.assertEqual(response.context["action"], "https://sandboxpp.asjservicios.com.ar/")
        total = macro.amount_text(Decimal("41800.00"))  # 38.000 de entradas más 10 % de cargo
        self.assertEqual(macro.decrypt(fields["Monto"], MACRO["secret"]), total)
        self.assertEqual(
            fields["Hash"], macro.signature("190.1.2.3", "COM-123", "", total, MACRO["secret"]),
        )
        self.assertEqual(macro.decrypt(fields["CallbackSuccess"], MACRO["secret"]),
                         f"{FRONT}/a/fecha/pedido/{order.pk}")
        self.assertEqual(fields["TransaccionComercioId"], order.pk.hex)
        self.assertNotContains(response, MACRO["secret"])
        self.assertEqual(response["X-Robots-Tag"], "noindex")

    def test_06_amount_is_in_cents_without_separators(self):
        self.assertEqual(macro.amount_text(Decimal("11000")), "1100000")
        self.assertEqual(macro.amount_text(Decimal("10.5")), "1050")

    def test_07_aes_matches_the_php_key_rule(self):
        # La clave se repite hasta 32 bytes y se corta; el texto descifra con la misma frase.
        self.assertEqual(macro._key("abc"), (b"abc" * 11)[:32])
        self.assertEqual(macro.decrypt(macro.encrypt("hola", "abc"), "abc"), "hola")

    def notice_and_query(self, order, rows):
        session = mock.Mock(status_code=200, json=mock.Mock(return_value={"data": "token-pp"}))
        listed = mock.Mock(status_code=200, json=mock.Mock(return_value={"data": {"transacciones": rows}}))
        post = mock.patch("catalog.gateways.macro.requests.post", return_value=session).start()
        get = mock.patch("catalog.gateways.macro.requests.get", return_value=listed).start()
        response = self.client.post(
            "/api/v1/pagos/macro/aviso/", data=json.dumps({"TransaccionComercioId": order.pk.hex, "EstadoId": 3}),
            content_type="application/json",
        )
        return response, post, get

    def test_08_notice_is_verified_with_the_api_and_issues_tickets(self):
        order = make_order(self.event)
        pay(self.client, order, "macro")
        row = {"transaccionComercioId": order.pk.hex, "estado": 3, "montoBruto": 41800.0, "monto": 40000.0}
        response, post, get = self.notice_and_query(order, [row])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(post.call_args.kwargs["json"], {"guid": "COM-123", "frase": MACRO["secret"]})
        self.assertEqual(get.call_args.kwargs["headers"], {"Authorization": "Bearer token-pp"})
        order.refresh_from_db()
        self.assertIsNotNone(order.paid_at)
        self.assertEqual(Ticket.objects.filter(order=order).count(), 3)
        self.assertEqual(GatewayCharge.objects.get(order=order).outcome, Outcome.ISSUED)

    def test_09_a_forged_notice_does_nothing_without_the_api(self):
        order = make_order(self.event)
        pay(self.client, order, "macro")
        self.notice_and_query(order, [])  # la API no conoce el cobro
        order.refresh_from_db()
        self.assertIsNone(order.paid_at)

    def test_10_wrong_amount_goes_to_review(self):
        order = make_order(self.event)
        pay(self.client, order, "macro")
        self.notice_and_query(order, [{"transaccionComercioId": order.pk.hex, "estado": 3, "montoBruto": 1.0}])
        order.refresh_from_db()
        self.assertIsNone(order.paid_at)
        self.assertEqual(GatewayCharge.objects.get(order=order).outcome, Outcome.MISMATCH)
        detail = self.client.get(f"/api/v1/venues/a/events/fecha/orders/{order.pk}/").json()["order"]
        self.assertIs(detail["paymentReview"], True)

    def test_11_unknown_reference_is_acknowledged(self):
        response = self.client.post(
            "/api/v1/pagos/macro/aviso/", data=json.dumps({"TransaccionComercioId": "nada"}),
            content_type="application/json",
        )
        self.assertEqual(response.json(), {"ok": True})

    def test_12_api_failure_asks_for_a_retry(self):
        order = make_order(self.event)
        pay(self.client, order, "macro")
        mock.patch("catalog.gateways.macro.requests.post", side_effect=macro.requests.ConnectionError).start()
        response = self.client.post(
            "/api/v1/pagos/macro/aviso/", data=json.dumps({"TransaccionComercioId": order.pk.hex}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 500)


class NaveTests(GatewayCase):
    def setUp(self):
        super().setUp()
        self.account = save_account(self.venue, "nave", NAVE, live_mode=False)
        self.calls = []
        mock.patch("catalog.gateways.nave.requests.post", side_effect=self.fake_auth).start()
        self.api = mock.patch("catalog.gateways.nave.requests.request", side_effect=self.fake_api).start()
        self.status = "SUCCESS_PROCESSED"
        self.value = "41800.00"

    def fake_auth(self, url, json=None, timeout=None):
        self.calls.append(("auth", url, json))
        return mock.Mock(status_code=200, json=mock.Mock(return_value={"access_token": "tok-nave", "expires_in": 3600}))

    def fake_api(self, method, url, json=None, timeout=None, headers=None):
        self.calls.append((method, url, json, headers))
        if method == "POST":
            body = {"id": "PR-1", "checkout_url": NAVE_CHECKOUT}
        else:
            body = {
                "id": "PR-1", "external_payment_id": str(self.order.pk), "status": {"name": self.status},
                "transactions": [{"amount": {"currency": "ARS", "value": self.value}}],
            }
        return mock.Mock(status_code=200, json=mock.Mock(return_value=body))

    def start(self):
        self.order = make_order(self.event)
        return pay(self.client, self.order, "nave")

    def test_13_creates_the_payment_request_with_the_venue_credentials(self):
        response = self.start()
        self.assertEqual(response.json(), {"initPoint": NAVE_CHECKOUT, "provider": "nave"})
        auth = self.calls[0]
        self.assertEqual(auth[2]["client_id"], "cid")
        method, called, body, headers = self.calls[1]
        self.assertTrue(called.endswith("/payment_request/ecommerce"))
        self.assertEqual(headers["Authorization"], "Bearer tok-nave")
        self.assertEqual(body["external_payment_id"], str(self.order.pk))
        self.assertEqual(body["seller"], {"pos_id": "POS-9"})
        self.assertEqual(body["transactions"][0]["amount"], {"currency": "ARS", "value": "41800.00"})
        charge = GatewayCharge.objects.get(order=self.order)
        self.assertEqual(charge.reference, "PR-1")
        self.assertIn(f"/api/v1/pagos/nave/{self.order.pk}/aviso/?secret={charge.notify_secret}",
                      body["additional_info"]["notification_url"])

    def test_14_rejects_a_checkout_outside_nave(self):
        def outside(method, url, **kwargs):
            return mock.Mock(status_code=200, json=mock.Mock(return_value={"id": "PR-1", "checkout_url": "https://evil.example/x"}))

        self.api.side_effect = outside
        self.assertEqual(self.start().json(), {"error": "gateway_unavailable"})
        self.assertFalse(GatewayCharge.objects.exists())

    def notify(self, secret):
        return self.client.post(f"/api/v1/pagos/nave/{self.order.pk}/aviso/?secret={secret}",
                                data=json.dumps({"payment_request_id": "PR-1"}), content_type="application/json")

    def test_15_notice_with_the_secret_is_verified_and_issues_tickets(self):
        self.start()
        charge = GatewayCharge.objects.get(order=self.order)
        self.assertEqual(self.notify(charge.notify_secret).status_code, 200)
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.paid_at)
        self.assertEqual(GatewayCharge.objects.get(pk=charge.pk).outcome, Outcome.ISSUED)
        # Un segundo aviso no emite de nuevo.
        self.notify(charge.notify_secret)
        self.assertEqual(Ticket.objects.filter(order=self.order).count(), 3)

    def test_16_notice_without_the_secret_is_rejected(self):
        self.start()
        self.assertEqual(self.notify("otro").status_code, 401)
        self.order.refresh_from_db()
        self.assertIsNone(self.order.paid_at)

    def test_17_pending_request_does_not_issue(self):
        self.start()
        self.status = "PENDING"
        charge = GatewayCharge.objects.get(order=self.order)
        self.notify(charge.notify_secret)
        self.order.refresh_from_db()
        self.assertIsNone(self.order.paid_at)

    def test_18_sync_endpoint_confirms_without_the_notice(self):
        self.start()
        response = self.client.post(f"/api/v1/venues/a/events/fecha/orders/{self.order.pk}/payment/sync/")
        self.assertEqual(response.json()["order"]["status"], "paid")

    def test_19_payment_methods_follow_the_connected_accounts(self):
        self.order = make_order(self.event)
        detail = self.client.get(f"/api/v1/venues/a/events/fecha/orders/{self.order.pk}/").json()["order"]
        self.assertEqual(detail["paymentMethods"], ["nave"])
        save_account(self.venue, "macro", MACRO, live_mode=False)
        self.assertEqual(gateways.payment_methods(self.venue), ["macro", "nave"])
        self.assertEqual(gateways.payment_methods(self.other), [])

    def test_20_the_token_is_reused(self):
        self.start()
        nave._token(self.account)
        self.assertEqual(sum(1 for c in self.calls if c[0] == "auth"), 1)

    def test_21_broken_api_answers_unavailable(self):
        self.api.side_effect = nave.requests.ConnectionError
        self.assertEqual(self.start().json(), {"error": "gateway_unavailable"})
        with self.assertRaises(GatewayError):
            nave._request(self.account, "GET", "/x")


class ReconcileTests(NaveTests):
    def test_22_cron_confirms_gateway_charges(self):
        from io import StringIO

        from django.core.management import call_command

        self.start()
        out = StringIO()
        call_command("reconcile_mp_payments", stdout=out)
        self.assertIn("Confirmados: 1", out.getvalue())
        self.assertIsNotNone(Order.objects.get(pk=self.order.pk).paid_at)
