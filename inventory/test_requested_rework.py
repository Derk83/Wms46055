from datetime import datetime
import re

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import Resolver404, resolve, reverse

from .forms import MaterialRequestForm
from .models import CategoryChoices, InventoryItem, PickTicket, PickTicketLine


class RequestedMaterialRequestChangesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("requested-changes-admin", "", "pw")
        self.client = Client(HTTP_HOST="bbx.rplwms.com")
        self.client.force_login(self.user)

    def test_delivery_date_and_time_is_required_and_uses_quarter_hour_select(self):
        missing = MaterialRequestForm({
            "requestor_name": "", "requestor_email": "", "building_room": "", "location": "", "notes": "",
            "delivery_at_0": "", "delivery_at_1": "",
        })
        off_slot = MaterialRequestForm({
            "requestor_name": "Derek", "requestor_email": "derek@example.com",
            "building_room": "", "location": "Job Site A", "notes": "",
            "delivery_at_0": "2026-09-15", "delivery_at_1": "10:07",
        })
        valid = MaterialRequestForm({
            "requestor_name": "Derek", "requestor_email": "derek@example.com",
            "building_room": "", "location": "Job Site A", "notes": "",
            "delivery_at_0": "2026-09-15", "delivery_at_1": "10:15",
        })

        self.assertFalse(missing.is_valid())
        self.assertIn("required", str(missing.errors).lower())
        self.assertFalse(off_slot.is_valid())
        self.assertTrue(valid.is_valid(), valid.errors)
        self.assertEqual(valid.cleaned_data["delivery_at"].minute, 15)
        html = str(MaterialRequestForm()["delivery_at"])
        self.assertIn('type="date"', html)
        self.assertIn('<option value="10:15">10:15 AM</option>', html)
        self.assertNotIn('value="10:07"', html)

    def test_requestor_name_email_and_location_are_required(self):
        """Requestor name, requestor email, and location are required so the
        warehouse always knows who is asking and where to deliver."""
        # Each individual missing field should produce a required error.
        missing_name = MaterialRequestForm({
            "requestor_name": "", "requestor_email": "derek@example.com",
            "building_room": "", "location": "Job Site A", "notes": "",
            "delivery_at_0": "2026-09-15", "delivery_at_1": "10:15",
        })
        self.assertFalse(missing_name.is_valid())
        self.assertIn("requestor_name", missing_name.errors)

        missing_email = MaterialRequestForm({
            "requestor_name": "Derek", "requestor_email": "",
            "building_room": "", "location": "Job Site A", "notes": "",
            "delivery_at_0": "2026-09-15", "delivery_at_1": "10:15",
        })
        self.assertFalse(missing_email.is_valid())
        self.assertIn("requestor_email", missing_email.errors)

        missing_location = MaterialRequestForm({
            "requestor_name": "Derek", "requestor_email": "derek@example.com",
            "building_room": "", "location": "", "notes": "",
            "delivery_at_0": "2026-09-15", "delivery_at_1": "10:15",
        })
        self.assertFalse(missing_location.is_valid())
        self.assertIn("location", missing_location.errors)

        # All present → valid
        fully_populated = MaterialRequestForm({
            "requestor_name": "Derek", "requestor_email": "derek@example.com",
            "building_room": "", "location": "Job Site A", "notes": "",
            "delivery_at_0": "2026-09-15", "delivery_at_1": "10:15",
        })
        self.assertTrue(fully_populated.is_valid(), fully_populated.errors)

    def test_material_request_form_marks_required_fields_in_template(self):
        """Regression: the form template must show a 'Required' badge on the
        labels for requestor_name, requestor_email, and location."""
        InventoryItem.objects.create(part_number="C-1", name="Test item", category=CategoryChoices.CFCI, quantity_on_hand=1)
        response = self.client.get(reverse("material_request_create"), secure=True)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")

        # Each required field label should be immediately followed by a Required chip.
        for label_for in ("id_requestor_name", "id_requestor_email", "id_location"):
            # Find the <label for="...">...</label> and assert it contains the Required chip
            pattern = rf'<label[^>]*for="{label_for}"[^>]*>.*?</label>'
            m = re.search(pattern, body, re.DOTALL)
            self.assertIsNotNone(m, f"Missing label for {label_for}")
            self.assertIn('class="required-label"', m.group(0),
                          f"Required label chip missing for {label_for}")
            self.assertNotIn("optional-label", m.group(0),
                             f"Optional chip should NOT appear on {label_for}")

        # Notes should remain Optional
        notes_match = re.search(r'<label[^>]*for="id_notes"[^>]*>.*?</label>', body, re.DOTALL)
        self.assertIsNotNone(notes_match, "Notes label not found")
        self.assertIn('class="optional-label"', notes_match.group(0))

    def test_material_request_inventory_has_full_screen_picker_and_category_sections(self):
        InventoryItem.objects.create(part_number="O-1", name="OFCI item", category=CategoryChoices.OFCI, quantity_on_hand=1)
        InventoryItem.objects.create(part_number="C-1", name="CFCI item", category=CategoryChoices.CFCI, quantity_on_hand=1)

        response = self.client.get(reverse("material_request_create"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="inventory-browser-dialog"', html=False)
        self.assertContains(response, "Browse full inventory")
        for value, label in CategoryChoices.choices:
            self.assertContains(response, f'data-category-filter="{value}"', html=False)
        self.assertContains(response, 'data-category="OFCI"', html=False)
        self.assertContains(response, 'data-category="CFCI"', html=False)


class RequestedPrintLayoutTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("print-layout-admin", "", "pw")
        self.client.force_login(self.user)

    def test_item_rows_fill_the_page_before_starting_another_page(self):
        ticket = PickTicket.objects.create(
            picked_by_name="Picker", received_by_name="Receiver", requested_by_name="Requester",
            building_room="B1", location="Dock", created_by=self.user,
        )
        for index in range(20):
            item = InventoryItem.objects.create(part_number=f"PAGE-{index}", name=f"Page item {index}", quantity_on_hand=10)
            PickTicketLine.objects.create(ticket=ticket, item=item, quantity=1)

        response = self.client.get(reverse("ticket_print", args=[ticket.pk]))
        html = response.content.decode()

        self.assertEqual(html.count('class="print-page"'), 1)
        self.assertEqual(html.count("DELIVERY / RECEIPT CONFIRMATION"), 1)
        self.assertIn(".receipt{position:absolute", html)
        self.assertIn("Page 1 of 1", html)
        self.assertEqual(html.count('class="page-number"'), 1)
        self.assertGreater(html.rfind("DELIVERY / RECEIPT CONFIRMATION"), html.rfind("Page item 19"))

        item = InventoryItem.objects.create(part_number="PAGE-20", name="Page item 20", quantity_on_hand=10)
        PickTicketLine.objects.create(ticket=ticket, item=item, quantity=1)
        overflow = self.client.get(reverse("ticket_print", args=[ticket.pk])).content.decode()
        self.assertEqual(overflow.count('class="print-page"'), 2)
        self.assertIn("Page 1 of 2", overflow)
        self.assertIn("Page 2 of 2", overflow)
        self.assertEqual(overflow.count("DELIVERY / RECEIPT CONFIRMATION"), 1)
        first_page, second_page = overflow.split('class="print-page"')[1:]
        self.assertIn("Page item 19", first_page)
        self.assertNotIn("Page item 20", first_page)
        self.assertNotIn("DELIVERY / RECEIPT CONFIRMATION", first_page)
        self.assertIn("Page item 20", second_page)
        self.assertIn("DELIVERY / RECEIPT CONFIRMATION", second_page)


class RequestedLocationAndNavigationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("location-fix-admin", "", "pw")
        self.client.force_login(self.user)

    def test_locations_page_ignores_incomplete_storage_tuples(self):
        InventoryItem.objects.create(part_number="PARTIAL", name="Partial", rack="", section="", bin_location="", quantity_on_hand=1)
        InventoryItem.objects.create(part_number="PARTIAL-2", name="Partial two", rack="A", section="", bin_location="", quantity_on_hand=1)
        InventoryItem.objects.create(part_number="FULL", name="Full", rack="A", section="1", bin_location="2", quantity_on_hand=1)

        response = self.client.get(reverse("location_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A-01-02")
        self.assertNotContains(response, 'location_key=""', html=False)

    def test_selected_section_prints_all_six_bins_on_one_page_in_order(self):
        InventoryItem.objects.create(
            part_number="QR-ONLY-BIN-3", name="Only occupied bin",
            rack="A", section="1", bin_location="3", quantity_on_hand=1,
        )

        response = self.client.get(reverse("location_qr_print"), {"section": "A-01"})
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(html.count('class="qr-print-page"'), 1)
        self.assertEqual(html.count('class="location-label"'), 6)
        self.assertEqual(html.count("SCAN ME"), 6)
        self.assertEqual(html.count("BIN LOCATION"), 6)
        for bin_number in range(1, 7):
            self.assertIn(f"A-01-{bin_number:02d}", html)
        self.assertLess(html.index("A-01-01"), html.index("A-01-06"))
        self.assertNotIn("currently assigned", html)
        self.assertNotIn("https://bbx.rplwms.com/locations/", html)

    def test_multiple_sections_keep_each_six_bin_section_on_its_own_page(self):
        response = self.client.get(
            reverse("location_qr_print"),
            {"section": ["A-01", "A-02"]},
        )
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(html.count('class="qr-print-page"'), 2)
        self.assertEqual(html.count('class="location-label"'), 12)
        first_page, second_page = html.split('class="qr-print-page"')[1:]
        self.assertIn("A-01-01", first_page)
        self.assertIn("A-01-06", first_page)
        self.assertNotIn("A-02-01", first_page)
        self.assertIn("A-02-01", second_page)
        self.assertIn("A-02-06", second_page)

    def test_location_list_selects_complete_six_bin_sections(self):
        InventoryItem.objects.create(
            part_number="QR-CONTROL", name="QR control", rack="A", section="1", bin_location="2", quantity_on_hand=1,
        )
        response = self.client.get(reverse("location_list"))
        self.assertContains(response, 'id="location-print-form"', html=False)
        self.assertContains(response, 'name="section"', html=False)
        self.assertContains(response, 'value="A-01"', html=False)
        self.assertContains(response, "A-01-01 through A-01-06")
        self.assertContains(response, "Print selected sections")

    def test_dashboard_places_quick_actions_before_detail_sections_and_links_metrics(self):
        response = self.client.get(reverse("dashboard"))
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertLess(html.index('id="dashboard-quick-actions"'), html.index('id="dashboard-detail-sections"'))
        self.assertEqual(html.count('class="stat dashboard-stat-link'), 7)
        self.assertContains(response, "Access requests")
        self.assertContains(response, f'href="{reverse("inventory_list")}"', html=False)
        self.assertContains(response, f'href="{reverse("ticket_list")}"', html=False)
        self.assertContains(response, 'class="mobile-dashboard-nav"', html=False)
        self.assertContains(response, 'aria-label="Mobile warehouse shortcuts"', html=False)
        self.assertIn(".dashboard-mobile-secondary", html)
        self.assertNotContains(response, '<div class="page-header">', html=False)
        self.assertNotContains(response, 'class="actions dashboard-header-actions"', html=False)
        self.assertNotContains(response, "New Pick Ticket")
        # Restore the full permission-aware operational Quick Actions set.
        self.assertEqual(html.count('class="quick-action-btn'), 7)

    def test_mobile_navigation_has_accessible_panel_controls(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, 'data-mobile-nav-toggle', html=False)
        self.assertContains(response, 'data-mobile-nav-label', html=False)
        self.assertContains(response, 'data-mobile-nav-panel', html=False)
        self.assertContains(response, 'data-mobile-nav-close', html=False)
        self.assertContains(response, 'data-mobile-nav-backdrop', html=False)
        self.assertContains(response, 'class="mobile-nav-account"', html=False)
        self.assertContains(response, 'aria-expanded="false"', html=False)

    def test_inventory_locations_are_normalized_to_two_digit_segments(self):
        item = InventoryItem.objects.create(
            part_number="PADDED", name="Padded", rack="a", section="1", bin_location="2", quantity_on_hand=1,
        )
        self.assertEqual((item.rack, item.section, item.bin_location), ("A", "01", "02"))
        self.assertEqual(item.storage_location, "A-01-02")

    def test_approval_queue_is_not_routable_or_shown_in_navigation(self):
        with self.assertRaises(Resolver404):
            resolve("/approvals/")
        response = self.client.get(reverse("dashboard"))
        self.assertNotContains(response, "Approval Queue")
        self.assertNotContains(response, ">Approvals<", html=False)
