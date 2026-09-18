from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from .forms import (
    EquipmentAllocationForm,
    EquipmentRequestAssignmentForm,
    EquipmentRequestForm,
    EquipmentRequestLineFormSet,
    EquipmentRequestStatusForm,
)
from .models import EquipmentRequest
from .request_services import (
    allowed_request_transitions,
    allocate_request_assets,
    assign_equipment_request,
    cancel_equipment_request,
    create_equipment_request,
    transition_equipment_request,
    update_equipment_request,
)
from .views import equipment_access, equipment_permission


def requester_access(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.has_perm("equipment.access_equipment_requests"):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


def _error_text(error):
    return " ".join(error.messages) if hasattr(error, "messages") else str(error)


def _line_payload(formset):
    return [form.cleaned_data for form in formset.forms if form.cleaned_data and not form.cleaned_data.get("DELETE")]


def _form_error_text(form):
    return " ".join(
        f"{form.fields[field].label or field.replace('_', ' ').capitalize()}: {message}" if field in form.fields else str(message)
        for field, errors in form.errors.items() for message in errors
    )


REQUEST_GUIDANCE = {
    EquipmentRequest.Status.SUBMITTED: "Your request is waiting for an equipment manager to review it.",
    EquipmentRequest.Status.REVIEWING: "An equipment manager is reviewing availability and allocating equipment.",
    EquipmentRequest.Status.APPROVED: "Your equipment is approved. The team will prepare it for pickup or delivery.",
    EquipmentRequest.Status.READY: "Your equipment is ready. Follow the pickup or delivery instructions from the equipment team.",
    EquipmentRequest.Status.FULFILLED: "This request was fulfilled. Contact the equipment team if anything is incorrect.",
    EquipmentRequest.Status.DECLINED: "This request is closed and cannot be changed online. Contact the equipment team with questions.",
    EquipmentRequest.Status.CANCELLED: "This request was cancelled and no further action will be taken.",
}


@require_safe
@never_cache
def pwa_manifest(request):
    response = JsonResponse({
        "id": "/",
        "name": "RPL Equipment Requests",
        "short_name": "Equipment Requests",
        "description": "Request equipment and track fulfillment.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#101317",
        "theme_color": "#1967d2",
        "icons": [
            {"src": "/static/equipment/eqreq/eqreq-mark.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
            {"src": "/static/equipment/eqreq/eqreq-mark.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "maskable"},
        ],
    }, content_type="application/manifest+json")
    response["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    return response


@require_safe
@never_cache
def service_worker(request):
    response = render(request, "equipment/requests/service_worker.js", content_type="application/javascript")
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Service-Worker-Allowed"] = "/"
    return response


@require_safe
def offline(request):
    return render(request, "equipment/requests/offline.html")


@requester_access
def dashboard(request):
    requests = EquipmentRequest.objects.filter(requester=request.user).prefetch_related("lines")
    status = request.GET.get("status", "").strip()
    if status in EquipmentRequest.Status.values:
        requests = requests.filter(status=status)
    return render(request, "equipment/requests/dashboard.html", {
        "page": Paginator(requests, 20).get_page(request.GET.get("page")),
        "statuses": EquipmentRequest.Status,
        "selected_status": status,
    })


@requester_access
@require_safe
def help_page(request):
    return render(request, "equipment/requests/help.html")


@requester_access
@require_safe
def account(request):
    return render(request, "equipment/requests/account.html")


@requester_access
@require_http_methods(["GET", "POST"])
def request_create(request):
    form = EquipmentRequestForm(request.POST or None)
    formset = EquipmentRequestLineFormSet(request.POST or None, prefix="lines")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        try:
            equipment_request = create_equipment_request(
                actor=request.user, values=form.cleaned_data, lines=_line_payload(formset)
            )
        except (ValidationError, PermissionDenied) as error:
            form.add_error(None, _error_text(error))
        else:
            messages.success(request, f"Request {equipment_request.request_number} was submitted.")
            return redirect("eqreq_detail", pk=equipment_request.pk)
    return render(request, "equipment/requests/form.html", {
        "form": form, "formset": formset, "title": "New equipment request", "submit_label": "Submit request",
    })


@requester_access
@require_http_methods(["GET", "POST"])
def request_edit(request, pk):
    equipment_request = get_object_or_404(EquipmentRequest.objects.prefetch_related("lines"), pk=pk, requester=request.user)
    if equipment_request.status != EquipmentRequest.Status.SUBMITTED:
        raise PermissionDenied
    initial = [{
        "category": line.category_id,
        "unlisted_equipment": line.unlisted_equipment,
        "quantity": line.quantity,
        "notes": line.notes,
    } for line in equipment_request.lines.all()]
    form = EquipmentRequestForm(request.POST or None, instance=equipment_request)
    formset = EquipmentRequestLineFormSet(request.POST or None, prefix="lines", initial=initial)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        try:
            update_equipment_request(
                actor=request.user, request_id=equipment_request.pk,
                values=form.cleaned_data, lines=_line_payload(formset),
            )
        except (ValidationError, PermissionDenied) as error:
            form.add_error(None, _error_text(error))
        else:
            messages.success(request, "Request updated.")
            return redirect("eqreq_detail", pk=equipment_request.pk)
    return render(request, "equipment/requests/form.html", {
        "form": form, "formset": formset, "title": f"Edit {equipment_request.request_number}",
        "submit_label": "Save changes", "equipment_request": equipment_request,
    })


@requester_access
def request_detail(request, pk):
    equipment_request = get_object_or_404(
        EquipmentRequest.objects.filter(requester=request.user)
        .select_related("assigned_to")
        .prefetch_related("lines", "events"), pk=pk,
    )
    return render(request, "equipment/requests/detail.html", {
        "equipment_request": equipment_request,
        "next_guidance": REQUEST_GUIDANCE[equipment_request.status],
        "can_cancel": equipment_request.status in {
            EquipmentRequest.Status.SUBMITTED, EquipmentRequest.Status.REVIEWING,
        },
    })


@requester_access
@require_POST
def request_cancel(request, pk):
    try:
        equipment_request = cancel_equipment_request(actor=request.user, request_id=pk)
    except EquipmentRequest.DoesNotExist:
        raise PermissionDenied
    except (ValidationError, PermissionDenied) as error:
        messages.error(request, _error_text(error))
    else:
        messages.success(request, f"Request {equipment_request.request_number} was cancelled.")
    return redirect("eqreq_detail", pk=pk)


@equipment_access
@equipment_permission("manage_equipment_requests")
def manager_queue(request):
    queryset = EquipmentRequest.objects.select_related("requester", "assigned_to").prefetch_related("lines")
    status = request.GET.get("status", "").strip()
    if status in EquipmentRequest.Status.values:
        queryset = queryset.filter(status=status)
    return render(request, "equipment/request_queue.html", {
        "page": Paginator(queryset, 50).get_page(request.GET.get("page")),
        "statuses": EquipmentRequest.Status,
        "selected_status": status,
    })


@equipment_access
@equipment_permission("manage_equipment_requests")
def manager_detail(request, pk):
    equipment_request = get_object_or_404(
        EquipmentRequest.objects.select_related("requester", "requestor_party", "assigned_to", "reservation")
        .prefetch_related("lines__category", "lines__allocations__asset", "events__actor"), pk=pk,
    )
    allowed = allowed_request_transitions(equipment_request.status)
    return render(request, "equipment/request_detail.html", {
        "equipment_request": equipment_request,
        "assignment_form": EquipmentRequestAssignmentForm(initial={
            "assigned_to": equipment_request.assigned_to,
            "manager_notes": equipment_request.manager_notes,
        }),
        "allocation_form": EquipmentAllocationForm(equipment_request=equipment_request),
        "status_form": EquipmentRequestStatusForm(initial={"expected_status": equipment_request.status}),
        "allowed_transitions": [
            (value, EquipmentRequest.Status(value).label) for value in allowed
        ],
        "is_terminal": not allowed,
        "can_allocate": equipment_request.status in {
            EquipmentRequest.Status.SUBMITTED, EquipmentRequest.Status.REVIEWING,
        },
    })


@equipment_access
@equipment_permission("manage_equipment_requests")
@require_POST
def manager_assign(request, pk):
    form = EquipmentRequestAssignmentForm(request.POST)
    if form.is_valid():
        try:
            assign_equipment_request(
                actor=request.user, request_id=pk, assigned_to=form.cleaned_data["assigned_to"],
                manager_notes=form.cleaned_data["manager_notes"],
            )
        except (ValidationError, PermissionDenied) as error:
            messages.error(request, _error_text(error))
        else:
            messages.success(request, "Assignment updated.")
    else:
        messages.error(request, _form_error_text(form) or "Correct the assignment details.")
    return redirect("equipment_request_detail", pk=pk)


@equipment_access
@equipment_permission("manage_equipment_requests")
@require_POST
def manager_allocate(request, pk):
    equipment_request = get_object_or_404(EquipmentRequest, pk=pk)
    form = EquipmentAllocationForm(request.POST, equipment_request=equipment_request)
    if form.is_valid():
        try:
            allocate_request_assets(
                actor=request.user, request_id=pk, line_id=form.cleaned_data["line"].pk,
                asset_ids=[asset.pk for asset in form.cleaned_data["assets"]],
            )
        except (ValidationError, PermissionDenied) as error:
            messages.error(request, _error_text(error))
        else:
            messages.success(request, "Assets allocated and linked reservation updated.")
    else:
        messages.error(request, _form_error_text(form) or "Choose a request line and available assets.")
    return redirect("equipment_request_detail", pk=pk)


@equipment_access
@equipment_permission("manage_equipment_requests")
@require_POST
def manager_transition(request, pk):
    form = EquipmentRequestStatusForm(request.POST)
    if form.is_valid():
        try:
            transition_equipment_request(
                actor=request.user, request_id=pk, status=form.cleaned_data["status"],
                expected_status=form.cleaned_data["expected_status"], note=form.cleaned_data["note"],
            )
        except (ValidationError, PermissionDenied) as error:
            messages.error(request, _error_text(error))
        else:
            messages.success(request, "Request status updated.")
    else:
        messages.error(request, _form_error_text(form) or "Choose a valid status action.")
    return redirect("equipment_request_detail", pk=pk)
