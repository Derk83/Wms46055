import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from .demo import SESSION_KEY
from .models import InventoryItem, InventoryTransaction


pytestmark = pytest.mark.django_db


def _login(client, username="demo-user"):
    user = get_user_model().objects.create_user(username=username, password="pw")
    client.force_login(user)
    return user


def _get(client):
    return client.get(reverse("inventory_demo"), HTTP_HOST="bbx.rplwms.com")


def _post(client, action, **data):
    return client.post(
        reverse("inventory_demo_action"),
        {"action": action, **data},
        HTTP_HOST="bbx.rplwms.com",
    )


RECEIPT = {"part_number": "DEMO-1002", "quantity": "12", "bin": "A-01-02"}
REQUEST = {"requester": "Training requester", "delivery_location": "Training dock", "part_number": "DEMO-2001", "quantity": "3"}
PICK = {"actual_quantity": "3", "picker": "Training picker", "qa": "Training QA"}
AUDIT = {"event_refs": ["RECEIVE", "REQUEST", "PICK"]}


def _advance(client, step):
    _post(client, "select_item", part_number="DEMO-1002")
    if step > 2:
        _post(client, "receive_stock", **RECEIPT)
    if step > 3:
        _post(client, "create_request", **REQUEST)
    if step > 4:
        _post(client, "fulfill_pick", **PICK)


@pytest.mark.parametrize("step,action", [(2, "receive_stock"), (3, "create_request"), (4, "fulfill_pick"), (5, "complete_demo")])
def test_empty_step_action_cannot_auto_advance(client, step, action):
    _login(client)
    _advance(client, step)
    before = dict(client.session[SESSION_KEY])
    response = _post(client, action)
    assert response.status_code == 400
    assert client.session[SESSION_KEY] == before
    assert _get(client).context["step"] == step
    assert "required" in response.content.decode().lower() or "select" in response.content.decode().lower()


def test_bad_receipt_request_pick_and_audit_inputs_do_not_change_state(client):
    _login(client)
    _advance(client, 2)
    for data in ({**RECEIPT, "bin": "wrong"}, {**RECEIPT, "quantity": "0"}):
        assert _post(client, "receive_stock", **data).status_code == 400
    assert _get(client).context["step"] == 2
    _post(client, "receive_stock", **RECEIPT)
    for data in ({**REQUEST, "quantity": "999"}, {**REQUEST, "requester": " "}, {**REQUEST, "quantity": "-1"}):
        assert _post(client, "create_request", **data).status_code == 400
    assert _get(client).context["step"] == 3
    _post(client, "create_request", **REQUEST)
    for data in ({**PICK, "qa": "Training picker"}, {**PICK, "actual_quantity": "2"}, {**PICK, "actual_quantity": "2", "variance_reason": " "}):
        assert _post(client, "fulfill_pick", **data).status_code == 400
    assert _get(client).context["step"] == 4
    _post(client, "fulfill_pick", **PICK)
    assert _post(client, "complete_demo", event_refs=["RECEIVE", "REQUEST", "START"]).status_code == 400
    assert _get(client).context["complete"] is False


def test_variance_and_session_state_integrity(client):
    _login(client)
    _advance(client, 4)
    assert _post(client, "fulfill_pick", actual_quantity="2", picker="Pat", qa="Sam", variance_reason="Damaged pack").status_code == 302
    state = client.session[SESSION_KEY]
    assert state["pick"]["actual_quantity"] == 2
    assert state["pick"]["variance_reason"] == "Damaged pack"
    assert "Damaged pack" in _get(client).content.decode()
    state["quantities"]["DEMO-2001"] = -1
    session = client.session
    session[SESSION_KEY] = state
    session.save()
    assert _get(client).context["step"] == 1


def test_demo_requires_authentication(client):
    response = _get(client)

    assert response.status_code == 302
    assert "/accounts/login/" in response.url


def test_demo_is_warehouse_only(client):
    _login(client)

    response = client.get("/demo/", HTTP_HOST="requests.rplwms.com")

    assert response.status_code == 404


def test_demo_starts_with_six_fictional_items_and_clear_isolation_notice(client):
    _login(client)

    response = _get(client)
    body = response.content.decode()

    assert response.status_code == 200
    assert response.context["step"] == 1
    assert len(response.context["items"]) == 6
    assert response.context["total_quantity"] == 124
    assert "Fictional training data" in body
    assert "Isolated from live inventory" in body
    assert "DEMO-1002" in body
    assert "Nitrile Work Gloves" in body


def test_complete_guided_demo_changes_only_session_backed_fictional_state(client):
    _login(client)
    real_item = InventoryItem.objects.create(
        part_number="REAL-UNCHANGED",
        name="Production item",
        quantity_on_hand=17,
    )
    real_counts = (InventoryItem.objects.count(), InventoryTransaction.objects.count())

    response = _post(client, "select_item", part_number="DEMO-1002")
    assert response.status_code == 302
    response = _get(client)
    assert response.context["step"] == 2
    assert next(item for item in response.context["items"] if item["part_number"] == "DEMO-1002")["quantity"] == 8
    assert 'name="bin"' in response.content.decode()
    assert 'name="quantity"' in response.content.decode()

    response = _post(client, "receive_stock", **RECEIPT)
    assert response.status_code == 302
    response = _get(client)
    assert response.context["step"] == 3
    assert next(item for item in response.context["items"] if item["part_number"] == "DEMO-1002")["quantity"] == 20
    assert response.context["total_quantity"] == 136
    assert 'name="requester"' in response.content.decode()
    assert 'name="delivery_location"' in response.content.decode()

    response = _post(client, "create_request", **REQUEST)
    assert response.status_code == 302
    response = _get(client)
    assert response.context["step"] == 4
    assert next(item for item in response.context["items"] if item["part_number"] == "DEMO-2001")["quantity"] == 37
    assert response.context["total_quantity"] == 133
    assert "Training requester" in response.content.decode()
    assert "Training dock" in response.content.decode()
    assert 'name="actual_quantity"' in response.content.decode()
    assert 'name="variance_reason"' in response.content.decode()

    assert _post(client, "fulfill_pick", **PICK).status_code == 302
    response = _get(client)
    assert response.context["step"] == 5
    assert response.context["demo_ticket_status"] == "Picked"
    assert len(response.context["events"]) == 5
    assert response.content.decode().count('name="event_refs"') == 3
    assert client.session[SESSION_KEY]["request"]["requester"] == "Training requester"
    assert client.session[SESSION_KEY]["pick"]["qa"] == "Training QA"

    assert _post(client, "complete_demo", **AUDIT).status_code == 302
    response = _get(client)
    assert response.context["complete"] is True
    assert response.context["progress_percent"] == 100
    assert "Demo complete" in response.content.decode()

    real_item.refresh_from_db()
    assert real_item.quantity_on_hand == 17
    assert (InventoryItem.objects.count(), InventoryTransaction.objects.count()) == real_counts


def test_demo_rejects_out_of_order_and_wrong_item_actions(client):
    _login(client)

    response = _post(client, "receive_stock")
    assert response.status_code == 400
    assert _get(client).context["step"] == 1

    response = _post(client, "select_item", part_number="DEMO-1001")
    assert response.status_code == 400
    assert _get(client).context["step"] == 1


def test_demo_reset_restores_original_fictional_quantities(client):
    _login(client)
    _post(client, "select_item", part_number="DEMO-1002")
    _post(client, "receive_stock", **RECEIPT)

    response = _post(client, "reset")

    assert response.status_code == 302
    response = _get(client)
    assert response.context["step"] == 1
    assert response.context["total_quantity"] == 124
    assert response.context["events"] == [
        {
            "kind": "START",
            "description": "Fictional demo inventory loaded",
            "quantity": "—",
        }
    ]


def test_demo_state_is_isolated_per_authenticated_session(client):
    _login(client, "demo-one")
    _post(client, "select_item", part_number="DEMO-1002")
    assert _get(client).context["step"] == 2

    second = Client()
    _login(second, "demo-two")
    response = _get(second)

    assert response.context["step"] == 1
    assert response.context["total_quantity"] == 124


def test_demo_recovers_from_malformed_session_state(client):
    _login(client)
    session = client.session
    session[SESSION_KEY] = {
        "step": "broken",
        "complete": False,
        "quantities": {},
        "events": [],
        "ticket_status": "Unknown",
    }
    session.save()

    response = _get(client)

    assert response.status_code == 200
    assert response.context["step"] == 1
    assert response.context["total_quantity"] == 124


def test_demo_enforces_http_methods_and_post_host_boundary(client):
    _login(client)

    assert client.post(reverse("inventory_demo"), HTTP_HOST="bbx.rplwms.com").status_code == 405
    assert client.get(reverse("inventory_demo_action"), HTTP_HOST="bbx.rplwms.com").status_code == 405
    assert client.post("/demo/action/", {"action": "reset"}, HTTP_HOST="requests.rplwms.com").status_code == 404


def test_public_demo_is_anonymous_session_only_and_host_isolated(client):
    public_url = "/inventory-demo/"
    public_action = "/inventory-demo/action/"
    assert client.get(public_url, HTTP_HOST="demo.rplwms.com").status_code == 200
    assert client.post(public_action, {"action": "select_item", "part_number": "DEMO-1002"}, HTTP_HOST="demo.rplwms.com").status_code == 302
    assert client.get(public_url, HTTP_HOST="demo.rplwms.com").context["step"] == 2
    assert client.post(public_action, {"action": "receive_stock"}, HTTP_HOST="demo.rplwms.com").status_code == 400
    assert client.get(public_url, HTTP_HOST="demo.rplwms.com").context["step"] == 2
    assert client.get(public_url, HTTP_HOST="bbx.rplwms.com").status_code == 404
    assert client.post(public_action, {"action": "reset"}, HTTP_HOST="bbx.rplwms.com").status_code == 404
    assert client.get("/demo/", HTTP_HOST="bbx.rplwms.com").status_code == 302


def test_public_demo_action_requires_csrf():
    csrf_client = Client(enforce_csrf_checks=True)
    assert csrf_client.post("/inventory-demo/action/", {"action": "reset"}, HTTP_HOST="demo.rplwms.com").status_code == 403


def test_demo_action_requires_authentication(client):
    response = _post(client, "reset")

    assert response.status_code == 302
    assert "/accounts/login/" in response.url


def test_demo_action_rejects_missing_csrf_token():
    csrf_client = Client(enforce_csrf_checks=True)
    _login(csrf_client, "csrf-demo")

    response = csrf_client.post(
        reverse("inventory_demo_action"),
        {"action": "reset"},
        HTTP_HOST="bbx.rplwms.com",
    )

    assert response.status_code == 403
