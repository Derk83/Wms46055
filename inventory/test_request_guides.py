import pytest
from datetime import date, timedelta
from inventory.training_progress import SESSION_KEY
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

    from datetime import date, timedelta
    draft_steps = (
        {"reference": "JOB-204", "destination": "Dock 2", "needed": (date.today() + timedelta(days=7)).isoformat(), "requester": "Training Operator", "delivery_time": "09:00", "urgency": "routine"},
        {"item": "RPL-DEMO-104", "quantity": "9"},
        {"allocated": "6", "disposition": "backorder"},
        {"review_reference": "JOB-204"},
    )
    for step in range(1, 6):
        page = client.get(guide, HTTP_HOST=MATERIAL_HOST, secure=True).content.decode()
        if step == 3:
            assert "Use available stock and cancel the rest" in page
            assert "Request the rest when available" in page
            assert "Ask Procurement to purchase the rest" in page
        data = draft_steps[step - 1] if step < 5 else {
            "tracking_number": client.session[SESSION_KEY]["drafts"]["material-requester:material-requests"]["number"]
        }
        response = client.post(progress, {"action": "complete", "step": str(step), **data},
                               HTTP_HOST=MATERIAL_HOST, secure=True)
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


@pytest.mark.parametrize(("slug", "item", "available", "disposition", "prefix"), [
    ("material-requests", "RPL-DEMO-104", 6, "backorder", "MR-"),
    ("equipment-requests", "laptop", 1, "waitlist", "ER-"),
])
def test_fictional_request_form_is_validated_tracked_and_session_isolated(slug, item, available, disposition, prefix, django_assert_num_queries):
    client, other = Client(), Client()
    path = f"/guides/{slug}/"
    progress = path + "progress/"
    with django_assert_num_queries(0):
        initial = client.get(path, HTTP_HOST="demo.rplwms.com", secure=True)
    assert initial.status_code == 200
    assert SESSION_KEY not in client.session
    assert 'name="reference"' in initial.content.decode()
    assert client.post(progress, {"action": "complete", "step": "1", "confirm": "yes", "evidence": "Done"}, HTTP_HOST="demo.rplwms.com", secure=True).status_code == 400

    def submit(step, data, expected=302):
        return client.post(progress, {"action": "complete", "step": str(step), **data}, HTTP_HOST="demo.rplwms.com", secure=True).status_code == expected

    first = {"reference": "JOB-204", "destination": "Dock 2", "needed": (date.today() + timedelta(days=7)).isoformat()}
    if slug == "equipment-requests":
        first.update(purpose="scheduled work", priority="routine")
    else:
        first.update(requester="Training Operator", delivery_time="09:00", urgency="routine")
    submit(1, {**first, "reference": "Jane Smith"}, 400)
    submit(2, {"item": item, "quantity": "9"}, 400)
    submit(1, first)
    assert set(client.session[SESSION_KEY]["drafts"][f"public-{slug}:{slug}"]) == set(first)
    assert "Jane Smith" not in str(client.session.items())
    submit(2, {"item": item, "quantity": "0"}, 400)
    submit(2, {"item": item, "quantity": "9"})
    assert f"{item} × 9" in client.get(path, HTTP_HOST="demo.rplwms.com", secure=True).content.decode()
    submit(3, {"allocated": "9", "disposition": disposition}, 400)
    submit(3, {"allocated": str(available), "disposition": "none"}, 400)
    submit(3, {"allocated": str(available), "disposition": disposition})
    submit(4, {"review_reference": "JOB-205"}, 400)
    submit(4, {"review_reference": "JOB-204"})
    draft = client.session[SESSION_KEY]["drafts"][f"public-{slug}:{slug}"]
    number = draft["number"]
    assert number.startswith(prefix)
    assert number in client.get(path, HTTP_HOST="demo.rplwms.com", secure=True).content.decode()
    assert number not in other.get(path, HTTP_HOST="demo.rplwms.com", secure=True).content.decode()
    submit(5, {"tracking_number": "MR-000000000000"}, 400)
    submit(5, {"tracking_number": number})
    assert "Module complete" in client.get(path, HTTP_HOST="demo.rplwms.com", secure=True).content.decode(), client.session[SESSION_KEY]
    assert client.post(progress, {"action": "reset"}, HTTP_HOST="demo.rplwms.com", secure=True).status_code == 302
    assert client.session[SESSION_KEY]["drafts"] == {}
    assert number not in client.get(path, HTTP_HOST="demo.rplwms.com", secure=True).content.decode()


@pytest.mark.parametrize("host", [
    "bbx.rplwms.com",
    "requests.rplwms.com",
    "equipment.rplwms.com",
])
def test_equipment_request_guide_isolated_from_other_app_hosts(host):
    assert Client().get("/help/", HTTP_HOST=host).status_code == 404
