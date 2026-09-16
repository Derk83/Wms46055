import base64
import csv
from datetime import datetime, time, timedelta
from decimal import Decimal
from functools import wraps
from io import BytesIO
from urllib.parse import urlencode

import qrcode
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe

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
    RentalAsset,
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


@require_safe
@never_cache
def pwa_manifest(request):
    manifest = {
        "id": "/",
        "name": "RPL Equipment",
        "short_name": "RPL Equipment",
        "description": "RPL equipment custody, reservations, maintenance, and asset control.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "display_override": ["window-controls-overlay", "standalone"],
        "background_color": "#0b0c0e",
        "theme_color": "#0b0c0e",
        "categories": ["business", "productivity"],
        "icons": [
            {"src": "/static/equipment/icons/equipment-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/equipment/icons/equipment-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/static/equipment/icons/equipment-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Equipment register", "short_name": "Equipment", "url": "/assets/", "icons": [{"src": "/static/equipment/icons/equipment-192.png", "sizes": "192x192"}]},
            {"name": "Scan equipment", "short_name": "Scan", "url": "/scan/", "icons": [{"src": "/static/equipment/icons/equipment-192.png", "sizes": "192x192"}]},
        ],
    }
    response = JsonResponse(manifest, content_type="application/manifest+json")
    response["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    return response


@require_safe
@never_cache
def service_worker(request):
    response = render(request, "equipment/service_worker.js", content_type="application/javascript")
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Service-Worker-Allowed"] = "/"
    return response


@require_safe
def offline(request):
    return render(request, "equipment/offline.html")


def _filter_assets(queryset, *, query="", category="", status="", ownership="", review=""):
    if query:
        queryset = queryset.filter(
            Q(asset_tag__icontains=query)
            | Q(legacy_tag__icontains=query)
            | Q(name__icontains=query)
            | Q(manufacturer__icontains=query)
            | Q(model_number__icontains=query)
            | Q(identifiers__value__icontains=query)
            | Q(current_party__display_name__icontains=query)
            | Q(current_location__name__icontains=query)
            | Q(current_location__code__icontains=query)
        ).distinct()
    if category:
        try:
            category_id = int(category)
        except (TypeError, ValueError):
            return queryset.none()
        queryset = queryset.filter(category_id=category_id)
    if status:
        valid_statuses = {choice for choice, _label in Asset.Status.choices}
        queryset = queryset.filter(status=status) if status in valid_statuses else queryset.none()
    if ownership:
        valid_ownerships = {choice for choice, _label in Asset.Ownership.choices}
        queryset = queryset.filter(ownership=ownership) if ownership in valid_ownerships else queryset.none()
    if review == "1":
        queryset = queryset.filter(review_required=True)
    return queryset


@equipment_access
@equipment_permission("view_asset")
def dashboard(request):
    now = timezone.now()
    today = timezone.localdate()
    rental_window = today + timedelta(days=14)
    assets = Asset.objects.filter(archived_at__isnull=True)
    status_counts = dict(assets.values_list("status").annotate(total=Count("id")))

    can_view_custody = request.user.has_perm("equipment.view_checkout")
    can_view_reservations = request.user.has_perm("equipment.view_reservation")
    can_view_maintenance = request.user.has_perm("equipment.view_maintenanceworkorder")
    can_view_rentals = request.user.has_perm("equipment.view_rentalcontract")
    can_view_audit = request.user.has_perm("equipment.view_equipment_audit")

    custody = (
        ActiveCustody.objects.select_related(
            "asset", "asset__category", "borrower", "checkout_item__checkout", "checkout_item__checkout__destination"
        )
        if can_view_custody
        else ActiveCustody.objects.none()
    )
    overdue_custody = custody.filter(due_at__lt=now).order_by("due_at")
    open_maintenance = (
        MaintenanceWorkOrder.objects.exclude(
            status__in=(MaintenanceWorkOrder.Status.COMPLETED, MaintenanceWorkOrder.Status.CANCELLED)
        ).select_related("asset", "vendor").order_by(F("due_at").asc(nulls_last=True), "-priority")
        if can_view_maintenance
        else MaintenanceWorkOrder.objects.none()
    )
    upcoming_reservations = (
        Reservation.objects.filter(
            ends_at__gte=now,
            status__in=(Reservation.Status.PENDING, Reservation.Status.APPROVED),
        ).select_related("requestor", "destination").prefetch_related("assets").order_by("starts_at")
        if can_view_reservations
        else Reservation.objects.none()
    )
    if can_view_reservations and not request.user.has_perm("equipment.manage_reservations") and not request.user.is_superuser:
        upcoming_reservations = upcoming_reservations.filter(requestor__user=request.user)
    active_rentals = (
        RentalContract.objects.filter(status=RentalContract.Status.ACTIVE)
        .select_related("vendor")
        .annotate(asset_count=Count("lines", filter=Q(lines__returned_on__isnull=True)))
        .order_by(F("ends_on").asc(nulls_last=True), "contract_number")
        if can_view_rentals
        else RentalContract.objects.none()
    )
    rental_obligations = (
        RentalAsset.objects.filter(contract__status=RentalContract.Status.ACTIVE, returned_on__isnull=True)
        .filter(
            Q(expected_return_on__lte=rental_window)
            | Q(expected_return_on__isnull=True, contract__ends_on__lte=rental_window)
        )
        .select_related("asset", "contract", "contract__vendor")
        if can_view_rentals
        else RentalAsset.objects.none()
    )

    lost_assets = assets.filter(status=Asset.Status.LOST).select_related("category", "current_party")
    review_assets_all = assets.filter(review_required=True).select_related("category", "current_party")
    review_assets = review_assets_all.exclude(status=Asset.Status.LOST)
    maintenance_attention = open_maintenance.filter(
        Q(priority=MaintenanceWorkOrder.Priority.CRITICAL) | Q(due_at__lt=now)
    )
    attention_items = []
    if can_view_custody:
        for custody_item in overdue_custody[:5]:
            attention_items.append(
                {
                    "priority": 0,
                    "kind": "Overdue",
                    "asset": custody_item.asset,
                    "issue": "Return is past due",
                    "owner": custody_item.borrower.display_name,
                    "due": custody_item.due_at,
                    "url": reverse("equipment_asset_detail", args=(custody_item.asset_id,)),
                    "action": "Review custody",
                }
            )
    for asset in lost_assets[:5]:
        attention_items.append(
            {
                "priority": 0,
                "kind": "Lost",
                "asset": asset,
                "issue": "Equipment is recorded as lost",
                "owner": asset.current_party.display_name if asset.current_party else "Unassigned",
                "due": None,
                "url": reverse("equipment_asset_detail", args=(asset.pk,)),
                "action": "Investigate asset",
            }
        )
    for asset in review_assets[:5]:
        attention_items.append(
            {
                "priority": 1,
                "kind": "Review",
                "asset": asset,
                "issue": asset.review_notes or "Imported record requires reconciliation",
                "owner": asset.current_party.display_name if asset.current_party else "Unassigned",
                "due": None,
                "url": reverse("equipment_asset_detail", args=(asset.pk,)),
                "action": "Resolve record",
            }
        )
    if can_view_maintenance:
        for order in maintenance_attention[:5]:
            attention_items.append(
                {
                    "priority": 0 if order.priority == MaintenanceWorkOrder.Priority.CRITICAL else 1,
                    "kind": "Service",
                    "asset": order.asset,
                    "issue": f"{order.work_order_number} · {order.title}",
                    "owner": order.vendor.name if order.vendor else "Internal service",
                    "due": order.due_at,
                    "url": reverse("equipment_asset_detail", args=(order.asset_id,)),
                    "action": "Open work order",
                }
            )
    if can_view_rentals:
        for rental_line in rental_obligations.order_by(
            Coalesce("expected_return_on", "contract__ends_on").asc(nulls_last=True),
            "contract__contract_number",
            "asset__asset_tag",
        )[:5]:
            due_on = rental_line.expected_return_on or rental_line.contract.ends_on
            due_at = timezone.make_aware(datetime.combine(due_on, time.min))
            attention_items.append(
                {
                    "priority": 1,
                    "kind": "Rental",
                    "asset": rental_line.asset,
                    "issue": f"Rental return due under {rental_line.contract.contract_number}",
                    "owner": rental_line.contract.vendor.name,
                    "due": due_at,
                    "url": reverse("equipment_rentals"),
                    "action": "Review rental",
                }
            )
    attention_items.sort(key=lambda item: (item["priority"], str(item.get("due") or "9999-12-31")))
    attention_total = (
        overdue_custody.count()
        + lost_assets.count()
        + review_assets.count()
        + maintenance_attention.count()
        + rental_obligations.count()
    )

    category_capacity = list(
        EquipmentCategory.objects.filter(active=True, assets__archived_at__isnull=True)
        .annotate(
            total=Count("assets"),
            available=Count("assets", filter=Q(assets__status=Asset.Status.AVAILABLE)),
            checked_out=Count("assets", filter=Q(assets__status=Asset.Status.CHECKED_OUT)),
            in_transit=Count("assets", filter=Q(assets__status=Asset.Status.IN_TRANSIT)),
            reserved=Count("assets", filter=Q(assets__status=Asset.Status.RESERVED)),
            service=Count("assets", filter=Q(assets__status__in=(Asset.Status.MAINTENANCE, Asset.Status.OUT_OF_SERVICE))),
        )
        .order_by("name")
    )
    for category_item in category_capacity:
        category_item.operational_pool = (
            category_item.available + category_item.reserved + category_item.checked_out + category_item.in_transit
        )
        category_item.utilized = category_item.checked_out + category_item.in_transit
        category_item.utilization_percent = (
            round(category_item.utilized * 100 / category_item.operational_pool)
            if category_item.operational_pool
            else 0
        )

    utilized_total = status_counts.get(Asset.Status.CHECKED_OUT, 0) + status_counts.get(Asset.Status.IN_TRANSIT, 0)
    operational_pool_total = (
        status_counts.get(Asset.Status.AVAILABLE, 0)
        + status_counts.get(Asset.Status.RESERVED, 0)
        + utilized_total
    )
    utilization_percent = round(utilized_total * 100 / operational_pool_total) if operational_pool_total else 0

    can_view_rental_costs = can_view_rentals and request.user.has_perm("equipment.view_asset_costs")
    rental_monthly_cost = Decimal("0.00")
    rental_uncosted_asset_total = 0
    rental_cost_by_vendor = []
    if can_view_rental_costs:
        cost_by_vendor = {}
        rate_multipliers = {
            RentalAsset.RatePeriod.DAY: Decimal(365) / Decimal(12),
            RentalAsset.RatePeriod.WEEK: Decimal(52) / Decimal(12),
            RentalAsset.RatePeriod.FOUR_WEEK: Decimal(13) / Decimal(12),
            RentalAsset.RatePeriod.MONTH: Decimal(1),
        }
        cost_lines = RentalAsset.objects.filter(
            returned_on__isnull=True,
            contract__status=RentalContract.Status.ACTIVE,
        ).select_related("contract__vendor")
        for rental_line in cost_lines:
            if rental_line.rate_amount is None:
                rental_uncosted_asset_total += 1
                continue
            monthly_cost = rental_line.rate_amount * rate_multipliers[rental_line.rate_period]
            rental_monthly_cost += monthly_cost
            vendor_name = rental_line.contract.vendor.name
            cost_by_vendor[vendor_name] = cost_by_vendor.get(vendor_name, Decimal("0.00")) + monthly_cost
        rental_monthly_cost = rental_monthly_cost.quantize(Decimal("0.01"))
        rental_cost_by_vendor = [
            {"vendor": vendor, "monthly_cost": amount.quantize(Decimal("0.01"))}
            for vendor, amount in sorted(cost_by_vendor.items(), key=lambda item: item[1], reverse=True)[:5]
        ]

    context = {
        "now": now,
        "asset_total": assets.count(),
        "available_total": status_counts.get(Asset.Status.AVAILABLE, 0),
        "checked_out_total": status_counts.get(Asset.Status.CHECKED_OUT, 0),
        "reserved_total": status_counts.get(Asset.Status.RESERVED, 0),
        "service_total": status_counts.get(Asset.Status.MAINTENANCE, 0) + status_counts.get(Asset.Status.OUT_OF_SERVICE, 0),
        "review_total": review_assets_all.count(),
        "overdue_total": overdue_custody.count(),
        "reservation_total": upcoming_reservations.count(),
        "rental_window": rental_window,
        "rental_due_total": rental_obligations.count(),
        "attention_items": attention_items[:8],
        "attention_total": attention_total,
        "active_custody": custody.order_by("-created_at")[:15],
        "upcoming_reservations": upcoming_reservations[:10],
        "register_assets": assets.select_related("category", "current_party", "current_location").order_by("-updated_at")[:10],
        "open_maintenance": open_maintenance[:8],
        "active_rentals": active_rentals[:8],
        "category_capacity": category_capacity,
        "utilized_total": utilized_total,
        "operational_pool_total": operational_pool_total,
        "utilization_percent": utilization_percent,
        "can_view_rental_costs": can_view_rental_costs,
        "rental_monthly_cost": rental_monthly_cost,
        "rental_uncosted_asset_total": rental_uncosted_asset_total,
        "rental_cost_by_vendor": rental_cost_by_vendor,
        "asset_statuses": Asset.Status,
        "active_categories": EquipmentCategory.objects.filter(active=True),
        "recent_events": AssetEvent.objects.select_related("asset", "actor")[:12] if can_view_audit else AssetEvent.objects.none(),
        "can_view_custody": can_view_custody,
        "can_view_reservations": can_view_reservations,
        "can_view_maintenance": can_view_maintenance,
        "can_view_rentals": can_view_rentals,
        "can_view_audit": can_view_audit,
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
    assets = _filter_assets(
        assets,
        query=query,
        category=category,
        status=status,
        ownership=ownership,
        review=review,
    )
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


@require_safe
@equipment_access
@equipment_permission("view_asset")
def asset_search_api(request):
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    status = request.GET.get("status", "").strip()
    if len(query) < 2 and not category and not status:
        return JsonResponse({"results": [], "truncated": False, "minimum_query": 2})

    assets = Asset.objects.filter(archived_at__isnull=True).select_related(
        "category", "current_party", "current_location"
    )
    assets = _filter_assets(assets, query=query, category=category, status=status).order_by("asset_tag")
    result_assets = list(assets[:21])
    results = [
        {
            "asset_tag": asset.asset_tag,
            "name": asset.name,
            "category": asset.category.name,
            "status": asset.get_status_display(),
            "status_code": asset.status,
            "condition": asset.get_condition_display(),
            "custodian": asset.current_party.display_name if asset.current_party else "Unassigned",
            "location": asset.current_location.name if asset.current_location else "Not set",
            "updated": timezone.localtime(asset.updated_at).strftime("%b %-d, %Y"),
            "url": reverse("equipment_asset_detail", args=(asset.pk,)),
        }
        for asset in result_assets[:20]
    ]
    return JsonResponse(
        {
            "results": results,
            "truncated": len(result_assets) > 20,
            "register_url": f'{reverse("equipment_asset_list")}?{urlencode({"q": query, "category": category, "status": status})}',
        }
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
