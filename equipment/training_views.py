from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_safe

from inventory.training_catalog import EQUIPMENT_TRAINING

from .views import equipment_access


@equipment_access
@require_safe
def equipment_training(request, slug):
    """Render a static Equipment course inside the manager permission boundary."""
    if not getattr(request, "is_equipment_portal", False):
        raise Http404
    module = EQUIPMENT_TRAINING.get(slug)
    if module is None:
        raise Http404
    if not request.user.is_superuser and not any(
        request.user.has_perm(permission) for permission in module["permissions"]
    ):
        raise PermissionDenied
    can_open_workspace = request.user.is_superuser or any(
        request.user.has_perm(permission)
        for permission in module["workspace_permissions"]
    )
    return render(
        request,
        "equipment/training/module.html",
        {
            "training": module,
            "training_slug": slug,
            "can_open_workspace": can_open_workspace,
        },
    )
