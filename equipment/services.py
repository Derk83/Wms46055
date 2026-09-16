import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    ActiveCustody,
    Asset,
    AssetEvent,
    Checkout,
    CheckoutItem,
    EquipmentLocation,
    EquipmentMutationLock,
    EquipmentParty,
    MaintenanceWorkOrder,
    Reservation,
    ReservationAsset,
    ReturnRecord,
)


def _require(actor, permission):
    if not actor or not actor.is_authenticated or not actor.has_perm(permission):
        raise PermissionDenied


def _number(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def serialize_equipment_mutation():
    """Acquire the equipment write mutex before reading mutable state.

    The first statement is an UPDATE. On SQLite this obtains the database
    write reservation without a read-to-write lock upgrade; on databases with
    row locking it serializes through the same singleton row.
    """
    updated = EquipmentMutationLock.objects.filter(key="global").update(touched_at=timezone.now())
    if not updated:
        EquipmentMutationLock.objects.create(key="global")


def _event(*, asset, actor, event_type, summary, metadata=None, correlation_id=None):
    return AssetEvent.objects.create(
        asset=asset,
        actor=actor,
        event_type=event_type,
        summary=summary,
        metadata=metadata or {},
        correlation_id=correlation_id or uuid.uuid4(),
    )


@transaction.atomic
def create_asset(*, actor, **values):
    _require(actor, "equipment.manage_equipment")
    if any(values.get(field) is not None for field in ("purchase_cost", "replacement_value")):
        _require(actor, "equipment.view_asset_costs")
    serialize_equipment_mutation()
    values["created_by"] = actor
    values["updated_by"] = actor
    asset = Asset(**values)
    asset.full_clean()
    asset.save()
    _event(asset=asset, actor=actor, event_type=AssetEvent.Type.CREATED, summary="Asset created")
    return asset


@transaction.atomic
def update_asset(*, actor, asset_id, expected_updated_at=None, **values):
    _require(actor, "equipment.manage_equipment")
    if any(field in values for field in ("purchase_cost", "replacement_value")):
        _require(actor, "equipment.view_asset_costs")
    serialize_equipment_mutation()
    asset = Asset.objects.select_for_update().get(pk=asset_id)
    if expected_updated_at and str(asset.updated_at) != str(expected_updated_at):
        raise ValidationError("This asset changed after the form was opened. Reload and try again.")
    protected = {"status", "current_party", "archived_at", "archived_by", "created_by"}
    changed = {}
    for key, value in values.items():
        if key not in protected and hasattr(asset, key):
            old = getattr(asset, key)
            if old != value:
                setattr(asset, key, value)
                changed[key] = {"from": str(old), "to": str(value)}
    asset.updated_by = actor
    asset.full_clean()
    asset.save()
    if changed:
        _event(
            asset=asset,
            actor=actor,
            event_type=AssetEvent.Type.UPDATED,
            summary="Asset registry details updated",
            metadata={"changes": changed},
        )
    return asset


@transaction.atomic
def checkout_assets(
    *, actor, borrower_id, asset_ids, due_at=None, destination_id=None, purpose="", condition_by_asset=None,
    reservation_id=None
):
    _require(actor, "equipment.checkout_asset")
    serialize_equipment_mutation()
    ids = sorted({str(value) for value in asset_ids})
    if not ids:
        raise ValidationError("Select at least one asset.")
    borrower = EquipmentParty.objects.get(pk=borrower_id, active=True)
    reservation = None
    if reservation_id:
        reservation = Reservation.objects.select_for_update().get(pk=reservation_id)
        if reservation.status != Reservation.Status.APPROVED:
            raise ValidationError("Only an approved reservation can be checked out.")
        if reservation.requestor_id != borrower.pk:
            raise ValidationError("The checkout borrower must match the reservation requestor.")
        reserved_ids = {str(value) for value in reservation.assets.values_list("pk", flat=True)}
        if set(ids) != reserved_ids:
            raise ValidationError("Checkout every asset held by the approved reservation together.")
    assets = list(Asset.objects.select_for_update().filter(pk__in=ids).order_by("asset_tag"))
    if len(assets) != len(ids):
        raise ValidationError("One or more assets no longer exist.")
    expected_status = Asset.Status.RESERVED if reservation else Asset.Status.AVAILABLE
    unavailable = [asset.asset_tag for asset in assets if asset.status != expected_status or asset.archived_at]
    if unavailable:
        raise ValidationError(f"Unavailable equipment: {', '.join(unavailable)}")
    destination = EquipmentLocation.objects.filter(pk=destination_id).first() if destination_id else None
    now = timezone.now()
    if due_at and due_at <= now:
        raise ValidationError("Due date must be in the future.")
    checkout = Checkout.objects.create(
        checkout_number=_number("EQ-CO"),
        borrower=borrower,
        borrower_snapshot=borrower.display_name,
        destination=destination,
        purpose=purpose.strip(),
        due_at=due_at,
        created_by=actor,
        reservation=reservation,
    )
    correlation = uuid.uuid4()
    condition_by_asset = condition_by_asset or {}
    for asset in assets:
        condition = condition_by_asset.get(str(asset.pk), asset.condition)
        item = CheckoutItem.objects.create(
            checkout=checkout,
            asset=asset,
            asset_tag_snapshot=asset.asset_tag,
            asset_name_snapshot=asset.name,
            condition_out=condition,
        )
        ActiveCustody.objects.create(
            asset=asset, checkout_item=item, borrower=borrower, due_at=due_at
        )
        asset.status = Asset.Status.CHECKED_OUT
        asset.current_party = borrower
        if destination:
            asset.current_location = destination
        asset.updated_by = actor
        asset.save(update_fields=("status", "current_party", "current_location", "updated_by", "updated_at"))
        _event(
            asset=asset,
            actor=actor,
            event_type=AssetEvent.Type.CHECKED_OUT,
            summary=f"Checked out to {borrower.display_name}",
            metadata={"checkout_number": checkout.checkout_number, "due_at": due_at.isoformat() if due_at else None},
            correlation_id=correlation,
        )
    if reservation:
        reservation.status = Reservation.Status.FULFILLED
        reservation.save(update_fields=("status", "updated_at"))
    return checkout


@transaction.atomic
def return_asset(
    *, actor, asset_id, condition, notes="", damage_reported=False, return_location_id=None
):
    _require(actor, "equipment.return_asset")
    serialize_equipment_mutation()
    asset = Asset.objects.select_for_update().get(pk=asset_id)
    custody = ActiveCustody.objects.select_for_update().select_related("checkout_item", "borrower").filter(asset=asset).first()
    if not custody:
        raise ValidationError("This asset is not checked out.")
    location = EquipmentLocation.objects.filter(pk=return_location_id).first() if return_location_id else asset.home_location
    record = ReturnRecord.objects.create(
        checkout_item=custody.checkout_item,
        received_by=actor,
        return_location=location,
        condition_in=condition,
        notes=notes.strip(),
        damage_reported=damage_reported,
    )
    borrower_name = custody.borrower.display_name
    custody.delete()
    asset.condition = condition
    asset.current_party = None
    asset.current_location = location
    asset.status = Asset.Status.MAINTENANCE if damage_reported else Asset.Status.AVAILABLE
    asset.updated_by = actor
    asset.save(update_fields=("condition", "current_party", "current_location", "status", "updated_by", "updated_at"))
    _event(
        asset=asset,
        actor=actor,
        event_type=AssetEvent.Type.RETURNED,
        summary=f"Returned from {borrower_name}",
        metadata={"damage_reported": damage_reported, "return_id": record.pk},
    )
    if damage_reported:
        order = MaintenanceWorkOrder.objects.create(
            work_order_number=_number("EQ-WO"),
            asset=asset,
            title="Damage reported during return",
            problem_description=notes.strip(),
            priority=MaintenanceWorkOrder.Priority.HIGH,
            out_of_service=True,
            opened_by=actor,
        )
        _event(
            asset=asset,
            actor=actor,
            event_type=AssetEvent.Type.MAINTENANCE_OPENED,
            summary=f"Maintenance {order.work_order_number} opened from return inspection",
        )
    return record


@transaction.atomic
def create_reservation(*, actor, requestor_id, asset_ids, starts_at, ends_at, purpose="", destination_id=None):
    _require(actor, "equipment.add_reservation")
    serialize_equipment_mutation()
    if ends_at <= starts_at:
        raise ValidationError("Reservation end must be after its start.")
    ids = sorted({str(value) for value in asset_ids})
    assets = list(Asset.objects.select_for_update().filter(pk__in=ids).order_by("asset_tag"))
    if len(assets) != len(ids) or not assets:
        raise ValidationError("Select valid equipment.")
    conflict = Reservation.objects.filter(
        assets__in=assets,
        status__in=(Reservation.Status.PENDING, Reservation.Status.APPROVED),
        starts_at__lt=ends_at,
        ends_at__gt=starts_at,
    ).first()
    if conflict:
        raise ValidationError(f"Conflicts with reservation {conflict.reservation_number}.")
    requestor = EquipmentParty.objects.get(pk=requestor_id, active=True)
    if not actor.is_superuser and not actor.has_perm("equipment.manage_reservations") and requestor.user_id != actor.pk:
        raise PermissionDenied("Self-service reservations must be created for your linked equipment profile.")
    reservation = Reservation.objects.create(
        reservation_number=_number("EQ-RS"),
        requestor=requestor,
        starts_at=starts_at,
        ends_at=ends_at,
        purpose=purpose.strip(),
        destination=EquipmentLocation.objects.filter(pk=destination_id).first() if destination_id else None,
        created_by=actor,
    )
    ReservationAsset.objects.bulk_create([ReservationAsset(reservation=reservation, asset=asset) for asset in assets])
    for asset in assets:
        _event(
            asset=asset,
            actor=actor,
            event_type=AssetEvent.Type.RESERVED,
            summary=f"Reserved for {requestor.display_name}",
            metadata={"reservation_number": reservation.reservation_number},
        )
    return reservation


@transaction.atomic
def set_reservation_status(*, actor, reservation_id, status):
    _require(actor, "equipment.manage_reservations")
    serialize_equipment_mutation()
    reservation = Reservation.objects.select_for_update().get(pk=reservation_id)
    assets = list(Asset.objects.select_for_update().filter(reservations=reservation).order_by("asset_tag"))
    allowed = {
        Reservation.Status.PENDING: {Reservation.Status.APPROVED, Reservation.Status.REJECTED, Reservation.Status.CANCELLED},
        Reservation.Status.APPROVED: {Reservation.Status.CANCELLED},
    }
    if status not in allowed.get(reservation.status, set()):
        raise ValidationError("That reservation status transition is not allowed.")
    old = reservation.status
    if status == Reservation.Status.APPROVED:
        unavailable = [asset.asset_tag for asset in assets if asset.status != Asset.Status.AVAILABLE or asset.archived_at]
        if unavailable:
            raise ValidationError(f"Cannot approve; unavailable equipment: {', '.join(unavailable)}")
        for asset in assets:
            asset.status = Asset.Status.RESERVED
            asset.updated_by = actor
            asset.save(update_fields=("status", "updated_by", "updated_at"))
        reservation.approved_by = actor
    elif old == Reservation.Status.APPROVED and status == Reservation.Status.CANCELLED:
        for asset in assets:
            if asset.status == Asset.Status.RESERVED:
                asset.status = Asset.Status.AVAILABLE
                asset.updated_by = actor
                asset.save(update_fields=("status", "updated_by", "updated_at"))
    reservation.status = status
    reservation.save(update_fields=("status", "approved_by", "updated_at"))
    for asset in assets:
        _event(
            asset=asset,
            actor=actor,
            event_type=AssetEvent.Type.RESERVATION_CHANGED,
            summary=f"Reservation {reservation.reservation_number}: {old} → {status}",
        )
    return reservation


@transaction.atomic
def open_maintenance(*, actor, asset_id, title, description="", priority=MaintenanceWorkOrder.Priority.NORMAL, due_at=None):
    _require(actor, "equipment.manage_maintenance")
    serialize_equipment_mutation()
    asset = Asset.objects.select_for_update().get(pk=asset_id)
    if MaintenanceWorkOrder.objects.filter(asset=asset).exclude(
        status__in=(MaintenanceWorkOrder.Status.COMPLETED, MaintenanceWorkOrder.Status.CANCELLED)
    ).exists():
        raise ValidationError("This asset already has an open maintenance work order.")
    if asset.status == Asset.Status.RESERVED:
        raise ValidationError("Cancel the approved reservation before opening maintenance.")
    if asset.status != Asset.Status.AVAILABLE or ActiveCustody.objects.filter(asset=asset).exists():
        raise ValidationError("The asset must be available before opening maintenance.")
    order = MaintenanceWorkOrder.objects.create(
        work_order_number=_number("EQ-WO"),
        asset=asset,
        title=title.strip(),
        problem_description=description.strip(),
        priority=priority,
        due_at=due_at,
        opened_by=actor,
    )
    asset.status = Asset.Status.MAINTENANCE
    asset.updated_by = actor
    asset.save(update_fields=("status", "updated_by", "updated_at"))
    _event(
        asset=asset,
        actor=actor,
        event_type=AssetEvent.Type.MAINTENANCE_OPENED,
        summary=f"Maintenance {order.work_order_number} opened: {order.title}",
    )
    return order


@transaction.atomic
def complete_maintenance(*, actor, work_order_id, work_performed, cost=None, condition=Asset.Condition.GOOD):
    _require(actor, "equipment.manage_maintenance")
    if cost is not None:
        _require(actor, "equipment.view_asset_costs")
    serialize_equipment_mutation()
    order = MaintenanceWorkOrder.objects.select_for_update().select_related("asset").get(pk=work_order_id)
    asset = Asset.objects.select_for_update().get(pk=order.asset_id)
    if order.status in {MaintenanceWorkOrder.Status.COMPLETED, MaintenanceWorkOrder.Status.CANCELLED}:
        raise ValidationError("This work order is already closed.")
    order.status = MaintenanceWorkOrder.Status.COMPLETED
    order.completed_at = timezone.now()
    order.completed_by = actor
    order.work_performed = work_performed.strip()
    order.cost = cost
    order.save(update_fields=("status", "completed_at", "completed_by", "work_performed", "cost", "updated_at"))
    other_open_orders = MaintenanceWorkOrder.objects.filter(asset=asset).exclude(pk=order.pk).exclude(
        status__in=(MaintenanceWorkOrder.Status.COMPLETED, MaintenanceWorkOrder.Status.CANCELLED)
    )
    asset.status = Asset.Status.MAINTENANCE if other_open_orders.exists() else Asset.Status.AVAILABLE
    asset.condition = condition
    asset.updated_by = actor
    asset.save(update_fields=("status", "condition", "updated_by", "updated_at"))
    _event(
        asset=asset,
        actor=actor,
        event_type=AssetEvent.Type.MAINTENANCE_COMPLETED,
        summary=f"Maintenance {order.work_order_number} completed",
        metadata={"cost": str(cost) if cost is not None else None},
    )
    return order
