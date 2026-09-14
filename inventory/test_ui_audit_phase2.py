"""Phase 2 UI audit contracts for clear, canonical workflow actions."""
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from .models import CycleCount, InventoryItem, MaterialRequest, PickTicket


User = get_user_model()
TEMPLATES = Path(__file__).parent / "templates" / "inventory"


def _perm(codename):
    return Permission.objects.get(content_type__app_label="inventory", codename=codename)


def _actor(username, *codenames):
    user = User.objects.create_user(username=username, password="pw")
    user.user_permissions.add(*[_perm(codename) for codename in codenames])
    return user


def _body(client, user, route, *, host="bbx.rplwms.com"):
    client.force_login(user)
    response = client.get(route, HTTP_HOST=host)
    assert response.status_code == 200
    return response.content.decode()


def _template(name):
    return (TEMPLATES / name).read_text()


def test_form_surfaces_have_one_canonical_exit_destination():
    contracts = {
        "change_password.html": ("{% url 'settings' %}", 1),
        "group_permissions.html": ("{% url 'settings' %}?tab=groups", 1),
        "inventory_bulk_edit.html": ("{% url 'inventory_list' %}", 1),
        "cycle_count_create.html": ("{% url 'cycle_count_list' %}", 1),
        "receiving_ticket_confirm_delete.html": ("{% url 'receiving_log' %}", 1),
        "user_confirm_delete.html": ("{% url 'settings' %}?tab=users", 1),
        "user_edit_merged.html": ("{% url 'settings' %}?tab=users", 1),
        "inventory_labels.html": ("{% url 'item_detail' item.pk %}", 1),
    }
    for name, (destination, count) in contracts.items():
        assert _template(name).count(destination) == count, name


def test_repeated_record_links_are_one_per_rendered_viewport_or_card():
    assert _template("inventory_list.html").count("{% url 'item_detail' item.pk %}") == 2
    assert _template("location_detail.html").count("{% url 'item_detail' pk=item.pk %}") == 1
    assert _template("dashboard.html").count("{% url 'ticket_detail' ticket.pk %}") == 1
    assert _template("_material_request_columns.html").count("{% url 'material_request_detail' material_request.pk %}") == 1
    queue = _template("_material_request_queue.html")
    assert queue.count("{% url 'material_request_detail' material_request.pk %}") == 1
    assert queue.count("{% url 'ticket_detail' material_request.pick_ticket.pk %}") == 1


@pytest.mark.django_db
def test_linked_ticket_uses_material_request_as_canonical_edit_surface_with_ticket_delete(client):
    actor = _actor(
        "linked-editor",
        "view_pickticket", "change_pickticket", "delete_pickticket", "print_pickticket",
        "view_materialrequest", "change_materialrequest", "change_materialrequestline",
        "delete_materialrequest", "delete_materialrequestline",
        "view_inventoryitem",
    )
    ticket = PickTicket.objects.create(created_by=actor)
    material_request = MaterialRequest.objects.create(creator=actor, pick_ticket=ticket)

    body = _body(client, actor, reverse("ticket_detail", args=[ticket.pk]))
    assert reverse("ticket_edit", args=[ticket.pk]) not in body
    assert reverse("ticket_delete", args=[ticket.pk]) in body
    assert f'href="{reverse("material_request_detail", args=[material_request.pk])}"' in body
    assert "Open Material Request" in body
    assert f'href="{reverse("material_request_edit", args=[material_request.pk])}"' in body
    assert "Edit Material Request" in body
    assert reverse("ticket_print", args=[ticket.pk]) in body
    assert reverse("ticket_status_update", args=[ticket.pk]) in body


@pytest.mark.django_db
def test_linked_ticket_material_request_actions_keep_exact_permissions(client):
    owner = _actor("linked-owner")
    ticket = PickTicket.objects.create(created_by=owner)
    material_request = MaterialRequest.objects.create(creator=owner, pick_ticket=ticket)
    viewer = _actor("linked-viewer", "view_pickticket", "view_all_materialrequests", "view_materialrequest")

    body = _body(client, viewer, reverse("ticket_detail", args=[ticket.pk]))
    assert reverse("material_request_detail", args=[material_request.pk]) in body
    assert reverse("material_request_edit", args=[material_request.pk]) not in body
    assert reverse("material_request_delete", args=[material_request.pk]) not in body


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("status", "next_status", "primary_label"),
    [
        ("OPEN", "PICKED", "Mark Picked"),
        ("PICKED", "RECEIVED", "Ready for Delivery"),
        ("RECEIVED", "CLOSED", "Mark Closed/Delivered"),
    ],
)
def test_ticket_detail_promotes_only_the_next_status(client, status, next_status, primary_label):
    actor = _actor(f"status-{status.lower()}", "view_pickticket", "change_pickticket")
    ticket = PickTicket.objects.create(created_by=actor, status=status)
    body = _body(client, actor, reverse("ticket_detail", args=[ticket.pk]))
    assert f'data-next-status="{next_status}"' in body
    assert primary_label in body


def test_ticket_closed_wording_and_receiving_labels_are_consistent():
    assert "Mark Closed/Delivered" in _template("ticket_detail.html")
    assert "Mark Closed/Delivered" in _template("ticket_form.html")
    for name in ("item_detail.html", "inventory_list.html", "low_stock_list.html", "receive_stock.html"):
        source = _template(name)
        assert "Adjust Inventory" not in source, name
        assert "+ Stock" not in source, name
        assert "Receive Stock" in source, name


def test_export_controls_link_to_export_center_with_accurate_labels():
    receiving = _template("receiving_log.html")
    low_stock = _template("low_stock_list.html")
    assert "Export Inventory" not in receiving
    assert "{% url 'export_center' %}" in receiving
    assert "Export Center" in receiving
    assert "Export Inventory CSV" not in low_stock
    assert "{% url 'export_center' %}" in low_stock
    assert "Export Center" in low_stock


@pytest.mark.django_db
def test_item_forms_and_archived_cycle_count_back_links_use_contextual_parent(client):
    admin = User.objects.create_superuser("phase2-admin", "", "pw")
    item = InventoryItem.objects.create(part_number="P2-BACK", name="Back target", quantity_on_hand=1)
    edit = _body(client, admin, reverse("inventory_edit", args=[item.pk]))
    receive = _body(client, admin, reverse("receive_stock", args=[item.pk]))
    assert f'href="{reverse("item_detail", args=[item.pk])}"' in edit
    assert f'href="{reverse("item_detail", args=[item.pk])}"' in receive

    cycle_count = CycleCount.objects.create(
        name="Archived phase 2",
        created_by=admin,
        status=CycleCount.Status.COMPLETED,
        archived_at=timezone.now(),
        archived_by=admin,
    )
    detail = _body(client, admin, reverse("cycle_count_detail", args=[cycle_count.pk]))
    assert f'href="{reverse("cycle_count_archive")}"' in detail
    assert "Back to archive" in detail
    assert "Back to cycle counts" not in detail


def test_receiving_edit_back_and_cancel_both_use_receiving_log():
    source = _template("receiving_ticket_edit.html")
    assert source.count("{% url 'receiving_log' %}") == 2
    assert "{% url 'receiving' %}" not in source
