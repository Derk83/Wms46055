"""Phase 5 design-system maintenance and dead-code contracts."""
import ast
import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse


ROOT = Path(__file__).parent
TEMPLATES = ROOT / "templates" / "inventory"
APP_CSS = ROOT / "static" / "inventory" / "css" / "app.css"
APP_JS = ROOT / "static" / "inventory" / "js" / "app.js"
VIEWS = ROOT / "views.py"

STANDARD_SCREENS = (
    "change_password.html",
    "group_permissions.html",
    "inventory_bulk_edit.html",
    "inventory_form.html",
    "item_transaction_history.html",
    "low_stock_list.html",
    "receive_stock.html",
    "settings.html",
    "ticket_confirm_delete.html",
    "ticket_detail.html",
    "ticket_form.html",
    "ticket_list.html",
    "transaction_history.html",
    "user_confirm_delete.html",
    "user_edit_merged.html",
)

MIGRATED_BUTTON_SCREENS = STANDARD_SCREENS + (
    "inventory_list.html",
    "item_images.html",
    "item_documents.html",
    "item_barcode_print.html",
    "item_upc_assign.html",
    "location_detail.html",
    "location_manage.html",
    "qr_codes.html",
)


def _template(name):
    return (TEMPLATES / name).read_text()


def test_reachable_normal_screens_have_one_page_heading_and_standard_header():
    for name in STANDARD_SCREENS:
        source = _template(name)
        assert source.count("<h1") == 1, name
        assert 'class="page-header' in source, name


def test_migrated_screen_buttons_are_server_rendered_with_native_semantics():
    bootstrap_only = re.compile(r"\bbtn-outline-(?:primary|secondary|success|warning|danger|info)\b")
    semantic = re.compile(r"\bbtn-(?:primary|secondary|success|warning|danger|info)\b")
    legacy_tokens = {"primary", "secondary", "light", "accent", "danger", "success", "warning", "warn", "info"}
    for name in MIGRATED_BUTTON_SCREENS:
        source = _template(name)
        assert not bootstrap_only.search(source), name
        for classes in re.findall(r'class="([^"]*\bbtn\b[^"]*)"', source):
            assert not (legacy_tokens & set(classes.split())), f"{name}: {classes}"
            assert semantic.search(classes), f"{name}: {classes}"


def test_shared_form_permission_and_empty_state_css_lives_in_app_stylesheet():
    css = APP_CSS.read_text()
    for selector in (".form-actions", ".form-label", ".form-text", ".permission-grid", ".permission-set", ".permission-chip", ".empty-state"):
        assert selector in css
    for name in ("change_password.html", "group_permissions.html", "inventory_bulk_edit.html", "user_confirm_delete.html", "receive_stock.html"):
        assert "<style>" not in _template(name), name


def test_inventory_and_ticket_scanners_share_stable_success_beep_helper():
    app_js = APP_JS.read_text()
    assert "window.WMSScanner" in app_js
    assert "successBeep" in app_js
    for name in ("inventory_list.html", "ticket_form.html"):
        source = _template(name)
        assert "WMSScanner.successBeep()" in source, name
        assert "createOscillator" not in source, name
        assert "new AudioContext" not in source, name


def test_duplicate_views_are_consolidated_and_unrouted_approval_ui_is_removed():
    tree = ast.parse(VIEWS.read_text())
    names = [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert names.count("receiving_ticket_delete") == 1
    assert names.count("export_inventory_xlsx") == 1
    assert "approval_queue" not in names
    assert "approval_request_review" not in names
    assert not (TEMPLATES / "approval_queue.html").exists()
    assert not (TEMPLATES / "groups.html").exists()
    assert not (TEMPLATES / "user_form.html").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("route_name", ("settings", "ticket_list", "transaction_history"))
def test_representative_live_screens_render_standard_heading_and_semantic_actions(client, route_name):
    actor = get_user_model().objects.create_superuser(f"phase5-{route_name}", "", "pw")
    client.force_login(actor)
    response = client.get(reverse(route_name), HTTP_HOST="bbx.rplwms.com")
    assert response.status_code == 200
    body = response.content.decode()
    assert body.count("<h1") == 1
    assert 'class="page-header' in body
    main = body.split("<main", 1)[1].split("</main>", 1)[0]
    button_classes = re.findall(r'class="([^"]*\bbtn\b[^"]*)"', main)
    assert button_classes
    assert all(re.search(r"\bbtn-(?:primary|secondary|success|warning|danger|info)\b", classes) for classes in button_classes)
