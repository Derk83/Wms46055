from django.conf import settings
from django.http import Http404, HttpResponsePermanentRedirect
from django.shortcuts import render
from django.views.decorators.http import require_safe

from .training_catalog import EQUIPMENT_TRAINING, WAREHOUSE_TRAINING


WAREHOUSE_COURSE_GROUPS = (
    (
        "Core warehouse operations",
        "Inventory movement, receiving, picking, and request fulfillment.",
        (
            "inventory-locations-scanning",
            "receiving",
            "pick-tickets-qa",
            "material-request-processing",
        ),
    ),
    (
        "Warehouse controls & administration",
        "Exceptions, counts, audit visibility, and access governance.",
        (
            "shortages-procurement",
            "cycle-counts",
            "transactions-reports",
            "users-permissions",
        ),
    ),
)
EQUIPMENT_COURSE_GROUPS = (
    (
        "Equipment lifecycle",
        "Serialized identity, custody, reservations, maintenance, and rentals.",
        (
            "asset-register",
            "custody-returns",
            "reservations",
            "maintenance-rentals",
        ),
    ),
    (
        "Equipment planning & administration",
        "Requester queue operations, staged imports, reporting, and audit controls.",
        ("request-queue", "imports-reporting"),
    ),
)


def _course_groups(groups, catalog, family, demo_url):
    return tuple({
        "title": title,
        "description": description,
        "courses": tuple({
            "slug": slug,
            **catalog[slug],
            "url": f"{demo_url}/training/{family}/{slug}/",
        } for slug in slugs),
    } for title, description, slugs in groups)


@require_safe
def hub_home(request):
    """Public application launcher for the RPL WMS domain family."""
    if not getattr(request, "is_hub_host", False):
        raise Http404
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
    if not getattr(request, "is_demo_host", False):
        raise Http404
    demo_url = settings.DEMO_URL.rstrip("/")
    return render(
        request,
        "inventory/demo/landing.html",
        {
            "inventory_demo_url": f"{demo_url}/inventory-demo/",
            "material_guide_url": f"{demo_url}/guides/material-requests/",
            "equipment_guide_url": f"{demo_url}/guides/equipment-requests/",
            "warehouse_course_groups": _course_groups(
                WAREHOUSE_COURSE_GROUPS, WAREHOUSE_TRAINING, "warehouse", demo_url
            ),
            "equipment_course_groups": _course_groups(
                EQUIPMENT_COURSE_GROUPS, EQUIPMENT_TRAINING, "equipment", demo_url
            ),
        },
    )
