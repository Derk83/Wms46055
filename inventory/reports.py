"""Data aggregations for the manager reports section.

All functions accept an inclusive [start, end] datetime range and return plain
Python dicts/lists so views and the PDF template can render without ORM
queries at template render time.

Status fields on MaterialRequest are implicit — they are derived from which
optional delivery/pick-ticket fields are set. These helpers make that derivation
explicit and testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, date

from django.contrib.auth import get_user_model
from django.db.models import Count, Sum
from django.utils import timezone

from .models import (
    InventoryItem,
    InventoryTransaction,
    MaterialRequest,
    MaterialRequestEvent,
    MaterialRequestLine,
    PickTicket,
    ReceivingLine,
)


User = get_user_model()


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------


def material_request_status(mr: MaterialRequest) -> str:
    """Derive a coarse status for a MaterialRequest.

    Mirrors what the rest of the app does, but centralized here so reports
    don't each invent their own derivation.
    """
    if mr.archived_at is not None:
        return "Archived"
    if mr.delivery_not_ready_at is not None:
        return "Denied / Reschedule requested"
    if mr.delivery_acceptance_confirmed_at is not None:
        return "Delivered"
    if mr.delivery_at is not None:
        return "Ready for pickup"
    try:
        ticket = mr.pick_ticket
    except PickTicket.DoesNotExist:
        return "Open"
    if ticket.status == PickTicket.Status.PICKED:
        return "Picked"
    if ticket.status == PickTicket.Status.RECEIVED:
        return "Picked (awaiting confirmation)"
    return "Open"


# ---------------------------------------------------------------------------
# Range parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DateRange:
    start: datetime  # inclusive
    end: datetime    # exclusive
    label: str

    def __iter__(self):
        return iter((self.start, self.end))


def parse_date_range(*, kind: str, raw_date: str | None = None,
                     raw_start: str | None = None, raw_end: str | None = None) -> DateRange:
    """Resolve a DateRange from URL params.

    `kind` is either 'daily' or 'weekly'/'range'. The two endpoints accept
    slightly different parameters but the result is the same DateRange shape.
    """
    today = timezone.localdate()
    if kind == "daily":
        d = _parse_date(raw_date) if raw_date else today
        start = timezone.make_aware(datetime.combine(d, time.min))
        end = timezone.make_aware(datetime.combine(d + timedelta(days=1), time.min))
        return DateRange(start=start, end=end, label=d.strftime("%A, %B %-d, %Y"))
    # 'weekly' or custom range
    if raw_start and raw_end:
        start_d = _parse_date(raw_start)
        end_d = _parse_date(raw_end)
    else:
        # default = last 7 days (today - 6 .. today inclusive)
        start_d = today - timedelta(days=6)
        end_d = today
    start = timezone.make_aware(datetime.combine(start_d, time.min))
    # End is exclusive, so add one day
    end = timezone.make_aware(datetime.combine(end_d + timedelta(days=1), time.min))
    return DateRange(
        start=start,
        end=end,
        label=f"{start_d.strftime('%b %-d, %Y')} – {end_d.strftime('%b %-d, %Y')}",
    )


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except (ValueError, TypeError):
        return timezone.localdate()


def preset_dates(preset: str) -> tuple[date, date]:
    """Resolve a quick-pick preset name to (start, end) inclusive dates."""
    today = timezone.localdate()
    if preset == "today":
        return today, today
    if preset == "yesterday":
        d = today - timedelta(days=1)
        return d, d
    if preset == "this_week":
        return today - timedelta(days=6), today
    if preset == "last_week":
        return today - timedelta(days=13), today - timedelta(days=7)
    if preset == "this_month":
        return today.replace(day=1), today
    if preset == "last_month":
        first_this = today.replace(day=1)
        last_of_prev = first_this - timedelta(days=1)
        first_of_prev = last_of_prev.replace(day=1)
        return first_of_prev, last_of_prev
    return today, today


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------


def activity_summary(rng: DateRange) -> dict:
    """High-level counts for the summary card."""
    new_requests = MaterialRequest.objects.filter(
        created_at__gte=rng.start, created_at__lt=rng.end
    ).count()
    confirmed = MaterialRequest.objects.filter(
        delivery_acceptance_confirmed_at__gte=rng.start,
        delivery_acceptance_confirmed_at__lt=rng.end,
    ).count()
    denied = MaterialRequest.objects.filter(
        delivery_not_ready_at__gte=rng.start,
        delivery_not_ready_at__lt=rng.end,
    ).count()
    tickets_opened = PickTicket.objects.filter(
        created_at__gte=rng.start, created_at__lt=rng.end
    ).count()
    # Tickets "picked" = transitioned out of OPEN during the range. We use
    # updated_at as a proxy for the pick timestamp because PickTicket has no
    # dedicated picked_at field.
    tickets_picked = PickTicket.objects.filter(
        updated_at__gte=rng.start, updated_at__lt=rng.end
    ).exclude(status=PickTicket.Status.OPEN).count()
    receiving_lines = ReceivingLine.objects.filter(
        created_at__gte=rng.start, created_at__lt=rng.end
    ).count()
    units_received = ReceivingLine.objects.filter(
        created_at__gte=rng.start, created_at__lt=rng.end
    ).aggregate(total=Sum("quantity"))["total"] or 0
    urgent_new = MaterialRequest.objects.filter(
        created_at__gte=rng.start, created_at__lt=rng.end, urgent=True
    ).count()
    urgent_closed = MaterialRequest.objects.filter(
        delivery_acceptance_confirmed_at__gte=rng.start,
        delivery_acceptance_confirmed_at__lt=rng.end,
        urgent=True,
    ).count()
    return {
        "new_requests": new_requests,
        "confirmed_deliveries": confirmed,
        "denied_or_rescheduled": denied,
        "tickets_opened": tickets_opened,
        "tickets_picked": tickets_picked,
        "receiving_lines": receiving_lines,
        "units_received": units_received,
        "urgent_new": urgent_new,
        "urgent_closed": urgent_closed,
    }


def new_material_requests(rng: DateRange) -> list[MaterialRequest]:
    return list(
        MaterialRequest.objects.filter(
            created_at__gte=rng.start, created_at__lt=rng.end
        )
        .select_related("creator", "pick_ticket", "assigned_to")
        .prefetch_related("lines__item")
        .order_by("-created_at")
    )


def delivery_confirmations(rng: DateRange) -> list[MaterialRequest]:
    return list(
        MaterialRequest.objects.filter(
            delivery_acceptance_confirmed_at__gte=rng.start,
            delivery_acceptance_confirmed_at__lt=rng.end,
        )
        .select_related("delivery_acceptance_confirmed_by", "pick_ticket")
        .order_by("-delivery_acceptance_confirmed_at")
    )


def delivery_denials(rng: DateRange) -> list[MaterialRequest]:
    return list(
        MaterialRequest.objects.filter(
            delivery_not_ready_at__gte=rng.start,
            delivery_not_ready_at__lt=rng.end,
        )
        .select_related("delivery_not_ready_by", "pick_ticket")
        .order_by("-delivery_not_ready_at")
    )


def pick_tickets_opened(rng: DateRange) -> list[PickTicket]:
    return list(
        PickTicket.objects.filter(
            created_at__gte=rng.start, created_at__lt=rng.end
        )
        .select_related("created_by")
        .order_by("-created_at")
    )


def pick_tickets_completed(rng: DateRange) -> list[PickTicket]:
    """Tickets that left OPEN during the range (PICKED/RECEIVED/CLOSED)."""
    return list(
        PickTicket.objects.filter(
            updated_at__gte=rng.start, updated_at__lt=rng.end
        )
        .exclude(status=PickTicket.Status.OPEN)
        .order_by("-updated_at")
    )


def pick_tickets_still_open() -> list[PickTicket]:
    """All open pick tickets, with age in days attached."""
    tickets = list(
        PickTicket.objects.filter(status__in=[PickTicket.Status.OPEN])
        .select_related("created_by", "material_request")
        .order_by("created_at")
    )
    now = timezone.now()
    annotated = []
    for t in tickets:
        age = (now - t.created_at).days if t.created_at else 0
        annotated.append((t, age))
    return annotated


def receiving_lines_in_range(rng: DateRange) -> list[ReceivingLine]:
    return list(
        ReceivingLine.objects.filter(
            created_at__gte=rng.start, created_at__lt=rng.end
        )
        .select_related("item", "ticket")
        .order_by("-created_at")
    )


def receiving_by_item(rng: DateRange, limit: int = 20) -> list[tuple]:
    qs = (
        ReceivingLine.objects.filter(
            created_at__gte=rng.start, created_at__lt=rng.end
        )
        .values("item__part_number", "item__name", "item_id")
        .annotate(units=Sum("quantity"), lines=Count("id"))
        .order_by("-units")
    )
    return list(qs[:limit])


def out_of_stock_items() -> list[InventoryItem]:
    return list(
        InventoryItem.objects.filter(
            quantity_on_hand__lte=0, active=True
        ).order_by("part_number")
    )


def low_stock_items_py() -> list[InventoryItem]:
    """Items at or below their low_stock_threshold (>0 threshold)."""
    out = []
    for it in InventoryItem.objects.filter(active=True).only(
        "part_number", "name", "quantity_on_hand", "low_stock_threshold"
    ):
        if (
            it.low_stock_threshold
            and it.low_stock_threshold > 0
            and it.quantity_on_hand <= it.low_stock_threshold
        ):
            out.append(it)
    out.sort(key=lambda i: i.quantity_on_hand)
    return out


def top_requesters(rng: DateRange, limit: int = 5) -> list[tuple]:
    qs = (
        MaterialRequest.objects.filter(
            created_at__gte=rng.start, created_at__lt=rng.end
        )
        .values("creator__username", "creator_id")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    return list(qs[:limit])


def top_items(rng: DateRange, limit: int = 10) -> list[tuple]:
    qs = (
        MaterialRequestLine.objects.filter(
            material_request__created_at__gte=rng.start,
            material_request__created_at__lt=rng.end,
        )
        .values("item__part_number", "item__name", "item_id")
        .annotate(units=Sum("quantity"), lines=Count("id"))
        .order_by("-units")
    )
    return list(qs[:limit])


def audit_events(rng: DateRange, limit: int = 50) -> list[MaterialRequestEvent]:
    return list(
        MaterialRequestEvent.objects.filter(
            created_at__gte=rng.start, created_at__lt=rng.end
        )
        .select_related("material_request", "actor")
        .order_by("-created_at")[:limit]
    )


# ---------------------------------------------------------------------------
# Trends (for the weekly report only)
# ---------------------------------------------------------------------------


def daily_request_counts(rng: DateRange) -> list[tuple[date, int]]:
    """Return one (date, count) per day in the range."""
    out = []
    cursor = timezone.localtime(rng.start).date()
    end = (timezone.localtime(rng.end) - timedelta(days=1)).date()
    while cursor <= end:
        day_start = timezone.make_aware(datetime.combine(cursor, time.min))
        day_end = day_start + timedelta(days=1)
        n = MaterialRequest.objects.filter(
            created_at__gte=day_start, created_at__lt=day_end
        ).count()
        out.append((cursor, n))
        cursor += timedelta(days=1)
    return out


def oldest_open_age_days() -> int | None:
    oldest = PickTicket.objects.filter(
        status__in=[PickTicket.Status.OPEN]
    ).order_by("created_at").values_list("created_at", flat=True).first()
    if not oldest:
        return None
    return (timezone.now() - oldest).days


def units_moved_summary(rng: DateRange) -> dict:
    """Sum of receipt vs pick transactions in the range, for the trends card."""
    receipts = InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.RECEIPT,
        created_at__gte=rng.start, created_at__lt=rng.end,
    ).aggregate(total=Sum("quantity_delta"))["total"] or 0
    picks = InventoryTransaction.objects.filter(
        transaction_type=InventoryTransaction.TransactionType.PICK,
        created_at__gte=rng.start, created_at__lt=rng.end,
    ).aggregate(total=Sum("quantity_delta"))["total"] or 0
    return {"receipts": receipts, "picks": abs(picks)}
