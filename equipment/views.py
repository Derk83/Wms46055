import base64
import csv
from functools import wraps
from io import BytesIO

import qrcode
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .forms import (
    AssetForm,
    CheckoutForm,
    ImportUploadForm,
    MaintenanceCompleteForm,
    MaintenanceForm,
    ReservationForm,
    ReservationStatusForm,
    ReturnForm,
)
from .importer import import_workbook
from .models import (
    ActiveCustody,
    Asset,
    AssetEvent,
    AssetIdentifier,
    Checkout,
    EquipmentCategory,
    EquipmentImportBatch,
    MaintenanceWorkOrder,
    RentalContract,
    Reservation,
)
from .services import (
    checkout_assets,
    complete_maintenance,
    create_asset,
    create_reservation,
    open_maintenance,
    return_asset,
    set_reservation_status,
    update_asset,
)


def equipment_access(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.has_perm("equipment.access_equipment_portal"):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapped


def equipment_permission(codename):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.has_perm(f"equipment.{codename}"):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def _service_error(request, error):
    if hasattr(error, "messages"):
        text = " ".join(error.messages)
    else:
        text = str(error)
    messages.error(request, text or "The operation could not be completed.")


def _csv_safe(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


@equipment_access
@equipment_permission("view_asset")
def dashboard(request):
    now = timezone.now()
    assets = Asset.objects.filter(archived_at__isnull=True)
    status_counts = dict(assets.values_list("status").annotate(total=Count("id")))
    active_custody = ActiveCustody.objects.select_related("asset", "borrower", "checkout_item__checkout") if request.user.has_perm("equipment.view_checkout") else ActiveCustody.objects.none()
    context = {
        "now": now,
        "asset_total": assets.count(),
        "available_total": status_counts.get(Asset.Status.AVAILABLE, 0),
        "checked_out_total": status_counts.get(Asset.Status.CHECKED_OUT, 0),
        "attention_total": assets.filter(Q(review_required=True) | Q(status__in=(Asset.Status.MAINTENANCE, Asset.Status.OUT_OF_SERVICE, Asset.Status.LOST))).count(),
        "overdue_total": active_custody.filter(due_at__lt=now).count(),
        "recent_assets": assets.select_related("category", "current_party", "current_location").order_by("-updated_at")[:8],
        "due_custody": active_custody.filter(due_at__isnull=False).order_by("due_at")[:8],
        "open_maintenance": MaintenanceWorkOrder.objects.exclude(status__in=(MaintenanceWorkOrder.Status.COMPLETED, MaintenanceWorkOrder.Status.CANCELLED)).select_related("asset").order_by("due_at", "-priority")[:6] if request.user.has_perm("equipment.view_maintenanceworkorder") else MaintenanceWorkOrder.objects.none(),
        "active_rentals": RentalContract.objects.filter(status=RentalContract.Status.ACTIVE).annotate(asset_count=Count("lines"))[:6] if request.user.has_perm("equipment.view_rentalcontract") else RentalContract.objects.none(),
    }
    return render(request, "equipment/dashboard.html", context)


@equipment_access
@equipment_permission("view_asset")
def asset_list(request):
    assets = Asset.objects.filter(archived_at__isnull=True).select_related("category", "current_party", "current_location")
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    status = request.GET.get("status", "").strip()
    ownership = request.GET.get("ownership", "").strip()
    review = request.GET.get("review", "").strip()
    if query:
        assets = assets.filter(
            Q(asset_tag__icontains=query)
            | Q(legacy_tag__icontains=query)
            | Q(name__icontains=query)
            | Q(manufacturer__icontains=query)
            | Q(model_number__icontains=query)
            | Q(identifiers__value__icontains=query)
            | Q(current_party__display_name__icontains=query)
        ).distinct()
    if category:
        assets = assets.filter(category_id=category)
    if status:
        assets = assets.filter(status=status)
    if ownership:
        assets = assets.filter(ownership=ownership)
    if review == "1":
        assets = assets.filter(review_required=True)
    page = Paginator(assets.order_by("asset_tag"), 50).get_page(request.GET.get("page"))
    return render(
        request,
        "equipment/asset_list.html",
        {
            "page": page,
            "query": query,
            "categories": EquipmentCategory.objects.filter(active=True),
            "statuses": Asset.Status,
            "ownerships": Asset.Ownership,
            "filters": {"category": category, "status": status, "ownership": ownership, "review": review},
        },
    )


@equipment_access
@equipment_permission("view_asset")
def asset_detail(request, pk):
    asset = get_object_or_404(
        Asset.objects.select_related("category", "current_party", "home_location", "current_location"), pk=pk
    )
    can_view_audit = request.user.has_perm("equipment.view_equipment_audit")
    events = asset.events.select_related("actor")[:100] if can_view_audit else AssetEvent.objects.none()
    active_custody = ActiveCustody.objects.filter(asset=asset).select_related("borrower", "checkout_item__checkout").first() if request.user.has_perm("equipment.view_checkout") else None
    reservations = asset.reservations.select_related("requestor").order_by("-starts_at")[:10] if request.user.has_perm("equipment.view_reservation") else Reservation.objects.none()
    maintenance = asset.maintenance_work_orders.select_related("vendor").order_by("-created_at")[:10] if request.user.has_perm("equipment.view_maintenanceworkorder") else MaintenanceWorkOrder.objects.none()
    return render(
        request,
        "equipment/asset_detail.html",
        {
            "asset": asset,
            "events": events,
            "active_custody": active_custody,
            "reservations": reservations,
            "maintenance": maintenance,
            "show_costs": request.user.has_perm("equipment.view_asset_costs"),
            "can_view_audit": can_view_audit,
        },
    )


@equipment_access
@require_http_methods(["GET", "POST"])
def asset_create(request):
    if not request.user.has_perm("equipment.manage_equipment"):
        raise PermissionDenied
    form = AssetForm(request.POST or None, include_costs=request.user.has_perm("equipment.view_asset_costs"))
    if request.method == "POST" and form.is_valid():
        payload = form.cleaned_data.copy()
        payload.pop("version", None)
        try:
            asset = create_asset(actor=request.user, **payload)
        except (ValidationError, PermissionDenied) as error:
            _service_error(request, error)
        else:
            messages.success(request, f"{asset.asset_tag} was created.")
            return redirect("equipment_asset_detail", pk=asset.pk)
    return render(request, "equipment/form.html", {"form": form, "title": "Add equipment", "submit_label": "Create asset"})


@equipment_access
@require_http_methods(["GET", "POST"])
def asset_edit(request, pk):
    if not request.user.has_perm("equipment.manage_equipment"):
        raise PermissionDenied
    asset = get_object_or_404(Asset, pk=pk)
    form = AssetForm(request.POST or None, instance=asset, include_costs=request.user.has_perm("equipment.view_asset_costs"))
    if request.method == "POST" and form.is_valid():
        payload = form.cleaned_data.copy()
        version = payload.pop("version")
        try:
            update_asset(actor=request.user, asset_id=asset.pk, expected_updated_at=version, **payload)
        except (ValidationError, PermissionDenied) as error:
            _service_error(request, error)
        else:
            messages.success(request, f"{asset.asset_tag} was updated.")
            return redirect("equipment_asset_detail", pk=asset.pk)
    return render(request, "equipment/form.html", {"form": form, "title": f"Edit {asset.asset_tag}", "submit_label": "Save changes", "asset": asset})


@equipment_access
@require_http_methods(["GET", "POST"])
def checkout_create(request):
    if not request.user.has_perm("equipment.checkout_asset"):
        raise PermissionDenied
    initial = {}
    if request.GET.get("asset"):
        initial["assets"] = [request.GET["asset"]]
    if request.GET.get("reservation"):
        reservation = get_object_or_404(Reservation.objects.prefetch_related("assets"), pk=request.GET["reservation"], status=Reservation.Status.APPROVED)
        initial.update({"reservation": reservation, "borrower": reservation.requestor, "assets": list(reservation.assets.all())})
    form = CheckoutForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            checkout = checkout_assets(
                actor=request.user,
                borrower_id=form.cleaned_data["borrower"].pk,
                asset_ids=[asset.pk for asset in form.cleaned_data["assets"]],
                due_at=form.cleaned_data["due_at"],
                destination_id=form.cleaned_data["destination"].pk if form.cleaned_data["destination"] else None,
                purpose=form.cleaned_data["purpose"],
                reservation_id=form.cleaned_data["reservation"].pk if form.cleaned_data["reservation"] else None,
            )
        except (ValidationError, PermissionDenied) as error:
            _service_error(request, error)
        else:
            messages.success(request, f"Checkout {checkout.checkout_number} completed.")
            return redirect("equipment_checkout_detail", pk=checkout.pk)
    return render(request, "equipment/form.html", {"form": form, "title": "Check out equipment", "submit_label": "Complete checkout", "form_intro": "Custody changes are recorded permanently. Verify the borrower and due date before submitting."})


@equipment_access
@equipment_permission("view_checkout")
def checkout_list(request):
    checkouts = Checkout.objects.select_related("borrower", "destination").annotate(item_count=Count("items")).order_by("-checked_out_at")
    return render(request, "equipment/checkout_list.html", {"page": Paginator(checkouts, 50).get_page(request.GET.get("page"))})


@equipment_access
@equipment_permission("view_checkout")
def checkout_detail(request, pk):
    checkout = get_object_or_404(Checkout.objects.select_related("borrower", "destination", "created_by"), pk=pk)
    items = checkout.items.select_related("asset").order_by("asset_tag_snapshot")
    return render(request, "equipment/checkout_detail.html", {"checkout": checkout, "items": items})


@equipment_access
@require_http_methods(["GET", "POST"])
def asset_return(request, pk):
    if not request.user.has_perm("equipment.return_asset"):
        raise PermissionDenied
    asset = get_object_or_404(Asset.objects.select_related("current_party"), pk=pk)
    if not ActiveCustody.objects.filter(asset=asset).exists():
        messages.error(request, "This asset is not currently checked out.")
        return redirect("equipment_asset_detail", pk=asset.pk)
    form = ReturnForm(request.POST or None, initial={"condition": asset.condition})
    if request.method == "POST" and form.is_valid():
        try:
            return_asset(
                actor=request.user,
                asset_id=asset.pk,
                condition=form.cleaned_data["condition"],
                notes=form.cleaned_data["notes"],
                damage_reported=form.cleaned_data["damage_reported"],
                return_location_id=form.cleaned_data["return_location"].pk if form.cleaned_data["return_location"] else None,
            )
        except (ValidationError, PermissionDenied) as error:
            _service_error(request, error)
        else:
            messages.success(request, f"{asset.asset_tag} was returned and its custody history preserved.")
            return redirect("equipment_asset_detail", pk=asset.pk)
    return render(request, "equipment/form.html", {"form": form, "title": f"Return {asset.asset_tag}", "submit_label": "Complete return", "asset": asset})


@equipment_access
@equipment_permission("view_reservation")
@require_http_methods(["GET", "POST"])
def reservation_list(request):
    form = ReservationForm(request.POST or None, user=request.user)
    if request.method == "POST":
        if not request.user.has_perm("equipment.add_reservation"):
            raise PermissionDenied
        if form.is_valid():
            try:
                reservation = create_reservation(
                    actor=request.user,
                    requestor_id=form.cleaned_data["requestor"].pk,
                    asset_ids=[asset.pk for asset in form.cleaned_data["assets"]],
                    starts_at=form.cleaned_data["starts_at"],
                    ends_at=form.cleaned_data["ends_at"],
                    purpose=form.cleaned_data["purpose"],
                    destination_id=form.cleaned_data["destination"].pk if form.cleaned_data["destination"] else None,
                )
            except (ValidationError, PermissionDenied) as error:
                _service_error(request, error)
            else:
                messages.success(request, f"Reservation {reservation.reservation_number} created.")
                return redirect("equipment_reservations")
    reservations = Reservation.objects.select_related("requestor", "destination").prefetch_related("assets").order_by("-starts_at")
    if not request.user.has_perm("equipment.manage_reservations") and not request.user.is_superuser:
        reservations = reservations.filter(requestor__user=request.user)
    return render(request, "equipment/reservations.html", {"form": form, "reservations": reservations[:100], "status_form": ReservationStatusForm()})


@equipment_access
@require_POST
def reservation_status(request, pk):
    form = ReservationStatusForm(request.POST)
    if form.is_valid():
        try:
            set_reservation_status(actor=request.user, reservation_id=pk, status=form.cleaned_data["status"])
        except (ValidationError, PermissionDenied) as error:
            _service_error(request, error)
        else:
            messages.success(request, "Reservation status updated.")
    else:
        messages.error(request, "Choose a valid reservation action.")
    return redirect("equipment_reservations")


@equipment_access
@equipment_permission("view_maintenanceworkorder")
@require_http_methods(["GET", "POST"])
def maintenance_list(request):
    form = MaintenanceForm(request.POST or None)
    if request.method == "POST":
        if not request.user.has_perm("equipment.manage_maintenance"):
            raise PermissionDenied
        if form.is_valid():
            try:
                order = open_maintenance(
                    actor=request.user,
                    asset_id=form.cleaned_data["asset"].pk,
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data["description"],
                    priority=form.cleaned_data["priority"],
                    due_at=form.cleaned_data["due_at"],
                )
            except (ValidationError, PermissionDenied) as error:
                _service_error(request, error)
            else:
                messages.success(request, f"Work order {order.work_order_number} opened.")
                return redirect("equipment_maintenance")
    orders = MaintenanceWorkOrder.objects.select_related("asset", "vendor").order_by("status", "due_at", "-created_at")[:150]
    return render(request, "equipment/maintenance.html", {"form": form, "orders": orders})


@equipment_access
@require_http_methods(["GET", "POST"])
def maintenance_complete(request, pk):
    if not request.user.has_perm("equipment.manage_maintenance"):
        raise PermissionDenied
    order = get_object_or_404(MaintenanceWorkOrder.objects.select_related("asset"), pk=pk)
    form = MaintenanceCompleteForm(request.POST or None, include_costs=request.user.has_perm("equipment.view_asset_costs"))
    if request.method == "POST" and form.is_valid():
        try:
            complete_maintenance(
                actor=request.user,
                work_order_id=order.pk,
                work_performed=form.cleaned_data["work_performed"],
                cost=form.cleaned_data.get("cost"),
                condition=form.cleaned_data["condition"],
            )
        except (ValidationError, PermissionDenied) as error:
            _service_error(request, error)
        else:
            messages.success(request, f"Work order {order.work_order_number} completed.")
            return redirect("equipment_maintenance")
    return render(request, "equipment/form.html", {"form": form, "title": f"Complete {order.work_order_number}", "submit_label": "Complete work order", "order": order})


@equipment_access
@equipment_permission("view_rentalcontract")
def rental_list(request):
    contracts = RentalContract.objects.select_related("vendor").prefetch_related("lines__asset").annotate(asset_count=Count("lines")).order_by("-starts_on")
    return render(request, "equipment/rentals.html", {"contracts": contracts, "show_costs": request.user.has_perm("equipment.view_asset_costs")})


@equipment_access
def history(request):
    if not request.user.has_perm("equipment.view_equipment_audit"):
        raise PermissionDenied
    events = AssetEvent.objects.select_related("asset", "actor")
    query = request.GET.get("q", "").strip()
    if query:
        events = events.filter(Q(asset__asset_tag__icontains=query) | Q(asset__legacy_tag__icontains=query) | Q(summary__icontains=query) | Q(actor__username__icontains=query))
    return render(request, "equipment/history.html", {"page": Paginator(events, 100).get_page(request.GET.get("page")), "query": query})


@equipment_access
@equipment_permission("view_equipment_audit")
def reports(request):
    assets = Asset.objects.filter(archived_at__isnull=True)
    category_rows = assets.values("category__name").annotate(total=Count("id"), quantity=Sum("quantity")).order_by("category__name")
    status_rows = assets.values("status").annotate(total=Count("id"), quantity=Sum("quantity")).order_by("status")
    overdue = ActiveCustody.objects.filter(due_at__lt=timezone.now()).select_related("asset", "borrower")
    review_assets = assets.filter(review_required=True).select_related("category")
    return render(request, "equipment/reports.html", {"category_rows": category_rows, "status_rows": status_rows, "overdue": overdue, "review_assets": review_assets})


@equipment_access
def export_assets(request):
    if not request.user.has_perm("equipment.export_equipment"):
        raise PermissionDenied
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="equipment-register.csv"'
    writer = csv.writer(response)
    header = ["Asset Tag", "Legacy Tag", "Source", "Category", "Name", "Manufacturer", "Model", "Ownership", "Status", "Condition", "Quantity", "Assignee", "Location", "Review Required"]
    if request.user.has_perm("equipment.view_asset_costs"):
        header.append("Purchase Cost")
    writer.writerow(header)
    for asset in Asset.objects.select_related("category", "current_party", "current_location").order_by("asset_tag"):
        row = [asset.asset_tag, asset.legacy_tag, asset.source_namespace, asset.category.name, asset.name, asset.manufacturer, asset.model_number, asset.get_ownership_display(), asset.get_status_display(), asset.get_condition_display(), asset.quantity, asset.current_party.display_name if asset.current_party else "", asset.current_location.name if asset.current_location else "", "Yes" if asset.review_required else "No"]
        if request.user.has_perm("equipment.view_asset_costs"):
            row.append(asset.purchase_cost or "")
        writer.writerow([_csv_safe(value) for value in row])
    return response


@equipment_access
@require_http_methods(["GET", "POST"])
def import_center(request):
    if not request.user.has_perm("equipment.import_equipment"):
        raise PermissionDenied
    form = ImportUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        workbook = form.cleaned_data["workbook"]
        try:
            batch, created = import_workbook(workbook, source_name=workbook.name, actor=request.user)
        except Exception as error:
            messages.error(request, f"Import failed and was rolled back: {error}")
        else:
            if created:
                messages.success(request, f"Import completed: {batch.summary.get('assets_created', 0)} assets; {batch.review_rows} rows require review.")
            else:
                messages.info(request, "That exact workbook was already imported; no duplicate records were created.")
            return redirect("equipment_import_batch", pk=batch.pk)
    return render(request, "equipment/import_center.html", {"form": form, "batches": EquipmentImportBatch.objects.all()[:20]})


@equipment_access
def import_batch(request, pk):
    if not request.user.has_perm("equipment.import_equipment"):
        raise PermissionDenied
    batch = get_object_or_404(EquipmentImportBatch, pk=pk)
    rows = batch.rows.select_related("asset")
    state = request.GET.get("status", "")
    if state:
        rows = rows.filter(status=state)
    return render(request, "equipment/import_batch.html", {"batch": batch, "page": Paginator(rows, 100).get_page(request.GET.get("page")), "states": EquipmentImportBatch.Status, "row_statuses": batch.rows.model.Status, "filter_status": state})


@equipment_access
@equipment_permission("view_asset")
def scan_lookup(request):
    query = request.GET.get("q", "").strip()
    asset = None
    if query:
        normalized = " ".join(query.upper().split())
        matches = list(Asset.objects.filter(Q(asset_tag__iexact=query) | Q(legacy_tag__iexact=query) | Q(identifiers__normalized_value=normalized)).distinct()[:2])
        if len(matches) == 1:
            return redirect("equipment_asset_detail", pk=matches[0].pk)
        if len(matches) > 1:
            messages.error(request, "That identifier matches multiple assets. Search the equipment register and refine by tag or serial type.")
        else:
            messages.error(request, "No equipment matched that tag or identifier.")
    return render(request, "equipment/scan.html", {"query": query})


@equipment_access
@equipment_permission("view_asset")
def scan_api(request):
    query = request.GET.get("q", "").strip()
    if not query:
        return JsonResponse({"found": False}, status=400)
    normalized = " ".join(query.upper().split())
    matches = list(Asset.objects.filter(Q(asset_tag__iexact=query) | Q(legacy_tag__iexact=query) | Q(identifiers__normalized_value=normalized)).distinct()[:2])
    if not matches:
        return JsonResponse({"found": False}, status=404)
    if len(matches) > 1:
        return JsonResponse({"found": False, "ambiguous": True}, status=409)
    asset = matches[0]
    return JsonResponse({"found": True, "asset_tag": asset.asset_tag, "name": asset.name, "status": asset.get_status_display(), "url": reverse("equipment_asset_detail", kwargs={"pk": asset.pk})})


@equipment_access
@equipment_permission("view_asset")
def asset_label(request, pk):
    if not request.user.has_perm("equipment.print_asset_labels"):
        raise PermissionDenied
    asset = get_object_or_404(Asset.objects.select_related("category"), pk=pk)
    url = request.build_absolute_uri(reverse("equipment_asset_detail", kwargs={"pk": asset.pk}))
    image = qrcode.make(url)
    stream = BytesIO()
    image.save(stream, format="PNG")
    qr_data = base64.b64encode(stream.getvalue()).decode("ascii")
    return render(request, "equipment/label.html", {"asset": asset, "qr_data": qr_data})
