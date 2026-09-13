import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


TEMPLATE_ROOT = Path(__file__).resolve().parent / "templates"
APP_CSS = Path(__file__).resolve().parent / "static" / "inventory" / "css" / "app.css"
SEMANTIC_BUTTON_CLASSES = {
    "btn-primary",
    "btn-secondary",
    "btn-success",
    "btn-warning",
    "btn-danger",
    "btn-info",
}
PRINT_TEMPLATES = {
    "location_qr_print.html",
    "ticket_print.html",
    "item_barcode_print.html",
    "receiving_ticket_print.html",
}


class UIConsistencyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="ui-auditor",
            email="ui@example.com",
            password="test-password",
        )
        self.client.force_login(self.user)

    def test_shared_styles_define_every_semantic_button_role(self):
        css = APP_CSS.read_text()
        for class_name in sorted(SEMANTIC_BUTTON_CLASSES):
            with self.subTest(class_name=class_name):
                self.assertIn(f".{class_name}", css)
        self.assertIn("--btn-primary", css)
        self.assertIn("--btn-success", css)
        self.assertIn("--btn-warning", css)
        self.assertIn("--btn-danger", css)
        self.assertIn("--btn-info", css)

    def test_global_script_assigns_one_semantic_role_to_every_standard_button(self):
        script = (
            Path(__file__).resolve().parent
            / "static"
            / "inventory"
            / "js"
            / "app.js"
        ).read_text()
        self.assertIn("document.querySelectorAll('.btn')", script)
        self.assertIn("SEMANTIC_BUTTON_CLASSES", script)
        self.assertIn("assignSemanticButton", script)
        for class_name in sorted(SEMANTIC_BUTTON_CLASSES):
            with self.subTest(class_name=class_name):
                self.assertIn(f"'{class_name}'", script)

    def test_dashboard_has_distinct_pick_ticket_list_button(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'href="{reverse("ticket_list")}" class="btn btn-secondary"',
            html=False,
        )
        self.assertContains(response, ">Pick Tickets</a>", html=False)

    def test_known_wide_tables_use_responsive_containers(self):
        for name in ("ticket_detail.html", "ticket_list.html", "low_stock_list.html", "item_detail.html"):
            with self.subTest(template=name):
                text = (TEMPLATE_ROOT / "inventory" / name).read_text()
                self.assertGreaterEqual(text.count('class="table-responsive"'), 1)

    def test_mobile_overflow_safeguards_are_shared(self):
        css = APP_CSS.read_text()
        required_rules = (
            "overflow-wrap:anywhere",
            ".flex-wrap",
            ".mobile-inventory-card:not(:has(.mobile-item-checkbox))",
            "max-width:100%",
            "min-width:0",
        )
        for rule in required_rules:
            with self.subTest(rule=rule):
                self.assertIn(rule, css)

    def test_inventory_mobile_card_only_reserves_checkbox_column_when_present(self):
        template = (TEMPLATE_ROOT / "inventory" / "inventory_list.html").read_text()
        self.assertIn('class="mobile-card-top{% if not request.is_request_portal and perms.inventory.bulk_adjust_inventory %} has-selection{% endif %}"', template)
        self.assertIn(
            ".mobile-card-top { display: grid; grid-template-columns: minmax(0, 1fr) auto;",
            template,
        )
        self.assertIn(
            ".mobile-card-top.has-selection { grid-template-columns: 24px minmax(0, 1fr) auto; }",
            template,
        )

    def test_inventory_mobile_part_numbers_do_not_wrap(self):
        template = (TEMPLATE_ROOT / "inventory" / "inventory_list.html").read_text()
        self.assertIn(".mobile-item-main .part-link", template)
        self.assertIn("white-space: nowrap", template)
        self.assertIn("text-overflow: ellipsis", template)

    def test_inventory_mobile_buttons_have_uniform_touch_dimensions(self):
        template = (TEMPLATE_ROOT / "inventory" / "inventory_list.html").read_text()
        self.assertIn(".inventory-filter-panel > .actions", template)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr))", template)
        self.assertIn(".mobile-actions .action-chip", template)
        self.assertIn("width: 100%", template)
        self.assertIn("min-height: 44px", template)
