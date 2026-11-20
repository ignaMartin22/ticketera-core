from datetime import timedelta
from decimal import Decimal
from unittest import mock
from uuid import uuid4

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog import crypto
from catalog.models import (
    Event, FeeChange, MercadoPagoAccount, Membership, Order, OrderItem, TicketType, Venue, VenueIntegration,
)
from catalog.mp_connect import STATE_SALT

User = get_user_model()


class BackofficeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.root = User.objects.create_superuser("root", "root@example.com", "x")
        cls.venue = Venue.objects.create(slug="moon", name="Moon", city="Jujuy", is_active=True)
        cls.other = Venue.objects.create(slug="other", name="Otro", city="Jujuy")
        cls.venue_admin = User.objects.create_user("moon-admin", "admin@moon.test")
        Membership.objects.create(venue=cls.venue, user=cls.venue_admin, role=Membership.Role.ADMIN)

    def setUp(self):
        self.client.force_login(self.root)

    def fees(self, mode="buyer", buyer="10", platform="5", note=""):
        return {"fee_mode": mode, "buyer_fee_percent": buyer, "platform_fee_percent": platform, "note": note}

    def test_only_superusers_can_use_the_panel(self):
        self.client.logout()
        response = self.client.get(reverse("backoffice:venue-list"))
        self.assertRedirects(response, f"{reverse('backoffice:login')}?next={reverse('backoffice:venue-list')}")
        self.client.force_login(self.venue_admin)
        for name, args in [("venue-list", []), ("venue-detail", ["moon"]), ("venue-fees", ["moon"]),
                           ("venue-orders", ["moon"])]:
            self.assertEqual(self.client.get(reverse(f"backoffice:{name}", args=args)).status_code, 403)
        response = self.client.post(reverse("backoffice:venue-fees", args=["moon"]), self.fees(platform="0"))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(FeeChange.objects.exists())

    def test_every_page_renders(self):
        Event.objects.create(venue=self.venue, slug="fecha", title="Fecha", starts_at=timezone.now(), is_published=True)
        pages = [("venue-list", []), ("venue-create", [])] + [
            (name, ["moon"])
            for name in ["venue-detail", "venue-edit", "venue-fees", "venue-team", "venue-integrations", "venue-orders"]
        ]
        for name, args in pages:
            with self.subTest(name):
                self.assertEqual(self.client.get(reverse(f"backoffice:{name}", args=args)).status_code, 200)

    def test_create_venue_starts_paused_with_fee_history(self):
        data = {"slug": "Nuevo", "name": "Nuevo", "city": "Palpalá", "address": "Ruta 66", "instagram": "@nuevo",
                "accent": "#112233", **self.fees(note="Lanzamiento")}
        response = self.client.post(reverse("backoffice:venue-create"), data)
        self.assertRedirects(response, reverse("backoffice:venue-detail", args=["nuevo"]))
        venue = Venue.objects.get(slug="nuevo")
        self.assertFalse(venue.is_active)
        self.assertEqual(venue.instagram, "nuevo")
        self.assertEqual((venue.buyer_fee_rate, venue.platform_fee_rate), (Decimal("0.1"), Decimal("0.05")))
        change = venue.fee_changes.get()
        self.assertEqual((change.changed_by, change.note), (self.root, "Lanzamiento"))

    def test_fee_change_is_recorded_once_and_applies_to_new_orders_only(self):
        event = Event.objects.create(venue=self.venue, slug="fecha", title="Fecha", starts_at=timezone.now(),
                                     is_published=True)
        TicketType.objects.create(event=event, slug="general", name="General", price="1000", capacity=10)

        def reserve():
            response = self.client.post(
                reverse("order-create", args=["moon", "fecha"]),
                {"email": "a@b.co", "name": "Ana", "phone": "+5493884123456",
                 "items": [{"ticketTypeId": "general", "quantity": 1}]},
                content_type="application/json", headers={"Idempotency-Key": str(uuid4())},
            )
            self.assertEqual(response.status_code, 201)
            return OrderItem.objects.get(order_id=response.json()["order"]["id"])

        url = reverse("backoffice:venue-fees", args=["moon"])
        self.client.post(url, self.fees(buyer="10", platform="5"))
        first = reserve()
        self.client.post(url, self.fees(buyer="10", platform="5"))
        self.assertEqual(self.venue.fee_changes.count(), 1)
        self.client.post(url, self.fees(mode="venue", buyer="10", platform="12.5"))
        second = reserve()
        first.refresh_from_db()
        self.assertEqual((first.unit_service_fee, first.unit_platform_fee), (Decimal("100"), Decimal("50")))
        self.assertEqual((second.unit_service_fee, second.unit_platform_fee), (Decimal("0"), Decimal("125")))
        self.assertEqual(self.venue.fee_changes.count(), 2)

    def test_fee_percent_is_validated(self):
        response = self.client.post(reverse("backoffice:venue-fees", args=["moon"]), self.fees(platform="150"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("platform_fee_percent", response.context["form"].errors)

    def test_add_member_creates_user_without_core_access(self):
        url = reverse("backoffice:venue-team", args=["other"])
        response = self.client.post(url, {"email": "Duena@Otro.test", "name": "Dueña", "role": "admin"})
        self.assertRedirects(response, url)
        user = User.objects.get(email="duena@otro.test")
        self.assertEqual(user.username, "duena@otro.test")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.has_usable_password())
        self.assertTrue(Membership.objects.filter(venue=self.other, user=user, role="admin").exists())

    def test_existing_user_changes_role_and_can_be_removed(self):
        url = reverse("backoffice:venue-team", args=["other"])
        self.client.post(url, {"email": "admin@moon.test", "role": "door"})
        self.client.post(url, {"email": "ADMIN@moon.test", "role": "cashier"})
        door = Membership.objects.get(venue=self.other, user=self.venue_admin)
        self.assertEqual(door.role, "cashier")
        self.assertEqual(User.objects.filter(email__iexact="admin@moon.test").count(), 1)

        moon = Membership.objects.get(venue=self.venue, user=self.venue_admin)
        self.client.post(reverse("backoffice:member-remove", args=["moon", moon.pk]))
        self.assertFalse(Membership.objects.filter(pk=moon.pk).exists())
        # Una membresía de otro boliche no se puede tocar desde la URL de este.
        response = self.client.post(reverse("backoffice:member-remove", args=["moon", door.pk]))
        self.assertEqual(response.status_code, 404)

    def test_toggle_hides_venue_from_public_api(self):
        self.client.post(reverse("backoffice:venue-toggle", args=["moon"]))
        self.assertEqual(self.client.get(reverse("event-list", args=["moon"])).status_code, 404)
        self.client.post(reverse("backoffice:venue-toggle", args=["moon"]))
        self.assertEqual(self.client.get(reverse("event-list", args=["moon"])).status_code, 200)

    def test_toggle_records_who_changed_the_status(self):
        self.client.post(reverse("backoffice:venue-toggle", args=["moon"]))
        change = self.venue.status_changes.get()
        self.assertEqual((change.changed_by, change.is_active), (self.root, False))
        self.client.post(reverse("backoffice:venue-toggle", args=["moon"]))
        self.assertEqual(self.venue.status_changes.count(), 2)
        self.assertTrue(self.venue.status_changes.first().is_active)
        response = self.client.get(reverse("backoffice:venue-detail", args=["moon"]))
        self.assertContains(response, "root@example.com")

    def test_integrations_show_every_provider(self):
        VenueIntegration.objects.create(venue=self.venue, provider="mercadopago", status="connected",
                                        account_label="moon@mp.test")
        response = self.client.get(reverse("backoffice:venue-integrations", args=["moon"]))
        statuses = {i.provider: i.status for i in response.context["integrations"]}
        self.assertEqual(statuses, {"mercadopago": "connected", "whatsapp": "disconnected", "email": "disconnected"})
        self.assertContains(response, "moon@mp.test")
        self.assertFalse(VenueIntegration.objects.filter(venue=self.venue, provider="whatsapp").exists())

    def order(self, event, ticket, expires=timedelta(minutes=10), quantity=2, email="ana@x.co"):
        """Un pedido reservado con un solo ítem, como sale del checkout."""
        order = Order.objects.create(
            event=event, buyer_email=email, buyer_name="Ana", buyer_phone="+5493884123456",
            idempotency_key=uuid4(), expires_at=timezone.now() + expires,
        )
        OrderItem.objects.create(
            order=order, ticket_type=ticket, ticket_slug=ticket.slug, quantity=quantity,
            unit_price=ticket.price, unit_service_fee="100",
        )
        return order

    def test_orders_lists_buyers_status_and_totals(self):
        event = Event.objects.create(venue=self.venue, slug="fecha", title="Fecha", starts_at=timezone.now(),
                                     is_published=True)
        ticket = TicketType.objects.create(event=event, slug="general", name="General", price="1000", capacity=10)
        order = self.order(event, ticket)
        self.order(event, ticket, expires=timedelta(minutes=-1), email="vieja@x.co")
        other = Event.objects.create(venue=self.other, slug="fecha", title="Fecha", starts_at=timezone.now())
        self.order(other, TicketType.objects.create(event=other, slug="general", name="General", price="500",
                                                    capacity=5), email="otra@x.co")

        response = self.client.get(reverse("backoffice:venue-orders", args=["moon"]))
        self.assertEqual(response.status_code, 200)
        rows = {row.pk: row for row in response.context["orders"]}
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[order.pk].ticket_count, rows[order.pk].total), (2, Decimal("2200")))
        self.assertContains(response, "ana@x.co")
        self.assertContains(response, "+5493884123456")
        self.assertContains(response, "Pendiente de pago")
        self.assertContains(response, "Vencido")
        self.assertContains(response, "$ 2.200,00")
        self.assertNotContains(response, "otra@x.co")

    def test_sales_report_sums_per_event_and_ticket_type(self):
        event = Event.objects.create(venue=self.venue, slug="fecha", title="Fecha", starts_at=timezone.now(),
                                     is_published=True)
        general = TicketType.objects.create(event=event, slug="general", name="General", price="1000", capacity=10,
                                            sold=3)
        vip = TicketType.objects.create(event=event, slug="vip", name="VIP", price="2500", capacity=4, sold=2)
        self.order(event, general)                                # reserva vigente
        self.order(event, vip, expires=timedelta(minutes=-1))     # vencida: no suma

        response = self.client.get(reverse("backoffice:venue-orders", args=["moon"]))
        row = response.context["sales"][0]
        self.assertEqual((row["sold"], row["reserved"], row["capacity"]), (5, 2, 14))
        self.assertEqual(row["revenue"], Decimal("8000"))  # 3 × $1000 + 2 × $2500, a precio de entrada
        self.assertEqual(row["occupancy"], 36)
        self.assertContains(response, "VIP: 2/4")
        self.assertContains(response, "$ 8.000,00")

    def test_orders_shows_paid_orders_and_does_not_count_them_as_reserved(self):
        event = Event.objects.create(venue=self.venue, slug="fecha", title="Fecha", starts_at=timezone.now(),
                                     is_published=True)
        ticket = TicketType.objects.create(event=event, slug="general", name="General", price="1000", capacity=10,
                                           sold=2)
        # Pagado: conserva expires_at vigente, pero ya está en las vendidas y no es una reserva.
        paid = self.order(event, ticket, email="pago@x.co")
        Order.objects.filter(pk=paid.pk).update(paid_at=timezone.now())

        response = self.client.get(reverse("backoffice:venue-orders", args=["moon"]))
        self.assertContains(response, "Pagado")
        self.assertNotContains(response, "Pendiente de pago")
        self.assertEqual(response.context["sales"][0]["reserved"], 0)
        detail = self.client.get(reverse("backoffice:venue-detail", args=["moon"]))
        self.assertEqual(detail.context["reserved"], 0)


MP_SETTINGS = dict(
    MP_APP_ID="123", MP_CLIENT_SECRET="secreto", MP_REDIRECT_URI="https://api.example.com/cb",
    MP_CONNECT_RETURN_URL="https://app.example.com/panel",
)
ACCESS, REFRESH, PUBLIC_KEY = "APP_USR-acceso-claro", "TG-refresco-claro", "APP_USR-pk-claro"


class PlatformMercadoPagoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.root = User.objects.create_superuser("root", "root@example.com", "x")
        cls.a = Venue.objects.create(slug="a", name="Boliche A", city="Jujuy")
        cls.b = Venue.objects.create(slug="b", name="Boliche B", city="Jujuy")
        cls.staff = User.objects.create_user("staff", "staff@example.com")
        Membership.objects.create(venue=cls.a, user=cls.staff, role=Membership.Role.ADMIN)

    def setUp(self):
        settings_ = override_settings(MP_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(), **MP_SETTINGS)
        settings_.enable()
        self.addCleanup(settings_.disable)
        self.client.force_login(self.root)

    def url(self, name, slug="a"):
        return reverse(f"backoffice:{name}", args=[slug])

    def generate(self, slug="a"):
        with mock.patch("catalog.mp.authorization_url", return_value="https://auth.mp/x?s=1") as auth:
            response = self.client.post(self.url("venue-mp-connect", slug))
        return response, auth

    def connect_account(self, venue):
        account = MercadoPagoAccount.objects.create(
            venue=venue, mp_user_id=777, access_token=crypto.encrypt(ACCESS),
            refresh_token=crypto.encrypt(REFRESH), public_key=PUBLIC_KEY, live_mode=True,
            expires_at=timezone.now() + timedelta(days=100),
        )
        VenueIntegration.objects.create(
            venue=venue, provider="mercadopago", status="connected", account_id="777",
            connected_at=timezone.now(), expires_at=account.expires_at,
        )

    def test_01_non_superusers_cannot_generate_or_disconnect(self):
        self.connect_account(self.a)
        self.client.force_login(self.staff)
        for name in ("venue-mp-connect", "venue-mp-disconnect"):
            with mock.patch("catalog.mp.authorization_url") as auth:
                self.assertEqual(self.client.post(self.url(name)).status_code, 403)
            auth.assert_not_called()
        self.assertTrue(MercadoPagoAccount.objects.filter(venue=self.a).exists())
        self.client.logout()
        for name in ("venue-mp-connect", "venue-mp-disconnect"):
            url = self.url(name)
            self.assertRedirects(self.client.post(url), f"{reverse('backoffice:login')}?next={url}")

    def test_02_generate_link_shows_it_once(self):
        response = self.client.get(self.url("venue-integrations"))
        self.assertContains(response, "Generar link de conexión")
        self.assertNotContains(response, "https://auth.mp/x")
        response, auth = self.generate()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "https://auth.mp/x?s=1")
        self.assertContains(response, "Vence en 10 minutos.")
        self.assertContains(response, "Copiar link")
        self.assertNotContains(self.client.get(self.url("venue-integrations")), "https://auth.mp/x")
        self.assertFalse(MercadoPagoAccount.objects.exists())

    def test_03_state_identifies_venue_and_platform_origin(self):
        _, auth = self.generate()
        payload = signing.loads(auth.call_args.args[0], salt=STATE_SALT, max_age=600)
        self.assertEqual((payload["v"], payload["u"], payload["o"]), (self.a.pk, self.root.pk, "platform"))
        _, auth_b = self.generate("b")
        self.assertEqual(signing.loads(auth_b.call_args.args[0], salt=STATE_SALT)["v"], self.b.pk)

    def test_04_not_configured_disables_the_button(self):
        with override_settings(MP_APP_ID=""):
            response = self.client.get(self.url("venue-integrations"))
            self.assertContains(response, "Faltan las credenciales de Mercado Pago en el servidor.")
            self.assertContains(response, 'disabled>Generar link de conexión</button>')
            self.assertNotContains(response, self.url("venue-mp-connect") + '"')
            with mock.patch("catalog.mp.authorization_url") as auth:
                post = self.client.post(self.url("venue-mp-connect"))
            auth.assert_not_called()
            self.assertNotContains(post, "https://auth.mp")

    def test_05_connected_shows_account_and_disconnect_is_isolated(self):
        self.connect_account(self.a)
        self.connect_account(self.b)
        response = self.client.get(self.url("venue-integrations"))
        self.assertContains(response, "777")
        self.assertContains(response, "Desconectar")
        self.assertContains(response, "Reconectar")
        self.assertRedirects(self.client.post(self.url("venue-mp-disconnect")), self.url("venue-integrations"))
        self.assertFalse(MercadoPagoAccount.objects.filter(venue=self.a).exists())
        integration = VenueIntegration.objects.get(venue=self.a, provider="mercadopago")
        self.assertEqual((integration.status, integration.account_id), ("disconnected", ""))
        self.assertTrue(MercadoPagoAccount.objects.filter(venue=self.b).exists())
        self.assertEqual(VenueIntegration.objects.get(venue=self.b, provider="mercadopago").status, "connected")

    def test_06_page_never_shows_secrets(self):
        self.connect_account(self.a)
        self.generate()
        html = self.client.get(self.url("venue-integrations")).content.decode()
        for secret in (ACCESS, REFRESH, PUBLIC_KEY, "secreto"):
            self.assertNotIn(secret, html)

    def test_06b_direct_account_shows_direct_charge_line_without_secrets(self):
        self.connect_account(self.a)
        MercadoPagoAccount.objects.filter(venue=self.a).update(direct=True)
        html = self.client.get(self.url("venue-integrations")).content.decode()
        self.assertIn("Cobro directo: toda la venta entra a esta cuenta, sin reparto de comisión.", html)
        self.assertIn("Reconectar", html)  # el botón sigue disponible
        for secret in (ACCESS, REFRESH, PUBLIC_KEY):
            self.assertNotIn(secret, html)
        other = self.client.get(self.url("venue-integrations", "b")).content.decode()
        self.assertNotIn("Cobro directo:", other)
        MercadoPagoAccount.objects.filter(venue=self.a).update(direct=False)
        self.assertNotIn("Cobro directo:", self.client.get(self.url("venue-integrations")).content.decode())

    def test_07_whatsapp_and_email_stay_disabled(self):
        html = self.client.get(self.url("venue-integrations")).content.decode()
        self.assertEqual(html.count("Disponible cuando se habilite la integración."), 2)
        self.assertEqual(html.count('<button type="button" class="btn btn-sm" disabled>Conectar</button>'), 2)
