from django.contrib.auth.models import Permission, User
from django.test import TestCase, override_settings
from django.urls import reverse

from axes.models import AccessAttempt


class LoginReliabilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="MixedCaseUser",
            email="mixed.case@blackbox.com",
            password="ValidPass!928",
            is_active=True,
        )

    def test_warehouse_login_accepts_username_case_whitespace_and_unique_email(self):
        credentials = (
            "mixedcaseuser",
            "  MixedCaseUser  ",
            "MIXED.CASE@BLACKBOX.COM",
        )
        for identifier in credentials:
            with self.subTest(identifier=identifier):
                self.client.logout()
                response = self.client.post(
                    reverse("login"),
                    {"username": identifier, "password": "ValidPass!928"},
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_duplicate_email_is_not_accepted_as_login_identifier(self):
        User.objects.create_user(
            username="OtherUser",
            email=self.user.email,
            password="ValidPass!928",
        )
        response = self.client.post(
            reverse("login"),
            {"username": self.user.email, "password": "ValidPass!928"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_request_portal_login_uses_the_same_email_authentication(self):
        response = self.client.post(
            "/login/",
            {"username": self.user.email.upper(), "password": "ValidPass!928"},
            HTTP_HOST="requests.rplwms.com",
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    @override_settings(
        AXES_ENABLED=True,
        AUTHENTICATION_BACKENDS=[
            "axes.backends.AxesStandaloneBackend",
            "django.contrib.auth.backends.ModelBackend",
        ],
    )
    def test_axes_records_normalized_email_failure_under_canonical_username(self):
        response = self.client.post(
            reverse("login"),
            {"username": self.user.email.upper(), "password": "wrong-password"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            AccessAttempt.objects.filter(username=self.user.username).exists()
        )
        self.assertFalse(
            AccessAttempt.objects.filter(username__iexact=self.user.email).exists()
        )


class AdministratorPasswordResetTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="reset-admin",
            email="reset-admin@blackbox.com",
            password="AdminPass!928",
        )
        self.target = User.objects.create_user(
            username="SWhitmore",
            email="scott.whitmore@blackbox.com",
            password="OldPass!928",
            is_active=True,
        )
        self.client.force_login(self.admin)

    def _attempt(self, identifier):
        return AccessAttempt.objects.create(
            username=identifier,
            ip_address="127.0.0.1",
            user_agent="test-browser",
            http_accept="text/html",
            path_info="/accounts/login/",
            attempt_time=self.target.date_joined,
            get_data="",
            post_data="",
            failures_since_start=4,
        )

    def _post_reset(self, password, confirmation):
        return self.client.post(
            reverse("user_edit", args=[self.target.pk]),
            {
                "username": self.target.username,
                "first_name": self.target.first_name,
                "last_name": self.target.last_name,
                "email": self.target.email,
                "is_active": "on",
                "password": password,
                "password_confirm": confirmation,
            },
        )

    def test_admin_reset_requires_matching_confirmation_and_preserves_old_password_on_error(self):
        response = self._post_reset("NewValidPass!928", "different")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "passwords do not match", status_code=200)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password("OldPass!928"))

    def test_admin_reset_changes_password_and_clears_username_and_email_attempts(self):
        self._attempt(self.target.username)
        self._attempt(self.target.email)

        response = self._post_reset("NewValidPass!928", "NewValidPass!928")

        self.assertRedirects(response, reverse("user_management"))
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password("NewValidPass!928"))
        self.assertFalse(
            AccessAttempt.objects.filter(
                username__in=[self.target.username, self.target.email]
            ).exists()
        )

    def test_delegated_user_manager_cannot_reset_superuser_password(self):
        manager = User.objects.create_user(
            username="delegated-manager",
            password="ManagerPass!928",
        )
        manager.user_permissions.add(
            Permission.objects.get(codename="manage_users")
        )
        self.client.force_login(manager)

        response = self.client.post(
            reverse("user_edit", args=[self.admin.pk]),
            {
                "username": self.admin.username,
                "email": self.admin.email,
                "is_active": "on",
                "is_staff": "on",
                "is_superuser": "on",
                "password": "TakeoverPass!928",
                "password_confirm": "TakeoverPass!928",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password("AdminPass!928"))
