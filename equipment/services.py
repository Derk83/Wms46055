import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal

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
    MaintenancePlan,
    MaintenanceWorkOrder,
    Reservation,
    ReservationAsset,
    ReturnRecord,
    VehicleMeterReading,
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
    linked_request = None
    if reservation_id:
        reservation = Reservation.objects.select_for_update().get(pk=reservation_id)
        if reservation.status != Reservation.Status.APPROVED:
            raise ValidationError("Only an approved reservation can be checked out.")
        if reservation.requestor_id != borrower.pk:
            raise ValidationError("The checkout borrower must match the reservation requestor.")
        reserved_ids = {str(value) for value in reservation.assets.values_list("pk", flat=True)}
        if set(ids) != reserved_ids:
            raise ValidationError("Checkout every asset held by the approved reservation together.")
        from .models import EquipmentRequest

        linked_request = EquipmentRequest.objects.select_for_update().filter(reservation=reservation).first()
        if linked_request and linked_request.status != EquipmentRequest.Status.READY:
            raise ValidationError("The linked equipment request must be ready before checkout.")
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
        if linked_request:
            from .models import EquipmentRequestEvent

            old_status = linked_request.status
            linked_request.status = linked_request.Status.FULFILLED
            linked_request.save(update_fields=("status", "updated_at"))
            EquipmentRequestEvent.objects.create(
                request=linked_request,
                actor=actor,
                event_type=EquipmentRequestEvent.Type.STATUS_CHANGED,
                from_status=old_status,
                to_status=linked_request.status,
                message=f"Fulfilled by checkout {checkout.checkout_number}",
                metadata={
                    "checkout_number": checkout.checkout_number,
                    "changes": {"status": {"from": old_status, "to": linked_request.status}},
                },
            )
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
    if hasattr(reservation, "equipment_request"):
        raise ValidationError("Linked request reservations must be managed from the equipment request.")
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
def record_meter_reading(*, actor, asset_id, meter_type, value, reading_on=None):
    _require(actor, "equipment.manage_maintenance")
    serialize_equipment_mutation()
    asset = Asset.objects.select_for_update().select_related("category").get(pk=asset_id)
    if not asset.is_vehicle:
        raise ValidationError("Meter readings can only be recorded for vehicle assets.")
    if meter_type not in VehicleMeterReading.MeterType.values:
        raise ValidationError("Choose a valid meter type.")
    value = Decimal(value)
    reading_on = reading_on or timezone.localdate()
    if reading_on > timezone.localdate():
        raise ValidationError("Meter reading date cannot be in the future.")
    latest = VehicleMeterReading.objects.filter(asset=asset, meter_type=meter_type).order_by("-recorded_at", "-id").first()
    if latest and value < latest.value:
        raise ValidationError(f"Reading cannot decrease below the current {latest.value}.")
    reading = VehicleMeterReading(
        asset=asset, meter_type=meter_type, value=value, reading_on=reading_on, recorded_by=actor
    )
    reading.full_clean()
    reading.save()
    return reading


@transaction.atomic
def save_maintenance_plan(*, actor, plan_id=None, **values):
    _require(actor, "equipment.manage_maintenance")
    serialize_equipment_mutation()
    if plan_id:
        plan = MaintenancePlan.objects.select_for_update().get(pk=plan_id)
        for key, value in values.items():
            if hasattr(plan, key) and key not in {"created_by", "updated_by", "next_due_date", "next_due_meter"}:
                setattr(plan, key, value)
        plan.updated_by = actor
    else:
        plan = MaintenancePlan(created_by=actor, updated_by=actor, **values)
    plan.calculate_next_due()
    plan.full_clean()
    plan.save()
    return plan


@transaction.atomic
def set_maintenance_plan_active(*, actor, plan_id, active):
    _require(actor, "equipment.manage_maintenance")
    serialize_equipment_mutation()
    plan = MaintenancePlan.objects.select_for_update().get(pk=plan_id)
    plan.active = bool(active)
    plan.updated_by = actor
    plan.save(update_fields=("active", "updated_by", "updated_at"))
    return plan


def forecast_plan_due(plan, *, as_of=None, current_meter=None):
    """Return trigger details. Date or meter entering its lead window makes a plan due."""
    as_of = as_of or timezone.localdate()
    if current_meter is None and plan.meter_type:
        reading = plan.asset.meter_readings.filter(meter_type=plan.meter_type).order_by("-recorded_at", "-id").first()
        current_meter = reading.value if reading else None
    date_due = bool(plan.next_due_date and plan.next_due_date <= as_of + timedelta(days=plan.lead_days))
    meter_due = bool(
        plan.next_due_meter is not None
        and current_meter is not None
        and current_meter >= plan.next_due_meter - plan.lead_meter
    )
    return {
        "due": date_due or meter_due,
        "date_due": date_due,
        "meter_due": meter_due,
        "current_meter": current_meter,
    }


MAINTENANCE_TRANSITIONS = {
    MaintenanceWorkOrder.Status.OPEN: (
        MaintenanceWorkOrder.Status.SCHEDULED, MaintenanceWorkOrder.Status.IN_PROGRESS,
        MaintenanceWorkOrder.Status.CANCELLED,
    ),
    MaintenanceWorkOrder.Status.SCHEDULED: (
        MaintenanceWorkOrder.Status.OPEN, MaintenanceWorkOrder.Status.IN_PROGRESS,
        MaintenanceWorkOrder.Status.CANCELLED,
    ),
    MaintenanceWorkOrder.Status.IN_PROGRESS: (
        MaintenanceWorkOrder.Status.WAITING_PARTS, MaintenanceWorkOrder.Status.CANCELLED,
    ),
    MaintenanceWorkOrder.Status.WAITING_PARTS: (
        MaintenanceWorkOrder.Status.IN_PROGRESS, MaintenanceWorkOrder.Status.CANCELLED,
    ),
}


def allowed_maintenance_transitions(status):
    """Return the service-owned transition allowlist for the current state."""
    return MAINTENANCE_TRANSITIONS.get(status, ())


def _occurrence_key(plan):
    date_part = plan.next_due_date.isoformat() if plan.next_due_date else "none"
    meter_part = str(plan.next_due_meter) if plan.next_due_meter is not None else "none"
    return f"maintenance-plan:{plan.pk}:date:{date_part}:meter:{meter_part}"


@transaction.atomic
def generate_due_maintenance(*, actor=None, as_of=None, plan_ids=None):
    if actor is not None:
        _require(actor, "equipment.manage_maintenance")
    serialize_equipment_mutation()
    plans = MaintenancePlan.objects.select_for_update().select_related("asset", "asset__category", "preferred_vendor", "updated_by").filter(
        active=True, auto_create_work_order=True, asset__archived_at__isnull=True,
    ).exclude(
        asset__status__in=(Asset.Status.RETIRED, Asset.Status.LOST, Asset.Status.RETURNED_VENDOR)
    )
    if plan_ids is not None:
        plans = plans.filter(pk__in=plan_ids)
    generated = []
    for plan in plans.order_by("pk"):
        if not forecast_plan_due(plan, as_of=as_of)["due"]:
            continue
        due_at = None
        if plan.next_due_date:
            due_at = timezone.make_aware(datetime.combine(plan.next_due_date, time.max))
        order, created = MaintenanceWorkOrder.objects.get_or_create(
            occurrence_key=_occurrence_key(plan),
            defaults={
                "work_order_number": _number("EQ-WO"),
                "asset": plan.asset,
                "plan": plan,
                "title": plan.service_title,
                "problem_description": plan.description,
                "priority": plan.default_priority,
                "out_of_service": False,
                "due_at": due_at,
                "meter_due": plan.next_due_meter,
                "vendor": plan.preferred_vendor,
                "opened_by": actor or plan.updated_by,
            },
        )
        if created:
            generated.append(order)
            _event(
                asset=plan.asset,
                actor=actor,
                event_type=AssetEvent.Type.MAINTENANCE_OPENED,
                summary=f"Recurring maintenance {order.work_order_number} generated: {order.title}",
                metadata={"plan_id": plan.pk, "occurrence_key": order.occurrence_key},
            )
    return generated


@transaction.atomic
def transition_maintenance(*, actor, work_order_id, status, scheduled_for=None):
    _require(actor, "equipment.manage_maintenance")
    serialize_equipment_mutation()
    order = MaintenanceWorkOrder.objects.select_for_update().select_related("asset").get(pk=work_order_id)
    asset = Asset.objects.select_for_update().get(pk=order.asset_id)
    if status not in allowed_maintenance_transitions(order.status):
        raise ValidationError("That maintenance status transition is not allowed.")
    if status == MaintenanceWorkOrder.Status.SCHEDULED:
        if not scheduled_for:
            raise ValidationError("A scheduled service date is required.")
        order.scheduled_for = scheduled_for
    if status == MaintenanceWorkOrder.Status.IN_PROGRESS and order.status != MaintenanceWorkOrder.Status.IN_PROGRESS:
        if asset.status == Asset.Status.RESERVED:
            raise ValidationError("Cancel the approved reservation before starting maintenance.")
        if asset.status not in {Asset.Status.AVAILABLE, Asset.Status.MAINTENANCE} or ActiveCustody.objects.filter(asset=asset).exists():
            raise ValidationError("The asset must be available before starting maintenance.")
        asset.status = Asset.Status.MAINTENANCE
        asset.updated_by = actor
        asset.save(update_fields=("status", "updated_by", "updated_at"))
    order.status = status
    order.save(update_fields=("status", "scheduled_for", "updated_at"))
    if status == MaintenanceWorkOrder.Status.CANCELLED and asset.status == Asset.Status.MAINTENANCE:
        other_open = MaintenanceWorkOrder.objects.filter(asset=asset).exclude(pk=order.pk).exclude(
            status__in=(MaintenanceWorkOrder.Status.COMPLETED, MaintenanceWorkOrder.Status.CANCELLED)
        )
        if not other_open.exists() and not ActiveCustody.objects.filter(asset=asset).exists():
            asset.status = Asset.Status.AVAILABLE
            asset.updated_by = actor
            asset.save(update_fields=("status", "updated_by", "updated_at"))
    return order


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
def complete_maintenance(
    *, actor, work_order_id, work_performed, cost=None, condition=Asset.Condition.GOOD,
    meter_at_completion=None, completed_on=None
):
    _require(actor, "equipment.manage_maintenance")
    if cost is not None:
        _require(actor, "equipment.view_asset_costs")
    completed_on = completed_on or timezone.localdate()
    if completed_on > timezone.localdate():
        raise ValidationError("Completion date cannot be in the future.")
    serialize_equipment_mutation()
    order = MaintenanceWorkOrder.objects.select_for_update().select_related("asset").get(pk=work_order_id)
    asset = Asset.objects.select_for_update().get(pk=order.asset_id)
    if order.status in {MaintenanceWorkOrder.Status.COMPLETED, MaintenanceWorkOrder.Status.CANCELLED}:
        raise ValidationError("This work order is already closed.")
    plan = None
    if order.plan_id:
        plan = MaintenancePlan.objects.select_for_update().get(pk=order.plan_id)
        if order.asset_id != plan.asset_id:
            raise ValidationError("The work order asset does not match its maintenance plan.")
        if order.occurrence_key != _occurrence_key(plan):
            raise ValidationError("This work order is not the plan's current maintenance occurrence.")
    order.status = MaintenanceWorkOrder.Status.COMPLETED
    order.completed_at = timezone.now()
    order.completed_by = actor
    order.work_performed = work_performed.strip()
    order.cost = cost
    if meter_at_completion is not None:
        if not plan or not plan.meter_type:
            raise ValidationError("A completion meter is only valid for a metered maintenance plan.")
        meter_value = Decimal(meter_at_completion)
        latest = VehicleMeterReading.objects.filter(asset=asset, meter_type=plan.meter_type).order_by("-recorded_at", "-id").first()
        if latest and meter_value < latest.value:
            raise ValidationError(f"Reading cannot decrease below the current {latest.value}.")
        reading = VehicleMeterReading(
            asset=asset,
            meter_type=plan.meter_type,
            value=meter_value,
            reading_on=completed_on,
            recorded_by=actor,
        )
        reading.save()
        order.meter_at_completion = meter_value
    order.full_clean()
    order.save(update_fields=("status", "completed_at", "completed_by", "work_performed", "cost", "meter_at_completion", "updated_at"))
    if plan:
        plan.last_service_date = completed_on
        if plan.meter_type:
            if order.meter_at_completion is not None:
                plan.last_service_meter = order.meter_at_completion
            else:
                latest = VehicleMeterReading.objects.filter(asset=asset, meter_type=plan.meter_type).order_by("-recorded_at", "-id").first()
                if latest:
                    plan.last_service_meter = latest.value
        plan.calculate_next_due()
        plan.updated_by = actor
        plan.save(update_fields=("last_service_date", "last_service_meter", "next_due_date", "next_due_meter", "updated_by", "updated_at"))
    other_open_orders = MaintenanceWorkOrder.objects.filter(asset=asset).exclude(pk=order.pk).exclude(
        status__in=(MaintenanceWorkOrder.Status.COMPLETED, MaintenanceWorkOrder.Status.CANCELLED)
    )
    has_custody = ActiveCustody.objects.filter(asset=asset).exists()
    # Only release a lifecycle state owned by maintenance. Unrelated,
    # reserved, out-of-service, and terminal states must be preserved.
    if asset.status == Asset.Status.MAINTENANCE and not has_custody and not other_open_orders.exists():
        asset.status = Asset.Status.AVAILABLE
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
