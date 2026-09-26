import html
import re

import pytest
from django.apps import apps
from django.contrib.sessions.models import Session
from django.test import Client
from datetime import date

from inventory.training_exercises import exercise_for



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
    slug = path.rstrip("/").split("/")[-1]
    fields = exercise_for(slug, 1)["fields"]
    answers = {}
    for field in fields:
        if field["kind"] == "choice":
            answers[field["name"]] = field.get("answer", field["options"][0])
        elif field["kind"] == "number":
            answers[field["name"]] = str(field["answer"])
        elif field["kind"] == "date":
            answers[field["name"]] = date.today().isoformat()
        elif field["kind"] == "reference":
            answers[field["name"]] = "JOB-204"
    assert client.post(progress, {
        "action": "complete",
        "step": "1",
        "confirm": "yes",
        "evidence": "Completed the first fictional task.",
    }, HTTP_HOST=DEMO_HOST, secure=True).status_code == 400
    response = client.post(progress, {"action": "complete", "step": "1", **answers},
                           HTTP_HOST=DEMO_HOST, secure=True)

    assert response.status_code == 302
    body = client.get(path, HTTP_HOST=DEMO_HOST, secure=True).content.decode()
    assert "Task 2 of" in body


@pytest.mark.parametrize("path", [path for path, _ in PUBLIC_COURSES])
def test_fictional_tabs_separate_instructions_from_exercise_and_lock_future_steps(path):
    page = Client().get(path, HTTP_HOST=DEMO_HOST, secure=True)
    body = page.content.decode()
    tasks = page.context["training_tasks"]
    instructions = body.split('<ol class="training-steps">', 1)[1].split('</ol>', 1)[0]
    assert '<form' not in instructions
    assert 'training-exercise-prompt' not in instructions
    assert len(re.findall(r'data-tab-status="locked"', body)) == len(tasks) - 1
    assert 'href="?tab=1#training-workspace"' in body
    assert 'aria-current="page"' in body
    assert 'data-panel-status="locked"' not in body
    assert body.count('class="training-completion-form"') == 1
    assert 'action="' + page.context["progress_url"] + '"' in body
    for task in tasks:
        assert f'{task["number"]}. {task["title"]}' in html.unescape(body)
    assert tasks[0]["exercise"]["prompt"] in html.unescape(body)
    assert "Open task workspace" not in body


def test_public_course_tabs_show_only_selected_available_panel():
    client = Client()
    path = "/training/warehouse/receiving/"
    progress = path + "progress/"
    for selected in ("2", "999999999999999999999999", "bogus"):
        page = client.get(path + "?tab=" + selected, HTTP_HOST=DEMO_HOST, secure=True)
        assert page.context["selected_training_tab"] == 1
        assert page.content.decode().count('class="training-completion-form"') == 1
    assert client.post(progress, {"action": "complete", "step": "1", "answer_1": "7"},
                       HTTP_HOST=DEMO_HOST, secure=True).status_code == 302
    old = client.get(path + "?tab=1", HTTP_HOST=DEMO_HOST, secure=True)
    assert old.context["selected_training_tab"] == 1
    assert old.content.decode().count('data-panel-status="complete"') == 1
    assert 'class="training-completion-form"' not in old.content.decode()
    current = client.get(path + "?tab=2", HTTP_HOST=DEMO_HOST, secure=True)
    assert current.context["selected_training_tab"] == 2
    assert current.content.decode().count('data-panel-status="current"') == 1
    assert current.content.decode().count('class="training-completion-form"') == 1
    assert client.get(path + "?tab=4", HTTP_HOST=DEMO_HOST, secure=True).context["selected_training_tab"] == 2


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
    assert first.post(progress, {"action": "complete", "step": "1", "answer_1": "7"},
                      HTTP_HOST=DEMO_HOST, secure=True).status_code == 302

    first_body = first.get(course, HTTP_HOST=DEMO_HOST, secure=True).content.decode()
    second_body = second.get(course, HTTP_HOST=DEMO_HOST, secure=True).content.decode()
    assert "Task 2 of" in first_body
    assert "Task 1 of" in second_body
    assert before == domain_counts()
    state = first.session.get("rpl_sequential_training_v1")
    assert "personal_note" not in repr(state)


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
        {"action": "receive_stock", "part_number": "DEMO-1002", "quantity": "12", "bin": "A-01-02"},
        {"action": "create_request", "requester": "Training requester", "delivery_location": "Training dock", "part_number": "DEMO-2001", "quantity": "3"},
        {"action": "fulfill_pick", "actual_quantity": "3", "picker": "Training picker", "qa": "Training QA"},
        {"action": "complete_demo", "event_refs": ["RECEIVE", "REQUEST", "PICK"]},
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


def test_practice_choices_do_not_consistently_put_correct_answer_first():
    from inventory.training_catalog import WAREHOUSE_TRAINING, EQUIPMENT_TRAINING
    placements = []
    for slug, course in {**WAREHOUSE_TRAINING, **EQUIPMENT_TRAINING}.items():
        for step in range(1, len(course["steps"]) + 1):
            for field in exercise_for(slug, step)["fields"]:
                if field["kind"] == "choice":
                    placements.append(field["options"].index(field["answer"]))
    assert 0 in placements and any(index > 0 for index in placements)


@pytest.mark.parametrize("corruption", [
    {"version": 2, "courses": {}, "drafts": {"public-material-requests:material-requests": {}}},
    {"version": 2, "courses": {"public-material-requests:material-requests": 1},
     "drafts": {"public-material-requests:material-requests": {
         "reference": "JOB-204", "destination": "Dock 2", "needed": "2026-02-30",
         "requester": "Training Operator", "delivery_time": "09:00", "urgency": "routine"}}},
])
def test_invalid_fictional_drafts_reset_safely(corruption):
    client = Client()
    session = client.session
    session["rpl_sequential_training_v1"] = corruption
    session.save()
    response = client.get("/guides/material-requests/", HTTP_HOST=DEMO_HOST, secure=True)
    assert response.status_code == 200
    assert "Task 1 of 5" in response.content.decode()


def test_compact_guide_date_is_rejected_without_advancing():
    client = Client()
    response = client.post("/guides/material-requests/progress/", {
        "action": "complete", "step": "1", "reference": "JOB-204", "destination": "Dock 2",
        "needed": "20271010", "requester": "Training Operator", "delivery_time": "09:00",
        "urgency": "routine",
    }, HTTP_HOST=DEMO_HOST, secure=True)
    assert response.status_code == 400
    assert "Task 1 of 5" in client.get("/guides/material-requests/", HTTP_HOST=DEMO_HOST, secure=True).content.decode()
