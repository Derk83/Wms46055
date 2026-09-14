"""Phase 4 UI audit contracts for tools and action consolidation."""
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse

from .models import CycleCount, InventoryItem


User = get_user_model()
ROOT = Path(__file__).parent
TEMPLATES = ROOT / "templates" / "inventory"


def _template(name):
    return (TEMPLATES / name).read_text()


def _actor(username, *codenames):
    user = User.objects.create_user(username=username, password="pw")
    user.user_permissions.add(*[
        Permission.objects.get(content_type__app_label="inventory", codename=codename)
        for codename in codenames
    ])
    return user


def _body(client, user, route):
    client.force_login(user)
    response = client.get(route, HTTP_HOST="bbx.rplwms.com")
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
def test_labels_and_qr_tools_are_one_permission_filtered_tools_surface(client):
    barcode_user = _actor("phase4-barcode", "view_inventoryitem", "manage_item_barcodes")
    with patch("inventory.views.generate_qr_code", return_value=""):
        body = _body(client, barcode_user, reverse("qr_codes"))
    assert "Label &amp; QR Tools" in body
    assert "Item Barcode" in body
    assert reverse("inventory_list") in body
    assert "Location Labels" not in body
    assert reverse("app_qr_code") not in body

    app_user = _actor("phase4-app-qr", "view_app_qr_code")
    with patch("inventory.views.generate_qr_code", return_value=""):
        app_body = _body(client, app_user, reverse("qr_codes"))
    assert "Label &amp; QR Tools" in app_body
    assert reverse("app_qr_code") in app_body
    assert "Item Barcode" not in app_body
    assert "Location Labels" not in app_body

    location_user = _actor("phase4-location-labels", "view_qr_codes")
    with patch("inventory.views.generate_qr_code", return_value=""):
        location_body = _body(client, location_user, reverse("qr_codes"))
    assert "Location Labels" in location_body
    assert "Item Barcode" not in location_body
    assert reverse("app_qr_code") not in location_body

    # Both desktop and mobile Tools navigation use the consolidated label.
    dashboard = _body(client, barcode_user, reverse("dashboard"))
    assert dashboard.count(f'href="{reverse("qr_codes")}">Labels &amp; QR</a>') == 2

    no_tools = _actor("phase4-no-label-tools", "view_inventoryitem")
    assert reverse("qr_codes") not in _body(client, no_tools, reverse("dashboard"))


@pytest.mark.django_db
def test_item_media_discovery_stays_on_the_wms_host(client):
    item = InventoryItem.objects.create(part_number="P4-PORTAL", name="Phase 4 portal item", quantity_on_hand=1)
    portal_user = _actor(
        "phase4-portal-viewer",
        "view_inventoryitem",
        "access_material_request_portal",
    )
    client.force_login(portal_user)
    response = client.get(reverse("item_detail", args=[item.pk]), HTTP_HOST="requests.rplwms.com")
    assert response.status_code == 200
    body = response.content.decode()
    assert reverse("item_images", args=[item.pk]) not in body
    assert reverse("item_documents", args=[item.pk]) not in body
    assert reverse("qr_codes") not in body


@pytest.mark.django_db
def test_item_detail_exposes_media_to_viewers_but_media_mutations_require_change(client):
    item = InventoryItem.objects.create(part_number="P4-MEDIA", name="Phase 4 media", quantity_on_hand=1)
    viewer = _actor("phase4-media-viewer", "view_inventoryitem")
    detail = _body(client, viewer, reverse("item_detail", args=[item.pk]))
    assert reverse("item_images", args=[item.pk]) in detail
    assert reverse("item_documents", args=[item.pk]) in detail

    images = _body(client, viewer, reverse("item_images", args=[item.pk]))
    documents = _body(client, viewer, reverse("item_documents", args=[item.pk]))
    assert reverse("item_image_add", args=[item.pk]) not in images
    assert reverse("item_document_add", args=[item.pk]) not in documents
    assert "Add Image" not in images
    assert "Upload Document" not in documents

    editor = _actor("phase4-media-editor", "view_inventoryitem", "change_inventoryitem")
    assert reverse("item_image_add", args=[item.pk]) in _body(client, editor, reverse("item_images", args=[item.pk]))
    assert reverse("item_document_add", args=[item.pk]) in _body(client, editor, reverse("item_documents", args=[item.pk]))


@pytest.mark.django_db
def test_documents_list_read_access_matches_inventory_view_permission(client):
    item = InventoryItem.objects.create(part_number="P4-DOC", name="Phase 4 document", quantity_on_hand=1)
    viewer = _actor("phase4-doc-viewer", "view_inventoryitem")
    client.force_login(viewer)
    response = client.get(reverse("item_documents", args=[item.pk]), HTTP_HOST="bbx.rplwms.com")
    assert response.status_code == 200


@pytest.mark.django_db
def test_report_navigation_and_compatibility_url_open_daily_activity_directly(client):
    manager = User.objects.create_superuser("phase4-reports", "", "pw")
    body = _body(client, manager, reverse("reports_index"))
    assert "Daily Activity Report" in body
    assert 'name="preset"' in body
    assert "Weekly report (auto-delivered)" in body

    dashboard = _body(client, manager, reverse("dashboard"))
    assert f'href="{reverse("reports_daily")}">Reports</a>' in dashboard
    assert f'href="{reverse("reports_index")}">Reports</a>' not in dashboard


def test_dashboard_quick_actions_restores_full_operational_set():
    source = _template("_dashboard_quick_actions.html")
    for action in (
        "Add Item", "Resume Work", "Receive Stock", "Bulk Adjust",
        "Transaction History", "Print Labels", "Phone Scanner",
    ):
        assert action in source


@pytest.mark.django_db
def test_item_detail_has_one_permission_appropriate_primary_action(client):
    item = InventoryItem.objects.create(part_number="P4-ACTION", name="Phase 4 action", quantity_on_hand=1)
    receiver = _actor("phase4-receiver", "view_inventoryitem", "receive_stock", "change_inventoryitem")
    body = _body(client, receiver, reverse("item_detail", args=[item.pk]))
    assert f'class="btn" href="{reverse("receive_stock", args=[item.pk])}" data-action="primary"' in body
    assert f'class="btn light" href="{reverse("inventory_edit", args=[item.pk])}"' in body
    assert body.count('data-action="primary"') == 1

    editor = _actor("phase4-editor", "view_inventoryitem", "change_inventoryitem")
    editor_body = _body(client, editor, reverse("item_detail", args=[item.pk]))
    assert f'class="btn" href="{reverse("inventory_edit", args=[item.pk])}" data-action="primary"' in editor_body
    assert editor_body.count('data-action="primary"') == 1


@pytest.mark.django_db
def test_in_progress_cycle_count_surfaces_single_save_action_near_heading(client):
    user = _actor("phase4-counter", "perform_cycle_count")
    count = CycleCount.objects.create(name="Phase 4 count", created_by=user, status=CycleCount.Status.IN_PROGRESS)
    body = _body(client, user, reverse("cycle_count_detail", args=[count.pk]))
    header = body.split("</div>\n</div>", 1)[0]
    assert 'form="cycle-count-form"' in header
    assert ">Save Counts</button>" in header
    assert body.count('data-action="primary"') == 1
    assert 'id="cycle-count-form"' in body
