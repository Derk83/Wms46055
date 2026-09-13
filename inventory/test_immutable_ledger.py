"""Tests for full immutability of the inventory transaction ledger."""
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from inventory.models import InventoryItem, InventoryTransaction

User = get_user_model()


@pytest.mark.django_db
def test_all_transaction_types_are_immutable():
    """Every transaction type must reject direct edits."""
    user = User.objects.create_user(username="ledger-test", password="x")
    item = InventoryItem.objects.create(part_number="LED-1", name="L", quantity_on_hand=10)
    for tx_type, delta in [
        (InventoryTransaction.TransactionType.RECEIPT, 5),
        (InventoryTransaction.TransactionType.PICK, -2),
        (InventoryTransaction.TransactionType.ADJUSTMENT, 3),
        (InventoryTransaction.TransactionType.IMPORT, 1),
    ]:
        item.adjust_quantity(delta, tx_type, user=user)
    rows = InventoryTransaction.objects.all()
    assert rows.count() == 4

    # Bulk update must fail
    with pytest.raises(ValidationError):
        rows.update(notes="hacked")

    # Bulk delete must fail
    with pytest.raises(ValidationError):
        rows.delete()

    # Individual delete must fail
    with pytest.raises(ValidationError):
        rows.first().delete()


@pytest.mark.django_db
def test_direct_save_rejects_notes_change_on_existing_ledger():
    user = User.objects.create_user(username="ledger-edit", password="x")
    item = InventoryItem.objects.create(part_number="LED-2", name="L2", quantity_on_hand=0)
    item.adjust_quantity(2, InventoryTransaction.TransactionType.RECEIPT, user=user)
    tx = InventoryTransaction.objects.get(transaction_type="RECEIPT")
    tx.notes = "altered"
    with pytest.raises(ValidationError):
        tx.save()


@pytest.mark.django_db
def test_bulk_create_rejects_immutable_types():
    user = User.objects.create_user(username="ledger-bulk", password="x")
    item = InventoryItem.objects.create(part_number="LED-3", name="L3", quantity_on_hand=0)
    with pytest.raises(ValidationError):
        InventoryTransaction.objects.bulk_create([
            InventoryTransaction(
                item=item,
                transaction_type=InventoryTransaction.TransactionType.PICK,
                quantity_delta=-1,
                created_by=user,
            )
        ])
