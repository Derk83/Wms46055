import io
import uuid

from unittest.mock import patch

import openpyxl
import pytest
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.contrib.auth.models import User
from django.urls import reverse

from inventory.models import InventoryItem, PickTicket, PickTicketLine, InventoryTransaction, ReceivingTicket, ReceivingLine


@pytest.mark.django_db
def test_inventory_requires_login(client):
    response = client.get(reverse("inventory_list"))
    assert response.status_code == 302
    assert reverse("login") in response["Location"]


@pytest.mark.django_db
def test_pick_ticket_line_delete_restores_inventory(client):
    """Deleting a pick ticket line should restore quantity and remove PICK transaction."""
    user = User.objects.create_user(username="deleter", password="testpass123")
    item = InventoryItem.objects.create(part_number="GLOVE-L", name="Gloves - L", quantity_on_hand=10)
    ticket = PickTicket.objects.create(
        picked_by_name="Alice",
        received_by_name="Bob",
        requested_by_name="Charlie",
        building_room="B1/101",
        location="Warehouse",
        created_by=user,
    )
    line = PickTicketLine.objects.create(ticket=ticket, item=item, quantity=3)
    item.refresh_from_db()
    assert item.quantity_on_hand == 7  # 10 - 3
    assert InventoryTransaction.objects.filter(transaction_type="PICK", pick_ticket=ticket).exists()

    # Delete the line
    line.delete()

    item.refresh_from_db()
    assert item.quantity_on_hand == 10  # Restored
    assert not InventoryTransaction.objects.filter(transaction_type="PICK", pick_ticket=ticket).exists()


@pytest.mark.django_db
def test_bulk_receiving_counts_each_quantity_once_and_creates_one_ledger_entry():
    from inventory.views import process_bulk_receiving

    user = User.objects.create_user(username="bulk-receiver", password="testpass123")
    item = InventoryItem.objects.create(
        part_number="BULK-RCV-1", name="Bulk receive item", quantity_on_hand=10
    )
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Part #", "Quantity", "Notes"])
    sheet.append([item.part_number, 3, "One shipment"])
    spreadsheet = io.BytesIO()
    workbook.save(spreadsheet)
    spreadsheet.seek(0)

    success_count, error_count, errors = process_bulk_receiving(spreadsheet, user)

    item.refresh_from_db()
    receipts = InventoryTransaction.objects.filter(
        item=item, transaction_type=InventoryTransaction.TransactionType.RECEIPT
    )
    assert (success_count, error_count, errors) == (1, 0, [])
    assert item.quantity_on_hand == 13
    assert receipts.count() == 1
    assert receipts.get().quantity_delta == 3


@pytest.mark.django_db(transaction=True)
def test_receiving_line_creation_rolls_back_if_inventory_adjustment_fails():
    user = User.objects.create_user(username="receive-failure", password="testpass123")
    item = InventoryItem.objects.create(part_number="RCV-FAIL", name="Failure item", quantity_on_hand=5)
    ticket = ReceivingTicket.objects.create(created_by=user)

    with patch.object(InventoryItem, "adjust_quantity", side_effect=RuntimeError("ledger failure")):
        with pytest.raises(RuntimeError, match="ledger failure"):
            ReceivingLine.objects.create(ticket=ticket, item=item, quantity=2)

    assert not ReceivingLine.objects.filter(ticket=ticket).exists()
    item.refresh_from_db()
    assert item.quantity_on_hand == 5


@pytest.mark.django_db
def test_deleting_one_duplicate_item_receiving_line_preserves_other_ledger():
    user = User.objects.create_user(username="duplicate-receiver", password="testpass123")
    item = InventoryItem.objects.create(part_number="RCV-DUP", name="Duplicate item", quantity_on_hand=10)
    ticket = ReceivingTicket.objects.create(created_by=user)
    first = ReceivingLine.objects.create(ticket=ticket, item=item, quantity=2)
    second = ReceivingLine.objects.create(ticket=ticket, item=item, quantity=3)

    first.delete()

    item.refresh_from_db()
    assert item.quantity_on_hand == 13
    assert ReceivingLine.objects.filter(pk=second.pk).exists()
    assert InventoryTransaction.objects.filter(receiving_line=second).count() == 1
    assert InventoryTransaction.objects.filter(
        item=item, transaction_type=InventoryTransaction.TransactionType.REVERSAL
    ).count() == 1


@pytest.mark.django_db
def test_direct_receiving_line_quantity_or_item_change_is_rejected():
    user = User.objects.create_user(username="update-receiver", password="testpass123")
    item = InventoryItem.objects.create(part_number="RCV-UPD", name="Update item", quantity_on_hand=10)
    ticket = ReceivingTicket.objects.create(created_by=user)
    line = ReceivingLine.objects.create(ticket=ticket, item=item, quantity=2)
    line.quantity = 5

    with pytest.raises(ValidationError):
        line.save()

    line.refresh_from_db()
    item.refresh_from_db()
    assert line.quantity == 2
    assert item.quantity_on_hand == 12


@pytest.mark.django_db
def test_direct_receiving_line_item_change_is_rejected():
    user = User.objects.create_user(username="item-update-receiver", password="testpass123")
    original_item = InventoryItem.objects.create(
        part_number="RCV-ITEM-A", name="Original item", quantity_on_hand=10
    )
    replacement_item = InventoryItem.objects.create(
        part_number="RCV-ITEM-B", name="Replacement item", quantity_on_hand=20
    )
    ticket = ReceivingTicket.objects.create(created_by=user)
    line = ReceivingLine.objects.create(ticket=ticket, item=original_item, quantity=2)
    line.item = replacement_item

    with pytest.raises(ValidationError):
        line.save()

    line.refresh_from_db()
    original_item.refresh_from_db()
    replacement_item.refresh_from_db()
    assert line.item == original_item
    assert original_item.quantity_on_hand == 12
    assert replacement_item.quantity_on_hand == 20


@pytest.mark.django_db
def test_direct_receiving_line_ticket_reassignment_is_rejected():
    user = User.objects.create_user(username="ticket-reassign-receiver", password="testpass123")
    item = InventoryItem.objects.create(
        part_number="RCV-TICKET-MOVE", name="Ticket move item", quantity_on_hand=10
    )
    original_ticket = ReceivingTicket.objects.create(created_by=user)
    replacement_ticket = ReceivingTicket.objects.create(created_by=user)
    line = ReceivingLine.objects.create(ticket=original_ticket, item=item, quantity=2)
    line.ticket = replacement_ticket

    with pytest.raises(ValidationError):
        line.save()

    line.refresh_from_db()
    assert line.ticket == original_ticket
    assert line.receipt_transaction.created_by == original_ticket.created_by


@pytest.mark.django_db
def test_receipt_and_reversal_transactions_are_immutable():
    user = User.objects.create_user(username="immutable-ledger", password="testpass123")
    item = InventoryItem.objects.create(
        part_number="RCV-IMMUTABLE", name="Immutable receipt", quantity_on_hand=10
    )
    ticket = ReceivingTicket.objects.create(created_by=user)
    line = ReceivingLine.objects.create(ticket=ticket, item=item, quantity=2)
    receipt = line.receipt_transaction
    line.delete()
    reversal = receipt.reversal_transaction

    with pytest.raises(ValidationError):
        receipt.delete()
    with pytest.raises(ValidationError):
        InventoryTransaction.objects.filter(pk=receipt.pk).delete()
    with pytest.raises(ValidationError):
        reversal.delete()
    with pytest.raises(ValidationError):
        InventoryTransaction.objects.filter(pk=reversal.pk).delete()

    assert InventoryTransaction.objects.filter(pk__in=[receipt.pk, reversal.pk]).count() == 2


@pytest.mark.django_db
def test_receipt_transaction_fields_cannot_be_mutated_or_forged():
    user = User.objects.create_user(username="immutable-ledger-fields", password="testpass123")
    item = InventoryItem.objects.create(
        part_number="RCV-IMMUTABLE-FIELDS", name="Immutable receipt fields", quantity_on_hand=10
    )
    ticket = ReceivingTicket.objects.create(created_by=user)
    line = ReceivingLine.objects.create(ticket=ticket, item=item, quantity=2)
    receipt = line.receipt_transaction

    receipt.transaction_type = InventoryTransaction.TransactionType.ADJUSTMENT
    with pytest.raises(ValidationError):
        receipt.save()
    with pytest.raises(ValidationError):
        InventoryTransaction.objects.filter(pk=receipt.pk).update(quantity_delta=99)
    with pytest.raises(ValidationError):
        InventoryTransaction.objects.bulk_update([receipt], ["notes"])
    with pytest.raises(ValidationError):
        InventoryTransaction.objects.create(
            item=item,
            transaction_type=InventoryTransaction.TransactionType.RECEIPT,
            quantity_delta=999,
            created_by=user,
        )
    with pytest.raises(ValidationError):
        InventoryTransaction.objects.bulk_create([
            InventoryTransaction(
                item=item,
                transaction_type=InventoryTransaction.TransactionType.RECEIPT,
                quantity_delta=1,
                created_by=user,
            )
        ])

    receipt.refresh_from_db()
    assert receipt.transaction_type == InventoryTransaction.TransactionType.RECEIPT
    assert receipt.quantity_delta == 2


@pytest.mark.django_db
def test_receiving_line_ledger_reference_is_immutable_on_all_manager_paths():
    user = User.objects.create_user(username="immutable-reference", password="testpass123")
    item = InventoryItem.objects.create(
        part_number="RCV-IMMUTABLE-REF", name="Immutable reference", quantity_on_hand=10
    )
    ticket = ReceivingTicket.objects.create(created_by=user)
    line = ReceivingLine.objects.create(ticket=ticket, item=item, quantity=2)
    original_reference = line.ledger_reference

    line.ledger_reference = uuid.uuid4()
    with pytest.raises(ValidationError):
        line.save()
    with pytest.raises(ValidationError):
        ReceivingLine.objects.filter(pk=line.pk).update(ledger_reference=uuid.uuid4())
    with pytest.raises(ValidationError):
        ReceivingLine._base_manager.filter(pk=line.pk).update(ledger_reference=uuid.uuid4())

    line.refresh_from_db()
    assert line.ledger_reference == original_reference


@pytest.mark.django_db
def test_receiving_base_managers_cannot_bypass_creation_or_ticket_reversal():
    user = User.objects.create_user(username="base-manager-ledger", password="testpass123")
    item = InventoryItem.objects.create(
        part_number="RCV-BASE-MANAGER", name="Base manager item", quantity_on_hand=10
    )
    ticket = ReceivingTicket.objects.create(created_by=user)

    with pytest.raises(ValidationError):
        ReceivingLine._base_manager.bulk_create([
            ReceivingLine(ticket=ticket, item=item, quantity=2)
        ])

    line = ReceivingLine.objects.create(ticket=ticket, item=item, quantity=2)
    ReceivingTicket._base_manager.filter(pk=ticket.pk).delete()

    item.refresh_from_db()
    assert item.quantity_on_hand == 10
    assert not ReceivingLine.objects.filter(pk=line.pk).exists()
    assert InventoryTransaction.objects.filter(
        item=item, transaction_type=InventoryTransaction.TransactionType.REVERSAL
    ).count() == 1


@pytest.mark.django_db
def test_inventory_delete_refuses_item_with_standalone_receipt_history(client):
    user = User.objects.create_user(
        username="ledger-item-deleter", password="testpass123", is_superuser=True
    )
    item = InventoryItem.objects.create(
        part_number="RCV-HISTORY", name="Receipt history", quantity_on_hand=10
    )
    receipt = InventoryTransaction.record_receipt(item=item, quantity=2, user=user)
    client.force_login(user)

    response = client.post(reverse("inventory_delete", args=[item.pk]))

    assert response.status_code == 302
    assert InventoryItem.objects.filter(pk=item.pk).exists()
    assert InventoryTransaction.objects.filter(pk=receipt.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_receiving_line_creation_rolls_back_if_ledger_insert_fails():
    user = User.objects.create_user(username="ledger-insert-failure", password="testpass123")
    item = InventoryItem.objects.create(part_number="RCV-LEDGER-FAIL", name="Ledger failure", quantity_on_hand=5)
    ticket = ReceivingTicket.objects.create(created_by=user)

    with patch.object(InventoryTransaction, "save", side_effect=RuntimeError("insert failed")):
        with pytest.raises(RuntimeError, match="insert failed"):
            ReceivingLine.objects.create(ticket=ticket, item=item, quantity=2)

    assert not ReceivingLine.objects.filter(ticket=ticket).exists()
    item.refresh_from_db()
    assert item.quantity_on_hand == 5


@pytest.mark.django_db
def test_receiving_line_queryset_mutations_cannot_bypass_ledger_rules():
    user = User.objects.create_user(username="queryset-receiver", password="testpass123")
    item = InventoryItem.objects.create(part_number="RCV-QS", name="Queryset item", quantity_on_hand=10)
    ticket = ReceivingTicket.objects.create(created_by=user)
    line = ReceivingLine.objects.create(ticket=ticket, item=item, quantity=2)

    with pytest.raises(ValidationError):
        ReceivingLine.objects.filter(pk=line.pk).update(quantity=9)
    with pytest.raises(ValidationError):
        ReceivingLine.objects.bulk_update([line], ["quantity"])
    with pytest.raises(ValidationError):
        ReceivingLine.objects.bulk_create([
            ReceivingLine(ticket=ticket, item=item, quantity=3),
        ])

    deleted_count, _ = ReceivingLine.objects.filter(pk=line.pk).delete()
    item.refresh_from_db()
    assert deleted_count >= 1
    assert item.quantity_on_hand == 10


@pytest.mark.django_db
def test_receipt_and_reversal_remain_exactly_correlated_after_line_delete():
    user = User.objects.create_user(username="correlation-receiver", password="testpass123")
    item = InventoryItem.objects.create(part_number="RCV-CORR", name="Correlation item", quantity_on_hand=10)
    ticket = ReceivingTicket.objects.create(created_by=user)
    line = ReceivingLine.objects.create(ticket=ticket, item=item, quantity=2)
    receipt = line.receipt_transaction
    reference = line.ledger_reference

    line.delete()

    receipt.refresh_from_db()
    reversal = receipt.reversal_transaction
    assert receipt.receiving_line is None
    assert receipt.receiving_reference == reference
    assert reversal.receiving_reference == reference
    assert reversal.reverses_transaction == receipt
    assert reversal.quantity_delta == -receipt.quantity_delta


@pytest.mark.django_db
def test_receiving_ticket_queryset_delete_reverses_all_lines():
    user = User.objects.create_user(username="ticket-queryset-receiver", password="testpass123")
    item = InventoryItem.objects.create(part_number="RCV-TICKET-QS", name="Ticket queryset item", quantity_on_hand=10)
    ticket = ReceivingTicket.objects.create(created_by=user)
    ReceivingLine.objects.create(ticket=ticket, item=item, quantity=4)

    deleted_count, _ = ReceivingTicket.objects.filter(pk=ticket.pk).delete()

    item.refresh_from_db()
    assert deleted_count >= 1
    assert item.quantity_on_hand == 10
    assert InventoryTransaction.objects.filter(
        item=item, transaction_type=InventoryTransaction.TransactionType.REVERSAL
    ).count() == 1


@pytest.mark.django_db
def test_receiving_line_delete_restores_inventory(client):
    """Deleting a receiving line should reduce quantity and remove RECEIPT transaction."""
    user = User.objects.create_user(username="receiver", password="testpass123")
    item = InventoryItem.objects.create(part_number="VEST-M", name="Vest - Medium", quantity_on_hand=5)
    ticket = ReceivingTicket.objects.create(
        po_number="PO-123",
        created_by=user,
    )
    line = ReceivingLine.objects.create(ticket=ticket, item=item, quantity=4)
    item.refresh_from_db()
    assert item.quantity_on_hand == 9  # 5 + 4
    assert InventoryTransaction.objects.filter(transaction_type="RECEIPT", item=item).exists()

    # Delete the line
    line.delete()

    item.refresh_from_db()
    assert item.quantity_on_hand == 5  # Restored
    # Preserve the original receipt and add an equal reversal audit entry.
    receipt_total = InventoryTransaction.objects.filter(
        transaction_type="RECEIPT", item=item, notes__icontains=ticket.ticket_number
    ).aggregate(total=Sum("quantity_delta"))["total"]
    reversal_total = InventoryTransaction.objects.filter(
        transaction_type="REVERSAL", item=item, notes__icontains=ticket.ticket_number
    ).aggregate(total=Sum("quantity_delta"))["total"]
    assert receipt_total == 4
    assert reversal_total == -4


@pytest.mark.django_db
def test_pick_ticket_delete_restores_inventory(client):
    """Deleting a pick ticket should restore all line quantities."""
    user = User.objects.create_user(username="ticket-deleter", password="testpass123")
    item1 = InventoryItem.objects.create(part_number="ITEM-A", name="Item A", quantity_on_hand=10)
    item2 = InventoryItem.objects.create(part_number="ITEM-B", name="Item B", quantity_on_hand=20)
    ticket = PickTicket.objects.create(
        picked_by_name="Alice",
        received_by_name="Bob",
        requested_by_name="Charlie",
        building_room="B1/101",
        location="Warehouse",
        created_by=user,
    )
    PickTicketLine.objects.create(ticket=ticket, item=item1, quantity=3)
    PickTicketLine.objects.create(ticket=ticket, item=item2, quantity=5)
    item1.refresh_from_db()
    item2.refresh_from_db()
    assert item1.quantity_on_hand == 7
    assert item2.quantity_on_hand == 15

    # Delete the ticket
    ticket_pk = ticket.pk
    ticket_number = ticket.ticket_number
    ticket.delete()

    item1.refresh_from_db()
    item2.refresh_from_db()
    assert item1.quantity_on_hand == 10  # Restored
    assert item2.quantity_on_hand == 20  # Restored
    # PICK transactions should be gone
    assert not InventoryTransaction.objects.filter(transaction_type="PICK", pick_ticket_id=ticket_pk).exists()


@pytest.mark.django_db
def test_receiving_ticket_delete_restores_inventory(client):
    """Deleting a receiving ticket should reduce all line quantities."""
    user = User.objects.create_user(username="rc-ticket-deleter", password="testpass123")
    item1 = InventoryItem.objects.create(part_number="RC-A", name="Receive A", quantity_on_hand=10)
    item2 = InventoryItem.objects.create(part_number="RC-B", name="Receive B", quantity_on_hand=20)
    ticket = ReceivingTicket.objects.create(
        po_number="PO-456",
        created_by=user,
    )
    ReceivingLine.objects.create(ticket=ticket, item=item1, quantity=4)
    ReceivingLine.objects.create(ticket=ticket, item=item2, quantity=6)
    item1.refresh_from_db()
    item2.refresh_from_db()
    assert item1.quantity_on_hand == 14
    assert item2.quantity_on_hand == 26

    # Delete the ticket
    ticket.delete()

    item1.refresh_from_db()
    item2.refresh_from_db()
    assert item1.quantity_on_hand == 10  # Restored
    assert item2.quantity_on_hand == 20  # Restored
    # Original receipts remain immutable and are offset by reversal entries.
    assert InventoryTransaction.objects.filter(
        transaction_type="RECEIPT", notes__icontains=ticket.ticket_number
    ).count() == 2
    assert InventoryTransaction.objects.filter(
        transaction_type="REVERSAL", notes__icontains=ticket.ticket_number
    ).count() == 2


@pytest.mark.django_db
def test_authenticated_user_can_create_pick_ticket_by_part_number_scan(client):
    user = User.objects.create_user(username="alice", password="testpass123", is_superuser=True)
    item = InventoryItem.objects.create(part_number="GLOVE-L", name="Gloves - L", quantity_on_hand=3, barcode_value="GLOVE-L", qr_code_value="GLOVE-L")
    client.force_login(user)

    response = client.post(
        reverse("ticket_create"),
        {
            "date": "2026-06-18T12:00",
            "status": "OPEN",
            "picked_by_name": str(user.pk),
            "received_by_name": "Bob",
            "requested_by_name": "Charlie",
            "building_room": "B1/101",
            "location": "Warehouse",
            "notes": "",
            "lines-TOTAL_FORMS": "5",
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "1",
            "lines-MAX_NUM_FORMS": "1000",
            "lines-0-scan_code": "GLOVE-L",
            "lines-0-item": "",
            "lines-0-quantity": "2",
            "lines-1-scan_code": "",
            "lines-1-item": "",
            "lines-1-quantity": "",
            "lines-2-scan_code": "",
            "lines-2-item": "",
            "lines-2-quantity": "",
            "lines-3-scan_code": "",
            "lines-3-item": "",
            "lines-3-quantity": "",
            "lines-4-scan_code": "",
            "lines-4-item": "",
            "lines-4-quantity": "",
        },
    )

    assert response.status_code == 302
    ticket = PickTicket.objects.get()
    assert ticket.ticket_number == "PT-000001"
    item.refresh_from_db()
    assert item.quantity_on_hand == 1


@pytest.mark.django_db
def test_scanner_page_requires_login_and_loads_for_authenticated_user(client):
    response = client.get(reverse("scanner"))
    assert response.status_code == 302

    user = User.objects.create_user(username="scanner", password="testpass123", is_superuser=True)
    client.force_login(user)
    response = client.get(reverse("scanner"))
    assert response.status_code == 200
    assert b"Phone Barcode / QR Scanner" in response.content
    assert b"playScanBeep" in response.content
    assert b"window.location.href = data.item_url" in response.content
    assert b"Scanned. Opening item" in response.content


@pytest.mark.django_db
def test_scanner_lookup_api_includes_item_url_for_navigation(client):
    user = User.objects.create_user(username="scanner-url", password="testpass123", is_superuser=True)
    item = InventoryItem.objects.create(part_number="VEST-URL", name="Safety Vest URL", quantity_on_hand=7, barcode_value="URL123")
    client.force_login(user)

    response = client.get(reverse("scan_lookup_api"), {"code": "URL123"})

    assert response.status_code == 200
    assert response.json()["id"] == item.pk
    assert response.json()["item_url"] == reverse("item_detail", kwargs={"pk": item.pk})


@pytest.mark.django_db
def test_scan_lookup_finds_barcode(client):
    user = User.objects.create_user(username="alice", password="testpass123", is_superuser=True)
    item = InventoryItem.objects.create(part_number="VEST-M", name="Safety Vest - Medium", quantity_on_hand=70, barcode_value="ABC123")
    client.force_login(user)

    response = client.get(reverse("scan_lookup"), {"code": "ABC123"})

    assert response.status_code == 302
    assert response["Location"] == reverse("item_detail", kwargs={"pk": item.pk})


@pytest.mark.django_db
def test_ticket_list_search_filters_by_ticket_number_and_names(client):
    user = User.objects.create_user(username="searcher", password="testpass123", is_superuser=True)
    first = PickTicket.objects.create(
        picked_by_name="Alice",
        received_by_name="Bob",
        requested_by_name="Charlie",
        building_room="B1/101",
        location="Warehouse",
        created_by=user,
    )
    PickTicket.objects.create(
        picked_by_name="Derek",
        received_by_name="Eve",
        requested_by_name="Frank",
        building_room="B2/202",
        location="Warehouse",
        created_by=user,
    )
    client.force_login(user)

    response = client.get(reverse("ticket_list"), {"q": first.ticket_number})

    assert response.status_code == 200
    assert first.ticket_number.encode() in response.content
    assert b"Derek" not in response.content
    assert b"Search tickets" in response.content


@pytest.mark.django_db
def test_ticket_list_search_filters_by_location_and_line_item(client):
    user = User.objects.create_user(username="searcher2", password="testpass123", is_superuser=True)
    gloves = InventoryItem.objects.create(part_number="GLOVE-L", name="Gloves - Large", quantity_on_hand=10)
    vest = InventoryItem.objects.create(part_number="VEST-L", name="Safety Vest - Large", quantity_on_hand=10)
    glove_ticket = PickTicket.objects.create(
        picked_by_name="Alice",
        received_by_name="Bob",
        requested_by_name="Charlie",
        building_room="B1/101",
        location="Warehouse",
        created_by=user,
    )
    PickTicketLine.objects.create(ticket=glove_ticket, item=gloves, quantity=1)
    vest_ticket = PickTicket.objects.create(
        picked_by_name="Derek",
        received_by_name="Eve",
        requested_by_name="Frank",
        building_room="B2/202",
        location="Warehouse",
        created_by=user,
    )
    PickTicketLine.objects.create(ticket=vest_ticket, item=vest, quantity=1)
    client.force_login(user)

    response = client.get(reverse("ticket_list"), {"q": "GLOVE-L"})

    assert response.status_code == 200
    assert glove_ticket.ticket_number.encode() in response.content
    assert vest_ticket.ticket_number.encode() not in response.content


@pytest.mark.django_db
def test_ticket_detail_links_to_printable_ticket(client):
    user = User.objects.create_user(username="printer", password="testpass123", is_superuser=True)
    item = InventoryItem.objects.create(part_number="VEST-L", name="Safety Vest - Large", quantity_on_hand=10)
    ticket = PickTicket.objects.create(
        picked_by_name="Alice",
        received_by_name="Bob",
        requested_by_name="Charlie",
        building_room="B1/101",
        location="Warehouse",
        created_by=user,
    )
    PickTicketLine.objects.create(ticket=ticket, item=item, quantity=2)
    client.force_login(user)

    response = client.get(reverse("ticket_detail", args=[ticket.pk]))

    assert response.status_code == 200
    assert reverse("ticket_print", args=[ticket.pk]).encode() in response.content
    assert b"Print Ticket" in response.content


@pytest.mark.django_db
def test_printable_ticket_page_loads_and_auto_opens_print_dialog(client):
    user = User.objects.create_user(username="printer", password="testpass123", is_superuser=True)
    item = InventoryItem.objects.create(part_number="VEST-L", name="Safety Vest - Large", quantity_on_hand=10)
    ticket = PickTicket.objects.create(
        picked_by_name="Alice",
        received_by_name="Bob",
        requested_by_name="Charlie",
        building_room="B1/101",
        location="Warehouse",
        created_by=user,
    )
    PickTicketLine.objects.create(ticket=ticket, item=item, quantity=2)
    client.force_login(user)

    response = client.get(reverse("ticket_print", args=[ticket.pk]))

    assert response.status_code == 200
    assert b"Pick Ticket" in response.content
    assert ticket.ticket_number.encode() in response.content
    assert b"Safety Vest - Large" in response.content
    assert b"VEST-L" in response.content
    assert b"BIN LOCATION" in response.content
    assert b"Received By Name" not in response.content
    assert b"Created By" not in response.content
    assert b"PRINT NAME" in response.content
    assert b"RECEIVER SIGNATURE" in response.content
    assert b"RECEIVED DATE / TIME" in response.content
    assert b"window.print()" in response.content
    assert b"backupAndPrint" in response.content
    assert b"PDF backup downloaded" in response.content


@pytest.mark.django_db
def test_ticket_detail_can_update_status_workflow(client):
    user = User.objects.create_user(username="status-user", password="testpass123", is_superuser=True)
    ticket = PickTicket.objects.create(
        picked_by_name="Alice",
        received_by_name="Bob",
        requested_by_name="Charlie",
        building_room="B1/101",
        location="Warehouse",
        created_by=user,
    )
    qa_user = User.objects.create_user(username="status-qa", password="testpass123", is_superuser=True)
    item = InventoryItem.objects.create(
        part_number="STATUS-ITEM", name="Status workflow item", quantity_on_hand=10
    )
    line = PickTicketLine.objects.create(ticket=ticket, item=item, quantity=2)
    client.force_login(user)

    detail = client.get(reverse("ticket_detail", args=[ticket.pk]))

    assert detail.status_code == 200
    assert b"Open" in detail.content
    assert b"Mark Picked" in detail.content
    assert b"Ready for Delivery" in detail.content
    assert b"Closed/Delivered" in detail.content

    response = client.post(
        reverse("ticket_status_update", args=[ticket.pk]),
        {
            "status": "PICKED",
            f"picked_quantity_{line.pk}": "2",
            f"pick_variance_reason_{line.pk}": "",
            "picked_by_user": str(user.pk),
            "qa_checked_by_user": str(qa_user.pk),
        },
    )

    assert response.status_code == 302
    ticket.refresh_from_db()
    assert ticket.status == "PICKED"


@pytest.mark.django_db
def test_ticket_list_can_filter_by_status(client):
    user = User.objects.create_user(username="status-search", password="testpass123", is_superuser=True)
    open_ticket = PickTicket.objects.create(
        picked_by_name="Alice",
        received_by_name="Bob",
        requested_by_name="Charlie",
        building_room="B1/101",
        location="Warehouse",
        created_by=user,
    )
    closed_ticket = PickTicket.objects.create(
        picked_by_name="Derek",
        received_by_name="Eve",
        requested_by_name="Frank",
        building_room="B2/202",
        location="Warehouse",
        status="CLOSED",
        created_by=user,
    )
    client.force_login(user)

    response = client.get(reverse("ticket_list"), {"status": "CLOSED"})

    assert response.status_code == 200
    assert closed_ticket.ticket_number.encode() in response.content
    assert open_ticket.ticket_number.encode() not in response.content


@pytest.mark.django_db
def test_low_stock_page_lists_low_and_negative_inventory(client):
    user = User.objects.create_user(username="stock-watch", password="testpass123")
    InventoryItem.objects.create(part_number="LOW", name="Low Gloves", quantity_on_hand=2, low_stock_threshold=5)
    InventoryItem.objects.create(part_number="NEG", name="Negative Vests", quantity_on_hand=-1, low_stock_threshold=0)
    InventoryItem.objects.create(part_number="OK", name="Healthy Stock", quantity_on_hand=20, low_stock_threshold=5)
    client.force_login(user)

    response = client.get(reverse("low_stock_list"))

    assert response.status_code == 200
    assert b"Low Gloves" in response.content
    assert b"Negative Vests" in response.content
    assert b"Healthy Stock" not in response.content


@pytest.mark.django_db
def test_ticket_report_csv_exports_ticket_history(client):
    user = User.objects.create_user(username="report-user", password="testpass123", is_superuser=True)
    ticket = PickTicket.objects.create(
        picked_by_name="Alice",
        received_by_name="Bob",
        requested_by_name="Charlie",
        building_room="B1/101",
        location="Warehouse",
        status="CLOSED",
        created_by=user,
    )
    client.force_login(user)

    response = client.get(reverse("export_tickets_csv"))

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    assert ticket.ticket_number.encode() in response.content
    assert b"CLOSED" in response.content


@pytest.mark.django_db
def test_printable_labels_page_includes_barcode_value(client):
    user = User.objects.create_user(username="labels-user", password="testpass123", is_superuser=True)
    item = InventoryItem.objects.create(
        part_number="VEST-L",
        name="Safety Vest - Large",
        quantity_on_hand=10,
        barcode_value="BAR-VEST-L",
        qr_code_value="QR-VEST-L",
    )
    client.force_login(user)

    response = client.get(reverse("inventory_labels", args=[item.pk]))

    assert response.status_code == 200
    assert b"Safety Vest - Large" in response.content
    assert b"BAR-VEST-L" in response.content
    assert b"Code 128 Barcode" in response.content
    assert b"Auto Barcode" in response.content
    assert b"window.print()" in response.content
