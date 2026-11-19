from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Event, Membership, TicketType, Venue


class CatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.moon = Venue.objects.create(slug="moon", name="Moon", is_active=True)
        cls.other = Venue.objects.create(slug="other", name="Otro boliche", is_active=True)
        cls.hidden = Venue.objects.create(slug="hidden", name="Inactivo")
        cls.moon_event = cls.create_event(cls.moon, "shared", "Moon")
        cls.other_event = cls.create_event(cls.other, "shared", "Otro")
        cls.draft = cls.create_event(cls.moon, "draft", "Borrador", published=False)
        cls.create_event(cls.hidden, "secret", "Oculto")
        cls.moon_ticket = TicketType.objects.create(
            event=cls.moon_event, slug="general", name="General Moon", price="15000.50", capacity=100,
        )
        cls.other_ticket = TicketType.objects.create(
            event=cls.other_event, slug="general", name="General Otro", price=10000, capacity=10,
        )
        TicketType.objects.create(
            event=cls.moon_event, slug="hidden", name="Oculta", price=100, capacity=5, is_active=False,
        )
        cls.venue_admin = get_user_model().objects.create_user(username="moon-admin", is_staff=True)
        Membership.objects.create(venue=cls.moon, user=cls.venue_admin, role=Membership.Role.ADMIN)
        Membership.objects.create(venue=cls.other, user=cls.venue_admin, role=Membership.Role.DOOR)

    @staticmethod
    def create_event(venue, slug, title, published=True):
        return Event.objects.create(
            venue=venue, slug=slug, title=title, starts_at=timezone.now(), is_published=published,
        )

    def test_public_catalog_only_exposes_active_and_published_data(self):
        venues = self.client.get(reverse("venue-list")).json()["venues"]
        self.assertEqual([venue["slug"] for venue in venues], ["moon", "other"])
        events = self.client.get(reverse("event-list", args=["moon"])).json()["events"]
        self.assertEqual([event["title"] for event in events], ["Moon"])
        self.assertEqual([ticket["id"] for ticket in events[0]["ticketTypes"]], ["general"])
        self.assertEqual(events[0]["ticketTypes"][0]["price"], 15000.50)
        other = self.client.get(reverse("event-detail", args=["other", "shared"])).json()
        self.assertEqual(other["event"]["title"], "Otro")
        for venue, event in [("moon", "draft"), ("hidden", "secret"), ("other", "draft")]:
            self.assertEqual(self.client.get(reverse("event-detail", args=[venue, event])).status_code, 404)

    def test_admin_catalog_only_lists_managed_venue(self):
        self.client.force_login(self.venue_admin)
        events = self.client.get(reverse("admin:catalog_event_changelist"))
        self.assertEqual(
            list(events.context["cl"].queryset.values_list("pk", flat=True)),
            [self.moon_event.pk, self.draft.pk],
        )
        tickets = self.client.get(reverse("admin:catalog_tickettype_changelist"))
        self.assertNotIn(self.other_ticket.pk, tickets.context["cl"].queryset.values_list("pk", flat=True))
        own_event = self.client.get(reverse("admin:catalog_event_change", args=[self.moon_event.pk]))
        self.assertEqual(own_event.status_code, 200)
        for model, obj in [("event", self.other_event), ("tickettype", self.other_ticket)]:
            response = self.client.get(reverse(f"admin:catalog_{model}_change", args=[obj.pk]))
            self.assertRedirects(response, reverse("admin:index"))

    def test_admin_rejects_cross_venue_creation(self):
        self.client.force_login(self.venue_admin)
        response = self.client.post(
            reverse("admin:catalog_tickettype_add"),
            {
                "event": self.other_event.pk,
                "slug": "forged",
                "name": "Ajena",
                "price": "100.00",
                "capacity": 10,
                "is_active": "on",
            },
        )
        self.assertIn("event", response.context["adminform"].form.errors)
        self.assertFalse(TicketType.objects.filter(slug="forged").exists())
