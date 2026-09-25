from .models import PickTicket


def pending_pick_assignment(request):
    """Expose the oldest unacknowledged warehouse assignment for the blocking prompt."""
    if (
        getattr(request, "is_training_page", False)
        or not getattr(request, "user", None)
        or not request.user.is_authenticated
        or not getattr(request, "is_wms_host", False)
    ):
        return {}
    pending = (
        PickTicket.objects.filter(
            assigned_to=request.user,
            acknowledged_at__isnull=True,
            status=PickTicket.Status.OPEN,
        )
        .order_by("assigned_at", "created_at")
        .first()
    )
    if pending is None:
        return {}
    return {
        "pending_pick_assignment": pending,
        "pending_pick_assignment_count": PickTicket.objects.filter(
            assigned_to=request.user,
            acknowledged_at__isnull=True,
            status=PickTicket.Status.OPEN,
        ).count(),
    }
