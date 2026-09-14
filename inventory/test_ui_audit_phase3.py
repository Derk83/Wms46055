"""Phase 3 UI audit contracts for responsive and accessible mobile workflows."""
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from .models import CycleCount, InventoryItem, PickTicket


User = get_user_model()
ROOT = Path(__file__).parent
TEMPLATES = ROOT / "templates" / "inventory"
APP_CSS = ROOT / "static" / "inventory" / "css" / "app.css"
APP_JS = ROOT / "static" / "inventory" / "js" / "app.js"


def _template(name):
    return (TEMPLATES / name).read_text()


def test_material_request_lines_use_one_responsive_formset_representation():
    source = _template("material_request_form.html")
    script = (ROOT / "static" / "inventory" / "js" / "material-request-form.js").read_text()
    assert 'class="table-responsive request-lines-table"' not in source
    assert 'class="request-lines"' in source
    assert source.count('id="request-lines-body"') == 1
    assert source.count('id="empty-request-line"') == 1
    for class_name in ("request-line-item", "request-line-quantity", "request-line-notes", "request-line-remove"):
        assert class_name in source
    assert "{{ formset.management_form }}" in source
    assert "{{ line_form.DELETE }}" in source
    assert "{{ formset.empty_form.DELETE }}" in source
    for behavior in ("id_lines-TOTAL_FORMS", "emptyTemplate.innerHTML.replaceAll", 'closest(".request-line")', "deleted.checked = true"):
        assert behavior in script


@pytest.mark.django_db
@pytest.mark.parametrize("host", ["bbx.rplwms.com", "requests.rplwms.com"])
def test_material_request_formset_names_are_identical_on_both_hosts(client, host):
    admin = User.objects.create_superuser(f"phase3-form-{host}", "", "pw")
    client.force_login(admin)
    response = client.get(reverse("material_request_create"), HTTP_HOST=host)
    assert response.status_code == 200
    body = response.content.decode()
    for name in ("lines-TOTAL_FORMS", "lines-INITIAL_FORMS", "lines-0-item", "lines-0-quantity", "lines-0-notes", "lines-0-DELETE"):
        assert f'name="{name}"' in body


def test_shared_css_stacks_request_lines_without_page_overflow_at_phone_widths():
    css = APP_CSS.read_text()
    assert ".request-line{display:grid" in css
    assert "grid-template-areas:\"item quantity remove\" \"notes notes notes\"" in css
    assert "@media(max-width:600px)" in css
    assert "grid-template-areas:\"item item\" \"quantity remove\" \"notes notes\"" in css
    assert ".request-line-item select{min-width:0" in css
    assert ".request-line[hidden]{display:none}" in css


def test_ticket_and_cycle_count_tables_transform_to_single_mobile_card_dom():
    for name, table_class in (("ticket_list.html", "ticket-records"), ("cycle_count_list.html", "cycle-count-records")):
        source = _template(name)
        assert f'class="responsive-records {table_class}"' in source
        assert "responsive-record-card" in source
        assert "data-label=" in source
        assert "mobile-card-list" not in source
    css = APP_CSS.read_text()
    assert "@media(max-width:760px)" in css
    assert ".responsive-records .responsive-record-card" in css
    assert ".responsive-records td::before" in css


@pytest.mark.django_db
def test_mobile_record_transform_preserves_routes_permissions_status_and_empty_state(client):
    admin = User.objects.create_superuser("phase3-admin", "", "pw")
    item = InventoryItem.objects.create(part_number="P3-ITEM", name="Phase 3 item", quantity_on_hand=2)
    ticket = PickTicket.objects.create(created_by=admin, requested_by_name="Requester")
    count = CycleCount.objects.create(name="Phase 3 count", created_by=admin)
    client.force_login(admin)

    tickets = client.get(reverse("ticket_list"), HTTP_HOST="bbx.rplwms.com").content.decode()
    assert reverse("ticket_detail", args=[ticket.pk]) in tickets
    assert reverse("ticket_edit", args=[ticket.pk]) in tickets
    assert reverse("ticket_print", args=[ticket.pk]) in tickets
    assert ticket.get_status_display() in tickets

    counts = client.get(reverse("cycle_count_list"), HTTP_HOST="bbx.rplwms.com").content.decode()
    assert reverse("cycle_count_detail", args=[count.pk]) in counts
    assert reverse("cycle_count_pdf", args=[count.pk]) in counts
    assert count.get_status_display() in counts

    ticket.delete()
    empty = client.get(reverse("ticket_list"), HTTP_HOST="bbx.rplwms.com").content.decode()
    assert "No pick tickets yet." in empty
    assert 'class="responsive-record-empty"' in empty


def test_mobile_controls_have_practical_touch_targets():
    css = APP_CSS.read_text()
    assert 'input[type="checkbox"],input[type="radio"]{min-width:40px;min-height:40px}' in css
    assert ".responsive-records .btn" in css
    assert "min-height:40px" in css
    assert ".remove-request-line" in css


def test_mobile_drawer_is_modal_traps_focus_and_restores_page_state():
    base = _template("base.html")
    script = APP_JS.read_text()
    assert "data-mobile-nav-panel" in base
    for contract in (
        "pageRegions", ".inert = true", ".inert = false", "aria-hidden",
        "setAttribute('role', 'dialog')", "setAttribute('aria-modal', 'true')",
        "focusableNavItems", "event.shiftKey", "event.preventDefault()",
        "event.key === 'Tab'", "navButton?.focus()",
    ):
        assert contract in script


def test_dashboard_mobile_metric_visibility_is_semantic_and_focus_is_explicit():
    dashboard = _template("dashboard.html")
    assert ".dashboard-stat-link:nth-child" not in dashboard
    assert dashboard.count('class="stat dashboard-stat-link accent dashboard-mobile-hide"') == 1
    assert dashboard.count('class="stat dashboard-stat-link danger dashboard-mobile-hide"') == 1
    assert ".dashboard-stat-link:focus-visible" in dashboard
    assert ".quick-action-btn:focus-visible" in dashboard
    assert "box-shadow:var(--focus)!important" in dashboard
    # Every intentionally retained phone shortcut has an explicit Django route.
    mobile_nav = dashboard.split('</nav>', 1)[0]
    assert mobile_nav.count("<a href=\"{% url ") == mobile_nav.count("<a ")


def test_item_history_scroller_is_keyboard_focusable_and_labeled():
    source = _template("item_transaction_history.html")
    assert 'class="table-responsive item-history-scroller"' in source
    assert 'tabindex="0"' in source
    assert 'role="region"' in source
    assert 'aria-label="Transaction history table; scroll horizontally for more columns"' in source
