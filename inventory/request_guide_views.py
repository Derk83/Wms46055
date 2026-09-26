"""Session-only requester practice endpoints; wire to existing /guide/ routes."""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST, require_safe

from .training_catalog import MATERIAL_REQUEST_GUIDE
from .training_progress import (
    InvalidTrainingProgress, apply_progress_action, completed_task_count,
    course_draft, progress_context,
)
from .views import all_perms_required, request_portal_access_required


@login_required
@request_portal_access_required
@all_perms_required(
    "inventory.add_materialrequest", "inventory.add_materialrequestline",
    "inventory.view_inventoryitem", "inventory.view_materialrequest",
)
@require_safe
def material_request_guide(request):
    request.is_training_page = True
    slug = "material-requests"
    namespace = "material-requester"
    return render(request, "inventory/material_request_guide.html", {
        "training": MATERIAL_REQUEST_GUIDE,
        "can_open_workspace": False,
        "progress_url": reverse("material_request_guide_progress"),
        **progress_context(
            MATERIAL_REQUEST_GUIDE["steps"],
            completed_task_count(request, namespace, slug, len(MATERIAL_REQUEST_GUIDE["steps"])),
            slug=slug, draft=course_draft(request, namespace, slug),
        ),
    })


@login_required
@request_portal_access_required
@all_perms_required(
    "inventory.add_materialrequest", "inventory.add_materialrequestline",
    "inventory.view_inventoryitem", "inventory.view_materialrequest",
)
@require_POST
def material_request_guide_progress(request):
    try:
        apply_progress_action(request, "material-requester", "material-requests", len(MATERIAL_REQUEST_GUIDE["steps"]))
    except InvalidTrainingProgress as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect("material_request_guide")
