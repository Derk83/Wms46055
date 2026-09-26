from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST, require_safe

from .training_catalog import WAREHOUSE_TRAINING
from .training_progress import (
    InvalidTrainingProgress,
    apply_progress_action,
    completed_task_count,
    progress_context,
)


def _warehouse_module_for(request, slug):
    if not getattr(request, "is_wms_host", False):
        raise Http404
    module = WAREHOUSE_TRAINING.get(slug)
    if module is None:
        raise Http404
    if not request.user.is_superuser and not any(
        request.user.has_perm(permission) for permission in module["permissions"]
    ):
        raise PermissionDenied
    return module


@login_required
@require_safe
def warehouse_training(request, slug):
    """Render one sequential Warehouse course without operational model access."""
    module = _warehouse_module_for(request, slug)
    request.is_training_page = True
    can_open_workspace = request.user.is_superuser or any(
        request.user.has_perm(permission)
        for permission in module["workspace_permissions"]
    )
    completed = completed_task_count(
        request, "warehouse", slug, len(module["steps"])
    )
    context = {
        "training": module,
        "training_slug": slug,
        "can_open_workspace": can_open_workspace,
        "progress_url": reverse("warehouse_training_progress", kwargs={"slug": slug}),
    }
    context.update(progress_context(module["steps"], completed, module["workspace_path"], slug=slug))
    return render(request, "inventory/training/module.html", context)


@login_required
@require_POST
def warehouse_training_progress(request, slug):
    """Advance only the current Warehouse task, or reset session progress."""
    module = _warehouse_module_for(request, slug)
    try:
        apply_progress_action(
            request, "warehouse", slug, len(module["steps"])
        )
    except InvalidTrainingProgress as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect("warehouse_training", slug=slug)
