import re

from django.test import SimpleTestCase


class HubHostTests(SimpleTestCase):
    hub_hosts = ("rplwms.com", "www.rplwms.com")

    def test_root_domain_serves_public_application_hub(self):
        response = self.client.get("/", HTTP_HOST="rplwms.com", secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "inventory/hub.html")
        self.assertContains(response, "Choose your workspace.")
        self.assertContains(response, "Access stays separated by application.")
        hrefs = re.findall(r'<a class="hub-app" href="([^"]+)">', response.content.decode())
        self.assertEqual(
            hrefs,
            [
                "https://bbx.rplwms.com",
                "https://requests.rplwms.com",
                "https://equipment.rplwms.com",
            ],
        )

    def test_www_domain_redirects_to_canonical_hub(self):
        response = self.client.get("/", HTTP_HOST="www.rplwms.com", secure=True)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "https://rplwms.com/")

    def test_hub_supports_head_requests(self):
        response = self.client.head("/", HTTP_HOST="rplwms.com", secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")

    def test_hub_hosts_do_not_expose_application_routes(self):
        protected_paths = (
            "/admin/",
            "/accounts/login/",
            "/inventory/",
            "/material-requests/",
            "/assets/",
        )
        for host in self.hub_hosts:
            for path in protected_paths:
                with self.subTest(host=host, path=path):
                    response = self.client.get(path, HTTP_HOST=host, secure=True)
                    self.assertEqual(response.status_code, 404)

    def test_warehouse_host_keeps_existing_route_boundary(self):
        response = self.client.get("/", HTTP_HOST="bbx.rplwms.com", secure=True)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])
