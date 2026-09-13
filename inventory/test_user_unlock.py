"""Admin unlock button — verify a superuser can clear a failed-login lockout via WMS settings."""
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

# Force bypass all axes cool-off timers so the test exercises AXES_FAILURE_LIMIT cleanly,
# independent of how long the lockout would normally last.
@override_settings(
    AXES_COOLOFF_TIME=None,
    AXES_RESET_ON_SUCCESS=False,
)
class UserUnlockTests(TestCase):
    def setUp(self):
        from axes.models import AccessAttempt
        # Clear any attempts left over from previous test runs
        AccessAttempt.objects.all().delete()
        self.admin = User.objects.create_superuser(
            "unlock-admin", "[email protected]", "pw"
        )
        self.target = User.objects.create_user(
            "locked-user", "[email protected]", "real-pw",
            is_active=True,
        )
        self.client.force_login(self.admin)

    def _fail_logins(self, n, *, username="locked-user", password="wrong-pw"):
        from django.conf import settings as django_settings
        from django.test import Client
        # Use a fresh client (no auth) to simulate actual login attempts.
        # The host matters in this project; mirror the request test runner used.
        from axes.models import AccessAttempt as AA
        from axes import utils as axes_utils
        from axes.helpers import make_client_ip_address_unknown
        from django.contrib.auth.signals import user_logged_in
        user_logged_in.receivers = []
        for _ in range(n):
            AA.objects.all().delete()  # start clean each iter so failures_since_start grows
            c = Client()
            for _try in range(5):  # > failure_limit
                r = c.post(
                    reverse("login"),
                    {"username": username, "password": password},
                    follow=False,
                )

    def test_locked_user_shows_badge_and_unlock_button(self):
        # Seed an axes attempt row the same way a real lockout would
        from axes.models import AccessAttempt
        from django.utils import timezone
        AccessAttempt.objects.create(
            username=self.target.username,
            ip_address="127.0.0.1",
            user_agent="test",
            http_accept="*/*",
            path_info="/admin/login/",
            attempt_time=timezone.now(),
            get_data={},
            post_data={},
            failures_since_start=5,
        )
        response = self.client.get(reverse("user_management"))
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('class="badge danger user-locked-badge"', body)
        self.assertIn("Locked</span>", body)
        # Unlock form & button render
        self.assertIn(
            f'action="{reverse("user_unlock", args=[self.target.pk])}"',
            body,
        )
        self.assertIn("Unlock</button>", body)
        # Locked badge is NOT shown for unlocked users (sanity)
        self.assertNotIn(
            'class="badge danger user-locked-badge">Locked',
            body.replace(self.target.username, ""),
        )

    def test_admin_can_unlock_and_attempts_cleared(self):
        from axes.models import AccessAttempt
        from django.utils import timezone
        # Seed two attempts with distinct IPs (mirrors a brute-forcer trying from
        # multiple addresses).
        for i, ip in enumerate(("127.0.0.1", "10.0.0.2"), start=1):
            AccessAttempt.objects.create(
                username=self.target.username,
                ip_address=ip,
                user_agent="t", http_accept="*/*", path_info="/admin/login/",
                attempt_time=timezone.now(),
                get_data={}, post_data={}, failures_since_start=5,
            )
        self.assertEqual(
            AccessAttempt.objects.filter(username=self.target.username).count(),
            2,
        )
        response = self.client.post(
            reverse("user_unlock", args=[self.target.pk]),
            {"next": reverse("user_management")},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            AccessAttempt.objects.filter(username=self.target.username).count(),
            0,
        )

    def test_unlock_user_with_no_attempts_returns_to_list(self):
        # No attempts seeded — should still succeed but with info message
        response = self.client.post(
            reverse("user_unlock", args=[self.target.pk]),
            {},
        )
        self.assertEqual(response.status_code, 302)

    def test_admin_cannot_unlock_self(self):
        response = self.client.post(
            reverse("user_unlock", args=[self.admin.pk]),
            {},
        )
        self.assertEqual(response.status_code, 302)
        # Did nothing (no failure)

    def test_non_superuser_cannot_unlock(self):
        # Demote admin and re-fetch; should bounce
        self.admin.is_superuser = False
        self.admin.is_staff = True
        self.admin.save()
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("user_unlock", args=[self.target.pk]),
            {},
        )
        # user_passes_test with login_url="/settings/" sends 302
        self.assertEqual(response.status_code, 302)
        self.assertIn("/settings/", response.url or "")

    def test_settings_page_users_tab_renders_lockout_state(self):
        """Regression: /settings/?tab=users must show the same Locked badge
        and Unlock form as /settings/users/ — both routes render the same
        template, so both must supply locked_usernames."""
        from axes.models import AccessAttempt
        AccessAttempt.objects.create(
            username=self.target.username,
            ip_address="10.0.0.1",
            user_agent="regression-test",
            failures_since_start=1,
            attempt_time=timezone.now(),
            path_info="/login/",
            http_accept="text/html",
        )
        # /settings/?tab=users (the one Derek's screenshot came from)
        self.client.force_login(self.admin)
        resp = self.client.get("/settings/?tab=users")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertIn("user-locked-badge", body,
                      "Locked badge missing on /settings/?tab=users")
        self.assertIn("settings-unlock-form", body,
                      "Unlock form missing on /settings/?tab=users")
        self.assertIn("Locked", body,
                      "Locked label missing on /settings/?tab=users")
        # And on the dedicated /settings/users/ route
        resp2 = self.client.get("/settings/users/")
        self.assertEqual(resp2.status_code, 200)
        body2 = resp2.content.decode("utf-8")
        self.assertIn("user-locked-badge", body2)
        self.assertIn("settings-unlock-form", body2)
