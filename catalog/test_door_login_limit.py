from datetime import timedelta

from django.utils import timezone

from .door import LOGIN_MAX_FAILURES
from .models import DoorLoginFailure, Membership
from .test_door import PASSWORD, DoorTestCase, make_user


class LoginLimitTests(DoorTestCase):
    def fail(self, username="portero", times=1):
        return [self.login(username, "mala") for _ in range(times)]

    def failures(self, username=None):
        qs = DoorLoginFailure.objects.all()
        return qs.filter(username=username).count() if username else qs.count()

    def test_eleventh_attempt_is_429(self):
        for response in self.fail(times=LOGIN_MAX_FAILURES):
            self.assertEqual(response.status_code, 401)
        response = self.login(password="mala")
        self.assertEqual(response.status_code, 429)
        body = response.json()
        self.assertEqual(body["error"], "too_many_attempts")
        self.assertTrue(1 <= body["retryAfter"] <= 900)
        self.assertEqual(response["Retry-After"], str(body["retryAfter"]))

    def test_correct_password_is_blocked_too(self):
        self.fail(times=LOGIN_MAX_FAILURES)
        self.assertEqual(self.login().status_code, 429)

    def test_usernames_are_normalized(self):
        for i in range(LOGIN_MAX_FAILURES):
            self.login(["Puerta", " puerta ", "PUERTA"][i % 3], "mala")
        self.assertEqual(self.failures("puerta"), LOGIN_MAX_FAILURES)
        self.assertEqual(self.login("puerta", "mala").status_code, 429)

    def test_block_does_not_affect_other_users(self):
        make_user("puerta_a", self.venue_a, Membership.Role.DOOR)
        make_user("puerta_b", self.venue_a, Membership.Role.DOOR)
        self.fail("puerta_a", LOGIN_MAX_FAILURES)
        self.assertEqual(self.login("puerta_a").status_code, 429)
        self.assertEqual(self.login("puerta_b").status_code, 200)

    def test_success_clears_failures(self):
        self.fail(times=LOGIN_MAX_FAILURES - 1)
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.failures("portero"), 0)
        for response in self.fail(times=LOGIN_MAX_FAILURES - 1):
            self.assertEqual(response.status_code, 401)

    def test_window_expires(self):
        self.fail(times=LOGIN_MAX_FAILURES)
        DoorLoginFailure.objects.update(created_at=timezone.now() - timedelta(minutes=16))
        self.assertEqual(self.login().status_code, 200)

    def test_unknown_user_is_blocked_too(self):
        for response in self.fail("fantasma", LOGIN_MAX_FAILURES):
            self.assertEqual(response.status_code, 401)
        self.assertEqual(self.login("fantasma", "mala").status_code, 429)

    def test_429_does_not_add_rows(self):
        self.fail(times=LOGIN_MAX_FAILURES)
        for response in self.fail(times=5):
            self.assertEqual(response.status_code, 429)
        self.assertEqual(self.failures(), LOGIN_MAX_FAILURES)

    def test_recording_failure_purges_old_rows(self):
        DoorLoginFailure.objects.create(username="otro")
        DoorLoginFailure.objects.update(created_at=timezone.now() - timedelta(hours=25))
        self.fail()
        self.assertEqual(self.failures("otro"), 0)
        self.assertEqual(self.failures("portero"), 1)

    def test_cashier_403_leaves_no_failures(self):
        make_user("cajero", self.venue_a, Membership.Role.CASHIER)
        response = self.login("cajero", PASSWORD)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.failures(), 0)
