import html
import re
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client


pytestmark = pytest.mark.django_db

WAREHOUSE_HOST = "bbx.rplwms.com"
EQUIPMENT_HOST = "equipment.rplwms.com"
DEMO_HOST = "demo.rplwms.com"


def user_with_permissions(username, *permission_names):
    user = get_user_model().objects.create_user(username=username, password="safe-test-password")
    for permission_name in permission_names:
        app_label, codename = permission_name.split(".", 1)
        user.user_permissions.add(Permission.objects.get(
            content_type__app_label=app_label,
            codename=codename,
        ))
    return user


def test_public_training_center_lists_every_demo_guide_and_protected_course_without_queries(
    django_assert_num_queries,
):
    with django_assert_num_queries(0):
        response = Client().get("/", HTTP_HOST=DEMO_HOST, secure=True)

    assert response.status_code == 200
    body = html.unescape(response.content.decode())
    for title in (
        "Guided Inventory Demo",
        "Request Material",
        "Request Equipment",
        "Inventory, Locations & Scanning",
        "Receiving",
        "Pick Tickets & QA",
        "Material Request Processing",
        "Shortages, Backorders & Procurement",
        "Cycle Counts",
        "Transactions, Audit & Reports",
        "Equipment Asset Register",
        "Equipment Custody & Returns",
        "Equipment Reservations",
        "Equipment Maintenance & Rentals",
        "Equipment Request Queue",
        "Equipment Imports & Reporting",
        "Users & Permissions",
    ):
        assert title in body

    hrefs = set(re.findall(r'<a class="hub-app" href="([^"]+)">', body))
    assert {
        "https://bbx.rplwms.com/demo/",
        "https://requests.rplwms.com/guide/",
        "https://eqreq.rplwms.com/help/",
        "https://bbx.rplwms.com/training/inventory-locations-scanning/",
        "https://bbx.rplwms.com/training/receiving/",
        "https://bbx.rplwms.com/training/pick-tickets-qa/",
        "https://bbx.rplwms.com/training/material-request-processing/",
        "https://bbx.rplwms.com/training/shortages-procurement/",
        "https://bbx.rplwms.com/training/cycle-counts/",
        "https://bbx.rplwms.com/training/transactions-reports/",
        "https://bbx.rplwms.com/training/users-permissions/",
        "https://equipment.rplwms.com/training/asset-register/",
        "https://equipment.rplwms.com/training/custody-returns/",
        "https://equipment.rplwms.com/training/reservations/",
        "https://equipment.rplwms.com/training/maintenance-rentals/",
        "https://equipment.rplwms.com/training/request-queue/",
        "https://equipment.rplwms.com/training/imports-reporting/",
    } == hrefs
    assert "Sign-in and permissions are still enforced" in body


@pytest.mark.parametrize(("slug", "permission", "expected_text"), [
    ("inventory-locations-scanning", "inventory.view_inventoryitem", "Fictional scenario"),
    ("receiving", "inventory.receive_stock", "Post the receipt"),
    ("pick-tickets-qa", "inventory.view_pickticket", "picker and QA checker"),
    ("material-request-processing", "inventory.view_materialrequest", "assignment acknowledgement"),
    ("shortages-procurement", "inventory.view_materialbackorder", "Procurement requisition"),
    ("cycle-counts", "inventory.perform_cycle_count", "blind count"),
    ("transactions-reports", "inventory.view_inventorytransaction", "immutable transaction history"),
    ("users-permissions", "inventory.manage_users", "least-privilege access"),
])
def test_warehouse_training_modules_require_the_matching_permission(slug, permission, expected_text):
    path = f"/training/{slug}/"
    anonymous = Client().get(path, HTTP_HOST=WAREHOUSE_HOST, secure=True)
    assert anonymous.status_code == 302
    assert "/accounts/login/" in anonymous.url

    denied = Client()
    denied.force_login(get_user_model().objects.create_user(username=f"denied-{slug}"))
    assert denied.get(path, HTTP_HOST=WAREHOUSE_HOST, secure=True).status_code == 403

    allowed = Client()
    allowed.force_login(user_with_permissions(f"allowed-{slug}", permission))
    response = allowed.get(path, HTTP_HOST=WAREHOUSE_HOST, secure=True)
    assert response.status_code == 200
    assert expected_text in response.content.decode()
    assert "Open the live workspace" in response.content.decode()
    assert allowed.post(path, HTTP_HOST=WAREHOUSE_HOST, secure=True).status_code == 405


@pytest.mark.parametrize(("slug", "permission", "expected_text"), [
    ("asset-register", "equipment.view_asset", "authoritative asset identity"),
    ("custody-returns", "equipment.view_checkout", "custody history"),
    ("reservations", "equipment.view_reservation", "approved future custody"),
    ("maintenance-rentals", "equipment.view_maintenanceworkorder", "service and rental obligations"),
    ("request-queue", "equipment.manage_equipment_requests", "Allocate specific assets"),
    ("imports-reporting", "equipment.import_equipment", "staged import"),
])
def test_equipment_training_modules_require_portal_and_module_permissions(slug, permission, expected_text):
    path = f"/training/{slug}/"
    anonymous = Client().get(path, HTTP_HOST=EQUIPMENT_HOST, secure=True)
    assert anonymous.status_code == 302
    assert "/login/" in anonymous.url

    outer_only = Client()
    outer_only.force_login(user_with_permissions(
        f"outer-{slug}", "equipment.access_equipment_portal"
    ))
    assert outer_only.get(path, HTTP_HOST=EQUIPMENT_HOST, secure=True).status_code == 403

    allowed = Client()
    allowed.force_login(user_with_permissions(
        f"allowed-equipment-{slug}",
        "equipment.access_equipment_portal",
        permission,
    ))
    response = allowed.get(path, HTTP_HOST=EQUIPMENT_HOST, secure=True)
    assert response.status_code == 200
    assert expected_text in response.content.decode()
    assert "Open the live workspace" in response.content.decode()
    assert allowed.post(path, HTTP_HOST=EQUIPMENT_HOST, secure=True).status_code == 405


def test_warehouse_training_suppresses_live_assignment_context():
    client = Client()
    client.force_login(user_with_permissions(
        "static-warehouse-training", "inventory.view_inventoryitem"
    ))

    with patch("inventory.context_processors.PickTicket.objects.filter") as live_tickets:
        response = client.get(
            "/training/inventory-locations-scanning/",
            HTTP_HOST=WAREHOUSE_HOST,
            secure=True,
        )

    assert response.status_code == 200
    live_tickets.assert_not_called()
    assert "assigned-ticket-prompt" not in response.content.decode()


def test_warehouse_training_disables_live_events_notifications_and_push():
    client = Client()
    client.force_login(user_with_permissions(
        "static-training-events",
        "inventory.view_inventoryitem",
        "inventory.view_materialrequest",
        "inventory.view_all_materialrequests",
    ))

    response = client.get(
        "/training/inventory-locations-scanning/",
        HTTP_HOST=WAREHOUSE_HOST,
        secure=True,
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "data-events-url" not in body
    assert "data-push-config-url" not in body
    assert "data-notification-center" not in body
    assert "push.js" not in body
    assert "claim_url" not in body


def test_course_access_does_not_show_workspace_link_without_destination_permission():
    warehouse = Client()
    warehouse.force_login(user_with_permissions(
        "receiving-log-training", "inventory.view_receiving_log"
    ))
    warehouse_response = warehouse.get(
        "/training/receiving/", HTTP_HOST=WAREHOUSE_HOST, secure=True
    )
    assert warehouse_response.status_code == 200
    assert 'href="/inventory/receiving/"' not in warehouse_response.content.decode()

    equipment = Client()
    equipment.force_login(user_with_permissions(
        "equipment-export-training",
        "equipment.access_equipment_portal",
        "equipment.export_equipment",
    ))
    equipment_response = equipment.get(
        "/training/imports-reporting/", HTTP_HOST=EQUIPMENT_HOST, secure=True
    )
    assert equipment_response.status_code == 200
    assert 'href="/imports/"' not in equipment_response.content.decode()


def test_requester_only_account_cannot_open_equipment_manager_training():
    user = user_with_permissions("requester-training-boundary", "equipment.access_equipment_requests")
    client = Client()
    client.force_login(user)

    assert client.get(
        "/training/request-queue/", HTTP_HOST="eqreq.rplwms.com", secure=True
    ).status_code == 404
    assert client.get(
        "/training/request-queue/", HTTP_HOST=EQUIPMENT_HOST, secure=True
    ).status_code == 403


def test_training_routes_are_isolated_to_their_own_application_hosts():
    warehouse_client = Client()
    warehouse_client.force_login(user_with_permissions(
        "warehouse-isolation", "inventory.view_inventoryitem"
    ))
    equipment_client = Client()
    equipment_client.force_login(user_with_permissions(
        "equipment-isolation", "equipment.access_equipment_portal", "equipment.view_asset"
    ))

    assert equipment_client.get(
        "/training/receiving/", HTTP_HOST=EQUIPMENT_HOST, secure=True
    ).status_code == 404
    assert warehouse_client.get(
        "/training/asset-register/", HTTP_HOST=WAREHOUSE_HOST, secure=True
    ).status_code == 404
    assert Client().get(
        "/training/receiving/", HTTP_HOST=DEMO_HOST, secure=True
    ).status_code == 404
