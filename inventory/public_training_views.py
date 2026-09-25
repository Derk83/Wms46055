"""Anonymous, session-only training hosted exclusively on demo.rplwms.com.

This module deliberately imports no operational models. Public course progress is stored
in the anonymous browser session and completion notes are validated but never retained.
"""

from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .training_catalog import (
    EQUIPMENT_REQUEST_GUIDE,
    EQUIPMENT_TRAINING,
    MATERIAL_REQUEST_GUIDE,
    WAREHOUSE_TRAINING,
)
from .training_progress import (
    InvalidTrainingProgress,
    apply_progress_action,
    completed_task_count,
    progress_context,
)


COURSE_FAMILIES = {
    "warehouse": ("Warehouse training", "public-warehouse", WAREHOUSE_TRAINING),
    "equipment": ("Equipment training", "public-equipment", EQUIPMENT_TRAINING),
}
REQUEST_GUIDES = {
    "material-requests": ("Requester workflow", "public-material-requests", MATERIAL_REQUEST_GUIDE),
    "equipment-requests": ("Requester workflow", "public-equipment-requests", EQUIPMENT_REQUEST_GUIDE),
}


def _require_demo_host(request):
    if not getattr(request, "is_demo_host", False):
        raise Http404
    request.is_training_page = True


def _render_course(request, label, namespace, slug, training, progress_url):
    completed = completed_task_count(request, namespace, slug, len(training["steps"]))
    context = {
        "training": training,
        "training_family_label": label,
        "public_training": True,
        "can_open_workspace": False,
        "progress_url": progress_url,
        **progress_context(training["steps"], completed),
    }
    return render(request, "inventory/training/module.html", context)


def _family_course(family, slug):
    try:
        label, namespace, catalog = COURSE_FAMILIES[family]
        training = catalog[slug]
    except KeyError as exc:
        raise Http404 from exc
    return label, namespace, training


def _request_guide(slug):
    try:
        return REQUEST_GUIDES[slug]
    except KeyError as exc:
        raise Http404 from exc


@require_GET
def public_training_course(request, family, slug):
    _require_demo_host(request)
    label, namespace, training = _family_course(family, slug)
    progress_url = reverse("public_training_progress", args=(family, slug))
    return _render_course(request, label, namespace, slug, training, progress_url)


@require_POST
def public_training_progress(request, family, slug):
    _require_demo_host(request)
    _label, namespace, training = _family_course(family, slug)
    try:
        apply_progress_action(request, namespace, slug, len(training["steps"]))
    except InvalidTrainingProgress as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect("public_training_course", family=family, slug=slug)


@require_GET
def public_request_guide(request, slug):
    _require_demo_host(request)
    label, namespace, training = _request_guide(slug)
    progress_url = reverse("public_request_guide_progress", args=(slug,))
    return _render_course(request, label, namespace, slug, training, progress_url)


@require_POST
def public_request_guide_progress(request, slug):
    _require_demo_host(request)
    _label, namespace, training = _request_guide(slug)
    try:
        apply_progress_action(request, namespace, slug, len(training["steps"]))
    except InvalidTrainingProgress as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect("public_request_guide", slug=slug)
