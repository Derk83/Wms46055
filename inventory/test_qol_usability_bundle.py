from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import InventoryItem


class QolUsabilityBundleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="qol-admin", email="qol@example.com", password="test-only-password"
        )
        self.client.force_login(self.user)
        self.complete = InventoryItem.objects.create(
            part_number="QOL-001",
            fb_part_number="FB-QOL-001",
            model_number="MODEL-1",
            name="Complete item",
            quantity_on_hand=8,
            rack="A",
            section="01",
            bin_location="01",
            barcode_value="BAR-QOL-001",
            qr_code_value="QR-QOL-001",
        )
        self.missing = InventoryItem.objects.create(
            part_number="QOL-002",
            name="Missing data item",
            quantity_on_hand=0,
        )
        for number in range(3, 31):
            InventoryItem.objects.create(
                part_number=f"QOL-{number:03d}",
                name=f"Item {number}",
                quantity_on_hand=number,
            )

    def test_data_quality_quick_filters_and_result_summary(self):
        response = self.client.get(reverse("inventory_list"), {"data_quality": "missing_fb"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["result_count"], 29)
        self.assertEqual(response.context["active_filters"], ["Missing FB Part #"])
        self.assertContains(response, "29</strong> matching items", html=False)
        self.assertContains(response, "Missing Location")
        self.assertNotContains(response, "FB-QOL-001")

    def test_missing_location_includes_incomplete_location_tuple(self):
        partial = InventoryItem.objects.create(
            part_number="QOL-PARTIAL-LOCATION",
            name="Incomplete location",
            rack="A",
        )

        response = self.client.get(
            reverse("inventory_list"), {"data_quality": "missing_location"}
        )
        item_ids = set(response.context["items"].values_list("pk", flat=True))

        self.assertIn(partial.pk, item_ids)
        self.assertIn(self.missing.pk, item_ids)
        self.assertNotIn(self.complete.pk, item_ids)

    def test_mobile_cards_expose_column_preference_hooks(self):
        response = self.client.get(reverse("inventory_list"))

        self.assertContains(response, 'class="name-link" data-column="name"')
        self.assertContains(response, 'data-label="FB Part #" data-column="fb"')
        self.assertContains(response, 'data-label="Bin" data-column="bin"')
        self.assertContains(response, 'inventory-action-group" data-column="actions"')

    def test_rows_per_page_paginates_and_preserves_query(self):
        response = self.client.get(
            reverse("inventory_list"),
            {"rows": "25", "stock": "", "sort": "part", "page": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["items"]), 25)
        self.assertEqual(response.context["page_obj"].paginator.num_pages, 2)
        self.assertContains(response, "rows=25")
        self.assertContains(response, "sort=part")
        self.assertContains(response, "page=2")

    def test_invalid_rows_and_quality_fall_back_safely(self):
        response = self.client.get(
            reverse("inventory_list"), {"rows": "50000", "data_quality": "unknown"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["rows"], "all")
        self.assertEqual(response.context["data_quality"], "")
        self.assertIsNone(response.context["page_obj"])
        self.assertEqual(response.context["result_count"], 30)

    def test_item_detail_has_copy_and_filter_aware_navigation_hooks(self):
        return_to = "/inventory/?stock=zero&sort=qty"
        response = self.client.get(
            reverse("item_detail", args=[self.complete.pk]), {"return_to": return_to}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["return_to"], return_to)
        self.assertContains(response, 'data-current-item="')
        self.assertContains(response, 'data-list-back')
        self.assertContains(response, 'href="/inventory/?stock=zero&amp;sort=qty"')
        self.assertContains(response, 'data-previous-item')
        self.assertContains(response, 'data-copy-text="FB-QOL-001"')
        self.assertContains(response, 'data-copy-text="BAR-QOL-001"')

    def test_item_detail_rejects_external_return_url(self):
        response = self.client.get(
            reverse("item_detail", args=[self.complete.pk]),
            {"return_to": "//evil.example/steal"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["return_to"], reverse("inventory_list"))
        self.assertNotContains(response, "evil.example")

    def test_shared_assets_include_accessible_qol_behaviors(self):
        root = Path(__file__).resolve().parent
        script = (root / "static/inventory/js/app.js").read_text()
        css = (root / "static/inventory/css/app.css").read_text()

        self.assertIn("sessionStorage.setItem(stateKey", script)
        self.assertIn("navigator.clipboard.writeText", script)
        self.assertIn("event.key === '/'", script)
        self.assertIn("clearableSearch", script)
        self.assertIn("window.addEventListener('pageshow'", script)
        self.assertIn("event.persisted", script)
        self.assertIn("form.dataset.submitting", script)
        self.assertIn("data-delete-item", script)
        self.assertIn(".inventory-result-summary", css)
        self.assertIn(".mobile-secondary-actions", css)
        self.assertIn("min-height:44px", css)
