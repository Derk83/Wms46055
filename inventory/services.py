"""Atomic material-request to pick-ticket conversion and synchronization."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    InventoryItem,
    MaterialRequest,
    MaterialRequestEvent,
    MaterialRequestLine,
    PickTicket,
    PickTicketLine,
)


def _normalized_lines(lines):
    normalized = []
    seen = set()
    for row in lines:
        item = row.get("item")
        quantity = row.get("quantity")
        if not item or not quantity or int(quantity) <= 0:
            raise ValidationError("Every request line needs an item and a positive quantity.")
        if item.pk in seen:
            raise ValidationError("Duplicate items are not allowed in a material request.")
        seen.add(item.pk)
        normalized.append({"item_id": item.pk, "quantity": int(quantity), "notes": row.get("notes", "")})
    if not normalized:
        raise ValidationError("At least one material request line is required.")
    return normalized


def _display_change(value):
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if value in (None, ""):
        return "—"
    if hasattr(value, "strftime"):
        return timezone.localtime(value).strftime("%b %-d, %Y at %-I:%M %p")
    return str(value)


@transaction.atomic
def assign_material_request(material_request, *, assignee, actor):
    """Assign warehouse ownership and publish one auditable update event."""
    locked = MaterialRequest.objects.select_for_update().select_related(
        "assigned_to", "pick_ticket"
    ).get(pk=material_request.pk)
    old_assignee = locked.assigned_to
    old_id = old_assignee.pk if old_assignee else None
    new_id = assignee.pk if assignee else None
    if old_id == new_id:
        return locked, False

    def display_user(user):
        return (user.get_full_name() or user.username) if user else "Unassigned"

    locked.assigned_to = assignee
    locked.save(update_fields=["assigned_to", "updated_at"])
    event = MaterialRequestEvent.objects.create(
        material_request=locked,
        event_type=MaterialRequestEvent.EventType.UPDATED,
        request_number_snapshot=locked.request_number,
        ticket_number_snapshot=locked.pick_ticket.ticket_number,
        requestor_snapshot=locked.requestor_name,
        change_summary=f"Assigned to: {display_user(old_assignee)} → {display_user(assignee)}",
        actor=actor,
    )
    from .push import queue_material_request_push

    queue_material_request_push(event)
    return locked, True


def _material_request_change_summary(material_request, new_values, rows):
    changes = []
    labels = {
        "requestor_name": "Requestor",
        "requestor_email": "Requestor email",
        "building_room": "Building / room",
        "location": "Location",
        "delivery_at": "Delivery time",
        "urgent": "Urgent",
        "notes": "Notes",
    }
    for field, label in labels.items():
        old_value = getattr(material_request, field)
        new_value = new_values[field]
        if old_value != new_value:
            changes.append(f"{label}: {_display_change(old_value)} → {_display_change(new_value)}")

    old_by_item = {
        line.item_id: line
        for line in material_request.lines.select_related("item").all()
    }
    new_by_item = {row["item_id"]: row for row in rows}
    items = {
        item.pk: item
        for item in InventoryItem.objects.filter(pk__in=old_by_item.keys() | new_by_item.keys())
    }
    for item_id in sorted(old_by_item.keys() | new_by_item.keys()):
        old_line = old_by_item.get(item_id)
        new_line = new_by_item.get(item_id)
        part = items[item_id].part_number
        if old_line is None:
            changes.append(f"{part} added: quantity {new_line['quantity']}")
            if new_line.get("notes"):
                changes.append(f"{part} line notes: — → {new_line['notes']}")
        elif new_line is None:
            changes.append(f"{part} removed: quantity {old_line.quantity}")
        else:
            if old_line.quantity != new_line["quantity"]:
                changes.append(f"{part} quantity: {old_line.quantity} → {new_line['quantity']}")
            old_notes = old_line.notes or ""
            new_notes = new_line.get("notes") or ""
            if old_notes != new_notes:
                changes.append(f"{part} line notes: {_display_change(old_notes)} → {_display_change(new_notes)}")

    if material_request.delivery_acceptance_confirmed_at and changes:
        changes.append("Delivery readiness: Confirmed → Awaiting confirmation")
    return "; ".join(changes)


def _create_lines(material_request, ticket, rows):
    # Refresh objects so legacy PickTicketLine hooks never save stale stock values.
    items = {
        item.pk: item
        for item in InventoryItem.objects.select_for_update().filter(
            pk__in=[row["item_id"] for row in rows]
        )
    }
    for row in rows:
        item = items[row["item_id"]]
        MaterialRequestLine.objects.create(
            material_request=material_request,
            item=item,
            quantity=row["quantity"],
            notes=row["notes"],
        )
        PickTicketLine.objects.create(ticket=ticket, item=item, quantity=row["quantity"])


@transaction.atomic
def create_material_request(
    *, creator, requestor_name, building_room, location, notes, lines, delivery_at=None,
    requestor_email="", urgent=False,
):
    rows = _normalized_lines(lines)
    ticket = PickTicket.objects.create(
        status=PickTicket.Status.OPEN,
        picked_by_name="",
        received_by_name="",
        requested_by_name=requestor_name,
        building_room=building_room,
        location=location,
        notes=notes,
        created_by=creator,
    )
    material_request = MaterialRequest.objects.create(
        requestor_name=requestor_name,
        requestor_email=requestor_email,
        urgent=urgent,
        building_room=building_room,
        location=location,
        delivery_at=delivery_at,
        notes=notes,
        creator=creator,
        pick_ticket=ticket,
    )
    _create_lines(material_request, ticket, rows)
    event = MaterialRequestEvent.objects.create(material_request=material_request)
    from .push import queue_material_request_push

    queue_material_request_push(event)
    return material_request


@transaction.atomic
def update_pick_ticket_status(ticket, new_status, *, actor):
    """Persist a real status transition and queue its requester notification once."""
    valid_statuses = {value for value, _label in PickTicket.Status.choices}
    if new_status not in valid_statuses:
        raise ValidationError("Invalid pick-ticket status.")
    ticket = PickTicket.objects.select_for_update().get(pk=ticket.pk)
    old_status = ticket.status
    if old_status == new_status:
        return None

    ticket.status = new_status
    ticket.save(update_fields=["status", "updated_at"])
    try:
        material_request = ticket.material_request
    except MaterialRequest.DoesNotExist:
        return None

    if new_status == PickTicket.Status.RECEIVED:
        material_request.delivery_acceptance_confirmed_at = None
        material_request.delivery_acceptance_confirmed_by = None
        material_request.delivery_not_ready_at = None
        material_request.delivery_not_ready_by = None
        material_request.delivery_response_note = ""
        material_request.save(update_fields=[
            "delivery_acceptance_confirmed_at", "delivery_acceptance_confirmed_by",
            "delivery_not_ready_at", "delivery_not_ready_by", "delivery_response_note", "updated_at"
        ])

    event = MaterialRequestEvent.objects.create(
        material_request=material_request,
        event_type=MaterialRequestEvent.EventType.STATUS_CHANGED,
        old_status=old_status,
        new_status=new_status,
        actor=actor,
    )
    from .push import queue_material_request_push

    queue_material_request_push(event)
    if new_status == PickTicket.Status.RECEIVED and material_request.requestor_email:
        from .delivery_email import queue_ready_for_delivery_email

        queue_ready_for_delivery_email(event)
    return event


@transaction.atomic
def confirm_delivery_acceptance(
    material_request, *, user, expected_ready_event_id=None
):
    """Record requester readiness without marking the physical order delivered."""
    material_request = MaterialRequest.objects.select_for_update().select_related("pick_ticket").get(
        pk=material_request.pk
    )
    if material_request.creator_id != user.pk:
        raise ValidationError("Only the requesting user can confirm delivery readiness.")
    if material_request.pick_ticket.status != PickTicket.Status.RECEIVED:
        raise ValidationError("This order is not ready for delivery.")
    if expected_ready_event_id and material_request.events.filter(
        pk__gt=expected_ready_event_id,
        event_type__in=[
            MaterialRequestEvent.EventType.STATUS_CHANGED,
            MaterialRequestEvent.EventType.UPDATED,
            MaterialRequestEvent.EventType.DELIVERY_ACCEPTANCE_CONFIRMED,
            MaterialRequestEvent.EventType.DELIVERY_NOT_READY,
        ],
    ).exists():
        raise ValidationError("This delivery-response link has already been used or replaced.")
    if material_request.delivery_acceptance_confirmed_at:
        return material_request.events.filter(
            event_type=MaterialRequestEvent.EventType.DELIVERY_ACCEPTANCE_CONFIRMED
        ).order_by("-id").first()

    from django.utils import timezone

    material_request.delivery_acceptance_confirmed_at = timezone.now()
    material_request.delivery_acceptance_confirmed_by = user
    material_request.delivery_not_ready_at = None
    material_request.delivery_not_ready_by = None
    material_request.delivery_response_note = ""
    material_request.save(update_fields=[
        "delivery_acceptance_confirmed_at", "delivery_acceptance_confirmed_by",
        "delivery_not_ready_at", "delivery_not_ready_by", "delivery_response_note", "updated_at"
    ])
    event = MaterialRequestEvent.objects.create(
        material_request=material_request,
        event_type=MaterialRequestEvent.EventType.DELIVERY_ACCEPTANCE_CONFIRMED,
        old_status=material_request.pick_ticket.status,
        new_status=material_request.pick_ticket.status,
        actor=user,
    )
    from .push import queue_material_request_push

    queue_material_request_push(event)
    return event


@transaction.atomic
def decline_delivery_readiness(
    material_request, *, user, delivery_at=None, note="", expected_ready_event_id=None
):
    """Record requester unavailability and optionally move the requested delivery slot."""
    from django.utils import timezone

    material_request = MaterialRequest.objects.select_for_update().select_related("pick_ticket").get(
        pk=material_request.pk
    )
    if material_request.creator_id != user.pk:
        raise ValidationError("Only the requesting user can respond to delivery readiness.")
    if material_request.pick_ticket.status != PickTicket.Status.RECEIVED:
        raise ValidationError("This order is not ready for delivery.")
    if expected_ready_event_id and material_request.events.filter(
        pk__gt=expected_ready_event_id,
        event_type__in=[
            MaterialRequestEvent.EventType.STATUS_CHANGED,
            MaterialRequestEvent.EventType.UPDATED,
            MaterialRequestEvent.EventType.DELIVERY_ACCEPTANCE_CONFIRMED,
            MaterialRequestEvent.EventType.DELIVERY_NOT_READY,
        ],
    ).exists():
        raise ValidationError("This delivery-response link has already been used or replaced.")

    note = (note or "").strip()
    new_slot = delivery_at if delivery_at is not None else material_request.delivery_at
    if (
        material_request.delivery_not_ready_at
        and material_request.delivery_at == new_slot
        and material_request.delivery_response_note == note
    ):
        return material_request.events.filter(
            event_type=MaterialRequestEvent.EventType.DELIVERY_NOT_READY
        ).order_by("-id").first()

    material_request.delivery_at = new_slot
    material_request.delivery_acceptance_confirmed_at = None
    material_request.delivery_acceptance_confirmed_by = None
    material_request.delivery_not_ready_at = timezone.now()
    material_request.delivery_not_ready_by = user
    material_request.delivery_response_note = note
    material_request.save(update_fields=[
        "delivery_at", "delivery_acceptance_confirmed_at", "delivery_acceptance_confirmed_by",
        "delivery_not_ready_at", "delivery_not_ready_by", "delivery_response_note", "updated_at",
    ])
    event = MaterialRequestEvent.objects.create(
        material_request=material_request,
        event_type=MaterialRequestEvent.EventType.DELIVERY_NOT_READY,
        old_status=material_request.pick_ticket.status,
        new_status=material_request.pick_ticket.status,
        actor=user,
    )
    from .push import queue_material_request_push

    queue_material_request_push(event)
    return event


@transaction.atomic
def update_material_request(
    material_request, *, requestor_name, building_room, location, notes, lines, delivery_at=None,
    actor=None, requestor_email="", urgent=None,
):
    rows = _normalized_lines(lines)
    material_request = MaterialRequest.objects.select_for_update().select_related("pick_ticket").get(
        pk=material_request.pk
    )
    change_summary = _material_request_change_summary(
        material_request,
        {
            "requestor_name": requestor_name,
            "requestor_email": requestor_email,
            "building_room": building_room,
            "location": location,
            "delivery_at": delivery_at,
            "urgent": material_request.urgent if urgent is None else urgent,
            "notes": notes,
        },
        rows,
    )
    ticket = PickTicket.objects.select_for_update().get(pk=material_request.pick_ticket_id)

    material_request.requestor_name = requestor_name
    material_request.requestor_email = requestor_email
    material_request.building_room = building_room
    material_request.location = location
    material_request.delivery_at = delivery_at
    material_request.urgent = material_request.urgent if urgent is None else urgent
    material_request.notes = notes
    material_request.save(
        update_fields=[
            "requestor_name", "requestor_email", "building_room", "location", "delivery_at", "urgent", "notes", "updated_at"
        ]
    )

    ticket.requested_by_name = requestor_name
    ticket.building_room = building_room
    ticket.location = location
    ticket.notes = notes
    ticket.save(update_fields=["requested_by_name", "building_room", "location", "notes", "updated_at"])

    for line in list(ticket.lines.select_related("item")):
        line.delete()
    material_request.lines.all().delete()
    _create_lines(material_request, ticket, rows)
    if change_summary:
        event = MaterialRequestEvent.objects.create(
            material_request=material_request,
            event_type=MaterialRequestEvent.EventType.UPDATED,
            actor=actor,
            change_summary=change_summary,
        )
        from .push import queue_material_request_push

        queue_material_request_push(event)
    return material_request


@transaction.atomic
def delete_material_request(material_request, *, actor=None):
    material_request = MaterialRequest.objects.select_for_update().select_related("pick_ticket").get(
        pk=material_request.pk
    )
    ticket = PickTicket.objects.select_for_update().get(pk=material_request.pick_ticket_id)
    event = MaterialRequestEvent.objects.create(
        material_request=material_request,
        event_type=MaterialRequestEvent.EventType.DELETED,
        actor=actor,
        request_number_snapshot=material_request.request_number,
        ticket_number_snapshot=ticket.ticket_number,
        requestor_snapshot=material_request.requestor_name,
    )
    from .push import queue_material_request_push

    queue_material_request_push(event)
    # Remove the protected link first; the event/outbox survives with its snapshots.
    material_request.delete()
    ticket.delete()
    return event
