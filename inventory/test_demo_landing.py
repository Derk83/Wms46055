import re

from django.test import TestCase, override_settings

from inventory.templatetags.inventory_extras import demo_url


class DemoLandingHostTests(TestCase):
    host = "demo.rplwms.com"

    def test_demo_host_serves_public_training_center(self):
        with self.assertNumQueries(0):
            response = self.client.get("/", HTTP_HOST=self.host, secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "inventory/demo/landing.html")
        self.assertContains(response, "Demos, guides, and module training")
        self.assertContains(response, "Guided Inventory Demo")
        self.assertContains(response, "fictional session-isolated data")
        hrefs = re.findall(r'<a class="hub-app" href="([^"]+)">', response.content.decode())
        self.assertEqual(len(hrefs), 17)
        self.assertEqual(
            hrefs[:3],
            [
                "https://demo.rplwms.com/inventory-demo/",
                "https://demo.rplwms.com/guides/material-requests/",
                "https://demo.rplwms.com/guides/equipment-requests/",
            ],
        )
        self.assertContains(response, "No sign-in required")

    def test_demo_landing_supports_head_but_rejects_post(self):
        response = self.client.head("/", HTTP_HOST=self.host, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.assertEqual(
            self.client.post("/", HTTP_HOST=self.host, secure=True).status_code,
            405,
        )

    def test_demo_host_does_not_expose_operational_or_demo_action_routes(self):
        for path in (
            "/admin/",
            "/accounts/login/",
            "/inventory/",
            "/material-requests/",
            "/demo/",
            "/demo/action/",
        ):
            with self.subTest(path=path):
                response = self.client.get(path, HTTP_HOST=self.host, secure=True)
                self.assertEqual(response.status_code, 404)

    def test_warehouse_navigation_points_to_central_demo_landing(self):
        source = open("inventory/templates/inventory/base.html", encoding="utf-8").read()
        self.assertEqual(source.count('href="{% demo_url %}"'), 2)
        self.assertNotIn("{% url 'inventory_demo' %}\">Guided Demo", source)

    @override_settings(DEMO_URL="https://training.example.test")
    def test_demo_navigation_uses_configured_canonical_url(self):
        self.assertEqual(demo_url(), "https://training.example.test")
