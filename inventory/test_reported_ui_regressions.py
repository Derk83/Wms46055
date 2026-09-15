"""Regression contracts for user-reported UI issues after the 2026-09-14 audit."""
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse

from .models import InventoryItem, MaterialRequest, PickTicket, PickTicketLine


User = get_user_model()
ROOT = Path(__file__).parent
APP_CSS = ROOT / "static" / "inventory" / "css" / "app.css"
NOTIFICATION_CSS = ROOT / "static" / "inventory" / "css" / "notification-center.css"
SETTINGS_TEMPLATE = ROOT / "templates" / "inventory" / "settings.html"
DASHBOARD_TEMPLATE = ROOT / "templates" / "inventory" / "dashboard.html"
BULK_ADJUST_TEMPLATE = ROOT / "templates" / "inventory" / "bulk_adjust.html"


def _actor(username, *codenames):
    user = User.objects.create_user(username=username, password="pw")
    user.user_permissions.add(*[
        Permission.objects.get(content_type__app_label="inventory", codename=codename)
        for codename in codenames
    ])
    return user


@pytest.mark.django_db
def test_dashboard_restores_all_seven_permission_aware_quick_actions(client):
    admin = User.objects.create_superuser("reported-actions-admin", "", "pw")
    client.force_login(admin)

    response = client.get(reverse("dashboard"), HTTP_HOST="bbx.rplwms.com")

    assert response.status_code == 200
    html = response.content.decode()
    expected = {
        "Add Item": reverse("inventory_new"),
        "Resume Work": reverse("recently_viewed"),
        "Receive Stock": reverse("receiving"),
        "Bulk Adjust": reverse("batch_adjust"),
        "Transaction History": reverse("transaction_history"),
        "Print Labels": reverse("qr_codes"),
        "Phone Scanner": reverse("scanner"),
    }
    assert html.count('class="quick-action-btn') == 7
    for label, href in expected.items():
        assert label in html
        assert f'href="{href}"' in html


@pytest.mark.django_db
def test_manager_delete_pickticket_routes_to_checked_request_cascade(client):
    actor = User.objects.create_superuser("reported-ticket-delete", "", "pw")
    item = InventoryItem.objects.create(
        part_number="REPORTED-DELETE-1", name="Delete regression item", quantity_on_hand=10,
    )
    ticket = PickTicket.objects.create(created_by=actor)
    PickTicketLine.objects.create(ticket=ticket, item=item, quantity=2)
    material_request = MaterialRequest.objects.create(creator=actor, pick_ticket=ticket)
    client.force_login(actor)

    detail = client.get(reverse("ticket_detail", args=[ticket.pk]), HTTP_HOST="bbx.rplwms.com")
    assert detail.status_code == 200
    assert f'href="{reverse("ticket_delete", args=[ticket.pk])}"' in detail.content.decode()

    delete_url = reverse("ticket_delete", args=[ticket.pk])
    cascade_url = reverse("material_request_delete", args=[material_request.pk])
    confirmation = client.get(delete_url, HTTP_HOST="bbx.rplwms.com")
    assert confirmation.status_code == 302
    assert confirmation.url == cascade_url
    unchecked = client.post(cascade_url, HTTP_HOST="bbx.rplwms.com")
    assert unchecked.status_code == 200
    assert PickTicket.objects.filter(pk=ticket.pk).exists()
    assert MaterialRequest.objects.filter(pk=material_request.pk).exists()

    deleted = client.post(
        cascade_url,
        {"confirm_linked_deletion": "yes"},
        HTTP_HOST="bbx.rplwms.com",
    )
    assert deleted.status_code == 302
    assert deleted.url == reverse("material_request_board")
    assert not PickTicket.objects.filter(pk=ticket.pk).exists()
    assert not MaterialRequest.objects.filter(pk=material_request.pk).exists()
    item.refresh_from_db()
    assert item.quantity_on_hand == 10


@pytest.mark.django_db
def test_linked_ticket_delete_requires_material_request_delete_permissions(client):
    actor = _actor("reported-ticket-delete-denied", "view_pickticket", "delete_pickticket")
    ticket = PickTicket.objects.create(created_by=actor)
    material_request = MaterialRequest.objects.create(creator=actor, pick_ticket=ticket)
    client.force_login(actor)

    detail = client.get(reverse("ticket_detail", args=[ticket.pk]), HTTP_HOST="bbx.rplwms.com")
    assert detail.status_code == 200
    assert reverse("ticket_delete", args=[ticket.pk]) not in detail.content.decode()

    delete_url = reverse("ticket_delete", args=[ticket.pk])
    assert client.get(delete_url, HTTP_HOST="bbx.rplwms.com").status_code == 403
    assert client.post(delete_url, HTTP_HOST="bbx.rplwms.com").status_code == 403
    assert PickTicket.objects.filter(pk=ticket.pk).exists()
    assert MaterialRequest.objects.filter(pk=material_request.pk).exists()


@pytest.mark.django_db
def test_public_access_splash_uses_shared_portal_theme_and_has_visible_sign_in(client):
    response = client.get("/", HTTP_HOST="requests.rplwms.com")

    assert response.status_code == 200
    html = response.content.decode()
    assert '<html lang="en" data-theme="dark">' in html
    assert 'class="btn btn-secondary access-sign-in"' in html
    assert 'class="site-header site-header--portal portal-public-header"' in html
    assert 'class="portal-access-panel"' in html
    assert 'data-theme-toggle' in html
    assert '<svg class="header-icon"' in html
    assert ">◐</button>" not in html
    assert html.index('class="btn btn-secondary access-sign-in"') < html.index('data-theme-toggle')
    css = (Path(__file__).parent / "static" / "inventory" / "css" / "app.css").read_text()
    assert ".site-header--portal .header-controls{margin-left:auto}" in css
    assert ".site-header--portal .icon-button{display:grid;width:40px;height:40px" in css
    assert ".site-header--portal .header-icon{display:block;width:20px;height:20px" in css
    assert "background:#1d2024;color:#f6f7f8" in css
    assert 'class="portal-mobile-guidance"' in html
    assert "@blackbox.com" in html
    for retained in ("full_name", "position", "email", "contact_number", "department"):
        assert f'name="{retained}"' in html
    for removed in ("project_jobsite", "sponsor", "business_reason"):
        assert f'name="{removed}"' not in html


def test_light_and_dark_theme_use_readable_semantic_color_tokens():
    css = APP_CSS.read_text()
    settings = SETTINGS_TEMPLATE.read_text()
    dashboard = DASHBOARD_TEMPLATE.read_text()
    bulk_adjust = BULK_ADJUST_TEMPLATE.read_text()
    notification_css = NOTIFICATION_CSS.read_text()

    for token in (
        "--link:#ff3341", "--link-hover:#ff7277", "--control-border:#68717d",
        "--link:#c91522", "--link-hover:#a90f19", "--control-border:#8b929c",
    ):
        assert token in css
    assert "a{color:var(--link)" in css
    assert "border:1px solid var(--control-border)" in css
    assert ':root[data-theme="light"] .status-received' in css
    assert ".btn-warning:hover{background:var(--btn-warning-hover);color:#17120a}" in css

    for selector in (
        ':root[data-theme="light"] .group-tag-logistics-manager',
        ':root[data-theme="light"] .group-tag-logistics-specialist',
        ':root[data-theme="light"] .group-tag-procurement-specialist',
        ':root[data-theme="light"] .group-tag-procurement-manager',
        ':root[data-theme="light"] .group-tag-material-requester',
        ':root[data-theme="light"] .group-tag-material-requests',
        ':root[data-theme="light"] .group-tag-viewer',
        ':root[data-theme="light"] .badge.primary',
    ):
        assert selector in settings

    assert "background:color-mix(in srgb,var(--warn) 14%,transparent)" in dashboard
    assert ".item-delta.positive { color:var(--ok); }" in bulk_adjust
    assert ".item-delta.negative { color:var(--danger); }" in bulk_adjust
    assert ".item-info .part-badge {\n  font-size: 12px;\n  color: var(--link);" in bulk_adjust
    assert "color:var(--ink);" in bulk_adjust
    assert "background:#b42318;color:#fff" in notification_css
