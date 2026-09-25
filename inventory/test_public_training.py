import html
import re

import pytest
from django.apps import apps
from django.contrib.sessions.models import Session
from django.test import Client


pytestmark = pytest.mark.django_db
DEMO_HOST = "demo.rplwms.com"

PUBLIC_COURSES = (
    ("/training/warehouse/inventory-locations-scanning/", "Inventory, Locations & Scanning"),
    ("/training/warehouse/receiving/", "Receiving"),
    ("/training/warehouse/pick-tickets-qa/", "Pick Tickets & QA"),
    ("/training/warehouse/material-request-processing/", "Material Request Processing"),
    ("/training/warehouse/shortages-procurement/", "Shortages, Backorders & Procurement"),
    ("/training/warehouse/cycle-counts/", "Cycle Counts"),
    ("/training/warehouse/transactions-reports/", "Transactions, Audit & Reports"),
    ("/training/warehouse/users-permissions/", "Users & Permissions"),
    ("/training/equipment/asset-register/", "Equipment Asset Register"),
    ("/training/equipment/custody-returns/", "Equipment Custody & Returns"),
    ("/training/equipment/reservations/", "Equipment Reservations"),
    ("/training/equipment/maintenance-rentals/", "Equipment Maintenance & Rentals"),
    ("/training/equipment/request-queue/", "Equipment Request Queue"),
    ("/training/equipment/imports-reporting/", "Equipment Imports & Reporting"),
    ("/guides/material-requests/", "Material request guide"),
    ("/guides/equipment-requests/", "Equipment request guide"),
)


def domain_counts():
    return {
        model._meta.label: model.objects.count()
        for app_label in ("inventory", "equipment")
        for model in apps.get_app_config(app_label).get_models()
    }


def test_training_center_groups_courses_and_links_only_to_public_demo_routes():
    response = Client().get("/", HTTP_HOST=DEMO_HOST, secure=True)

    assert response.status_code == 200
    body = html.unescape(response.content.decode())
    assert "No sign-in required" in body
    for group in (
        "Practice demos",
        "Requester workflows",
        "Core warehouse operations",
        "Warehouse controls & administration",
        "Equipment lifecycle",
        "Equipment planning & administration",
    ):
        assert group in body
    hrefs = re.findall(r'<a class="hub-app" href="([^"]+)">', body)
    assert len(hrefs) == 17
    assert all(href.startswith("https://demo.rplwms.com/") for href in hrefs)
    assert "Permission protected" not in body
    assert "Requester sign-in" not in body


@pytest.mark.parametrize(("path", "title"), PUBLIC_COURSES)
def test_every_public_course_is_anonymous_session_only_and_has_no_live_action(path, title):
    response = Client().get(path, HTTP_HOST=DEMO_HOST, secure=True)

    assert response.status_code == 200
    assert response.wsgi_request.session.modified is False
    assert "sessionid" not in response.cookies
    body = html.unescape(response.content.decode())
    assert title in body
    assert "Task 1 of" in body
    assert "No sign-in required" in body
    assert "Open task workspace" not in body
    assert "data-events-url" not in body
    assert "data-push-config-url" not in body
    assert "data-notification-center" not in body
    assert "push.js" not in body
    assert "Do not enter fictional training data in a live app" in body


@pytest.mark.parametrize("path", [path for path, _ in PUBLIC_COURSES])
def test_public_course_routes_exist_only_on_demo_host(path):
    assert Client().get(path, HTTP_HOST="bbx.rplwms.com", secure=True).status_code == 404
    assert Client().get(path, HTTP_HOST="requests.rplwms.com", secure=True).status_code == 404
    assert Client().get(path, HTTP_HOST="equipment.rplwms.com", secure=True).status_code == 404


@pytest.mark.parametrize("path", [path for path, _ in PUBLIC_COURSES])
def test_every_public_course_advances_anonymously_from_task_one(path):
    client = Client()
    progress = f"{path}progress/"

    response = client.post(progress, {
        "action": "complete",
        "step": "1",
        "confirm": "yes",
        "evidence": "Completed the first fictional task.",
    }, HTTP_HOST=DEMO_HOST, secure=True)

    assert response.status_code == 302
    body = client.get(path, HTTP_HOST=DEMO_HOST, secure=True).content.decode()
    assert "Task 2 of" in body


def test_anonymous_progress_is_sequential_private_to_session_and_does_not_write_domain_models():
    first = Client()
    second = Client()
    course = "/training/warehouse/receiving/"
    progress = "/training/warehouse/receiving/progress/"
    before = domain_counts()

    assert first.post(progress, {
        "action": "complete",
        "step": "2",
        "confirm": "yes",
        "evidence": "Tried to skip a task.",
    }, HTTP_HOST=DEMO_HOST, secure=True).status_code == 400
    assert first.post(progress, {
        "action": "complete",
        "step": "1",
        "confirm": "yes",
        "evidence": "Verified the fictional shipment identity.",
    }, HTTP_HOST=DEMO_HOST, secure=True).status_code == 302

    first_body = first.get(course, HTTP_HOST=DEMO_HOST, secure=True).content.decode()
    second_body = second.get(course, HTTP_HOST=DEMO_HOST, secure=True).content.decode()
    assert "Task 2 of" in first_body
    assert "Task 1 of" in second_body
    assert before == domain_counts()
    state = first.session.get("rpl_sequential_training_v1")
    assert "Verified the fictional shipment identity" not in repr(state)


def test_anonymous_progress_requires_csrf_and_post():
    course = "/training/equipment/asset-register/"
    progress = "/training/equipment/asset-register/progress/"
    client = Client(enforce_csrf_checks=True)

    assert client.get(progress, HTTP_HOST=DEMO_HOST, secure=True).status_code == 405
    assert client.post(progress, {
        "action": "complete",
        "step": "1",
        "confirm": "yes",
        "evidence": "Completed the fictional lookup.",
    }, HTTP_HOST=DEMO_HOST, secure=True).status_code == 403
    assert Client().post(course, HTTP_HOST=DEMO_HOST, secure=True).status_code == 405


def test_fictional_inventory_demo_is_public_and_completes_without_domain_writes():
    client = Client()
    page = "/inventory-demo/"
    action = "/inventory-demo/action/"
    before = domain_counts()
    sessions_before = Session.objects.count()

    initial = client.get(page, HTTP_HOST=DEMO_HOST, secure=True)
    assert initial.status_code == 200
    assert initial.wsgi_request.session.modified is False
    assert "sessionid" not in initial.cookies
    assert Session.objects.count() == sessions_before
    assert "Fictional training data" in initial.content.decode()
    for payload in (
        {"action": "select_item", "part_number": "DEMO-1002"},
        {"action": "receive_stock"},
        {"action": "create_request"},
        {"action": "fulfill_pick"},
        {"action": "complete_demo"},
    ):
        response = client.post(action, payload, HTTP_HOST=DEMO_HOST, secure=True)
        assert response.status_code == 302

    completed = client.get(page, HTTP_HOST=DEMO_HOST, secure=True)
    assert "Demo complete" in completed.content.decode()
    assert before == domain_counts()


def test_public_training_unknown_family_and_slug_are_404():
    client = Client()
    assert client.get(
        "/training/not-a-family/receiving/", HTTP_HOST=DEMO_HOST, secure=True
    ).status_code == 404
    assert client.get(
        "/training/warehouse/not-a-course/", HTTP_HOST=DEMO_HOST, secure=True
    ).status_code == 404
