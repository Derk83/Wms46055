from django.conf import settings
from django.shortcuts import render
from django.views.decorators.http import require_safe


@require_safe
def hub_home(request):
    """Public application launcher for the RPL WMS domain family."""
    return render(
        request,
        "inventory/hub.html",
        {
            "warehouse_url": settings.APP_URL,
            "requests_url": settings.REQUESTS_URL,
            "equipment_url": settings.EQUIPMENT_URL,
        },
    )
