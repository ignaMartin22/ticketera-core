from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.test import RequestFactory

from .models import DoorLoginFailure, Membership
from .staff_auth import LOGIN_MAX_FAILURES, venue_staff_view
from .test_door import PASSWORD, DoorTestCase, make_user

User = get_user_model()
AUTH_LOGIN = "/api/v1/auth/login/"
ME = "/api/v1/auth/me/"
ADMIN, DOOR, CASHIER = Membership.Role.ADMIN, Membership.Role.DOOR, Membership.Role.CASHIER


class StaffAuthTestCase(DoorTestCase):
    def auth_login(self, username, password=PASSWORD):
        return self.post(AUTH_LOGIN, {"username": username, "password": password})

    def me(self, token=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.get(ME, **headers)

    def member(self, username, *pairs):
        user = make_user(username)
        for venue, role in pairs:
            Membership.objects.create(venue=venue, user=user, role=role)
        return user


class AuthLoginTests(StaffAuthTestCase):
    def test_multi_venue_memberships_sorted_by_slug(self):
        self.member("jefe", (self.venue_b, DOOR), (self.venue_a, ADMIN))
        response = self.auth_login("jefe")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["expiresIn"], 43200)
        self.assertEqual(body["user"], {"username": "jefe"})
        self.assertTrue(body["token"])
        self.assertEqual(body["memberships"], [
            {"venue": {"slug": "a", "name": "Boliche a"}, "role": "admin"},
            {"venue": {"slug": "b", "name": "Boliche b"}, "role": "door"},
        ])

    def test_cashier_can_login(self):
        self.member("caja", (self.venue_a, CASHIER))
        response = self.auth_login("caja")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["memberships"][0]["role"], "cashier")
        self.assertEqual(self.login("caja").status_code, 403)

    def test_wrong_password_is_401(self):
        response = self.auth_login("portero", "mala")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "invalid_credentials"})

    def test_user_without_memberships_is_403(self):
        self.member("suelto")
        response = self.auth_login("suelto")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "no_membership"})

    def test_superuser_without_memberships_is_403(self):
        User.objects.create_superuser("root", password=PASSWORD)
        response = self.auth_login("root")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "no_membership"})

    def test_inactive_venue_is_hidden(self):
        self.venue_b.is_active = False
        self.venue_b.save()
        self.member("mixto", (self.venue_a, DOOR), (self.venue_b, ADMIN))
        slugs = [m["venue"]["slug"] for m in self.auth_login("mixto").json()["memberships"]]
        self.assertEqual(slugs, ["a"])
        self.member("solo_b", (self.venue_b, ADMIN))
        response = self.auth_login("solo_b")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "no_membership"})


class TokenInterchangeTests(StaffAuthTestCase):
    def test_auth_token_works_on_door_route(self):
        token = self.auth_login("portero").json()["token"]
        response = self.client.get(
            "/api/v1/door/venues/a/events/", HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(response.status_code, 200)

    def test_door_token_works_on_me(self):
        response = self.me(self.token())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "user": {"username": "portero"},
            "memberships": [{"venue": {"slug": "a", "name": "Boliche a"}, "role": "door"}],
        })


class MeTests(StaffAuthTestCase):
    def test_me_requires_valid_token(self):
        self.assertEqual(self.me().status_code, 401)
        self.assertEqual(self.me().json(), {"error": "unauthorized"})
        token = self.token()
        self.assertEqual(self.me(token[:-2] + "xx").status_code, 401)

    def test_me_reflects_changes(self):
        token = self.token()
        Membership.objects.filter(user=self.door).delete()
        response = self.me(token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["memberships"], [])


class SharedLimitTests(StaffAuthTestCase):
    def test_failures_count_across_both_logins(self):
        for _ in range(LOGIN_MAX_FAILURES - 4):
            self.assertEqual(self.login("portero", "mala").status_code, 401)
        for _ in range(4):
            self.assertEqual(self.auth_login("portero", "mala").status_code, 401)
        self.assertEqual(self.login("portero", "mala").status_code, 429)
        response = self.auth_login("portero")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"], "too_many_attempts")
        self.assertIn("Retry-After", response)

    def test_no_membership_clears_failures(self):
        self.member("suelto")
        self.auth_login("suelto", "mala")
        self.assertEqual(self.auth_login("suelto").status_code, 403)
        self.assertEqual(DoorLoginFailure.objects.filter(username="suelto").count(), 0)


class VenueStaffViewTests(StaffAuthTestCase):
    def setUp(self):
        super().setUp()
        self.admin_a = self.member("admin_a", (self.venue_a, ADMIN))
        self.calls = []

        @venue_staff_view(ADMIN)
        def view(request, venue, user, membership):
            self.calls.append((venue.slug, user.pk, membership.role))
            return JsonResponse({"ok": True})

        self.view = view

    def call(self, slug, username=None):
        request = RequestFactory().get("/x")
        if username:
            token = self.auth_login(username).json()["token"]
            request.META["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.view(request, slug)

    def test_admin_in_own_venue_passes(self):
        self.assertEqual(self.call("a", "admin_a").status_code, 200)
        self.assertEqual(self.calls, [("a", self.admin_a.pk, "admin")])

    def test_other_role_is_404(self):
        self.assertEqual(self.call("a", "portero").status_code, 404)

    def test_other_venue_is_404(self):
        self.assertEqual(self.call("b", "admin_a").status_code, 404)

    def test_no_token_is_401(self):
        self.assertEqual(self.call("a").status_code, 401)
