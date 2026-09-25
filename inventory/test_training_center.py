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
    ("inventory-locations-scanning", "inventory.view_inventoryitem", "Find the item"),
    ("receiving", "inventory.receive_stock", "Verify shipment identity"),
    ("pick-tickets-qa", "inventory.view_pickticket", "Accept assigned work"),
    ("material-request-processing", "inventory.view_materialrequest", "Review the request"),
    ("shortages-procurement", "inventory.view_materialbackorder", "Identify the shortage"),
    ("cycle-counts", "inventory.perform_cycle_count", "Prepare the count"),
    ("transactions-reports", "inventory.view_inventorytransaction", "Filter the ledger"),
    ("users-permissions", "inventory.manage_users", "Confirm the requested role"),
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
    assert "Open task workspace" in response.content.decode()
    assert allowed.post(path, HTTP_HOST=WAREHOUSE_HOST, secure=True).status_code == 405
    progress = allowed.post(f"/training/{slug}/progress/", {
        "action": "complete",
        "step": "1",
        "confirm": "yes",
        "evidence": "Completed and verified the first required task.",
    }, HTTP_HOST=WAREHOUSE_HOST, secure=True)
    assert progress.status_code == 302
    advanced = allowed.get(path, HTTP_HOST=WAREHOUSE_HOST, secure=True).content.decode()
    assert "Task 2 of" in advanced
    assert 'data-task-status="complete"' in advanced


@pytest.mark.parametrize(("slug", "permission", "expected_text"), [
    ("asset-register", "equipment.view_asset", "Search before creating"),
    ("custody-returns", "equipment.view_checkout", "Confirm availability"),
    ("reservations", "equipment.view_reservation", "Review the requested window"),
    ("maintenance-rentals", "equipment.view_maintenanceworkorder", "Review service and rental obligations"),
    ("request-queue", "equipment.manage_equipment_requests", "Triage the request"),
    ("imports-reporting", "equipment.import_equipment", "Upload to staged import"),
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
    assert "Open task workspace" in response.content.decode()
    assert allowed.post(path, HTTP_HOST=EQUIPMENT_HOST, secure=True).status_code == 405
    progress = allowed.post(f"/training/{slug}/progress/", {
        "action": "complete",
        "step": "1",
        "confirm": "yes",
        "evidence": "Completed and verified the first required task.",
    }, HTTP_HOST=EQUIPMENT_HOST, secure=True)
    assert progress.status_code == 302
    advanced = allowed.get(path, HTTP_HOST=EQUIPMENT_HOST, secure=True).content.decode()
    assert "Task 2 of" in advanced
    assert 'data-task-status="complete"' in advanced


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


def test_warehouse_tasks_unlock_only_after_current_task_is_completed():
    client = Client()
    client.force_login(user_with_permissions(
        "warehouse-sequential", "inventory.view_inventoryitem"
    ))
    course = "/training/inventory-locations-scanning/"
    progress = "/training/inventory-locations-scanning/progress/"

    initial = client.get(course, HTTP_HOST=WAREHOUSE_HOST, secure=True)
    body = initial.content.decode()
    assert initial.status_code == 200
    assert "Task 1 of 4" in body
    assert "Find the item" in body
    assert "Complete task 1 to unlock" in body
    assert 'data-task-status="current"' in body
    assert 'data-task-status="locked"' in body

    assert client.post(progress, {
        "action": "complete",
        "step": "2",
        "confirm": "yes",
        "evidence": "Tried to skip",
    }, HTTP_HOST=WAREHOUSE_HOST, secure=True).status_code == 400
    assert client.post(progress, {
        "action": "complete",
        "step": "1",
        "confirm": "yes",
        "evidence": "",
    }, HTTP_HOST=WAREHOUSE_HOST, secure=True).status_code == 400

    completed = client.post(progress, {
        "action": "complete",
        "step": "1",
        "confirm": "yes",
        "evidence": "Confirmed the fictional item identity and description.",
    }, HTTP_HOST=WAREHOUSE_HOST, secure=True)
    assert completed.status_code == 302
    assert completed.url == course

    next_page = client.get(course, HTTP_HOST=WAREHOUSE_HOST, secure=True)
    next_body = next_page.content.decode()
    assert "Task 2 of 4" in next_body
    assert "Read the location" in next_body
    assert 'data-task-status="complete"' in next_body

    reset = client.post(progress, {"action": "reset"}, HTTP_HOST=WAREHOUSE_HOST, secure=True)
    assert reset.status_code == 302
    assert "Task 1 of 4" in client.get(
        course, HTTP_HOST=WAREHOUSE_HOST, secure=True
    ).content.decode()


def test_equipment_tasks_are_sequential_and_session_isolated():
    user = user_with_permissions(
        "equipment-sequential",
        "equipment.access_equipment_portal",
        "equipment.view_asset",
    )
    first = Client()
    second = Client()
    first.force_login(user)
    second.force_login(user)
    course = "/training/asset-register/"
    progress = "/training/asset-register/progress/"

    assert first.post(progress, {
        "action": "complete",
        "step": "1",
        "confirm": "yes",
        "evidence": "Searched tag, serial, name, and category.",
    }, HTTP_HOST=EQUIPMENT_HOST, secure=True).status_code == 302

    assert "Task 2 of 4" in first.get(
        course, HTTP_HOST=EQUIPMENT_HOST, secure=True
    ).content.decode()
    assert "Task 1 of 4" in second.get(
        course, HTTP_HOST=EQUIPMENT_HOST, secure=True
    ).content.decode()


def test_training_progress_requires_post_csrf_permission_and_correct_host():
    progress = "/training/inventory-locations-scanning/progress/"
    allowed = user_with_permissions("training-csrf", "inventory.view_inventoryitem")
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(allowed)

    assert csrf_client.get(progress, HTTP_HOST=WAREHOUSE_HOST, secure=True).status_code == 405
    assert csrf_client.post(progress, {
        "action": "complete", "step": "1", "confirm": "yes", "evidence": "Done"
    }, HTTP_HOST=WAREHOUSE_HOST, secure=True).status_code == 403

    denied = Client()
    denied.force_login(get_user_model().objects.create_user(username="progress-denied"))
    assert denied.post(progress, {
        "action": "complete", "step": "1", "confirm": "yes", "evidence": "Done"
    }, HTTP_HOST=WAREHOUSE_HOST, secure=True).status_code == 403
    host_client = Client()
    host_client.force_login(user_with_permissions(
        "training-host-isolation",
        "inventory.view_inventoryitem",
        "equipment.access_equipment_portal",
    ))
    assert host_client.post(progress, {
        "action": "complete", "step": "1", "confirm": "yes", "evidence": "Done"
    }, HTTP_HOST=EQUIPMENT_HOST, secure=True).status_code == 404


def test_every_course_has_multiple_required_tasks():
    from inventory.training_catalog import EQUIPMENT_TRAINING, WAREHOUSE_TRAINING

    for course in (*WAREHOUSE_TRAINING.values(), *EQUIPMENT_TRAINING.values()):
        assert len(course["steps"]) >= 4
        assert all(title.strip() and body.strip() for title, body in course["steps"])
