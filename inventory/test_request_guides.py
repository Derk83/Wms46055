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
        "Use available stock and cancel the rest",
        "Request the rest when available",
        "Ask Procurement to purchase the rest",
        "Track your request",
        "project or work-order reference in <strong>Notes</strong>",
        "https://eqreq.rplwms.com/help/",
    ):
        assert expected in body
    assert f'href="{url}"' in body
    assert 'aria-current="page"' in body
    assert allowed_client.post(url, HTTP_HOST=MATERIAL_HOST).status_code == 405


def test_material_guide_is_not_exposed_on_warehouse_host():
    assert Client().get("/guide/", HTTP_HOST="bbx.rplwms.com").status_code == 404


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
