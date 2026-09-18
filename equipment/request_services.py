from datetime import datetime, time, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    Asset,
    EquipmentParty,
    EquipmentRequest,
    EquipmentRequestAllocation,
    EquipmentRequestEvent,
    EquipmentRequestLine,
    Reservation,
    ReservationAsset,
)
from .services import create_reservation, serialize_equipment_mutation, set_reservation_status


def _require(actor, permission):
    if not actor or not actor.is_authenticated or not actor.has_perm(permission):
        raise PermissionDenied


def _party_for_user(user):
    party = EquipmentParty.objects.select_for_update().filter(user=user).first()
    display_name = user.get_full_name().strip() or user.username
    if party:
        changed = []
        if party.display_name != display_name:
            party.display_name = display_name
            changed.append("display_name")
        if user.email and party.email != user.email:
            party.email = user.email
            changed.append("email")
        if not party.active:
            party.active = True
            changed.append("active")
        if changed:
            party.save(update_fields=(*changed, "updated_at"))
        return party
    return EquipmentParty.objects.create(user=user, display_name=display_name, email=user.email, active=True)


def _event(equipment_request, actor, event_type, message, *, old="", new="", metadata=None):
    return EquipmentRequestEvent.objects.create(
        request=equipment_request,
        actor=actor,
        event_type=event_type,
        from_status=old,
        to_status=new,
        message=message,
        metadata=metadata or {},
    )


def _validated_lines(lines):
    output = []
    for line in lines:
        category = line.get("category")
        unlisted = (line.get("unlisted_equipment") or "").strip()
        if not category and not unlisted:
            continue
        quantity = line.get("quantity") or 1
        if quantity < 1:
            raise ValidationError("Equipment quantities must be at least one.")
        output.append({
            "category": category,
            "unlisted_equipment": unlisted,
            "quantity": quantity,
            "notes": (line.get("notes") or "").strip(),
        })
    if not output:
        raise ValidationError("Add at least one equipment item.")
    return output


@transaction.atomic
def create_equipment_request(*, actor, values, lines):
    _require(actor, "equipment.access_equipment_requests")
    serialize_equipment_mutation()
    clean_lines = _validated_lines(lines)
    party = _party_for_user(actor)
    equipment_request = EquipmentRequest(
        id=None,
        requester=actor,
        requestor_party=party,
        **values,
    )
    # Force the UUID before deriving a readable, collision-safe reference.
    from uuid import uuid4
    equipment_request.id = uuid4()
    equipment_request.request_number = f"ER-{timezone.localdate():%Y}-{equipment_request.id.hex[:8].upper()}"
    equipment_request.full_clean()
    equipment_request.save(force_insert=True)
    for line in clean_lines:
        EquipmentRequestLine.objects.create(request=equipment_request, **line)
    _event(equipment_request, actor, EquipmentRequestEvent.Type.CREATED, "Request submitted", new=equipment_request.status)
    return equipment_request


@transaction.atomic
def update_equipment_request(*, actor, request_id, values, lines):
    _require(actor, "equipment.access_equipment_requests")
    serialize_equipment_mutation()
    equipment_request = EquipmentRequest.objects.select_for_update().get(pk=request_id, requester=actor)
    if equipment_request.status != EquipmentRequest.Status.SUBMITTED:
        raise ValidationError("Only submitted requests can be edited.")
    clean_lines = _validated_lines(lines)
    for field, value in values.items():
        if field in {"needed_from", "needed_until", "destination", "purpose", "project", "priority", "accepts_substitutes", "requester_notes"}:
            setattr(equipment_request, field, value)
    equipment_request.full_clean()
    equipment_request.save()
    equipment_request.lines.all().delete()
    for line in clean_lines:
        EquipmentRequestLine.objects.create(request=equipment_request, **line)
    _event(equipment_request, actor, EquipmentRequestEvent.Type.UPDATED, "Requester updated request details")
    return equipment_request


@transaction.atomic
def cancel_equipment_request(*, actor, request_id):
    _require(actor, "equipment.access_equipment_requests")
    serialize_equipment_mutation()
    equipment_request = EquipmentRequest.objects.select_for_update().get(pk=request_id, requester=actor)
    if equipment_request.status not in {EquipmentRequest.Status.SUBMITTED, EquipmentRequest.Status.REVIEWING}:
        raise ValidationError("This request can no longer be cancelled online.")
    if equipment_request.reservation_id:
        reservation = Reservation.objects.select_for_update().get(pk=equipment_request.reservation_id)
        if reservation.status != Reservation.Status.PENDING:
            raise ValidationError("This request can no longer be cancelled online.")
        reservation.status = Reservation.Status.CANCELLED
        reservation.save(update_fields=("status", "updated_at"))
    old = equipment_request.status
    equipment_request.status = EquipmentRequest.Status.CANCELLED
    equipment_request.cancelled_at = timezone.now()
    equipment_request.cancelled_by = actor
    equipment_request.save(update_fields=("status", "cancelled_at", "cancelled_by", "updated_at"))
    _event(equipment_request, actor, EquipmentRequestEvent.Type.CANCELLED, "Request cancelled", old=old, new=equipment_request.status)
    return equipment_request


@transaction.atomic
def assign_equipment_request(*, actor, request_id, assigned_to, manager_notes=""):
    _require(actor, "equipment.manage_equipment_requests")
    serialize_equipment_mutation()
    equipment_request = EquipmentRequest.objects.select_for_update().get(pk=request_id)
    equipment_request.assigned_to = assigned_to
    equipment_request.manager_notes = manager_notes.strip()
    equipment_request.save(update_fields=("assigned_to", "manager_notes", "updated_at"))
    _event(
        equipment_request, actor, EquipmentRequestEvent.Type.ASSIGNED,
        f"Assigned to {assigned_to.get_full_name() or assigned_to.username}" if assigned_to else "Assignment cleared",
    )
    return equipment_request


def _reservation_datetimes(equipment_request):
    starts = timezone.make_aware(datetime.combine(equipment_request.needed_from, time.min))
    end_date = equipment_request.needed_until or equipment_request.needed_from
    ends = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min))
    return starts, ends


@transaction.atomic
def allocate_request_assets(*, actor, request_id, line_id, asset_ids):
    _require(actor, "equipment.manage_equipment_requests")
    serialize_equipment_mutation()
    equipment_request = EquipmentRequest.objects.select_for_update().get(pk=request_id)
    if equipment_request.status not in {EquipmentRequest.Status.SUBMITTED, EquipmentRequest.Status.REVIEWING}:
        raise ValidationError("Assets can only be allocated while a request is under review.")
    line = EquipmentRequestLine.objects.select_for_update().get(pk=line_id, request=equipment_request)
    ids = sorted({str(value) for value in asset_ids})
    assets = list(Asset.objects.select_for_update().filter(pk__in=ids, archived_at__isnull=True).order_by("asset_tag"))
    if not assets or len(assets) != len(ids):
        raise ValidationError("Select valid equipment assets.")
    unavailable = [asset.asset_tag for asset in assets if asset.status != Asset.Status.AVAILABLE]
    if unavailable:
        raise ValidationError(f"Unavailable equipment: {', '.join(unavailable)}")
    if line.category_id and not equipment_request.accepts_substitutes:
        wrong = [asset.asset_tag for asset in assets if asset.category_id != line.category_id]
        if wrong:
            raise ValidationError(f"Substitutes are not accepted: {', '.join(wrong)}")
    existing_ids = set(line.allocations.values_list("asset_id", flat=True))
    if existing_ids.intersection({asset.pk for asset in assets}):
        raise ValidationError("One or more selected assets are already allocated to this line.")
    allocated = line.allocations.aggregate(total=Sum("quantity"))["total"] or 0
    remaining = line.quantity - allocated
    if remaining <= 0:
        raise ValidationError("This request line is already fully allocated.")
    new_allocations = []
    for asset in assets:
        if remaining <= 0:
            raise ValidationError("Selected assets exceed the requested quantity.")
        amount = min(asset.quantity, remaining)
        new_allocations.append(EquipmentRequestAllocation(
            line=line, asset=asset, quantity=amount, allocated_by=actor
        ))
        remaining -= amount
    EquipmentRequestAllocation.objects.bulk_create(new_allocations)
    if equipment_request.reservation_id:
        reservation = Reservation.objects.select_for_update().get(pk=equipment_request.reservation_id)
        if reservation.status != Reservation.Status.PENDING:
            raise ValidationError("The linked reservation is no longer editable.")
        conflicts = Reservation.objects.filter(
            assets__in=assets,
            status__in=(Reservation.Status.PENDING, Reservation.Status.APPROVED),
            starts_at__lt=reservation.ends_at,
            ends_at__gt=reservation.starts_at,
        ).exclude(pk=reservation.pk)
        if conflicts.exists():
            raise ValidationError(f"Conflicts with reservation {conflicts.first().reservation_number}.")
        ReservationAsset.objects.bulk_create([
            ReservationAsset(reservation=reservation, asset=asset) for asset in assets
        ])
    else:
        starts, ends = _reservation_datetimes(equipment_request)
        reservation = create_reservation(
            actor=actor,
            requestor_id=equipment_request.requestor_party_id,
            asset_ids=[asset.pk for asset in assets],
            starts_at=starts,
            ends_at=ends,
            purpose=f"{equipment_request.request_number}: {equipment_request.purpose}",
        )
        equipment_request.reservation = reservation
    if equipment_request.status == EquipmentRequest.Status.SUBMITTED:
        equipment_request.status = EquipmentRequest.Status.REVIEWING
    equipment_request.save(update_fields=("reservation", "status", "updated_at"))
    _event(
        equipment_request, actor, EquipmentRequestEvent.Type.ALLOCATED,
        f"Allocated {len(assets)} asset(s) to {line.description}",
        metadata={"asset_tags": [asset.asset_tag for asset in assets], "line_id": line.pk},
    )
    return equipment_request


@transaction.atomic
def transition_equipment_request(*, actor, request_id, status, expected_status, note=""):
    _require(actor, "equipment.manage_equipment_requests")
    serialize_equipment_mutation()
    equipment_request = EquipmentRequest.objects.select_for_update().get(pk=request_id)
    if equipment_request.status != expected_status:
        raise ValidationError("This request changed after the page was opened. Reload and try again.")
    allowed = {
        EquipmentRequest.Status.SUBMITTED: {EquipmentRequest.Status.REVIEWING, EquipmentRequest.Status.DECLINED},
        EquipmentRequest.Status.REVIEWING: {EquipmentRequest.Status.APPROVED, EquipmentRequest.Status.DECLINED},
        EquipmentRequest.Status.APPROVED: {EquipmentRequest.Status.READY, EquipmentRequest.Status.DECLINED},
        EquipmentRequest.Status.READY: {EquipmentRequest.Status.FULFILLED},
    }
    if status not in allowed.get(equipment_request.status, set()):
        raise ValidationError("That request status transition is not allowed.")
    old = equipment_request.status
    if status == EquipmentRequest.Status.APPROVED:
        if not equipment_request.reservation_id:
            raise ValidationError("Allocate equipment before approval.")
        for line in equipment_request.lines.prefetch_related("allocations"):
            if sum(item.quantity for item in line.allocations.all()) < line.quantity:
                raise ValidationError(f"Allocate the full quantity for {line.description} before approval.")
        set_reservation_status(
            actor=actor, reservation_id=equipment_request.reservation_id, status=Reservation.Status.APPROVED
        )
    elif status == EquipmentRequest.Status.DECLINED and equipment_request.reservation_id:
        reservation = Reservation.objects.get(pk=equipment_request.reservation_id)
        if reservation.status in {Reservation.Status.PENDING, Reservation.Status.APPROVED}:
            set_reservation_status(
                actor=actor, reservation_id=reservation.pk, status=Reservation.Status.CANCELLED
            )
    equipment_request.status = status
    if note:
        equipment_request.manager_notes = note.strip()
    equipment_request.save(update_fields=("status", "manager_notes", "updated_at"))
    _event(
        equipment_request, actor, EquipmentRequestEvent.Type.STATUS_CHANGED,
        note.strip() or f"Status changed from {old} to {status}", old=old, new=status,
    )
    return equipment_request
