from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_safe

from .training_catalog import WAREHOUSE_TRAINING


@login_required
@require_safe
def warehouse_training(request, slug):
    """Render a static Warehouse course only to users who can perform its workflow."""
    if not getattr(request, "is_wms_host", False):
        raise Http404
    module = WAREHOUSE_TRAINING.get(slug)
    if module is None:
        raise Http404
    if not request.user.is_superuser and not any(
        request.user.has_perm(permission) for permission in module["permissions"]
    ):
        raise PermissionDenied
    request.is_training_page = True
    can_open_workspace = request.user.is_superuser or any(
        request.user.has_perm(permission)
        for permission in module["workspace_permissions"]
    )
    return render(
        request,
        "inventory/training/module.html",
        {
            "training": module,
            "training_slug": slug,
            "can_open_workspace": can_open_workspace,
        },
    )
