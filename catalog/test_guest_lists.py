import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Event, GuestList, Membership, Order, Ticket, TicketType, Venue
from .qr import sign_ticket
from .staff_auth import issue_token

User = get_user_model()
ADMIN, DOOR, CASHIER, RRPP = (
    Membership.Role.ADMIN, Membership.Role.DOOR, Membership.Role.CASHIER, Membership.Role.RRPP,
)
MANAGE = "/api/v1/manage/venues"
RRPP_API = "/api/v1/rrpp/lists/"


def make_user(username, *pairs):
    user = User.objects.create_user(username, password="clave-segura-123")
    for venue, role in pairs:
        Membership.objects.create(venue=venue, user=user, role=role)
    return user


@override_settings(FRONT_BASE_URL="https://front.example.com", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class GuestListTestCase(TestCase):
    def setUp(self):
        self.a = Venue.objects.create(name="Boliche A", slug="a", is_active=True)
        self.b = Venue.objects.create(name="Boliche B", slug="b", is_active=True)
        self.event_a = self.make_event(self.a, "fecha", 3)
        self.event_a2 = self.make_event(self.a, "otra", 10)
        self.event_b = self.make_event(self.b, "fecha", 3)
        self.admin_a = make_user("admin-a", (self.a, ADMIN))
        self.admin_b = make_user("admin-b", (self.b, ADMIN))
        self.rrpp1 = make_user("rrpp1", (self.a, RRPP))
        self.rrpp2 = make_user("rrpp2", (self.a, RRPP))
        self.rrpp_b = make_user("rrpp-b", (self.b, RRPP))
        self.door_a = make_user("door-a", (self.a, DOOR))
        self.cashier_a = make_user("cashier-a", (self.a, CASHIER))
        self.until = timezone.now() + timedelta(days=2)

    @staticmethod
    def make_event(venue, slug, days):
        return Event.objects.create(
            venue=venue, slug=slug, title=f"Fecha {slug}", is_published=True,
            starts_at=timezone.now() + timedelta(days=days),
        )

    def call(self, method, path, user=None, body=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {issue_token(user)}"} if user else {}
        kwargs = {"content_type": "application/json", **headers}
        if body is not None:
            kwargs["data"] = json.dumps(body)
        return getattr(self.client, method)(path, **kwargs)

    def lists_url(self, venue="a", event="fecha", list_id=None):
        url = f"{MANAGE}/{venue}/events/{event}/lists/"
        return f"{url}{list_id}/" if list_id else url

    def create_list(self, promoter="rrpp1", event=None, user=None, **overrides):
        event = event or self.event_a
        body = {
            "name": "Lista Juan", "promoterUsername": promoter, "quota": 3,
            "validUntil": self.until.isoformat(), **overrides,
        }
        response = self.call("post", self.lists_url(event.venue.slug, event.slug), user or self.admin_a, body)
        return response

    def make_list(self, promoter="rrpp1", event=None, **overrides):
        event = event or self.event_a
        user = self.admin_a if event.venue == self.a else self.admin_b
        response = self.create_list(promoter, event, user=user, **overrides)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["list"]

    def add_guest(self, list_id, user, name="Ana Gómez", email="ana@example.com"):
        return self.call("post", f"{RRPP_API}{list_id}/guests/", user, {"name": name, "email": email})


class ManageListTests(GuestListTestCase):
    def test_create_list(self):
        response = self.create_list()
        self.assertEqual(response.status_code, 201)
        data = response.json()["list"]
        self.assertEqual(data["name"], "Lista Juan")
        self.assertEqual(data["promoter"], {"username": "rrpp1", "name": "rrpp1"})
        self.assertEqual((data["quota"], data["issued"], data["admitted"], data["isActive"]), (3, 0, 0, True))
        self.assertEqual(data["event"], {
            "slug": "fecha", "title": "Fecha fecha", "startsAt": self.event_a.starts_at.isoformat(),
            "venueSlug": "a", "venueName": "Boliche A",
        })
        guest_list = GuestList.objects.get(pk=data["id"])
        ticket_type = guest_list.ticket_type
        self.assertEqual((ticket_type.kind, ticket_type.price, ticket_type.capacity), ("list", 0, 3))
        self.assertTrue(ticket_type.slug.startswith("lista-"))

    def test_list_slugs_are_unique_per_event(self):
        self.make_list("rrpp1")
        self.make_list("rrpp2")
        slugs = TicketType.objects.filter(event=self.event_a, kind="list").values_list("slug", flat=True)
        self.assertEqual(len(set(slugs)), 2)

    def test_create_validations(self):
        cases = {
            "invalid_name": {"name": "  "},
            "invalid_quota": {"quota": 0},
            "invalid_valid_until": {"validUntil": "mañana"},
            "invalid_promoter": {"promoterUsername": "nadie"},
        }
        for code, override in cases.items():
            response = self.create_list(**override)
            self.assertEqual((response.status_code, response.json()), (400, {"error": code}), code)
        for quota in (5001, "3", True, 2.5):
            self.assertEqual(self.create_list(quota=quota).json(), {"error": "invalid_quota"}, quota)
        past = (timezone.now() - timedelta(minutes=1)).isoformat()
        self.assertEqual(self.create_list(validUntil=past).json(), {"error": "invalid_valid_until"})
        self.assertEqual(self.create_list(validUntil="2030-01-01T10:00:00").json(), {"error": "invalid_valid_until"})
        self.assertEqual(self.create_list(name="x" * 121).json(), {"error": "invalid_name"})
        self.assertFalse(GuestList.objects.exists())

    def test_promoter_must_be_rrpp_or_admin_of_this_venue(self):
        self.assertEqual(self.create_list("door-a").json(), {"error": "invalid_promoter"})
        self.assertEqual(self.create_list("cashier-a").json(), {"error": "invalid_promoter"})
        self.assertEqual(self.create_list("rrpp-b").json(), {"error": "invalid_promoter"})
        self.assertEqual(self.create_list("admin-a").status_code, 201)

    def test_one_list_per_promoter_per_event(self):
        self.make_list()
        response = self.create_list()
        self.assertEqual((response.status_code, response.json()), (409, {"error": "list_exists"}))
        self.assertEqual(self.create_list(event=self.event_a2).status_code, 201)

    def test_get_lists_and_promoters(self):
        first = self.make_list("rrpp1")
        second = self.make_list("rrpp2")
        body = self.call("get", self.lists_url(), self.admin_a).json()
        self.assertEqual([item["id"] for item in body["lists"]], [first["id"], second["id"]])
        body = self.call("get", f"{MANAGE}/a/rrpp/", self.admin_a).json()
        self.assertEqual([p["username"] for p in body["promoters"]], ["rrpp1", "rrpp2"])

    def test_patch_list(self):
        data = self.make_list()
        guest_list = GuestList.objects.get(pk=data["id"])
        new_until = (timezone.now() - timedelta(hours=1)).isoformat()
        response = self.call("patch", self.lists_url(list_id=data["id"]), self.admin_a, {
            "name": "Otra", "quota": 7, "validUntil": new_until, "isActive": False,
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()["list"]
        self.assertEqual((body["name"], body["quota"], body["isActive"]), ("Otra", 7, False))
        guest_list.refresh_from_db()
        self.assertEqual(guest_list.ticket_type.name, "Otra")
        self.assertEqual(guest_list.ticket_type.capacity, 7)

    def test_patch_quota_cannot_drop_below_issued(self):
        data = self.make_list()
        self.add_guest(data["id"], self.rrpp1, email="a@example.com")
        self.add_guest(data["id"], self.rrpp1, email="b@example.com")
        url = self.lists_url(list_id=data["id"])
        response = self.call("patch", url, self.admin_a, {"quota": 1})
        self.assertEqual((response.status_code, response.json()), (409, {"error": "quota_below_issued"}))
        self.assertEqual(self.call("patch", url, self.admin_a, {"quota": 2}).status_code, 200)

    def test_patch_validations(self):
        data = self.make_list()
        url = self.lists_url(list_id=data["id"])
        for body, code in (
            ({"name": ""}, "invalid_name"), ({"quota": -1}, "invalid_quota"),
            ({"validUntil": "x"}, "invalid_valid_until"), ({"isActive": "no"}, "invalid_is_active"),
        ):
            response = self.call("patch", url, self.admin_a, body)
            self.assertEqual((response.status_code, response.json()), (400, {"error": code}))

    def test_list_types_are_not_editable_through_ticket_type_api(self):
        data = self.make_list()
        slug = GuestList.objects.get(pk=data["id"]).ticket_type.slug
        event_url = f"{MANAGE}/a/events/fecha/"
        detail = self.call("get", event_url, self.admin_a).json()["event"]
        self.assertEqual(detail["ticketTypes"], [])
        response = self.call("patch", event_url, self.admin_a, {"ticketTypes": [{"slug": slug, "capacity": 99}]})
        self.assertEqual((response.status_code, response.json()), (400, {"error": "invalid_ticket_types"}))
        response = self.call("patch", event_url, self.admin_a, {"ticketTypes": [
            {"name": "Lista", "kind": "list", "price": 10, "capacity": 5},
        ]})
        self.assertEqual((response.status_code, response.json()), (400, {"error": "invalid_kind"}))
        self.assertEqual(TicketType.objects.get(event=self.event_a).capacity, 3)

    def test_multi_venue_isolation(self):
        data = self.make_list()
        list_b = self.make_list("rrpp-b", event=self.event_b)
        # El admin de A no ve ni crea listas en B.
        for method, path, body in (
            ("get", self.lists_url("b"), None),
            ("post", self.lists_url("b"), {"name": "x", "promoterUsername": "rrpp-b", "quota": 1, "validUntil": self.until.isoformat()}),
            ("patch", self.lists_url("b", list_id=list_b["id"]), {"quota": 4}),
            ("get", f"{MANAGE}/b/rrpp/", None),
        ):
            self.assertEqual(self.call(method, path, self.admin_a, body).status_code, 403, path)
        # Ni editar una lista de B colgándola de la URL de A.
        response = self.call("patch", self.lists_url("a", list_id=list_b["id"]), self.admin_a, {"quota": 4})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.call("get", self.lists_url("b"), self.admin_b).json()["lists"][0]["id"], list_b["id"])
        self.assertEqual(self.call("get", self.lists_url("a"), self.admin_b).status_code, 403)
        self.assertNotIn(data["id"], [item["id"] for item in self.call("get", self.lists_url("b"), self.admin_b).json()["lists"]])

    def test_other_roles_and_anonymous_cannot_manage(self):
        for user in (self.rrpp1, self.door_a, self.cashier_a):
            self.assertEqual(self.call("get", self.lists_url(), user).status_code, 403)
            self.assertEqual(self.call("get", f"{MANAGE}/a/rrpp/", user).status_code, 403)
            self.assertEqual(self.create_list(user=user).status_code, 403)
        self.assertEqual(self.call("get", self.lists_url()).status_code, 401)


class PublicHidingTests(GuestListTestCase):
    def test_list_type_is_hidden_and_not_reservable(self):
        data = self.make_list()
        slug = GuestList.objects.get(pk=data["id"]).ticket_type.slug
        TicketType.objects.create(event=self.event_a, slug="general", name="General", price=100, capacity=5)
        for url in ("/api/v1/venues/a/events/fecha/", "/api/v1/venues/a/events/"):
            body = self.client.get(url).json()
            event = body["event"] if "event" in body else body["events"][0]
            self.assertEqual([t["id"] for t in event["ticketTypes"]], ["general"])
        response = self.client.post(
            "/api/v1/venues/a/events/fecha/orders/",
            data=json.dumps({"email": "x@example.com", "name": "X", "items": [{"ticketTypeId": slug, "quantity": 1}]}),
            content_type="application/json", HTTP_IDEMPOTENCY_KEY="7f9b3a52-6a56-4c5b-9a56-1b0f2f1c0a11",
        )
        self.assertEqual((response.status_code, response.json()), (409, {"error": "ticket_type_unavailable"}))
        self.assertFalse(Order.objects.exists())


class RrppTests(GuestListTestCase):
    def test_add_guest_issues_ticket_and_sends_mail(self):
        data = self.make_list()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.add_guest(data["id"], self.rrpp1)
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["guest"]["name"], "Ana Gómez")
        self.assertEqual(body["guest"]["email"], "ana@example.com")
        self.assertIsNone(body["guest"]["usedAt"])
        self.assertEqual((body["list"]["issued"], body["list"]["quota"]), (1, 3))
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.order.buyer_phone, "")
        self.assertEqual(ticket.order.items.get().unit_price, 0)
        self.assertIsNotNone(ticket.order.paid_at)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["ana@example.com"])
        self.assertIn("Válida para ingresar hasta las", message.body)
        self.assertIn("Válida para ingresar hasta las", message.alternatives[0].content)
        # Hora local de Jujuy (UTC-3).
        local = self.until - timedelta(hours=3)
        self.assertIn(f"hasta las {local:%H:%M} del", message.body)

    def test_regular_ticket_mail_has_no_validity_line(self):
        from .test_tickets import make_event, make_order, make_type
        from .tickets import issue_tickets

        event = make_event("moon")
        order = make_order(event, [(make_type(event, "general"), 1)])
        with self.captureOnCommitCallbacks(execute=True):
            issue_tickets(order.pk)
        self.assertNotIn("Válida para ingresar", mail.outbox[-1].body)

    def test_invalid_input(self):
        data = self.make_list()
        for body, code in (
            ({"name": "", "email": "a@example.com"}, "invalid_name"),
            ({"name": "x" * 151, "email": "a@example.com"}, "invalid_name"),
            ({"name": "Ana", "email": "no-es-mail"}, "invalid_email"),
            ({"name": "Ana"}, "invalid_email"),
        ):
            response = self.call("post", f"{RRPP_API}{data['id']}/guests/", self.rrpp1, body)
            self.assertEqual((response.status_code, response.json()), (400, {"error": code}))
        self.assertFalse(Ticket.objects.exists())

    def test_already_on_list_across_lists_and_case(self):
        first = self.make_list("rrpp1")
        second = self.make_list("rrpp2")
        self.assertEqual(self.add_guest(first["id"], self.rrpp1, email="ana@example.com").status_code, 201)
        for list_id, user, email in (
            (first["id"], self.rrpp1, "ana@example.com"),
            (second["id"], self.rrpp2, "ana@example.com"),
            (second["id"], self.rrpp2, "  ANA@Example.COM "),
        ):
            response = self.add_guest(list_id, user, email=email)
            self.assertEqual((response.status_code, response.json()), (409, {"error": "already_on_list"}))
        self.assertEqual(Ticket.objects.count(), 1)

    def test_same_email_can_be_on_another_date(self):
        first = self.make_list("rrpp1")
        other = self.make_list("rrpp1", event=self.event_a2)
        self.assertEqual(self.add_guest(first["id"], self.rrpp1).status_code, 201)
        self.assertEqual(self.add_guest(other["id"], self.rrpp1).status_code, 201)

    def test_regular_ticket_buyer_can_still_join_a_list(self):
        from .test_tickets import make_order
        from .tickets import issue_tickets

        data = self.make_list()
        ticket_type = TicketType.objects.create(event=self.event_a, slug="general", name="G", price=10, capacity=5)
        order = make_order(self.event_a, [(ticket_type, 1)])
        order.buyer_email = "ana@example.com"
        order.save()
        issue_tickets(order.pk)
        self.assertEqual(self.add_guest(data["id"], self.rrpp1).status_code, 201)

    def test_quota_full(self):
        data = self.make_list(quota=1)
        self.assertEqual(self.add_guest(data["id"], self.rrpp1, email="a@example.com").status_code, 201)
        response = self.add_guest(data["id"], self.rrpp1, email="b@example.com")
        self.assertEqual((response.status_code, response.json()), (409, {"error": "quota_full"}))
        self.assertEqual(TicketType.objects.get(event=self.event_a).sold, 1)

    def test_list_closed_when_inactive_or_past(self):
        data = self.make_list()
        self.call("patch", self.lists_url(list_id=data["id"]), self.admin_a, {"isActive": False})
        response = self.add_guest(data["id"], self.rrpp1)
        self.assertEqual((response.status_code, response.json()), (409, {"error": "list_closed"}))
        past = (timezone.now() - timedelta(minutes=1)).isoformat()
        self.call("patch", self.lists_url(list_id=data["id"]), self.admin_a, {"isActive": True, "validUntil": past})
        response = self.add_guest(data["id"], self.rrpp1)
        self.assertEqual((response.status_code, response.json()), (409, {"error": "list_closed"}))
        self.assertFalse(Ticket.objects.exists())

    def test_my_lists_only_mine_and_only_upcoming(self):
        mine = self.make_list("rrpp1")
        later = self.make_list("rrpp1", event=self.event_a2)
        self.make_list("rrpp2")
        old = Event.objects.create(
            venue=self.a, slug="vieja", title="Vieja", starts_at=timezone.now() - timedelta(days=3),
        )
        GuestList.objects.create(
            event=old, promoter=self.rrpp1, name="Vieja", valid_until=self.until,
            ticket_type=TicketType.objects.create(
                event=old, slug="lista-1", name="Vieja", kind="list", price=0, capacity=1,
            ),
        )
        body = self.call("get", RRPP_API, self.rrpp1).json()
        self.assertEqual([item["id"] for item in body["lists"]], [mine["id"], later["id"]])

    def test_my_lists_span_venues(self):
        make_user("multi", (self.a, RRPP), (self.b, RRPP))
        multi = User.objects.get(username="multi")
        self.make_list("multi")
        self.make_list("multi", event=self.event_b)
        body = self.call("get", RRPP_API, multi).json()
        self.assertEqual({item["event"]["venueSlug"] for item in body["lists"]}, {"a", "b"})

    def test_detail_shows_guests_newest_first(self):
        data = self.make_list()
        self.add_guest(data["id"], self.rrpp1, name="Primera", email="a@example.com")
        self.add_guest(data["id"], self.rrpp1, name="Segunda", email="b@example.com")
        body = self.call("get", f"{RRPP_API}{data['id']}/", self.rrpp1).json()
        self.assertEqual([guest["name"] for guest in body["guests"]], ["Segunda", "Primera"])
        self.assertEqual(body["list"]["issued"], 2)
        self.assertEqual(set(body["guests"][0]), {"name", "email", "addedAt", "usedAt"})

    def test_not_my_list_is_404(self):
        data = self.make_list("rrpp1")
        list_b = self.make_list("rrpp-b", event=self.event_b)
        for user, list_id in ((self.rrpp2, data["id"]), (self.rrpp_b, data["id"]), (self.rrpp1, list_b["id"])):
            self.assertEqual(self.call("get", f"{RRPP_API}{list_id}/", user).status_code, 404)
            self.assertEqual(self.add_guest(list_id, user).status_code, 404)
        self.assertEqual(self.call("get", f"{RRPP_API}99999/", self.rrpp1).status_code, 404)
        self.assertFalse(Ticket.objects.exists())

    def test_venue_admin_can_operate_lists_of_their_venue_only(self):
        data = self.make_list("rrpp1")
        list_b = self.make_list("rrpp-b", event=self.event_b)
        self.assertEqual(self.call("get", f"{RRPP_API}{data['id']}/", self.admin_a).status_code, 200)
        self.assertEqual(self.add_guest(data["id"], self.admin_a).status_code, 201)
        self.assertEqual(self.call("get", f"{RRPP_API}{list_b['id']}/", self.admin_a).status_code, 404)
        self.assertEqual(self.add_guest(list_b["id"], self.admin_a).status_code, 404)
        # Pero en "mis listas" aparecen solo las propias.
        self.assertEqual(self.call("get", RRPP_API, self.admin_a).json()["lists"], [])

    def test_door_and_cashier_have_no_access(self):
        data = self.make_list("rrpp1")
        for user in (self.door_a, self.cashier_a):
            self.assertEqual(self.call("get", RRPP_API, user).json()["lists"], [])
            self.assertEqual(self.call("get", f"{RRPP_API}{data['id']}/", user).status_code, 404)
            self.assertEqual(self.add_guest(data["id"], user).status_code, 404)

    def test_removed_membership_loses_access(self):
        data = self.make_list("rrpp1")
        Membership.objects.filter(user=self.rrpp1).delete()
        self.assertEqual(self.call("get", RRPP_API, self.rrpp1).json()["lists"], [])
        self.assertEqual(self.add_guest(data["id"], self.rrpp1).status_code, 404)

    def test_requires_token(self):
        self.assertEqual(self.call("get", RRPP_API).status_code, 401)
        response = self.client.get(RRPP_API, HTTP_AUTHORIZATION="Bearer basura")
        self.assertEqual((response.status_code, response.json()), (401, {"error": "unauthorized"}))
        self.assertEqual(self.add_guest(1, None).status_code, 401)

    def test_rrpp_role_login_and_me(self):
        response = self.client.post(
            "/api/v1/auth/login/", data=json.dumps({"username": "rrpp1", "password": "clave-segura-123"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["memberships"], [{"venue": {"slug": "a", "name": "Boliche A"}, "role": "rrpp"}])
        # No es personal de puerta.
        door = self.client.post(
            "/api/v1/door/login/", data=json.dumps({"username": "rrpp1", "password": "clave-segura-123"}),
            content_type="application/json",
        )
        self.assertEqual(door.status_code, 403)
        token = response.json()["token"]
        events = self.client.get("/api/v1/door/venues/a/events/", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(events.status_code, 404)


class DoorAndOrderTests(GuestListTestCase):
    def issue_guest(self, valid_until=None):
        data = self.make_list()
        self.add_guest(data["id"], self.rrpp1)
        if valid_until is not None:
            GuestList.objects.filter(pk=data["id"]).update(valid_until=valid_until)
        return data, Ticket.objects.get()

    def scan(self, ticket, venue="a", event="fecha"):
        door = self.door_a if venue == "a" else None
        return self.call("post", f"/api/v1/door/venues/{venue}/events/{event}/scan/", door, {"token": sign_ticket(ticket.pk)})

    def test_scan_before_deadline_admits(self):
        data, ticket = self.issue_guest()
        response = self.scan(ticket)
        self.assertEqual((response.status_code, response.json()["result"]), (200, "admitted"))
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.used_at)
        body = self.call("get", f"{RRPP_API}{data['id']}/", self.rrpp1).json()
        self.assertEqual(body["list"]["admitted"], 1)
        self.assertIsNotNone(body["guests"][0]["usedAt"])

    def test_scan_after_deadline_is_list_closed_and_does_not_burn(self):
        data, ticket = self.issue_guest(valid_until=timezone.now() - timedelta(minutes=1))
        response = self.scan(ticket)
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["result"], "list_closed")
        self.assertEqual(body["ticket"]["holderName"], "Ana Gómez")
        self.assertEqual(body["ticket"]["ticketType"], "Lista Juan")
        self.assertEqual(body["ticket"]["validUntil"], GuestList.objects.get().valid_until.isoformat())
        ticket.refresh_from_db()
        self.assertIsNone(ticket.used_at)
        # Si el boliche amplía la hora, la misma entrada entra.
        GuestList.objects.update(valid_until=timezone.now() + timedelta(hours=1))
        self.assertEqual(self.scan(ticket).json()["result"], "admitted")

    def test_inactive_list_still_honors_issued_tickets(self):
        data, ticket = self.issue_guest()
        self.call("patch", self.lists_url(list_id=data["id"]), self.admin_a, {"isActive": False})
        self.assertEqual(self.scan(ticket).json()["result"], "admitted")

    def test_already_used_wins_over_closed(self):
        data, ticket = self.issue_guest()
        self.scan(ticket)
        GuestList.objects.update(valid_until=timezone.now() - timedelta(minutes=1))
        response = self.scan(ticket)
        self.assertEqual((response.status_code, response.json()["result"]), (409, "already_used"))

    def test_order_detail_exposes_valid_until(self):
        from .test_tickets import make_order, make_type
        from .tickets import issue_tickets

        data, ticket = self.issue_guest()
        order = ticket.order
        response = self.client.get(f"/api/v1/venues/a/events/fecha/orders/{order.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["order"]["tickets"][0]["validUntil"], GuestList.objects.get().valid_until.isoformat())
        self.assertEqual(response.json()["order"]["total"], "0.00")
        # Entrada común: null.
        general = TicketType.objects.create(event=self.event_a, slug="general", name="G", price=10, capacity=5)
        regular = make_order(self.event_a, [(general, 1)])
        issue_tickets(regular.pk)
        response = self.client.get(f"/api/v1/venues/a/events/fecha/orders/{regular.pk}/")
        self.assertIsNone(response.json()["order"]["tickets"][0]["validUntil"])
