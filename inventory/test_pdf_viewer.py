"""Tests for the PDF iframe wrapper (viewer).

The viewer exists because iPad Safari traps users inside its native PDF
viewer with no visible back gesture. The viewer wraps the same PDF inside
an HTML chrome bar so the back button is always present.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings
from django.urls import reverse

User = get_user_model()


def _make_client(username):
    user = User.objects.get(username=username)
    user.failed_login_attempts = 0
    user.account_locked_until = None
    user.save()
    client = Client()
    client.force_login(user)
    return client


@override_settings(ALLOWED_HOSTS=["testserver", "bbx.rplwms.com", "requests.rplwms.com"])
class ReportsPdfViewerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = User.objects.create_user(
            username="viewer-mgr",
            password="x",
            is_staff=True,
        )
        for grp_name in ("Logistics Manager", "Sr. Logistics Manager", "Procurement Manager"):
            cls.manager.groups.add(Group.objects.get_or_create(name=grp_name)[0])
        cls.specialist = User.objects.create_user(
            username="viewer-spec",
            password="x",
        )
        cls.specialist.groups.add(Group.objects.get_or_create(name="Logistics Specialist")[0])

    def test_viewer_renders_chrome_and_iframe(self):
        client = _make_client("viewer-mgr")
        response = client.get(reverse("reports_pdf_viewer", args=["daily"]))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # Back button is always present so iPad users can escape
        self.assertIn('class="btn secondary pdf-viewer-back"', body)
        self.assertIn("Back to Daily Report", body)
        # Iframe is the PDF and has a sensible title
        self.assertIn('class="pdf-viewer-frame"', body)
        self.assertIn('title="Daily Activity Report PDF"', body)
        # iframe src points to the existing PDF endpoint
        self.assertIn(reverse("reports_pdf", args=["daily"]), body)
        # Secondary actions are exposed
        self.assertIn("Download", body)
        self.assertIn("Print", body)
        self.assertIn("Open in new tab", body)
        # No-print class on the chrome bar so print preview hides it
        self.assertIn("pdf-viewer-chrome no-print", body)

    def test_viewer_preserves_date_query(self):
        client = _make_client("viewer-mgr")
        response = client.get(reverse("reports_pdf_viewer", args=["daily"]) + "?date=2026-09-01")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("date=2026-09-01", body)

    def test_viewer_preserves_preset_query(self):
        client = _make_client("viewer-mgr")
        response = client.get(reverse("reports_pdf_viewer", args=["daily"]) + "?preset=yesterday")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("preset=yesterday", body)

    def test_viewer_rejects_unknown_kind(self):
        client = _make_client("viewer-mgr")
        response = client.get(reverse("reports_pdf_viewer", args=["weekly"]))
        self.assertEqual(response.status_code, 404)

    def test_viewer_requires_manager(self):
        client = _make_client("viewer-spec")
        response = client.get(reverse("reports_pdf_viewer", args=["daily"]))
        # Manager-gated decorator should redirect or 403, not 200
        self.assertIn(response.status_code, (302, 403))

    def test_viewer_requires_auth(self):
        response = self.client.get(reverse("reports_pdf_viewer", args=["daily"]))
        self.assertIn(response.status_code, (302, 403))

