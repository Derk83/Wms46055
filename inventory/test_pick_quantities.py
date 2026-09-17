import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from .forms import PickTicketForm
from .models import InventoryItem, InventoryTransaction, PickTicket, PickTicketLine


pytestmark = pytest.mark.django_db


def _picker():
    user = get_user_model().objects.create_user(username="line-picker", password="pw")
    user.user_permissions.add(
        Permission.objects.get(content_type__app_label="inventory", codename="view_pickticket"),
        Permission.objects.get(content_type__app_label="inventory", codename="change_pickticket"),
    )
    return user


def _qa_user(username="qa-checker"):
    user = get_user_model().objects.create_user(username=username, password="pw")
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="inventory", codename="change_pickticket"
        )
    )
    return user


def _ticket(user, *, stock=20, requested=5):
    item = InventoryItem.objects.create(
        part_number="PICK-QTY-1", name="Pick quantity item", quantity_on_hand=stock
    )
    ticket = PickTicket.objects.create(
        picked_by_name="Line Picker",
        received_by_name="",
        requested_by_name="Requester",
        building_room="B1",
        location="Staging",
        created_by=user,
    )
    line = PickTicketLine.objects.create(ticket=ticket, item=item, quantity=requested)
    item.refresh_from_db()
    return ticket, line, item


def _pick(client, ticket, line, quantity, reason=""):
    qa_user = _qa_user()
    return client.post(
        reverse("ticket_status_update", args=[ticket.pk]),
        {
            "status": PickTicket.Status.PICKED,
            f"picked_quantity_{line.pk}": str(quantity),
            f"pick_variance_reason_{line.pk}": reason,
            "picked_by_user": str(ticket.created_by_id),
            "qa_checked_by_user": str(qa_user.pk),
        },
        HTTP_HOST="bbx.rplwms.com",
    )


def test_open_ticket_renders_per_line_picked_quantity_and_variance_reason(client):
    user = _picker()
    ticket, line, _item = _ticket(user)
    client.force_login(user)

    response = client.get(
        reverse("ticket_detail", args=[ticket.pk]), HTTP_HOST="bbx.rplwms.com"
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert f'name="picked_quantity_{line.pk}"' in body
    assert f'name="pick_variance_reason_{line.pk}"' in body
    assert "Requested" in body
    assert "Quantity picked" in body


def test_exact_pick_records_quantity_without_extra_stock_movement(client):
    user = _picker()
    ticket, line, item = _ticket(user)
    client.force_login(user)

    response = _pick(client, ticket, line, 5)

    assert response.status_code == 302
    ticket.refresh_from_db()
    line.refresh_from_db()
    item.refresh_from_db()
    assert ticket.status == PickTicket.Status.PICKED
    assert line.picked_quantity == 5
    assert line.pick_variance_reason == ""
    assert item.quantity_on_hand == 15
    assert InventoryTransaction.objects.filter(pick_ticket=ticket).count() == 1


def test_short_pick_requires_reason_and_changes_nothing_when_missing(client):
    user = _picker()
    ticket, line, item = _ticket(user)
    client.force_login(user)

    response = _pick(client, ticket, line, 3)

    assert response.status_code == 302
    ticket.refresh_from_db()
    line.refresh_from_db()
    item.refresh_from_db()
    assert ticket.status == PickTicket.Status.OPEN
    assert line.picked_quantity is None
    assert item.quantity_on_hand == 15
    assert InventoryTransaction.objects.filter(pick_ticket=ticket).count() == 1


def test_short_pick_records_reason_and_returns_unpicked_stock(client):
    user = _picker()
    ticket, line, item = _ticket(user)
    client.force_login(user)

    _pick(client, ticket, line, 3, "Only three units were found in the assigned bin.")

    ticket.refresh_from_db()
    line.refresh_from_db()
    item.refresh_from_db()
    assert ticket.status == PickTicket.Status.PICKED
    assert line.picked_quantity == 3
    assert line.pick_variance_reason == "Only three units were found in the assigned bin."
    assert item.quantity_on_hand == 17
    variance = line.pick_variance_transaction
    assert variance.transaction_type == InventoryTransaction.TransactionType.ADJUSTMENT
    assert variance.quantity_delta == 2
    assert "Short pick" in variance.notes


def test_over_pick_requires_reason_and_consumes_additional_stock(client):
    user = _picker()
    ticket, line, item = _ticket(user)
    client.force_login(user)

    _pick(client, ticket, line, 7, "Two extra units were required at the installation point.")

    ticket.refresh_from_db()
    line.refresh_from_db()
    item.refresh_from_db()
    assert ticket.status == PickTicket.Status.PICKED
    assert line.picked_quantity == 7
    assert item.quantity_on_hand == 13
    assert line.pick_variance_transaction.transaction_type == InventoryTransaction.TransactionType.PICK
    assert line.pick_variance_transaction.quantity_delta == -2


def test_unavailable_over_pick_rolls_back_all_lines_and_status(client):
    user = _picker()
    ticket, line, item = _ticket(user, stock=5, requested=5)
    other = InventoryItem.objects.create(
        part_number="PICK-QTY-2", name="Second item", quantity_on_hand=10
    )
    other_line = PickTicketLine.objects.create(ticket=ticket, item=other, quantity=2)
    qa_user = _qa_user()
    client.force_login(user)

    response = client.post(
        reverse("ticket_status_update", args=[ticket.pk]),
        {
            "status": PickTicket.Status.PICKED,
            f"picked_quantity_{line.pk}": "6",
            f"pick_variance_reason_{line.pk}": "Requested one additional unit.",
            f"picked_quantity_{other_line.pk}": "1",
            f"pick_variance_reason_{other_line.pk}": "One unit could not be located.",
            "picked_by_user": str(user.pk),
            "qa_checked_by_user": str(qa_user.pk),
        },
        HTTP_HOST="bbx.rplwms.com",
    )

    assert response.status_code == 302
    ticket.refresh_from_db()
    line.refresh_from_db()
    other_line.refresh_from_db()
    item.refresh_from_db()
    other.refresh_from_db()
    assert ticket.status == PickTicket.Status.OPEN
    assert line.picked_quantity is None
    assert other_line.picked_quantity is None
    assert item.quantity_on_hand == 0
    assert other.quantity_on_hand == 8


def test_deleting_short_picked_line_restores_only_actual_picked_quantity(client):
    user = _picker()
    ticket, line, item = _ticket(user)
    client.force_login(user)
    _pick(client, ticket, line, 3, "Two units were not in the bin.")

    line.refresh_from_db()
    line.delete()

    item.refresh_from_db()
    assert item.quantity_on_hand == 20
    assert not InventoryTransaction.objects.filter(pick_ticket=ticket, item=item).exists()


def test_transaction_history_uses_readable_types_and_descriptive_activity(client):
    user = _picker()
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="inventory", codename="view_inventorytransaction"
        )
    )
    ticket, _line, _item = _ticket(user)
    client.force_login(user)

    response = client.get(reverse("transaction_history"), HTTP_HOST="bbx.rplwms.com")

    assert response.status_code == 200
    body = response.content.decode()
    assert "Removed 5 units from inventory" in body
    assert ticket.ticket_number in body
    assert "transaction-type tx-type-badge transaction-type-pick tx-type-pick" in body


def test_picker_and_qa_checker_are_required_and_must_be_different(client):
    user = _picker()
    ticket, line, item = _ticket(user)
    client.force_login(user)

    response = client.post(
        reverse("ticket_status_update", args=[ticket.pk]),
        {
            "status": PickTicket.Status.PICKED,
            f"picked_quantity_{line.pk}": "5",
            f"pick_variance_reason_{line.pk}": "",
            "picked_by_user": str(user.pk),
            "qa_checked_by_user": str(user.pk),
        },
        HTTP_HOST="bbx.rplwms.com",
    )

    assert response.status_code == 302
    ticket.refresh_from_db()
    line.refresh_from_db()
    item.refresh_from_db()
    assert ticket.status == PickTicket.Status.OPEN
    assert line.picked_quantity is None
    assert item.quantity_on_hand == 15


def test_assigned_picker_must_acknowledge_ticket_and_prompt_clears(client):
    user = _picker()
    ticket, _line, _item = _ticket(user)
    ticket.assigned_to = user
    ticket.assigned_at = ticket.created_at
    ticket.save(update_fields=["assigned_to", "assigned_at", "updated_at"])
    client.force_login(user)

    page = client.get(reverse("ticket_list"), HTTP_HOST="bbx.rplwms.com")
    assert page.status_code == 200
    assert "data-assignment-acknowledgement" in page.content.decode()
    assert ticket.ticket_number in page.content.decode()

    response = client.post(
        reverse("ticket_acknowledge", args=[ticket.pk]),
        {"next": reverse("ticket_list")},
        HTTP_HOST="bbx.rplwms.com",
    )
    assert response.status_code == 302
    ticket.refresh_from_db()
    assert ticket.acknowledged_by == user
    assert ticket.acknowledged_at is not None

    page = client.get(reverse("ticket_list"), HTTP_HOST="bbx.rplwms.com")
    assert "data-assignment-acknowledgement" not in page.content.decode()


def test_other_user_cannot_acknowledge_someone_elses_assignment(client):
    assigned = _picker()
    other = _qa_user("other-picker")
    ticket, _line, _item = _ticket(assigned)
    ticket.assigned_to = assigned
    ticket.save(update_fields=["assigned_to", "updated_at"])
    client.force_login(other)

    response = client.post(
        reverse("ticket_acknowledge", args=[ticket.pk]), HTTP_HOST="bbx.rplwms.com"
    )

    assert response.status_code == 404
    ticket.refresh_from_db()
    assert ticket.acknowledged_at is None


def test_duplicate_item_line_delete_removes_only_its_reservation():
    user = _picker()
    item = InventoryItem.objects.create(
        part_number="DUP-LINE", name="Duplicate line item", quantity_on_hand=10
    )
    ticket = PickTicket.objects.create(
        picked_by_name=user.username,
        received_by_name="Receiver",
        requested_by_name="Requester",
        building_room="B1",
        location="Dock",
        created_by=user,
    )
    first = PickTicketLine.objects.create(ticket=ticket, item=item, quantity=2)
    second = PickTicketLine.objects.create(ticket=ticket, item=item, quantity=3)

    first.delete()
    item.refresh_from_db()

    assert item.quantity_on_hand == 7
    assert PickTicketLine.objects.filter(pk=second.pk).exists()
    assert InventoryTransaction.objects.filter(
        item=item,
        pick_ticket=ticket,
        transaction_type=InventoryTransaction.TransactionType.PICK,
    ).values_list("pk", flat=True).get() == second.reservation_transaction_id


def test_malformed_picker_id_is_a_validation_error_not_a_server_error(client):
    user = _picker()
    ticket, line, item = _ticket(user)
    qa_user = _qa_user()
    client.force_login(user)

    response = client.post(
        reverse("ticket_status_update", args=[ticket.pk]),
        {
            "status": PickTicket.Status.PICKED,
            f"picked_quantity_{line.pk}": str(line.quantity),
            "picked_by_user": "not-a-user-id",
            "qa_checked_by_user": str(qa_user.pk),
        },
        HTTP_HOST="bbx.rplwms.com",
        follow=True,
    )
    ticket.refresh_from_db()
    item.refresh_from_db()

    assert response.status_code == 200
    assert ticket.status == PickTicket.Status.OPEN
    assert item.quantity_on_hand == 15
    assert "Choose an active warehouse user who picked the order" in response.content.decode()


def test_open_ticket_cannot_skip_picked_status(client):
    user = _picker()
    ticket, line, item = _ticket(user)
    client.force_login(user)

    response = client.post(
        reverse("ticket_status_update", args=[ticket.pk]),
        {"status": PickTicket.Status.RECEIVED},
        HTTP_HOST="bbx.rplwms.com",
        follow=True,
    )
    ticket.refresh_from_db()
    line.refresh_from_db()
    item.refresh_from_db()

    assert response.status_code == 200
    assert ticket.status == PickTicket.Status.OPEN
    assert line.picked_quantity is None
    assert item.quantity_on_hand == 15
    assert "Record picked quantities and signoff" in response.content.decode()


def test_assigned_ticket_requires_acknowledgement_and_assigned_picker(client):
    assigned = _picker()
    ticket, line, item = _ticket(assigned)
    ticket.assigned_to = assigned
    ticket.assigned_at = ticket.created_at
    ticket.save(update_fields=["assigned_to", "assigned_at", "updated_at"])
    client.force_login(assigned)

    response = _pick(client, ticket, line, line.quantity)
    ticket.refresh_from_db()
    assert ticket.status == PickTicket.Status.OPEN
    qa_user = get_user_model().objects.get(username="qa-checker")

    client.post(
        reverse("ticket_acknowledge", args=[ticket.pk]),
        HTTP_HOST="bbx.rplwms.com",
    )
    response = client.post(
        reverse("ticket_status_update", args=[ticket.pk]),
        {
            "status": PickTicket.Status.PICKED,
            f"picked_quantity_{line.pk}": str(line.quantity),
            "picked_by_user": str(assigned.pk),
            "qa_checked_by_user": str(qa_user.pk),
        },
        HTTP_HOST="bbx.rplwms.com",
    )
    ticket.refresh_from_db()
    item.refresh_from_db()

    assert response.status_code == 302
    assert ticket.status == PickTicket.Status.PICKED
    assert item.quantity_on_hand == 15


def test_fulfilled_standalone_ticket_cannot_be_edited(client):
    user = _picker()
    ticket, line, _item = _ticket(user)
    client.force_login(user)
    _pick(client, ticket, line, line.quantity)
    ticket.refresh_from_db()
    client.force_login(user)

    response = client.get(
        reverse("ticket_edit", args=[ticket.pk]),
        HTTP_HOST="bbx.rplwms.com",
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("ticket_detail", args=[ticket.pk])


def test_assignment_prompt_is_visible_on_warehouse_alias(client):
    user = _picker()
    ticket, _line, _item = _ticket(user)
    ticket.assigned_to = user
    ticket.assigned_at = ticket.created_at
    ticket.save(update_fields=["assigned_to", "assigned_at", "updated_at"])
    client.force_login(user)

    response = client.get(
        reverse("ticket_list"), HTTP_HOST="warehouse.bonksystems.com"
    )

    assert response.status_code == 200
    assert "data-assignment-acknowledgement" in response.content.decode()


def test_ticket_form_cannot_set_workflow_status_directly():
    user = _picker()
    form = PickTicketForm(
        {
            "date": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "status": PickTicket.Status.CLOSED,
            "picked_by_name": str(user.pk),
            "received_by_name": "",
            "requested_by_name": "Requester",
            "building_room": "B1",
            "location": "Staging",
            "notes": "",
        }
    )

    assert "status" not in form.fields
    assert form.is_valid(), form.errors
    ticket = form.save(commit=False)
    ticket.created_by = user
    ticket.save()
    assert ticket.status == PickTicket.Status.OPEN


def test_ticket_form_excludes_users_who_cannot_fulfill_tickets():
    eligible = _picker()
    ineligible = get_user_model().objects.create_user(username="no-ticket-permission")

    choices = dict(PickTicketForm().fields["picked_by_name"].choices)

    assert str(eligible.pk) in choices
    assert str(ineligible.pk) not in choices
