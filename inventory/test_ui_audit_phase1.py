"""Phase 1 UI audit contracts: routes, permissions, accessibility, and tokens."""
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.urls import resolve, reverse

from . import views
from .models import InventoryItem, InventoryTransaction, MaterialRequest, PickTicket, ReceivingTicket


User = get_user_model()
APP_CSS = Path(__file__).parent / "static" / "inventory" / "css" / "app.css"


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


@pytest.mark.django_db
def test_batch_adjust_and_selection_bulk_edit_have_distinct_usable_routes(client):
    actor = _actor("batch-adjuster", "bulk_adjust_inventory")
    client.force_login(actor)

    assert reverse("batch_adjust") == "/inventory/batch-adjust/"
    assert resolve(reverse("batch_adjust")).func is views.bulk_adjust
    assert reverse("bulk_adjust") == "/inventory/bulk-edit/"
    assert resolve(reverse("bulk_adjust")).func is views.inventory_bulk_edit
    assert reverse("inventory_bulk_edit") == "/inventory/bulk-edit/"

    # Phase 4 removes list/navigation duplicates from Dashboard Quick Actions;
    # the compatibility route remains directly usable below.
    inventory = client.get(reverse("inventory_list"), HTTP_HOST="bbx.rplwms.com")
    assert f'action="{reverse("inventory_bulk_edit")}"' in inventory.content.decode()
    assert "Bulk Edit Selected" in inventory.content.decode()

    batch = client.get(reverse("batch_adjust"), HTTP_HOST="bbx.rplwms.com")
    assert batch.status_code == 200
    assert "Bulk Adjust Inventory" in batch.content.decode()

    item = InventoryItem.objects.create(part_number="BATCH-1", name="Batch item", quantity_on_hand=4)
    added = client.post(
        reverse("batch_adjust"),
        {"action": "add_line", "item": item.pk, "quantity_delta": 2, "notes": "audit"},
        HTTP_HOST="bbx.rplwms.com",
    )
    assert added.status_code == 302
    assert added.url == reverse("batch_adjust")


@pytest.mark.django_db
def test_batch_adjust_commits_through_immutable_ledger_and_defers_new_items(client):
    actor = _actor("batch-ledger-adjuster", "bulk_adjust_inventory")
    client.force_login(actor)
    existing = InventoryItem.objects.create(
        part_number="BATCH-LEDGER-1", name="Existing batch item", quantity_on_hand=4,
    )

    staged_existing = client.post(
        reverse("batch_adjust"),
        {"action": "add_line", "item": existing.pk, "quantity_delta": 3, "notes": "existing adjustment"},
        HTTP_HOST="bbx.rplwms.com",
    )
    assert staged_existing.status_code == 302

    staged_new = client.post(
        reverse("batch_adjust"),
        {
            "action": "add_line", "item": "", "part_number": "BATCH-NEW-1",
            "name": "Deferred batch item", "quantity_delta": 2, "notes": "new adjustment",
        },
        HTTP_HOST="bbx.rplwms.com",
    )
    assert staged_new.status_code == 302
    assert not InventoryItem.objects.filter(part_number="BATCH-NEW-1").exists()

    committed = client.post(
        reverse("batch_adjust"), {"action": "commit"}, HTTP_HOST="bbx.rplwms.com",
    )
    assert committed.status_code == 302
    existing.refresh_from_db()
    assert existing.quantity_on_hand == 7
    new_item = InventoryItem.objects.get(part_number="BATCH-NEW-1")
    assert new_item.quantity_on_hand == 2
    assert list(
        InventoryTransaction.objects.filter(
            item__in=(existing, new_item),
            transaction_type=InventoryTransaction.TransactionType.ADJUSTMENT,
        ).order_by("item__part_number").values_list("quantity_delta", flat=True)
    ) == [3, 2]


@pytest.mark.django_db
def test_ticket_surfaces_only_show_actions_with_exact_endpoint_permissions(client):
    ticket = PickTicket.objects.create(created_by=_actor("ticket-owner"))
    viewer = _actor("ticket-viewer", "view_pickticket")

    list_body = _body(client, viewer, reverse("ticket_list"))
    assert 'for="ticket-status-filter"' in list_body
    assert reverse("ticket_create") not in list_body
    assert reverse("export_tickets_csv") in list_body
    assert reverse("ticket_edit", args=[ticket.pk]) not in list_body
    assert reverse("ticket_print", args=[ticket.pk]) not in list_body

    detail_body = _body(client, viewer, reverse("ticket_detail", args=[ticket.pk]))
    for route in (
        reverse("ticket_create"),
        reverse("ticket_edit", args=[ticket.pk]),
        reverse("ticket_print", args=[ticket.pk]),
        reverse("ticket_status_update", args=[ticket.pk]),
        reverse("ticket_delete", args=[ticket.pk]),
    ):
        assert route not in detail_body

    editor = _actor("ticket-editor", "view_pickticket", "change_pickticket")
    editor_body = _body(client, editor, reverse("ticket_detail", args=[ticket.pk]))
    assert reverse("ticket_edit", args=[ticket.pk]) in editor_body
    assert reverse("ticket_status_update", args=[ticket.pk]) in editor_body
    assert reverse("ticket_print", args=[ticket.pk]) not in editor_body


@pytest.mark.django_db
def test_material_request_surfaces_require_complete_decorator_permission_sets(client):
    owner = _actor("request-owner")
    ticket = PickTicket.objects.create(created_by=owner)
    request_obj = MaterialRequest.objects.create(creator=owner, pick_ticket=ticket)
    viewer = _actor("request-viewer", "view_materialrequest", "view_all_materialrequests")

    board = _body(client, viewer, reverse("material_request_board"))
    assert reverse("material_request_create") not in board
    assert reverse("material_request_edit", args=[request_obj.pk]) not in board
    assert reverse("ticket_detail", args=[ticket.pk]) not in board

    detail = _body(client, viewer, reverse("material_request_detail", args=[request_obj.pk]))
    assert reverse("material_request_edit", args=[request_obj.pk]) not in detail
    assert reverse("material_request_delete", args=[request_obj.pk]) not in detail

    partial = _body(client, viewer, reverse("material_request_board") + "?view=board&partial=1")
    assert reverse("material_request_edit", args=[request_obj.pk]) not in partial

    editor = _actor(
        "request-editor", "view_materialrequest", "view_all_materialrequests",
        "change_materialrequest", "change_materialrequestline", "view_inventoryitem",
    )
    editor_board = _body(client, editor, reverse("material_request_board") + "?view=board")
    assert reverse("material_request_edit", args=[request_obj.pk]) in editor_board


@pytest.mark.django_db
def test_receiving_low_stock_scanner_and_dashboard_controls_match_endpoint_permissions(client):
    owner = _actor("operations-owner")
    item = InventoryItem.objects.create(part_number="AUDIT-1", name="Audit item", quantity_on_hand=-1)
    pick = PickTicket.objects.create(created_by=owner)
    receiving = ReceivingTicket.objects.create(created_by=owner)

    log_viewer = _actor("log-viewer", "view_receiving_log")
    log_body = _body(client, log_viewer, reverse("receiving_log"))
    for route in (
        reverse("receiving"), reverse("export_inventory_xlsx"),
        reverse("receiving_ticket_print", args=[receiving.pk]),
        reverse("receiving_ticket_edit", args=[receiving.pk]),
        reverse("receiving_ticket_delete", args=[receiving.pk]),
    ):
        assert route not in log_body

    basic = _actor("basic-operator")
    low_body = _body(client, basic, reverse("low_stock_list"))
    assert reverse("export_inventory_csv") not in low_body
    assert reverse("receive_stock", args=[item.pk]) not in low_body
    assert reverse("inventory_edit", args=[item.pk]) not in low_body

    scanner = _actor("scanner-only", "scan_codes")
    scanner_body = _body(client, scanner, reverse("scanner"))
    assert reverse("ticket_create") not in scanner_body

    dashboard = _body(client, basic, reverse("dashboard"))
    assert reverse("ticket_list") not in dashboard
    assert reverse("ticket_detail", args=[pick.pk]) not in dashboard
    assert reverse("receiving_ticket_print", args=[receiving.pk]) not in dashboard


@pytest.mark.django_db
def test_delegated_settings_exposes_only_corresponding_actionable_sections(client):
    target = User.objects.create_user(username="settings-target", password="pw")
    group = Group.objects.create(name="Audit group")

    users_manager = _actor("users-manager", "manage_users")
    users_body = _body(client, users_manager, reverse("settings") + "?tab=users")
    assert reverse("user_create") in users_body
    assert reverse("user_edit", args=[target.pk]) in users_body
    assert reverse("group_create") not in users_body
    assert reverse("group_permissions", args=[group.pk]) not in users_body

    groups_manager = _actor("groups-manager", "manage_groups")
    groups_body = _body(client, groups_manager, reverse("settings") + "?tab=groups")
    assert reverse("group_create") in groups_body
    assert reverse("group_rename", args=[group.pk]) in groups_body
    assert reverse("group_delete", args=[group.pk]) in groups_body
    assert reverse("user_create") not in groups_body
    assert reverse("group_permissions", args=[group.pk]) not in groups_body

    permissions_manager = _actor("permissions-manager", "manage_group_permissions")
    permissions_body = _body(client, permissions_manager, reverse("settings") + "?tab=groups")
    assert reverse("group_permissions", args=[group.pk]) in permissions_body
    assert reverse("group_create") not in permissions_body
    assert reverse("group_rename", args=[group.pk]) not in permissions_body
    assert reverse("group_delete", args=[group.pk]) not in permissions_body


@pytest.mark.django_db
def test_phase1_form_controls_have_accessible_names(client):
    admin = User.objects.create_superuser("accessible-admin", "", "pw")
    item = InventoryItem.objects.create(part_number="A11Y-1", name="Accessible item", quantity_on_hand=1)

    inventory = _body(client, admin, reverse("inventory_list"))
    assert f'aria-label="Select {item.part_number}"' in inventory

    request_form = _body(client, admin, reverse("material_request_create"), host="requests.rplwms.com")
    for expected in (
        'aria-label="Delivery date"',
        'aria-label="Delivery time"',
        'aria-label="Item for request line 1"',
        'aria-label="Quantity for request line 1"',
        'aria-label="Notes for request line 1"',
        'aria-label="Item for new request line"',
    ):
        assert expected in request_form



def test_phase1_compatibility_tokens_and_light_contrast_rules_are_defined():
    css = APP_CSS.read_text()
    for token in (
        "--card:var(--surface)", "--primary:var(--accent)",
        "--text-muted:var(--muted)", "--text-secondary:var(--muted)",
        "--radius-sm:", "--radius-md:var(--radius)", "--radius-lg:",
        "--shadow-sm:", "--shadow-md:", "--focus-ring:var(--focus)",
        "--border-strong:", "--accent-soft:",
    ):
        assert token in css
    assert ':root[data-theme="light"] .item-detail-link' in css
    assert ':root[data-theme="light"] .mobile-inventory-card' in css
    assert ':root[data-theme="light"] .mobile-meta-grid' in css
    assert ':root[data-theme="light"] .urgent-request-control label span' in css
    for transaction_class in (
        ".transaction-pick", ".transaction-receipt",
        ".transaction-adjustment", ".transaction-other",
    ):
        assert transaction_class in css
