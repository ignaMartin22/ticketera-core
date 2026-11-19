from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Event, FeeChange, Membership, OrderItem, TicketType, Venue, VenueIntegration

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
        cache.clear()  # el límite por IP de orders cuenta todos los POST del proceso

    def fees(self, mode="buyer", buyer="10", platform="5", note=""):
        return {"fee_mode": mode, "buyer_fee_percent": buyer, "platform_fee_percent": platform, "note": note}

    def test_only_superusers_can_use_the_panel(self):
        self.client.logout()
        response = self.client.get(reverse("backoffice:venue-list"))
        self.assertRedirects(response, f"{reverse('backoffice:login')}?next={reverse('backoffice:venue-list')}")
        self.client.force_login(self.venue_admin)
        for name, args in [("venue-list", []), ("venue-detail", ["moon"]), ("venue-fees", ["moon"])]:
            self.assertEqual(self.client.get(reverse(f"backoffice:{name}", args=args)).status_code, 403)
        response = self.client.post(reverse("backoffice:venue-fees", args=["moon"]), self.fees(platform="0"))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(FeeChange.objects.exists())

    def test_every_page_renders(self):
        Event.objects.create(venue=self.venue, slug="fecha", title="Fecha", starts_at=timezone.now(), is_published=True)
        pages = [("venue-list", []), ("venue-create", [])] + [
            (name, ["moon"]) for name in ["venue-detail", "venue-edit", "venue-fees", "venue-team", "venue-integrations"]
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

    def test_integrations_show_every_provider(self):
        VenueIntegration.objects.create(venue=self.venue, provider="mercadopago", status="connected",
                                        account_label="moon@mp.test")
        response = self.client.get(reverse("backoffice:venue-integrations", args=["moon"]))
        statuses = {i.provider: i.status for i in response.context["integrations"]}
        self.assertEqual(statuses, {"mercadopago": "connected", "whatsapp": "disconnected", "email": "disconnected"})
        self.assertContains(response, "moon@mp.test")
        self.assertFalse(VenueIntegration.objects.filter(venue=self.venue, provider="whatsapp").exists())
