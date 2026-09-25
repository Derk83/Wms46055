from django.conf import settings
from django.http import HttpResponsePermanentRedirect
from django.shortcuts import render
from django.views.decorators.http import require_safe

from .training_catalog import EQUIPMENT_TRAINING, WAREHOUSE_TRAINING


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
            "equipment_requests_url": settings.EQUIPMENT_REQUESTS_URL,
        },
    )


@require_safe
def demo_home(request):
    """Public, data-free catalog for demos, requester guides, and protected training."""
    warehouse_url = settings.APP_URL.rstrip("/")
    equipment_url = settings.EQUIPMENT_URL.rstrip("/")
    warehouse_courses = [
        {**course, "slug": slug, "url": f"{warehouse_url}/training/{slug}/"}
        for slug, course in WAREHOUSE_TRAINING.items()
    ]
    equipment_courses = [
        {**course, "slug": slug, "url": f"{equipment_url}/training/{slug}/"}
        for slug, course in EQUIPMENT_TRAINING.items()
    ]
    return render(
        request,
        "inventory/demo/landing.html",
        {
            "inventory_demo_url": f"{warehouse_url}/demo/",
            "material_guide_url": f"{settings.REQUESTS_URL.rstrip('/')}/guide/",
            "equipment_guide_url": f"{settings.EQUIPMENT_REQUESTS_URL.rstrip('/')}/help/",
            "warehouse_courses": warehouse_courses,
            "equipment_courses": equipment_courses,
        },
    )
