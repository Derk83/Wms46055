from django.conf import settings
from django.http import HttpResponsePermanentRedirect
from django.shortcuts import render
from django.views.decorators.http import require_safe


@require_safe
def hub_home(request):
    """Public application launcher for the RPL WMS domain family."""
    host = request.get_host().split(":", 1)[0].lower().rstrip(".")
    if host == "www.rplwms.com":
        return HttpResponsePermanentRedirect("https://rplwms.com/")
    return render(
        request,
        "inventory/hub.html",
        {
            "warehouse_url": settings.APP_URL,
            "requests_url": settings.REQUESTS_URL,
            "equipment_url": settings.EQUIPMENT_URL,
        },
    )
