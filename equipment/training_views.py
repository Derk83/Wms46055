from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST, require_safe

from inventory.training_catalog import EQUIPMENT_TRAINING
from inventory.training_progress import (
    InvalidTrainingProgress,
    apply_progress_action,
    completed_task_count,
    progress_context,
)

from .views import equipment_access


def _equipment_module_for(request, slug):
    if not getattr(request, "is_equipment_portal", False):
        raise Http404
    module = EQUIPMENT_TRAINING.get(slug)
    if module is None:
        raise Http404
    if not request.user.is_superuser and not any(
        request.user.has_perm(permission) for permission in module["permissions"]
    ):
        raise PermissionDenied
    return module


@equipment_access
@require_safe
def equipment_training(request, slug):
    """Render one sequential Equipment course inside the manager boundary."""
    module = _equipment_module_for(request, slug)
    can_open_workspace = request.user.is_superuser or any(
        request.user.has_perm(permission)
        for permission in module["workspace_permissions"]
    )
    completed = completed_task_count(
        request, "equipment", slug, len(module["steps"])
    )
    context = {
        "training": module,
        "training_slug": slug,
        "can_open_workspace": can_open_workspace,
        "progress_url": reverse("equipment_training_progress", kwargs={"slug": slug}),
    }
    context.update(progress_context(module["steps"], completed, module["workspace_path"], slug=slug))
    return render(request, "equipment/training/module.html", context)


@equipment_access
@require_POST
def equipment_training_progress(request, slug):
    """Advance only the current Equipment task, or reset session progress."""
    module = _equipment_module_for(request, slug)
    try:
        apply_progress_action(
            request, "equipment", slug, len(module["steps"])
        )
    except InvalidTrainingProgress as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect("equipment_training", slug=slug)
