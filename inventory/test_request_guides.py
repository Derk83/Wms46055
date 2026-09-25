import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse


pytestmark = pytest.mark.django_db

MATERIAL_HOST = "requests.rplwms.com"
EQUIPMENT_HOST = "eqreq.rplwms.com"


def user_with_permissions(username, app_label, *codenames):
    user = get_user_model().objects.create_user(username, password="safe-test-password")
    user.user_permissions.add(*Permission.objects.filter(
        content_type__app_label=app_label, codename__in=codenames
    ))
    return user


def test_material_request_guide_requires_requester_access_and_explains_complete_flow():
    url = reverse("material_request_guide", urlconf="inventory.request_urls")
    anonymous = Client().get(url, HTTP_HOST=MATERIAL_HOST)
    assert anonymous.status_code == 302
    assert "/login/" in anonymous.url

    denied_client = Client()
    denied_client.force_login(get_user_model().objects.create_user("material-denied"))
    assert denied_client.get(url, HTTP_HOST=MATERIAL_HOST).status_code == 403

    allowed_client = Client()
    allowed_client.force_login(user_with_permissions(
        "material-guide",
        "inventory",
        "access_material_request_portal",
        "add_materialrequest",
        "add_materialrequestline",
        "view_inventoryitem",
        "view_materialrequest",
    ))
    response = allowed_client.get(url, HTTP_HOST=MATERIAL_HOST)
    body = response.content.decode()

    assert response.status_code == 200
    for expected in (
        "Material request guide",
        "Choose from inventory",
        "Decide how to handle shortages",
        "Submit and keep the request number",
        "Track your request",
        "project or work-order reference",
        "https://eqreq.rplwms.com/help/",
    ):
        assert expected in body
    assert f'href="{url}"' in body
    assert 'aria-current="page"' in body
    assert allowed_client.post(url, HTTP_HOST=MATERIAL_HOST).status_code == 405


def test_material_guide_is_not_exposed_on_warehouse_host():
    assert Client().get("/guide/", HTTP_HOST="bbx.rplwms.com").status_code == 404


def test_material_request_guide_tasks_must_be_completed_in_order():
    client = Client()
    client.force_login(user_with_permissions(
        "material-guide-progress",
        "inventory",
        "access_material_request_portal",
        "add_materialrequest",
        "add_materialrequestline",
        "view_inventoryitem",
        "view_materialrequest",
    ))
    guide = "/guide/"
    progress = "/guide/progress/"

    initial = client.get(guide, HTTP_HOST=MATERIAL_HOST, secure=True)
    initial_body = initial.content.decode()
    assert initial.status_code == 200
    assert "Task 1 of 5" in initial_body
    assert "Enter request details" in initial_body
    assert "Complete task 1 to unlock" in initial_body

    assert client.post(progress, {
        "action": "complete",
        "step": "2",
        "confirm": "yes",
        "evidence": "Skipped",
    }, HTTP_HOST=MATERIAL_HOST, secure=True).status_code == 400

    for step in range(1, 6):
        page = client.get(guide, HTTP_HOST=MATERIAL_HOST, secure=True).content.decode()
        if step == 3:
            assert "Use available stock and cancel the rest" in page
            assert "Request the rest when available" in page
            assert "Ask Procurement to purchase the rest" in page
        response = client.post(progress, {
            "action": "complete",
            "step": str(step),
            "confirm": "yes",
            "evidence": f"Completed material request task {step}.",
        }, HTTP_HOST=MATERIAL_HOST, secure=True)
        assert response.status_code == 302
        assert response.url == guide

    finished = client.get(guide, HTTP_HOST=MATERIAL_HOST, secure=True)
    assert "Module complete" in finished.content.decode()
    assert "5 of 5 tasks complete" in finished.content.decode()


def test_material_request_progress_is_post_only_permission_gated_and_host_isolated():
    progress = "/guide/progress/"
    allowed = Client(enforce_csrf_checks=True)
    allowed.force_login(user_with_permissions(
        "material-guide-csrf",
        "inventory",
        "access_material_request_portal",
        "add_materialrequest",
        "add_materialrequestline",
        "view_inventoryitem",
        "view_materialrequest",
    ))
    assert allowed.get(progress, HTTP_HOST=MATERIAL_HOST, secure=True).status_code == 405
    assert allowed.post(progress, {
        "action": "complete", "step": "1", "confirm": "yes", "evidence": "Done"
    }, HTTP_HOST=MATERIAL_HOST, secure=True).status_code == 403
    assert Client().post(progress, HTTP_HOST="bbx.rplwms.com", secure=True).status_code == 404


def test_equipment_request_guide_requires_access_and_explains_complete_flow():
    url = reverse("eqreq_help", urlconf="equipment.request_urls")
    anonymous = Client().get(url, HTTP_HOST=EQUIPMENT_HOST)
    assert anonymous.status_code == 302
    assert "/login/" in anonymous.url

    denied_client = Client()
    denied_client.force_login(get_user_model().objects.create_user("equipment-denied"))
    assert denied_client.get(url, HTTP_HOST=EQUIPMENT_HOST).status_code == 403

    allowed_client = Client()
    allowed_client.force_login(
        user_with_permissions("equipment-guide", "equipment", "access_equipment_requests")
    )
    response = allowed_client.get(url, HTTP_HOST=EQUIPMENT_HOST)
    body = response.content.decode()

    assert response.status_code == 200
    for expected in (
        "Equipment request guide",
        "Choose equipment",
        "Preferred item",
        "Track your request",
        "Edit or cancel",
        "Under review",
        "https://requests.rplwms.com/guide/",
    ):
        assert expected in body
    assert f'href="{url}"' in body
    assert 'aria-current="page"' in body
    assert allowed_client.post(url, HTTP_HOST=EQUIPMENT_HOST).status_code == 405


@pytest.mark.parametrize("host", [
    "bbx.rplwms.com",
    "requests.rplwms.com",
    "equipment.rplwms.com",
])
def test_equipment_request_guide_isolated_from_other_app_hosts(host):
    assert Client().get("/help/", HTTP_HOST=host).status_code == 404
