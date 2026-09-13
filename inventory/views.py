import csv
import io
import json
import openpyxl
import re
import weasyprint
from datetime import datetime
from functools import wraps

from django.utils import timezone
from django.conf import settings as django_settings
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import Group, Permission, User
from django.contrib.auth.forms import UserCreationForm, UserChangeForm, PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from django.db import IntegrityError, models, transaction
from django.db.models import Count, Q, Sum
from django import forms
from django.forms import formset_factory, inlineformset_factory
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_safe


def _authorized_or_redirect(test_func, login_url=None):
    def decorator(view_func):
        def wrapper(request, *args, **kwargs):
            if request.user.is_authenticated and not test_func(request.user):
                messages.error(request, "You do not have permission to do that.")
                fallback = "dashboard" if getattr(request, "is_request_portal", False) else "inventory_list"
                return redirect(request.META.get("HTTP_REFERER") or fallback)
            return user_passes_test(test_func, login_url=login_url or "login")(
                view_func
            )(request, *args, **kwargs)
        return wrapper
    return decorator


superuser_required = _authorized_or_redirect(lambda u: u.is_superuser)
staff_or_superuser_required = _authorized_or_redirect(lambda u: u.is_staff or u.is_superuser)
approval_reviewer_required = _authorized_or_redirect(
    lambda u: u.is_superuser or u.has_perm("inventory.change_approvalrequest")
)
lead_or_higher = _authorized_or_redirect(
    lambda u: u.is_superuser or u.has_perm("inventory.manage_storage_locations")
)


def user_has_any_perm(user, *perm_codes):
    """Return True when a user has any listed permission via user or group."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return any(user.has_perm(perm_code) for perm_code in perm_codes)


def any_perm_required(*perm_codes):
    return _authorized_or_redirect(lambda u: user_has_any_perm(u, *perm_codes))


def all_perms_required(*perm_codes):
    return _authorized_or_redirect(
        lambda u: u.is_authenticated and (u.is_superuser or all(u.has_perm(code) for code in perm_codes))
    )


def request_portal_access_required(view_func):
    """Require the dedicated portal permission only on the request hostname."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if getattr(request, "is_request_portal", False) and not request.user.has_perm(
            "inventory.access_material_request_portal"
        ):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper


def portal_inventory_access_required(view_func):
    """Keep WMS inventory behavior unchanged while enforcing read access on the portal."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if getattr(request, "is_request_portal", False) and not request.user.has_perm(
            "inventory.view_inventoryitem"
        ):
            messages.error(request, "You do not have permission to view inventory.")
            return redirect("dashboard")
        return view_func(request, *args, **kwargs)

    return wrapper


def settings_access_required(view_func):
    return any_perm_required(
        "inventory.manage_users",
        "inventory.manage_groups",
        "inventory.manage_group_permissions",
    )(view_func)
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import get_template, render_to_string
from django.urls import reverse


@require_safe
@never_cache
def pwa_manifest(request):
    """Return an install manifest tailored to the hostname being installed."""
    is_portal = getattr(request, "is_request_portal", False)
    name = "Black Box Material Requests" if is_portal else "Black Box Warehouse"
    short_name = "BBX Requests" if is_portal else "BBX WMS"
    shortcuts = [
        {
            "name": "New Material Request",
            "short_name": "New Request",
            "url": "/material-requests/new/",
            "icons": [{"src": "/static/inventory/icons/icon-192.png", "sizes": "192x192"}],
        },
        {
            "name": "Inventory",
            "short_name": "Inventory",
            "url": "/inventory/",
            "icons": [{"src": "/static/inventory/icons/icon-192.png", "sizes": "192x192"}],
        },
    ]
    manifest = {
        "id": "/",
        "name": name,
        "short_name": short_name,
        "description": "Black Box warehouse inventory and material request operations.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "display_override": ["window-controls-overlay", "standalone"],
        "background_color": "#0b0c0e",
        "theme_color": "#0b0c0e",
        "categories": ["business", "productivity"],
        "icons": [
            {"src": "/static/inventory/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/inventory/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
        "shortcuts": shortcuts,
    }
    response = JsonResponse(manifest, content_type="application/manifest+json")
    response["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    return response


@require_safe
@never_cache
def pwa_service_worker(request):
    """Root-scoped worker: cache only the static shell; never intercept writes."""
    response = render(request, "inventory/service_worker.js", content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    return response


@require_safe
def pwa_offline(request):
    response = render(request, "inventory/offline.html")
    response["Cache-Control"] = "no-store"
    return response


def _require_push_access(request):
    if getattr(request, "is_request_portal", False):
        if not request.user.has_perm("inventory.access_material_request_portal"):
            raise PermissionDenied
        return
    if not getattr(request, "is_wms_host", False):
        raise Http404
    if not request.user.has_perm("inventory.view_all_materialrequests"):
        raise PermissionDenied


@login_required
@require_GET
@never_cache
def push_config(request):
    from .models import PushSubscription
    from .push import push_is_configured

    _require_push_access(request)
    configured = push_is_configured()
    return JsonResponse({
        "enabled": configured,
        "publicKey": django_settings.WEBPUSH_VAPID_PUBLIC_KEY if configured else "",
        "subscribed": PushSubscription.objects.filter(user=request.user, enabled=True).exists(),
    })


def _push_json(request):
    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > 10_000:
            raise ValueError
        payload = json.loads(request.body or b"{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


@login_required
@require_POST
def push_subscribe(request):
    from .models import PushSubscription
    from .push import push_endpoint_is_allowed, push_is_configured

    _require_push_access(request)
    if not push_is_configured():
        return JsonResponse({"error": "Push notifications are not configured."}, status=503)
    payload = _push_json(request)
    keys = payload.get("keys") if payload else None
    endpoint = payload.get("endpoint") if payload else None
    p256dh = keys.get("p256dh") if isinstance(keys, dict) else None
    auth = keys.get("auth") if isinstance(keys, dict) else None
    if not all(isinstance(value, str) and value for value in (endpoint, p256dh, auth)):
        return JsonResponse({"error": "Invalid push subscription."}, status=400)
    if (
        len(endpoint) > 2048 or len(p256dh) > 512 or len(auth) > 512
        or not push_endpoint_is_allowed(endpoint)
    ):
        return JsonResponse({"error": "Invalid push subscription."}, status=400)
    subscription, created = PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "user": request.user,
            "p256dh": p256dh,
            "auth": auth,
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:512],
            "session_key": request.session.session_key or "",
            "audience": (
                PushSubscription.Audience.REQUEST_PORTAL
                if getattr(request, "is_request_portal", False)
                else PushSubscription.Audience.WMS
            ),
            "enabled": True,
            "failure_count": 0,
            "last_error": "",
        },
    )
    if subscription.audience == PushSubscription.Audience.REQUEST_PORTAL:
        from .models import MaterialRequest, MaterialRequestEvent, PickTicket
        from .push import queue_material_request_push

        ready_requests = MaterialRequest.objects.filter(
            creator=request.user,
            pick_ticket__status=PickTicket.Status.RECEIVED,
        ).prefetch_related("events")
        for material_request in ready_requests:
            latest_ready_event = material_request.events.filter(
                event_type=MaterialRequestEvent.EventType.STATUS_CHANGED,
                new_status=PickTicket.Status.RECEIVED,
            ).order_by("-pk").first()
            if latest_ready_event:
                queue_material_request_push(latest_ready_event)
    return JsonResponse({"subscribed": True, "id": subscription.pk}, status=201 if created else 200)


@login_required
@require_POST
def push_unsubscribe(request):
    from .models import PushSubscription

    _require_push_access(request)
    payload = _push_json(request)
    endpoint = payload.get("endpoint") if payload else None
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > 2048:
        return JsonResponse({"error": "Invalid push subscription."}, status=400)
    PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
    return JsonResponse({"subscribed": False})


@csrf_exempt
@login_required
@require_POST
def material_request_confirm_ready(request, pk):
    """Accept a signed push action from the authenticated request owner."""
    from .models import MaterialRequest, MaterialRequestEvent, PickTicket
    from .services import confirm_delivery_acceptance

    if not request.is_request_portal:
        return JsonResponse({"error": "Not authorized."}, status=403)
    if not request.user.has_perm("inventory.access_material_request_portal") or not request.user.has_perm(
        "inventory.view_materialrequest"
    ):
        return JsonResponse({"error": "Not authorized."}, status=403)

    payload = _push_json(request)
    token = payload.get("token") if payload else None
    if not isinstance(token, str) or not token:
        return JsonResponse({"error": "Invalid confirmation."}, status=400)
    try:
        claims = signing.loads(
            token, salt="material-request-delivery-readiness", max_age=7 * 24 * 60 * 60
        )
    except signing.BadSignature:
        return JsonResponse({"error": "Invalid or expired confirmation."}, status=400)

    if claims.get("request_id") != pk or claims.get("user_id") != request.user.pk:
        return JsonResponse({"error": "Not authorized."}, status=403)
    event = MaterialRequestEvent.objects.filter(
        pk=claims.get("event_id"),
        material_request_id=pk,
        event_type=MaterialRequestEvent.EventType.STATUS_CHANGED,
        new_status=PickTicket.Status.RECEIVED,
    ).first()
    if event is None:
        return JsonResponse({"error": "Invalid confirmation."}, status=400)
    latest_ready_event_id = MaterialRequestEvent.objects.filter(
        material_request_id=pk,
        event_type=MaterialRequestEvent.EventType.STATUS_CHANGED,
        new_status=PickTicket.Status.RECEIVED,
    ).order_by("-pk").values_list("pk", flat=True).first()
    if event.pk != latest_ready_event_id:
        return JsonResponse({"error": "This confirmation request is no longer current."}, status=400)

    material_request = get_object_or_404(MaterialRequest, pk=pk)
    if material_request.creator_id != request.user.pk:
        return JsonResponse({"error": "Not authorized."}, status=403)
    try:
        confirm_delivery_acceptance(material_request, user=request.user)
    except ValidationError as exc:
        return JsonResponse({"error": exc.message}, status=409)
    return JsonResponse({"confirmed": True})

from .forms import (
    BulkAdjustForm,
    InventoryBulkEditForm,
    BulkReceivingForm,
    InventoryItemForm,
    GroupRenameForm,
    DeliveryResponseForm,
    MaterialRequestForm,
    MaterialRequestLineFormSet,
    PickTicketForm,
    PickTicketLineForm,
    PickTicketLineFormSet,
    ReceivingForm,
    ReceivingLineFormSet,
    ReceivingTicketForm,
    ReceivingTicketEditForm,
)


class WarehouseUserCreationForm(UserCreationForm):
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=False)
    is_staff = forms.BooleanField(required=False, label="Staff Status (can access admin)")
    is_superuser = forms.BooleanField(required=False, label="Superuser (full admin access)")
    is_active = forms.BooleanField(required=False, initial=True, label="Active (can log in)")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = (
            "username",
            "first_name",
            "last_name",
            "email",
            "password1",
            "password2",
            "is_staff",
            "is_superuser",
            "is_active",
        )


class WarehouseUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = (
            "username",
            "first_name",
            "last_name",
            "email",
            "is_staff",
            "is_superuser",
            "is_active",
        )

from .models import (
    _LEDGER_WRITE_TOKEN,
    ApprovalRequest,
    BIN_LOCATION_CHOICES,
    CategoryChoices,
    CycleCount,
    CycleCountItem,
    ItemDocument,
    ItemImage,
    InventoryItem,
    InventoryTransaction,
    MaterialRequest,
    MaterialRequestEvent,
    PickTicket,
    PickTicketLine,
    RACK_CHOICES,
    ReceivingDocument,
    ReceivingLine,
    ReceivingTicket,
    SECTION_CHOICES,
    STANDALONE_BIN_VALUES,
)
from .models import generate_qr_code, pick_random_items_for_cycle_count


# --- Item image / document serving ----------------------------------------
#
# These views intentionally sit *between* Django's MEDIA_URL and the
# on-disk file. They enforce authentication AND the inventory.view
# permission so that guessing item/image IDs cannot bypass access
# control. Templates render <img src="{% url 'serve_item_image' ... %}">
# and <a href="{% url 'serve_item_document' ... %}"> instead of using
# raw MEDIA_URL paths.


def _item_media_response(media_file, allowed_mime_types, max_bytes, declared_content_type=""):
    """Stream an uploaded media file to the response after validation."""
    try:
        size = media_file.size
    except (ValueError, OSError):
        raise Http404("File missing on disk")
    if size is None or size <= 0:
        raise Http404("File is empty")
    if size > max_bytes:
        raise Http404("File exceeds size limit")
    content_type = (declared_content_type or "").lower().split(";")[0].strip()
    if not content_type or content_type not in allowed_mime_types:
        raise Http404("Unsupported file type")
    try:
        media_file.open("rb")
    except (ValueError, FileNotFoundError):
        raise Http404("File missing on disk")
    try:
        response = FileResponse(media_file, content_type=content_type)
    finally:
        try:
            media_file.close()
        except Exception:
            pass
    response["Content-Length"] = size
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@any_perm_required("inventory.view_inventoryitem")
def serve_item_image(request, item_id, image_id):
    """Permission-checked image serve endpoint."""
    item = get_object_or_404(InventoryItem, pk=item_id)
    image = get_object_or_404(ItemImage, pk=image_id, item=item)
    if not image.image:
        raise Http404("Image has no file")
    return _item_media_response(
        image.image,
        allowed_mime_types=django_settings.ITEM_IMAGE_ALLOWED_MIME_TYPES,
        max_bytes=django_settings.ITEM_IMAGE_MAX_BYTES,
        declared_content_type=image.content_type,
    )


@login_required
@any_perm_required("inventory.view_inventoryitem")
def serve_item_document(request, item_id, doc_id):
    """Permission-checked document serve endpoint."""
    item = get_object_or_404(InventoryItem, pk=item_id)
    document = get_object_or_404(ItemDocument, pk=doc_id, item=item)
    if not document.document:
        raise Http404("Document has no file")
    return _item_media_response(
        document.document,
        allowed_mime_types=django_settings.ITEM_DOCUMENT_ALLOWED_MIME_TYPES,
        max_bytes=django_settings.ITEM_DOCUMENT_MAX_BYTES,
        declared_content_type=document.content_type,
    )


def _validate_uploaded_file(upload, allowed_mime_types, max_bytes):
    """Return an error string if invalid, else None."""
    if upload is None:
        return "Choose a file to upload."
    try:
        size = upload.size
    except (ValueError, OSError):
        return "Upload could not be read."
    if size is None or size <= 0:
        return "Uploaded file is empty."
    if size > max_bytes:
        return f"File exceeds {max_bytes // (1024 * 1024)} MB size limit."
    content_type = (upload.content_type or "").lower().split(";")[0].strip()
    if not content_type or content_type not in allowed_mime_types:
        return "Unsupported file type."
    return None


def _can_view_all_material_requests(user):
    return user.has_perm("inventory.view_all_materialrequests")


def _visible_pick_tickets(request, queryset=None):
    queryset = queryset if queryset is not None else PickTicket.objects.all()
    if _can_view_all_material_requests(request.user):
        return queryset
    return queryset.filter(
        Q(material_request__isnull=True) | Q(material_request__creator=request.user)
    )


def _stale_warehouse_notification_redirect(request, model, pk):
    """Return warehouse users safely to the queue when a cached target was deleted."""
    if (
        getattr(request, "is_wms_host", False)
        and _can_view_all_material_requests(request.user)
        and not model.objects.filter(pk=pk).exists()
    ):
        messages.warning(
            request,
            "That request is no longer available. It may have been deleted after the notification was sent.",
        )
        return redirect("material_request_board")
    return None


def _visible_inventory_transactions(request, queryset=None):
    queryset = queryset if queryset is not None else InventoryTransaction.objects.all()
    if _can_view_all_material_requests(request.user):
        return queryset
    return queryset.filter(
        Q(pick_ticket__material_request__isnull=True)
        | Q(pick_ticket__material_request__creator=request.user)
    )


@login_required
def dashboard(request):
    from datetime import date, timedelta
    from django.db.models import Count, Sum
    
    items = InventoryItem.objects.filter(active=True)
    total_items = items.count()
    low_stock_count = items.filter(low_stock_threshold__gt=0, quantity_on_hand__lte=models.F("low_stock_threshold")).count()
    negative_count = items.filter(quantity_on_hand__lt=0).count()
    
    # Additional stats
    total_qty_on_hand = items.aggregate(total=Sum("quantity_on_hand"))["total"] or 0
    total_active_items = items.filter(quantity_on_hand__gt=0).count()
    
    # Status counts
    visible_tickets = _visible_pick_tickets(request)
    open_ticket_count = visible_tickets.filter(status=PickTicket.Status.OPEN).count()
    draft_ticket_count = 0
    picked_ticket_count = visible_tickets.filter(status=PickTicket.Status.PICKED).count()

    # Recent data
    recent_tickets = _visible_pick_tickets(
        request, PickTicket.objects.select_related("created_by")
    ).order_by("-created_at")[:8]
    recent_transactions = _visible_inventory_transactions(
        request,
        InventoryTransaction.objects.select_related("item", "pick_ticket", "created_by"),
    ).order_by("-created_at")[:10]
    recent_receiving = ReceivingTicket.objects.select_related("created_by").prefetch_related("lines__item").order_by("-created_at")[:5]
    
    # Top picked items (last 30 days)
    thirty_days_ago = date.today() - timedelta(days=30)
    top_picked = (
        PickTicketLine.objects
        .filter(
            ticket__in=visible_tickets,
            ticket__created_at__date__gte=thirty_days_ago,
            ticket__status__in=[
                PickTicket.Status.PICKED,
                PickTicket.Status.RECEIVED,
                PickTicket.Status.CLOSED,
            ],
        )
        .values("item__name", "item__part_number")
        .annotate(total_qty=Sum("quantity"))
        .order_by("-total_qty")[:5]
    )
    
    # Top received items (last 30 days)
    top_received = (
        ReceivingLine.objects
        .filter(ticket__created_at__date__gte=thirty_days_ago)
        .values("item__name", "item__part_number")
        .annotate(total_qty=Sum("quantity"))
        .order_by("-total_qty")[:5]
    )
    
    # Items needing attention
    zero_stock_items = items.filter(quantity_on_hand=0).count()
    below_reorder = items.filter(low_stock_threshold__gt=0, quantity_on_hand__lte=models.F("low_stock_threshold")).count()
    
    return render(
        request,
        "inventory/dashboard.html",
        {
            "total_items": total_items,
            "total_qty_on_hand": total_qty_on_hand,
            "total_active_items": total_active_items,
            "low_stock_count": low_stock_count,
            "negative_count": negative_count,
            "open_ticket_count": open_ticket_count,
            "draft_ticket_count": draft_ticket_count,
            "picked_ticket_count": picked_ticket_count,
            "recent_tickets": recent_tickets,
            "recent_transactions": recent_transactions,
            "recent_receiving": recent_receiving,
            "top_picked": top_picked,
            "top_received": top_received,
            "zero_stock_items": zero_stock_items,
            "below_reorder": below_reorder,
            "thirty_days_ago": thirty_days_ago,
        },
    )


@login_required
@portal_inventory_access_required
def inventory_list(request):
    query = request.GET.get("q", "").strip()
    part_number = request.GET.get("part_number", "").strip()
    po_number = request.GET.get("po_number", "").strip()
    category = request.GET.get("category", "").strip()
    stock = request.GET.get("stock", "").strip()
    sort = request.GET.get("sort", "part")

    items = InventoryItem.objects.all()
    if query:
        items = items.filter(
            Q(part_number__icontains=query)
            | Q(fb_part_number__icontains=query)
            | Q(model_number__icontains=query)
            | Q(name__icontains=query)
            | Q(description__icontains=query)
            | Q(category__icontains=query)
            | Q(barcode_value__icontains=query)
            | Q(qr_code_value__icontains=query)
            | Q(bin_location__icontains=query)
            | Q(rack__icontains=query)
            | Q(section__icontains=query)
            | Q(building_room__icontains=query)
        )
    if part_number:
        items = items.filter(part_number__icontains=part_number)
    if po_number:
        items = items.filter(
            receivingline__ticket__po_number__icontains=po_number
        ).distinct()
    if category:
        items = items.filter(category=category)
    if stock == "low":
        items = items.filter(
            low_stock_threshold__gt=0,
            quantity_on_hand__lte=models.F("low_stock_threshold"),
        )
    elif stock == "zero":
        items = items.filter(quantity_on_hand=0)
    elif stock == "negative":
        items = items.filter(quantity_on_hand__lt=0)

    sort_map = {
        "part": "part_number",
        "part_desc": "-part_number",
        "name": "description",
        "category": "category",
        "qty": "quantity_on_hand",
        "qty_desc": "-quantity_on_hand",
        "location": "building_room",
        "bin": "bin_location",
        "updated": "-updated_at",
    }
    items = items.distinct().order_by(sort_map.get(sort, "part_number"))
    filters_active = any([query, part_number, po_number, category, stock])
    return render(
        request,
        "inventory/inventory_list.html",
        {
            "items": items,
            "query": query,
            "part_number": part_number,
            "po_number": po_number,
            "category": category,
            "category_choices": CategoryChoices.choices,
            "stock": stock,
            "sort": sort,
            "filters_active": filters_active,
        },
    )


INVENTORY_IMPORT_HEADER_ALIASES = {
    "part_number": {"part #", "part", "part number", "part no", "part no.", "sku"},
    "fb_part_number": {"fb part #", "fb part", "fb part number", "fb part no", "fb part no."},
    "model_number": {"model", "model #", "model number", "model no", "model no."},
    "name": {"name", "item", "item name", "product", "description/name"},
    "shipper": {"shipper", "carrier", "freight", "shipping company"},
    "category": {"category", "type"},
    "description": {"description", "desc"},
    "quantity_on_hand": {"qty", "quantity", "quantity on hand", "on hand", "on-hand", "stock"},
    "unit": {"unit", "uom"},
    "building_room": {"bldg/room #", "building/room", "building room", "bldg room", "room"},
    "rack": {"rack"},
    "combined_location": {"location", "rack location", "storage location"},
    "section": {"section"},
    "bin_location": {"bin location", "bin", "bin loc"},
    "low_stock_threshold": {"low stock threshold", "low stock", "reorder point", "minimum"},
    "barcode_value": {"barcode value", "barcode", "barcode_value"},
    "qr_code_value": {"qr code value", "qr value", "qr", "qr_code_value"},
    "active": {"active", "enabled"},
}


def normalize_inventory_header(value):
    return str(value or "").strip().lower()


def parse_inventory_bool(value, default=True):
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "active", "enabled"}


def parse_inventory_int(value, default=0):
    if value is None or str(value).strip() == "":
        return default
    return int(float(value))


def process_inventory_import(spreadsheet_file, user):
    wb = openpyxl.load_workbook(spreadsheet_file, data_only=True)
    ws = wb.active

    raw_headers = [normalize_inventory_header(cell.value) for cell in ws[1]]
    columns = {}
    for field, aliases in INVENTORY_IMPORT_HEADER_ALIASES.items():
        for idx, header in enumerate(raw_headers):
            if header in aliases:
                columns[field] = idx
                break

    if ("part_number" not in columns and "model_number" not in columns) or ("name" not in columns and "description" not in columns):
        return 0, 0, ["Spreadsheet must include Part # or Model # and Name or Description columns."]

    imported = 0
    updated = 0
    errors = []
    for row_number, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
        def cell(field, default=""):
            idx = columns.get(field)
            if idx is None or idx >= len(row):
                return default
            value = row[idx]
            return default if value is None else value

        part_number = str(cell("part_number")).strip() or str(cell("model_number")).strip()
        raw_name = str(cell("name")).strip()
        description = str(cell("description", "")).strip()
        name = description or raw_name
        if not part_number and not name:
            continue
        if not part_number or not name:
            errors.append(f"Row {row_number}: skipped because Part # and Name/Description are required.")
            continue

        try:
            quantity = parse_inventory_int(cell("quantity_on_hand"), 0)
            low_stock_threshold = parse_inventory_int(cell("low_stock_threshold"), 0)
        except (TypeError, ValueError):
            errors.append(f"Row {row_number}: skipped because quantity or low stock threshold is invalid.")
            continue

        rack = str(cell("rack", "")).strip().upper()
        section = str(cell("section", "")).strip()
        raw_bin = str(cell("bin_location", "")).strip()
        combined_location = str(cell("combined_location", "")).strip().upper()
        location_match = re.fullmatch(
            r"(?:(?P<rack>[A-D])\s*[-/ ]\s*)?0*(?P<section>\d{1,2})\s*[-/.]\s*0*(?P<bin>\d{1,2})",
            combined_location,
        )
        if location_match:
            rack = rack or (location_match.group("rack") or "")
            section = section or location_match.group("section")
            raw_bin = raw_bin or location_match.group("bin")
        section = section.zfill(2) if section.isdigit() else section
        bin_location = raw_bin.zfill(2) if raw_bin.isdigit() else raw_bin.upper()

        valid_racks = {value for value, _label in RACK_CHOICES}
        valid_sections = {value for value, _label in SECTION_CHOICES}
        valid_bins = {value for value, _label in BIN_LOCATION_CHOICES}
        if rack and rack not in valid_racks:
            errors.append(f"Row {row_number}: skipped because rack must be A, B, C, or D.")
            continue
        if section and section not in valid_sections:
            errors.append(f"Row {row_number}: skipped because section must be from 1 to 20.")
            continue
        if bin_location and bin_location not in valid_bins:
            errors.append(f"Row {row_number}: skipped because bin must be from 1 to 6.")
            continue

        barcode_value = str(cell("barcode_value", "")).strip().upper()
        qr_code_value = str(cell("qr_code_value", "")).strip()

        item = None
        has_structured_location_columns = (
            "rack" in columns or "section" in columns or "combined_location" in columns
        )
        if has_structured_location_columns:
            item = (
                InventoryItem.objects
                .filter(part_number=part_number, rack=rack, section=section, bin_location=bin_location)
                .first()
            )
        if item is None and not has_structured_location_columns:
            item = InventoryItem.objects.filter(part_number=part_number).first()

        conflict_qs = InventoryItem.objects.all()
        if item:
            conflict_qs = conflict_qs.exclude(pk=item.pk)
        barcode_conflict = barcode_value and conflict_qs.filter(barcode_value=barcode_value).exists()
        qr_conflict = qr_code_value and conflict_qs.filter(qr_code_value=qr_code_value).exists()
        if barcode_conflict or qr_conflict:
            errors.append(f"Row {row_number}: skipped because barcode or QR code already belongs to another item.")
            continue

        defaults = {
            "fb_part_number": str(cell("fb_part_number", "")).strip(),
            "model_number": str(cell("model_number", "")).strip(),
            "name": name,
            "shipper": str(cell("shipper", "")).strip(),
            "category": str(cell("category", "")).strip(),
            "description": description or raw_name,
            "quantity_on_hand": quantity,
            "unit": str(cell("unit", "each")).strip() or "each",
            "building_room": str(cell("building_room", "")).strip() or (
                combined_location if combined_location and not location_match else ""
            ),
            "rack": rack,
            "section": section,
            "bin_location": bin_location,
            "low_stock_threshold": low_stock_threshold,
            "barcode_value": barcode_value or None,
            "qr_code_value": qr_code_value or None,
            "active": parse_inventory_bool(cell("active", None), True),
        }

        with transaction.atomic():
            if item:
                old_quantity = item.quantity_on_hand
                for field, value in defaults.items():
                    setattr(item, field, value)
                item.save()
                delta = quantity - old_quantity
                updated += 1
            else:
                item = InventoryItem.objects.create(part_number=part_number, **defaults)
                delta = quantity
                imported += 1

            if delta:
                import_tx = InventoryTransaction(
                    item=item,
                    transaction_type=InventoryTransaction.TransactionType.IMPORT,
                    quantity_delta=delta,
                    notes=f"Spreadsheet import from {getattr(spreadsheet_file, 'name', 'upload')}",
                    created_by=user,
                )
                import_tx.save(_ledger_write_token=_LEDGER_WRITE_TOKEN)

    return imported, updated, errors


@login_required
@any_perm_required("inventory.clear_inventory")
def inventory_clear(request):
    if request.method != "POST":
        return redirect("inventory_list")

    if request.POST.get("confirm") != "DELETE":
        messages.error(request, "Inventory clear was not confirmed.")
        return redirect("inventory_list")

    if InventoryTransaction.objects.filter(
        transaction_type__in=[
            InventoryTransaction.TransactionType.RECEIPT,
            InventoryTransaction.TransactionType.REVERSAL,
        ]
    ).exists():
        messages.error(
            request,
            "Inventory cannot be cleared while immutable receiving history exists.",
        )
        return redirect("inventory_list")

    with transaction.atomic():
        item_count = InventoryItem.objects.count()
        transaction_count = InventoryTransaction.objects.count()
        pick_line_count = PickTicketLine.objects.count()
        receiving_line_count = ReceivingLine.objects.count()
        image_count = ItemImage.objects.count()
        document_count = ItemDocument.objects.count() + ReceivingDocument.objects.count()

        ItemImage.objects.all().delete()
        ItemDocument.objects.all().delete()
        ReceivingDocument.objects.all().delete()
        PickTicketLine.objects.all().delete()
        ReceivingLine.objects.all().delete()
        InventoryTransaction.objects._audit_wipe_query().delete()
        InventoryItem.objects.all().delete()

    messages.success(
        request,
        f"Cleared inventory: deleted {item_count} item(s), {transaction_count} transaction(s), "
        f"{pick_line_count + receiving_line_count} ticket line(s), {image_count} image(s), "
        f"and {document_count} document(s).",
    )
    return redirect("inventory_list")


@login_required
@any_perm_required("inventory.delete_inventoryitem")
def inventory_delete(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    if request.method == "POST":
        from django.db import transaction as db_transaction

        part_number = item.part_number
        has_pick_lines = PickTicketLine.objects.filter(item=item).exists()
        has_receiving_lines = ReceivingLine.objects.filter(item=item).exists()
        if has_pick_lines or has_receiving_lines:
            messages.error(
                request,
                f"Cannot delete \"{part_number}\" because it is referenced by existing tickets. Remove the ticket lines first or use Clear Inventory.",
            )
            return redirect("inventory_list")

        tx_count = InventoryTransaction.objects.filter(item=item).count()
        if tx_count:
            messages.error(
                request,
                f"Cannot delete \"{part_number}\" because it has transaction history. Deactivate the item instead.",
            )
            return redirect("inventory_list")

        with db_transaction.atomic():
            ItemImage.objects.filter(item=item).delete()
            ItemDocument.objects.filter(item=item).delete()
            item.delete()

        messages.success(request, f"Deleted \"{part_number}\".")
        return redirect("inventory_list")
    return redirect("inventory_list")


@login_required
@any_perm_required("inventory.import_inventory")
def inventory_import_xlsx(request):
    if request.method != "POST":
        return redirect("inventory_list")

    spreadsheet = request.FILES.get("spreadsheet")
    if not spreadsheet:
        messages.error(request, "Choose an .xlsx spreadsheet to import.")
        return redirect("inventory_list")

    if not spreadsheet.name.lower().endswith(".xlsx"):
        messages.error(request, "Inventory import only supports .xlsx files.")
        return redirect("inventory_list")

    try:
        imported, updated, errors = process_inventory_import(spreadsheet, request.user)
    except Exception as exc:
        messages.error(request, f"Import failed: {exc}")
        return redirect("inventory_list")

    if imported or updated:
        messages.success(request, f"Import complete: {imported} new item(s), {updated} updated item(s).")
    else:
        messages.warning(request, "No inventory rows were imported.")
    for error in errors[:10]:
        messages.error(request, error)
    if len(errors) > 10:
        messages.error(request, f"{len(errors) - 10} more row error(s) were skipped from display.")
    return redirect("inventory_list")


@login_required
def low_stock_list(request):
    items = InventoryItem.objects.filter(active=True).filter(
        Q(quantity_on_hand__lt=0)
        | Q(low_stock_threshold__gt=0, quantity_on_hand__lte=models.F("low_stock_threshold"))
    )
    return render(request, "inventory/low_stock_list.html", {"items": items})


@login_required
def inventory_edit(request, pk=None):
    required_perm = "inventory.change_inventoryitem" if pk else "inventory.add_inventoryitem"
    if not user_has_any_perm(request.user, required_perm):
        messages.error(request, "You do not have permission to do that.")
        return redirect("dashboard")
    item = get_object_or_404(InventoryItem, pk=pk) if pk else None
    if request.method == "POST":
        form = InventoryItemForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, "Inventory item saved.")
            return redirect("inventory_list")
    else:
        form = InventoryItemForm(instance=item)
    return render(request, "inventory/inventory_form.html", {"form": form, "item": item})


@login_required
@any_perm_required("inventory.receive_stock")
def receive_stock(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    if request.method == "POST":
        quantity = int(request.POST.get("quantity", "0"))
        notes = request.POST.get("notes", "")
        InventoryTransaction.record_receipt(item=item, quantity=quantity, user=request.user, notes=notes)
        messages.success(request, f"Received {quantity} into {item.name}.")
        return redirect("inventory_list")
    return render(request, "inventory/receive_stock.html", {"item": item})


@login_required
@any_perm_required("inventory.view_pickticket")
def ticket_list(request):
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    tickets = _visible_pick_tickets(
        request,
        PickTicket.objects.select_related("created_by").prefetch_related("lines__item"),
    )
    if query:
        tickets = tickets.filter(
            Q(ticket_number__icontains=query)
            | Q(picked_by_name__icontains=query)
            | Q(received_by_name__icontains=query)
            | Q(requested_by_name__icontains=query)
            | Q(building_room__icontains=query)
            | Q(location__icontains=query)
            | Q(lines__item__part_number__icontains=query)
            | Q(lines__item__name__icontains=query)
        ).distinct()
    if status:
        tickets = tickets.filter(status=status)
    return render(
        request,
        "inventory/ticket_list.html",
        {"tickets": tickets, "query": query, "status": status, "status_choices": PickTicket.Status.choices},
    )


@login_required
@any_perm_required("inventory.add_pickticket")
def ticket_create(request):
    if request.method == "POST":
        form = PickTicketForm(request.POST)
        formset = PickTicketLineFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            ticket = form.save(commit=False)
            ticket.created_by = request.user
            ticket.save()
            formset.instance = ticket
            _rebuild_pick_ticket_lines(ticket, formset)
            messages.success(request, f"Created pick ticket {ticket.ticket_number}.")
            return redirect("ticket_print", pk=ticket.pk)
    else:
        form = PickTicketForm()
        formset = PickTicketLineFormSet()
    return render(request, "inventory/ticket_form.html", {"form": form, "formset": formset})


def _rebuild_pick_ticket_lines(ticket, formset):
    """Replace pick-ticket lines, letting model delete/save hooks rebalance inventory."""
    for line in list(ticket.lines.all()):
        line.delete()
    for form in formset:
        if not form.cleaned_data or form.cleaned_data.get("DELETE"):
            continue
        item = form.cleaned_data.get("item")
        quantity = form.cleaned_data.get("quantity")
        if item and quantity:
            PickTicketLine.objects.create(ticket=ticket, item=item, quantity=quantity)


@login_required
@any_perm_required("inventory.change_pickticket")
def ticket_edit(request, pk):
    ticket = get_object_or_404(
        _visible_pick_tickets(request, PickTicket.objects.prefetch_related("lines__item")),
        pk=pk,
    )
    linked_request = MaterialRequest.objects.filter(pick_ticket=ticket).first()
    if linked_request:
        messages.error(
            request,
            f"{ticket.ticket_number} is managed by material request {linked_request.request_number}. Edit the request instead.",
        )
        return redirect("material_request_edit", pk=linked_request.pk)
    if request.method == "POST":
        form = PickTicketForm(request.POST, instance=ticket)
        formset = PickTicketLineFormSet(request.POST, instance=ticket)
        if form.is_valid() and formset.is_valid():
            form.save()
            _rebuild_pick_ticket_lines(ticket, formset)
            messages.success(request, f"Updated pick ticket {ticket.ticket_number}.")
            return redirect("ticket_list")
    else:
        form = PickTicketForm(instance=ticket)
        formset = PickTicketLineFormSet(instance=ticket)
    return render(request, "inventory/ticket_form.html", {"form": form, "formset": formset, "ticket": ticket, "is_edit": True})


@login_required
@any_perm_required("inventory.view_pickticket")
def ticket_detail(request, pk):
    ticket = _visible_pick_tickets(
        request, PickTicket.objects.prefetch_related("lines__item")
    ).filter(pk=pk).first()
    if ticket is None:
        stale_redirect = _stale_warehouse_notification_redirect(request, PickTicket, pk)
        if stale_redirect is not None:
            return stale_redirect
        raise Http404
    return render(request, "inventory/ticket_detail.html", {"ticket": ticket, "status_choices": PickTicket.Status.choices})


@login_required
@any_perm_required("inventory.change_pickticket")
def ticket_status_update(request, pk):
    ticket = get_object_or_404(_visible_pick_tickets(request), pk=pk)
    if request.method != "POST":
        return redirect("ticket_detail", pk=ticket.pk)
    new_status = request.POST.get("status")
    valid_statuses = {choice[0] for choice in PickTicket.Status.choices}
    if new_status in valid_statuses:
        from .services import update_pick_ticket_status

        update_pick_ticket_status(ticket, new_status, actor=request.user)
        ticket.refresh_from_db(fields=["status"])
        messages.success(request, f"Updated {ticket.ticket_number} to {ticket.get_status_display()}.")
    else:
        messages.error(request, "Invalid ticket status.")
    return redirect("ticket_detail", pk=ticket.pk)


def _ticket_print_context(ticket, *, pdf_mode=False, generated_at_display=""):
    lines = list(ticket.lines.select_related("item").all())
    numbered_rows = [{"number": index, "line": line} for index, line in enumerate(lines, start=1)]
    # Twenty operational line rows fit safely on a landscape Letter page while
    # preserving the ticket header, delivery details, receipt confirmation,
    # and footer. Line 21 starts the next page to prevent clipping.
    rows_per_page = 20
    chunks = [
        numbered_rows[index : index + rows_per_page]
        for index in range(0, len(numbered_rows), rows_per_page)
    ] or [[]]
    total_pages = len(chunks)
    ticket_pages = [
        {
            "number": index,
            "rows": rows,
            "is_first": index == 1,
            "is_last": index == total_pages,
        }
        for index, rows in enumerate(chunks, start=1)
    ]
    delivery_parts = [part for part in (ticket.building_room, ticket.location) if part]
    try:
        scheduled_delivery = ticket.material_request.delivery_at
    except MaterialRequest.DoesNotExist:
        scheduled_delivery = None
    context = {
        "ticket": ticket,
        "ticket_pages": ticket_pages,
        "total_pages": total_pages,
        "generated_at": timezone.localtime(),
        "generated_at_display": generated_at_display,
        "scheduled_delivery": scheduled_delivery,
        "delivery_location": " — ".join(delivery_parts),
        "pdf_mode": pdf_mode,
    }
    if pdf_mode:
        logo_path = django_settings.BASE_DIR / "inventory" / "static" / "inventory" / "img" / "blackbox-logo.png"
        context["logo_uri"] = logo_path.as_uri()
    return context


@login_required
@any_perm_required("inventory.print_pickticket")
def ticket_print(request, pk):
    ticket = get_object_or_404(
        _visible_pick_tickets(request, PickTicket.objects.prefetch_related("lines__item")),
        pk=pk,
    )
    return render(request, "inventory/ticket_print.html", _ticket_print_context(ticket))


@login_required
@any_perm_required("inventory.print_pickticket")
def ticket_print_pdf(request, pk):
    from weasyprint import HTML

    ticket = get_object_or_404(
        _visible_pick_tickets(request, PickTicket.objects.prefetch_related("lines__item")),
        pk=pk,
    )
    generated_at_display = request.GET.get("generated_at", "").strip()[:80]
    html = render_to_string(
        "inventory/ticket_print.html",
        _ticket_print_context(
            ticket, pdf_mode=True, generated_at_display=generated_at_display
        ),
        request=request,
    )
    pdf = HTML(string=html, base_url=str(django_settings.BASE_DIR)).write_pdf()
    response = HttpResponse(pdf, content_type="application/pdf")
    filename = f"{ticket.ticket_number}_{ticket.date.date().isoformat()}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@any_perm_required("inventory.delete_pickticket")
def ticket_delete(request, pk):
    ticket = get_object_or_404(
        _visible_pick_tickets(request, PickTicket.objects.prefetch_related("lines__item")),
        pk=pk,
    )
    linked_request = MaterialRequest.objects.filter(pick_ticket=ticket).first()
    if linked_request:
        messages.error(
            request,
            f"{ticket.ticket_number} is managed by material request {linked_request.request_number}. Delete the request instead.",
        )
        return redirect("material_request_detail", pk=linked_request.pk)
    if request.method == "POST":
        ticket_number = ticket.ticket_number
        # Deleting the ticket will cascade to lines, which will reverse inventory via their delete() methods
        ticket.delete()
        messages.success(request, f"Deleted pick ticket {ticket_number} and restored inventory.")
        return redirect("ticket_list")
    return render(request, "inventory/ticket_confirm_delete.html", {"ticket": ticket})


@login_required
@any_perm_required("inventory.print_qr_codes")
def inventory_labels(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    return render(request, "inventory/inventory_labels.html", {"item": item})


@login_required
@any_perm_required("inventory.manage_item_barcodes")
def item_barcode_print(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    return render(request, "inventory/item_barcode_print.html", {"item": item})


@login_required
@any_perm_required("inventory.manage_item_barcodes")
def item_upc_assign(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    if request.method == "POST":
        upc = request.POST.get("barcode_value", request.POST.get("upc", "")).strip()
        if not upc:
            messages.error(request, "A UPC or barcode value is required.")
        elif InventoryItem.objects.exclude(pk=item.pk).filter(barcode_value__iexact=upc).exists():
            messages.error(request, "That UPC is already assigned to another inventory item.")
        else:
            item.barcode_value = upc
            item.save(update_fields=["barcode_value", "updated_at"])
            messages.success(request, f"UPC {upc} assigned to {item.part_number}.")
            return redirect("item_detail", pk=item.pk)
    return render(request, "inventory/item_upc_assign.html", {"item": item})


@login_required
@any_perm_required("inventory.scan_codes")
def scanner(request):
    return render(request, "inventory/scanner.html")


def _find_item_by_code(code):
    if not code:
        return None
    normalized = code.strip()
    parsed_part = ""
    if normalized.upper().startswith("ITEM:"):
        first_segment = normalized.split("|", 1)[0]
        parsed_part = first_segment.split(":", 1)[1].strip() if ":" in first_segment else ""
    return InventoryItem.objects.filter(
        Q(part_number=normalized) | Q(barcode_value=normalized) | Q(qr_code_value=normalized) | Q(part_number=parsed_part)
    ).first()


@login_required
@any_perm_required("inventory.scan_codes")
def scan_lookup_api(request):
    code = request.GET.get("code", "").strip()

    # Handle location QR codes
    location_match = re.fullmatch(r"https?://bbx\.rplwms\.com/locations/([^/?#]+)/?", code, re.IGNORECASE)
    if location_match:
        code = f"LOC:{location_match.group(1)}"
    if code.upper().startswith("LOC:"):
        location_key = code[4:].strip()
        if _parse_location_key(location_key):
            return JsonResponse({
                "found": True,
                "is_location": True,
                "location_url": reverse("location_detail", kwargs={"location_key": location_key}),
                "location_key": location_key,
            })
        return JsonResponse({"found": False}, status=404)

    item = _find_item_by_code(code)
    if not item:
        return JsonResponse({"found": False}, status=404)
    return JsonResponse({
        "found": True,
        "is_location": False,
        "id": item.pk,
        "item_url": reverse("item_detail", kwargs={"pk": item.pk}),
        "part_number": item.part_number,
        "name": item.name,
        "quantity_on_hand": item.quantity_on_hand,
    })


@login_required
@any_perm_required("inventory.view_inventoryitem")
def item_search_api(request):
    """JSON API: search items by part_number or name (min 3 chars)."""
    q = request.GET.get("q", "").strip()
    if len(q) < 3:
        return JsonResponse({"results": []})

    items = InventoryItem.objects.filter(active=True).filter(
        Q(part_number__icontains=q)
        | Q(fb_part_number__icontains=q)
        | Q(model_number__icontains=q)
        | Q(name__icontains=q)
    ).order_by("part_number")[:50]

    results = [
        {
            "id": item.pk,
            "part_number": item.part_number,
            "fb_part_number": item.fb_part_number,
            "name": item.name,
            "quantity_on_hand": item.quantity_on_hand,
            "storage_location": item.storage_location,
        }
        for item in items
    ]
    return JsonResponse({"results": results})


@login_required
@any_perm_required("inventory.scan_codes")
def scan_lookup(request):
    code = request.GET.get("code", "").strip()

    # Handle location QR codes
    location_match = re.fullmatch(r"https?://bbx\.rplwms\.com/locations/([^/?#]+)/?", code, re.IGNORECASE)
    if location_match:
        code = f"LOC:{location_match.group(1)}"
    if code.upper().startswith("LOC:"):
        location_key = code[4:].strip()
        if _parse_location_key(location_key):
            return redirect("location_detail", location_key=location_key)
        messages.error(request, "Invalid location QR code.")
        return redirect("inventory_list")

    item = _find_item_by_code(code)
    if not item:
        messages.error(request, "No matching inventory item found.")
        return redirect("inventory_list")
    return redirect("item_detail", pk=item.pk)


@login_required
@portal_inventory_access_required
def item_detail(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    transactions = _visible_inventory_transactions(
        request,
        item.transactions.select_related("created_by", "pick_ticket"),
    ).order_by("-created_at")[:10]
    return render(request, "inventory/item_detail.html", {"item": item, "transactions": transactions})


@login_required
@any_perm_required("inventory.export_inventory")
def export_inventory_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="warehouse_inventory_export.csv"'
    writer = csv.writer(response)
    writer.writerow(["Part #", "FB Part #", "Model #", "Name", "Shipper", "Category", "Qty", "BLDG/Room #", "Rack", "Section", "Bin Location", "Barcode", "QR"])
    for item in InventoryItem.objects.all():
        writer.writerow([
            item.part_number,
            item.fb_part_number,
            item.model_number,
            item.description or item.name,
            item.shipper,
            item.category,
            item.quantity_on_hand,
            item.building_room,
            item.rack,
            item.section,
            item.bin_location,
            item.barcode_value or "",
            item.qr_code_value or "",
        ])
    return response


@login_required
@any_perm_required("inventory.view_pickticket")
def export_tickets_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="warehouse_ticket_history.csv"'
    writer = csv.writer(response)
    writer.writerow([
        "Ticket #",
        "Status",
        "Date",
        "Picked By",
        "Received By",
        "Requested By",
        "BLDG/Room #",
        "Location",
        "Line Count",
    ])
    tickets = _visible_pick_tickets(
        request, PickTicket.objects.prefetch_related("lines")
    )
    for ticket in tickets:
        writer.writerow([
            ticket.ticket_number,
            ticket.status,
            ticket.date.isoformat(),
            ticket.picked_by_name,
            ticket.received_by_name,
            ticket.requested_by_name,
            ticket.building_room,
            ticket.location,
            ticket.lines.count(),
        ])
    return response


@login_required
@any_perm_required("inventory.receive_stock")
def receiving(request):
    """Dedicated receiving page with form for multi-item receiving tickets."""
    items = InventoryItem.objects.filter(active=True).order_by("category", "name")
    receiving_form = ReceivingForm()
    ticket_form = ReceivingTicketForm()
    recent_tickets = ReceivingTicket.objects.select_related("created_by").prefetch_related("lines__item")[:10]

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create_receiving_ticket":
            ticket_form = ReceivingTicketForm(request.POST)
            if ticket_form.is_valid():
                ticket = ReceivingTicket.objects.create(
                    date=ticket_form.cleaned_data.get("date") or timezone.now(),
                    po_number=ticket_form.cleaned_data["po_number"],
                    vendor=ticket_form.cleaned_data["vendor"],
                    notes=ticket_form.cleaned_data["notes"],
                    created_by=request.user,
                )
                request.session["receiving_ticket_id"] = ticket.pk
                messages.success(request, f"Created receiving ticket {ticket.ticket_number}. Add items below.")
                return redirect("receiving")

        elif action == "add_line":
            ticket_id = request.session.get("receiving_ticket_id")
            if not ticket_id:
                messages.error(request, "No active receiving ticket. Create one first.")
                return redirect("receiving")

            try:
                ticket = ReceivingTicket.objects.get(pk=ticket_id)
            except ReceivingTicket.DoesNotExist:
                messages.error(request, "Receiving ticket not found.")
                if "receiving_ticket_id" in request.session:
                    del request.session["receiving_ticket_id"]
                return redirect("receiving")

            receiving_form = ReceivingForm(request.POST, request.FILES)
            if receiving_form.is_valid():
                item = receiving_form.cleaned_data.get("item")
                part_number = receiving_form.cleaned_data.get("part_number")
                name = receiving_form.cleaned_data.get("name")
                quantity = receiving_form.cleaned_data["quantity"]
                notes = receiving_form.cleaned_data.get("notes", "")
                document = receiving_form.cleaned_data.get("document")
                source = receiving_form.cleaned_data.get("source", "file")
                po_number = receiving_form.cleaned_data.get("po_number")
                shipper = receiving_form.cleaned_data.get("shipper", "")
                rack = receiving_form.cleaned_data.get("rack", "")
                section = receiving_form.cleaned_data.get("section", "")
                bin_location = receiving_form.cleaned_data.get("bin_location", "")

                # A selected item may be received into a different destination.
                # Keep existing stock in place and create/reuse a location-specific
                # inventory record for the incoming quantity.
                if item and rack and section and bin_location:
                    requested_location = (rack, section, bin_location)
                    current_location = (item.rack, item.section, item.bin_location)
                    if requested_location != current_location:
                        destination = InventoryItem.objects.filter(
                            part_number=item.part_number,
                            rack=rack,
                            section=section,
                            bin_location=bin_location,
                        ).first()
                        if destination is None:
                            destination = InventoryItem.objects.create(
                                part_number=item.part_number,
                                name=item.name,
                                description=item.description,
                                category=item.category,
                                shipper=shipper or item.shipper,
                                quantity_on_hand=0,
                                unit=item.unit,
                                building_room=item.building_room,
                                rack=rack,
                                section=section,
                                bin_location=bin_location,
                                low_stock_threshold=item.low_stock_threshold,
                                active=item.active,
                            )
                        item = destination

                # If new item, create or get it
                if not item:
                    if not part_number or not name:
                        messages.error(request, "Part # and Name are required for new items.")
                        return redirect("receiving")
                    if bin_location in STANDALONE_BIN_VALUES:
                        rack = ""
                        section = ""
                    item, created = InventoryItem.objects.get_or_create(
                        part_number=part_number,
                        rack=rack,
                        section=section,
                        bin_location=bin_location,
                        defaults={"name": name, "description": name, "shipper": shipper, "quantity_on_hand": 0, "active": True}
                    )
                    if not created:
                        changed_fields = []
                        if item.name != name:
                            item.name = name
                            changed_fields.append("name")
                        if name and item.description != name:
                            item.description = name
                            changed_fields.append("description")
                        if shipper and item.shipper != shipper:
                            item.shipper = shipper
                            changed_fields.append("shipper")
                        if changed_fields:
                            changed_fields.append("updated_at")
                            item.save(update_fields=changed_fields)

                with transaction.atomic():
                    # Creating the line applies stock and writes its uniquely linked ledger entry.
                    ReceivingLine.objects.create(
                        ticket=ticket,
                        item=item,
                        quantity=quantity,
                        shipper=shipper,
                        notes=notes,
                    )

                    if document:
                        ReceivingDocument.objects.create(
                            item=item,
                            document=document,
                            original_filename=document.name,
                            uploaded_by=request.user,
                            notes=notes,
                            source=source,
                        )

                    if po_number and not ticket.po_number:
                        ticket.po_number = po_number
                        ticket.save(update_fields=["po_number"])

                messages.success(request, f"Added {quantity} {item.unit} of {item.name} to {ticket.ticket_number}.")
                return redirect("receiving")

        elif action == "finalize_ticket":
            ticket_id = request.session.get("receiving_ticket_id")
            if not ticket_id:
                messages.error(request, "No active receiving ticket.")
                return redirect("receiving")

            try:
                ticket = ReceivingTicket.objects.get(pk=ticket_id)
            except ReceivingTicket.DoesNotExist:
                messages.error(request, "Receiving ticket not found.")
                if "receiving_ticket_id" in request.session:
                    del request.session["receiving_ticket_id"]
                return redirect("receiving")

            if not ticket.lines.exists():
                messages.error(request, "Cannot finalize empty receiving ticket.")
                return redirect("receiving")

            messages.success(request, f"Finalized {ticket.ticket_number} with {ticket.lines.count()} line items.")
            del request.session["receiving_ticket_id"]
            return redirect("receiving_ticket_print", pk=ticket.pk)

        elif action == "discard_ticket":
            ticket_id = request.session.get("receiving_ticket_id")
            if ticket_id:
                try:
                    ticket = ReceivingTicket.objects.get(pk=ticket_id)
                    # Delete lines first (will reverse inventory transactions? Not automatically)
                    # For now just delete the ticket
                    ticket.delete()
                    messages.success(request, "Receiving ticket discarded.")
                except ReceivingTicket.DoesNotExist:
                    pass
                finally:
                    if "receiving_ticket_id" in request.session:
                        del request.session["receiving_ticket_id"]
            return redirect("receiving")

        elif action == "bulk_receive":
            bulk_form = BulkReceivingForm(request.POST, request.FILES)
            if bulk_form.is_valid():
                spreadsheet = bulk_form.cleaned_data["spreadsheet"]
                notes = bulk_form.cleaned_data["notes"]
                success_count, error_count, errors = process_bulk_receiving(spreadsheet, request.user, notes)
                if success_count:
                    messages.success(request, f"Bulk received {success_count} line items from spreadsheet.")
                if error_count:
                    for error in errors:
                        messages.error(request, error)
                return redirect("receiving")

    # Get active ticket if any
    active_ticket = None
    ticket_lines = []
    if "receiving_ticket_id" in request.session:
        try:
            active_ticket = ReceivingTicket.objects.prefetch_related("lines__item").get(pk=request.session["receiving_ticket_id"])
            ticket_lines = list(active_ticket.lines.all())
            if request.method != "POST":
                receiving_form = ReceivingForm(initial={"po_number": active_ticket.po_number})
        except ReceivingTicket.DoesNotExist:
            del request.session["receiving_ticket_id"]

    return render(request, "inventory/receiving.html", {
        "items": items,
        "receiving_form": receiving_form,
        "ticket_form": ticket_form,
        "bulk_form": BulkReceivingForm(),
        "active_ticket": active_ticket,
        "ticket_lines": ticket_lines,
        "recent_tickets": recent_tickets,
    })


def process_bulk_receiving(spreadsheet_file, user, default_notes=""):
    """Process an uploaded Excel spreadsheet for bulk receiving."""
    success_count = 0
    error_count = 0
    errors = []

    try:
        wb = openpyxl.load_workbook(spreadsheet_file)
        ws = wb.active

        # Expected headers (case-insensitive)
        headers = {}
        for col_idx, cell in enumerate(ws[1], 1):
            if cell.value:
                headers[cell.value.strip().lower()] = col_idx

        # Find required columns
        part_col = None
        qty_col = None
        shipper_col = None
        notes_col = None
        for key, idx in headers.items():
            if "part" in key or "sku" in key:
                part_col = idx
            elif "qty" in key or "quantity" in key:
                qty_col = idx
            elif "shipper" in key or "carrier" in key:
                shipper_col = idx
            elif "note" in key:
                notes_col = idx

        if not part_col or not qty_col:
            return 0, 1, ["Spreadsheet must have 'Part #' (or SKU) and 'Quantity' columns"]

        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=False), 2):
            part_cell = row[part_col - 1] if part_col <= len(row) else None
            qty_cell = row[qty_col - 1] if qty_col <= len(row) else None
            shipper_cell = row[shipper_col - 1] if shipper_col and shipper_col <= len(row) else None
            notes_cell = row[notes_col - 1] if notes_col and notes_col <= len(row) else None

            part_number = part_cell.value
            quantity = qty_cell.value
            shipper = str(shipper_cell.value).strip() if shipper_cell and shipper_cell.value else ""
            row_notes = notes_cell.value if notes_cell else default_notes

            if not part_number:
                continue  # Skip empty rows

            try:
                quantity = int(quantity) if quantity is not None else 0
            except (ValueError, TypeError):
                errors.append(f"Row {row_idx}: Invalid quantity '{quantity}'")
                error_count += 1
                continue

            if quantity <= 0:
                errors.append(f"Row {row_idx}: Quantity must be positive")
                error_count += 1
                continue

            with transaction.atomic():
                item = InventoryItem.objects.select_for_update().filter(
                    part_number=part_number.strip()
                ).first()
                if not item:
                    errors.append(f"Row {row_idx}: Item not found for part number '{part_number}'")
                    error_count += 1
                    continue

                if shipper and item.shipper != shipper:
                    item.shipper = shipper
                    item.save(update_fields=["shipper", "updated_at"])
                ticket = ReceivingTicket.objects.create(created_by=user, notes=row_notes or "")
                ReceivingLine.objects.create(
                    ticket=ticket,
                    item=item,
                    quantity=quantity,
                    shipper=shipper,
                    notes=row_notes or "",
                )
            success_count += 1

    except Exception as e:
        errors.append(f"Failed to process spreadsheet: {str(e)}")
        error_count += 1

    return success_count, error_count, errors


@login_required
@any_perm_required("inventory.view_qr_codes")
def qr_codes(request):
    """Generate QR codes for all rack/bin locations (A-D × 1-20 × 1-6)."""
    from django.db.models import Count
    racks = [value for value, _label in RACK_CHOICES]
    sections = [value for value, _label in SECTION_CHOICES]
    bins = [value for value, _label in BIN_LOCATION_CHOICES]

    # Get actual item counts from DB for locations that have items
    db_counts = {
        (r, s, b): cnt
        for r, s, b, cnt in InventoryItem.objects.filter(active=True)
        .exclude(rack="")
        .exclude(section="")
        .exclude(bin_location="")
        .values_list("rack", "section", "bin_location")
        .annotate(cnt=Count("id"))
    }

    location_qrs = []
    for rack in racks:
        for section in sections:
            for bin_loc in bins:
                key = f"{rack}-{section}-{bin_loc}"
                item_count = db_counts.get((rack, section, bin_loc), 0)
                location_qrs.append({
                    "key": key,
                    "item_count": item_count,
                    "qr_url": f"https://bbx.rplwms.com/locations/{key}/",
                    "location_url": f"https://bbx.rplwms.com/locations/{key}/",
                    "qr_b64": generate_qr_code(f"https://bbx.rplwms.com/locations/{key}/"),
                })

    return render(request, "inventory/qr_codes.html", {
        "location_qrs": location_qrs,
    })


@login_required
@any_perm_required("inventory.view_app_qr_code")
def app_qr_code(request):
    """Full-page QR code for the main app URL — designed for printing."""
    app_url = django_settings.APP_URL
    # Larger QR for full-page use — bigger box_size for higher res
    import qrcode
    import io
    import base64
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=16,
        border=4,
    )
    qr.add_data(app_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    qr_b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

    return render(request, "inventory/app_qr.html", {
        "app_url": app_url,
        "qr_b64": qr_b64,
    })



@any_perm_required("inventory.delete_receivingticket")
def receiving_ticket_delete(request, pk):
    ticket = get_object_or_404(ReceivingTicket.objects.prefetch_related("lines__item"), pk=pk)
    if request.method == "POST":
        ticket_number = ticket.ticket_number
        ticket.delete()
        messages.success(request, f"Deleted receiving ticket {ticket_number} and reversed received inventory.")
        return redirect("receiving_log")
    return render(request, "inventory/receiving_ticket_confirm_delete.html", {"ticket": ticket})


@login_required
@any_perm_required("inventory.export_inventory")
def export_inventory_xlsx(request):
    """Export current inventory to .xlsx file."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Inventory"

    # Headers
    headers = [
        "Part #", "FB Part #", "Model #", "Name", "Shipper", "Category", "Description", "Qty", "Unit",
        "BLDG/Room #", "Rack", "Section", "Bin Location", "Low Stock Threshold",
        "Barcode Value", "QR Code Value", "Active", "Updated At",
    ]
    ws.append(headers)

    from openpyxl.styles import Font
    header_font = Font(bold=True)
    for cell in ws[1]:
        cell.font = header_font

    items = InventoryItem.objects.all().order_by("category", "part_number")
    for item in items:
        ws.append([
            item.part_number,
            item.fb_part_number,
            item.model_number,
            item.description or item.name,
            item.shipper,
            item.category,
            item.description,
            item.quantity_on_hand,
            item.unit,
            item.building_room,
            item.rack,
            item.section,
            item.bin_location,
            item.low_stock_threshold,
            item.barcode_value or "",
            item.qr_code_value or "",
            "Yes" if item.active else "No",
            item.updated_at.isoformat() if item.updated_at else "",
        ])

    from openpyxl.worksheet.datavalidation import DataValidation
    rack_validation = DataValidation(type="list", formula1='"' + ','.join(value for value, _ in RACK_CHOICES) + '"', allow_blank=True)
    section_validation = DataValidation(type="list", formula1='"' + ','.join(value for value, _ in SECTION_CHOICES) + '"', allow_blank=True)
    bin_validation = DataValidation(type="list", formula1='"' + ','.join(value for value, _ in BIN_LOCATION_CHOICES) + '"', allow_blank=True)
    ws.add_data_validation(rack_validation)
    ws.add_data_validation(section_validation)
    ws.add_data_validation(bin_validation)
    rack_validation.add("K2:K1000")
    section_validation.add("L2:L1000")
    bin_validation.add("M2:M1000")

    for col in ws.columns:
        max_length = 0
        column = col[0].column_letter
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except Exception:
                pass
        ws.column_dimensions[column].width = min(max_length + 2, 50)

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="dl_warehouse_inventory_export.xlsx"'
    wb.save(response)
    return response


@any_perm_required("inventory.change_receivingticket")
def receiving_ticket_edit(request, pk):
    ticket = get_object_or_404(ReceivingTicket.objects.prefetch_related("lines__item"), pk=pk)
    if request.method == "POST":
        form = ReceivingTicketEditForm(request.POST, instance=ticket)
        formset = ReceivingLineFormSet(request.POST, instance=ticket)
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                ticket = ReceivingTicket.objects.select_for_update().get(pk=ticket.pk)
                locked_form = ReceivingTicketEditForm(request.POST, instance=ticket)
                if not locked_form.is_valid():
                    raise ValidationError("Receiving ticket changed while it was being edited.")
                locked_form.save()
                for line in list(ticket.lines.select_for_update()):
                    line.delete()
                for line_form in formset:
                    cleaned_data = line_form.cleaned_data
                    if not cleaned_data or cleaned_data.get("DELETE"):
                        continue
                    ReceivingLine.objects.create(
                        ticket=ticket,
                        item=cleaned_data["item"],
                        quantity=cleaned_data["quantity"],
                        shipper=cleaned_data.get("shipper", ""),
                        notes=cleaned_data.get("notes", ""),
                    )
            messages.success(request, f"Updated receiving ticket {ticket.ticket_number}.")
            return redirect("receiving_ticket_print", pk=ticket.pk)
    else:
        form = ReceivingTicketEditForm(instance=ticket)
        formset = ReceivingLineFormSet(instance=ticket)
    return render(request, "inventory/receiving_ticket_edit.html", {"form": form, "formset": formset, "ticket": ticket})




@any_perm_required("inventory.delete_receivingticket")
def receiving_ticket_delete(request, pk):
    ticket = get_object_or_404(ReceivingTicket.objects.prefetch_related("lines__item"), pk=pk)
    if request.method == "POST":
        ticket_number = ticket.ticket_number
        ticket.delete()
        messages.success(request, f"Deleted receiving ticket {ticket_number} and reversed received inventory.")
        return redirect("receiving_log")
    return render(request, "inventory/receiving_ticket_confirm_delete.html", {"ticket": ticket})


@login_required
@any_perm_required("inventory.export_inventory")
def export_inventory_xlsx(request):
    """Export current inventory to .xlsx file."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Inventory"

    headers = [
        "Part #", "FB Part #", "Model #", "Name", "Shipper", "Category", "Description", "Qty", "Unit",
        "BLDG/Room #", "Rack", "Section", "Bin Location", "Low Stock Threshold",
        "Barcode Value", "QR Code Value", "Active", "Updated At",
    ]
    ws.append(headers)

    from openpyxl.styles import Font
    header_font = Font(bold=True)
    for cell in ws[1]:
        cell.font = header_font

    items = InventoryItem.objects.all().order_by("category", "part_number")
    for item in items:
        ws.append([
            item.part_number,
            item.fb_part_number,
            item.model_number,
            item.description or item.name,
            item.shipper,
            item.category,
            item.description,
            item.quantity_on_hand,
            item.unit,
            item.building_room,
            item.rack,
            item.section,
            item.bin_location,
            item.low_stock_threshold,
            item.barcode_value or "",
            item.qr_code_value or "",
            "Yes" if item.active else "No",
            item.updated_at.isoformat() if item.updated_at else "",
        ])

    from openpyxl.worksheet.datavalidation import DataValidation
    rack_validation = DataValidation(type="list", formula1='"' + ','.join(value for value, _ in RACK_CHOICES) + '"', allow_blank=True)
    section_validation = DataValidation(type="list", formula1='"' + ','.join(value for value, _ in SECTION_CHOICES) + '"', allow_blank=True)
    bin_validation = DataValidation(type="list", formula1='"' + ','.join(value for value, _ in BIN_LOCATION_CHOICES) + '"', allow_blank=True)
    ws.add_data_validation(rack_validation)
    ws.add_data_validation(section_validation)
    ws.add_data_validation(bin_validation)
    rack_validation.add("K2:K1000")
    section_validation.add("L2:L1000")
    bin_validation.add("M2:M1000")

    for col in ws.columns:
        max_length = 0
        column = col[0].column_letter
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except Exception:
                pass
        ws.column_dimensions[column].width = min(max_length + 2, 50)

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="dl_warehouse_inventory_export.xlsx"'
    wb.save(response)
    return response


@login_required
@any_perm_required("inventory.print_receivingticket")
def receiving_ticket_print(request, pk):
    """Print-friendly receiving ticket with inventory sheet for marking boxes."""
    ticket = get_object_or_404(
        ReceivingTicket.objects.select_related("created_by").prefetch_related("lines__item"),
        pk=pk
    )
    return render(request, "inventory/receiving_ticket_print.html", {"ticket": ticket})


@login_required
@any_perm_required("inventory.view_receiving_log")
def receiving_log(request):
    """Receiving log with date, PO, vendor, part #, and user filters."""
    tickets = (
        ReceivingTicket.objects
        .select_related("created_by")
        .prefetch_related("lines__item")
        .annotate(total_quantity=Sum("lines__quantity"))
        .order_by("-date")
    )
    
    # Date filters
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    po_search = request.GET.get("po_number", "").strip()
    vendor_search = request.GET.get("vendor", "").strip()
    part_search = request.GET.get("part_number", "").strip()
    user_filter = request.GET.get("user")
    
    if date_from:
        from datetime import datetime
        try:
            date_from_obj = datetime.strptime(date_from, "%Y-%m-%d")
            tickets = tickets.filter(date__gte=date_from_obj)
        except ValueError:
            pass
    
    if date_to:
        from datetime import datetime, timedelta
        try:
            date_to_obj = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            tickets = tickets.filter(date__lt=date_to_obj)
        except ValueError:
            pass
    
    if po_search:
        tickets = tickets.filter(po_number__icontains=po_search)
    
    if vendor_search:
        tickets = tickets.filter(vendor__icontains=vendor_search)

    if part_search:
        tickets = tickets.filter(lines__item__part_number__icontains=part_search)
    
    if user_filter:
        tickets = tickets.filter(created_by_id=user_filter)

    tickets = tickets.distinct()

    vendor_history = []
    exact_part = None
    if part_search:
        exact_part = InventoryItem.objects.filter(part_number__iexact=part_search).first()
        vendor_history = ReceivingLine.objects.filter(item__part_number__icontains=part_search)
        if vendor_search:
            vendor_history = vendor_history.filter(ticket__vendor__icontains=vendor_search)
        vendor_history = (
            vendor_history
            .select_related("ticket", "item")
            .values("ticket__vendor", "ticket__po_number", "item__part_number")
            .annotate(total_quantity=Sum("quantity"))
            .order_by("ticket__vendor", "ticket__po_number", "item__part_number")
        )
    
    # Pagination
    from django.core.paginator import Paginator
    paginator = Paginator(tickets, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    
    users = User.objects.all().order_by("username")
    
    return render(request, "inventory/receiving_log.html", {
        "page_obj": page_obj,
        "date_from": date_from,
        "date_to": date_to,
        "po_search": po_search,
        "vendor_search": vendor_search,
        "part_search": part_search,
        "vendor_history": vendor_history,
        "exact_part": exact_part,
        "user_filter": user_filter,
        "users": users,
    })


@login_required
@any_perm_required("inventory.bulk_adjust_inventory")
def bulk_adjust(request):
    """Adjust multiple items at once with inline new item creation."""
    lines = request.session.get("bulk_adjust_lines", [])

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "add_line":
            form = BulkAdjustForm(request.POST)
            if form.is_valid():
                item = form.cleaned_data.get("item")
                part_number = form.cleaned_data.get("part_number")
                name = form.cleaned_data.get("name")
                quantity_delta = form.cleaned_data.get("quantity_delta")
                notes = form.cleaned_data.get("notes")

                # If new item, create or get it
                if not item:
                    if not part_number or not name:
                        messages.error(request, "Part # and Name are required for new items.")
                        return redirect("bulk_adjust")
                    item, created = InventoryItem.objects.get_or_create(
                        part_number=part_number,
                        defaults={"name": name, "description": name, "shipper": shipper, "quantity_on_hand": 0, "active": True}
                    )
                    if not created:
                        changed_fields = []
                        if item.name != name:
                            item.name = name
                            changed_fields.append("name")
                        if name and item.description != name:
                            item.description = name
                            changed_fields.append("description")
                        if shipper and item.shipper != shipper:
                            item.shipper = shipper
                            changed_fields.append("shipper")
                        if changed_fields:
                            changed_fields.append("updated_at")
                            item.save(update_fields=changed_fields)

                lines.append({
                    "item_id": item.pk,
                    "item_name": item.name,
                    "part_number": item.part_number,
                    "quantity_delta": quantity_delta,
                    "notes": notes,
                })
                request.session["bulk_adjust_lines"] = lines
                messages.success(request, f"Added {item.name} (delta: {quantity_delta:+d})")
                return redirect("bulk_adjust")

        elif action == "commit":
            if not lines:
                messages.error(request, "No adjustment lines to commit.")
                return redirect("bulk_adjust")

            with transaction.atomic():
                for line in lines:
                    item = InventoryItem.objects.get(pk=line["item_id"])
                    delta = line["quantity_delta"]
                    item.quantity_on_hand += delta
                    item.save()
                    InventoryTransaction.objects.create(
                        item=item,
                        transaction_type=InventoryTransaction.TransactionType.ADJUSTMENT,
                        quantity_delta=delta,
                        created_by=request.user,
                        notes=line["notes"] or f"Bulk adjustment: {delta:+d}",
                    )

            messages.success(request, f"Committed {len(lines)} adjustments.")
            del request.session["bulk_adjust_lines"]
            return redirect("bulk_adjust")

        elif action == "clear":
            if "bulk_adjust_lines" in request.session:
                del request.session["bulk_adjust_lines"]
            return redirect("bulk_adjust")

    # Render with current lines
    active_items = InventoryItem.objects.filter(active=True).order_by("category", "name")
    form = BulkAdjustForm()

    # Build display of pending lines
    for line in lines:
        line["item_obj"] = InventoryItem.objects.get(pk=line["item_id"])

    # Compute net change for summary
    net_change = sum(line.get("quantity_delta", 0) for line in lines)

    return render(request, "inventory/bulk_adjust.html", {
        "form": form,
        "lines": lines,
        "net_change": net_change,
        "active_items": active_items,
    })


@login_required
@any_perm_required("inventory.bulk_adjust_inventory")
def inventory_bulk_edit(request):
    """Bulk edit multiple inventory items inline."""
    if request.method == "POST" and request.POST.get("selected_items") and not request.POST.getlist("item_ids"):
        raw_ids = request.POST.get("selected_items", "")
        item_ids = [item_id.strip() for item_id in raw_ids.split(",") if item_id.strip().isdigit()]
        if not item_ids:
            messages.info(request, "No items selected. Please select items from the inventory list.")
            return redirect("inventory_list")

        items = InventoryItem.objects.filter(pk__in=item_ids).order_by("category", "name")
        item_data = [
            {
                "id": item.id,
                "name": item.name,
                "shipper": item.shipper,
                "category": item.category,
                "quantity_on_hand": item.quantity_on_hand,
                "building_room": item.building_room,
                "rack": item.rack,
                "section": item.section,
                "bin_location": item.bin_location,
                "low_stock_threshold": item.low_stock_threshold,
                "part_number": item.part_number,
            }
            for item in items
        ]
        form = InventoryBulkEditForm(item_data=item_data)
    elif request.method == "POST":
        # Save posted edits from the bulk edit form
        item_ids = request.POST.getlist("item_ids")
        if not item_ids:
            messages.error(request, "No items selected for editing.")
            return redirect("inventory_list")
        
        items = InventoryItem.objects.filter(pk__in=item_ids).order_by("category", "name")
        item_data = [
            {
                "id": item.id,
                "name": item.name,
                "shipper": item.shipper,
                "category": item.category,
                "quantity_on_hand": item.quantity_on_hand,
                "building_room": item.building_room,
                "rack": item.rack,
                "section": item.section,
                "bin_location": item.bin_location,
                "low_stock_threshold": item.low_stock_threshold,
                "part_number": item.part_number,
            }
            for item in items
        ]
        
        form = InventoryBulkEditForm(request.POST, item_data=item_data)
        
        if form.is_valid():
            updated_count = 0
            with transaction.atomic():
                for item in items:
                    prefix = f"item_{item.id}"
                    new_name = form.cleaned_data.get(f"{prefix}_name")
                    new_shipper = form.cleaned_data.get(f"{prefix}_shipper", "")
                    new_category = form.cleaned_data.get(f"{prefix}_category")
                    new_qty = form.cleaned_data.get(f"{prefix}_quantity_on_hand")
                    new_building_room = form.cleaned_data.get(f"{prefix}_building_room")
                    new_rack = form.cleaned_data.get(f"{prefix}_rack")
                    new_section = form.cleaned_data.get(f"{prefix}_section")
                    new_bin = form.cleaned_data.get(f"{prefix}_bin_location")
                    if new_bin in STANDALONE_BIN_VALUES:
                        new_rack = ""
                        new_section = ""
                    new_threshold = form.cleaned_data.get(f"{prefix}_low_stock_threshold")
                    
                    # Track changes for transaction log
                    changes = []
                    if item.name != new_name:
                        changes.append(f"Name: '{item.name}' → '{new_name}'")
                    if item.shipper != new_shipper:
                        changes.append(f"Shipper: '{item.shipper}' → '{new_shipper}'")
                    if item.category != new_category:
                        changes.append(f"Category: '{item.category}' → '{new_category}'")
                    if item.quantity_on_hand != new_qty:
                        changes.append(f"Qty: {item.quantity_on_hand} → {new_qty}")
                    if item.building_room != new_building_room:
                        changes.append(f"Building/Room: '{item.building_room}' → '{new_building_room}'")
                    if item.rack != new_rack:
                        changes.append(f"Rack: '{item.rack}' → '{new_rack}'")
                    if item.section != new_section:
                        changes.append(f"Section: '{item.section}' → '{new_section}'")
                    if item.bin_location != new_bin:
                        changes.append(f"Bin: '{item.bin_location}' → '{new_bin}'")
                    if item.low_stock_threshold != new_threshold:
                        changes.append(f"Low Stock: {item.low_stock_threshold} → {new_threshold}")
                    
                    if changes:
                        old_qty = item.quantity_on_hand
                        qty_changed = old_qty != new_qty
                        item.name = new_name
                        item.shipper = new_shipper
                        item.category = new_category
                        item.building_room = new_building_room
                        item.rack = new_rack
                        item.section = new_section
                        item.bin_location = new_bin
                        item.low_stock_threshold = new_threshold or 0
                        # Persist non-quantity fields first so the audit row references final metadata
                        item.save()

                        if qty_changed:
                            # Route the quantity change through adjust_quantity so the
                            # ledger entry is created atomically with select_for_update.
                            item.adjust_quantity(
                                new_qty - old_qty,
                                transaction_type=InventoryTransaction.TransactionType.ADJUSTMENT,
                                user=request.user,
                                notes=f"Bulk edit: {', '.join(changes)}",
                            )
                        updated_count += 1
            
            if updated_count:
                messages.success(request, f"Updated {updated_count} item(s).")
            else:
                messages.info(request, "No changes detected.")
            return redirect("inventory_list")
    else:
        # GET - show form with selected items
        item_ids = request.GET.getlist("items")
        if not item_ids:
            messages.info(request, "No items selected. Please select items from the inventory list.")
            return redirect("inventory_list")
        
        items = InventoryItem.objects.filter(pk__in=item_ids).order_by("category", "name")
        item_data = [
            {
                "id": item.id,
                "name": item.name,
                "shipper": item.shipper,
                "category": item.category,
                "quantity_on_hand": item.quantity_on_hand,
                "building_room": item.building_room,
                "rack": item.rack,
                "section": item.section,
                "bin_location": item.bin_location,
                "low_stock_threshold": item.low_stock_threshold,
                "part_number": item.part_number,
            }
            for item in items
        ]
        
        form = InventoryBulkEditForm(item_data=item_data)
    
    bulk_rows = []
    for item in items:
        prefix = f"item_{item.id}"
        bulk_rows.append({
            "item": item,
            "name": form[f"{prefix}_name"],
            "shipper": form[f"{prefix}_shipper"],
            "id": form[f"{prefix}_id"],
            "category": form[f"{prefix}_category"],
            "part_number": form[f"{prefix}_part_number"],
            "quantity_on_hand": form[f"{prefix}_quantity_on_hand"],
            "building_room": form[f"{prefix}_building_room"],
            "rack": form[f"{prefix}_rack"],
            "section": form[f"{prefix}_section"],
            "bin_location": form[f"{prefix}_bin_location"],
            "low_stock_threshold": form[f"{prefix}_low_stock_threshold"],
        })
    return render(request, "inventory/inventory_bulk_edit.html", {
        "form": form,
        "items": items,
        "bulk_rows": bulk_rows,
    })


@login_required
@any_perm_required("inventory.view_inventorytransaction")
def transaction_history(request):
    """Global transaction history with filtering and pagination."""
    from django.core.paginator import Paginator
    
    transactions = _visible_inventory_transactions(
        request,
        InventoryTransaction.objects.select_related("item", "created_by", "pick_ticket"),
    ).order_by("-created_at")
    
    # Filters
    tx_type = request.GET.get("type")
    if tx_type:
        transactions = transactions.filter(transaction_type=tx_type)
    
    item_id = request.GET.get("item")
    if item_id:
        transactions = transactions.filter(item_id=item_id)
    
    user_id = request.GET.get("user")
    if user_id:
        transactions = transactions.filter(created_by_id=user_id)
    
    date_from = request.GET.get("date_from")
    if date_from:
        transactions = transactions.filter(created_at__date__gte=date_from)
    
    date_to = request.GET.get("date_to")
    if date_to:
        transactions = transactions.filter(created_at__date__lte=date_to)
    
    # Pagination
    paginator = Paginator(transactions, 50)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    
    # Get filter options
    all_items = InventoryItem.objects.filter(active=True).order_by("name")
    all_users = User.objects.filter(is_active=True).order_by("username")
    tx_types = InventoryTransaction.TransactionType.choices
    
    return render(request, "inventory/transaction_history.html", {
        "page_obj": page_obj,
        "tx_type": tx_type,
        "item_id": item_id,
        "user_id": user_id,
        "date_from": date_from,
        "date_to": date_to,
        "all_items": all_items,
        "all_users": all_users,
        "tx_types": tx_types,
    })


@login_required
@any_perm_required("inventory.view_inventorytransaction")
def item_transaction_history(request, pk):
    """Transaction history for a specific item."""
    from django.core.paginator import Paginator
    
    item = get_object_or_404(InventoryItem, pk=pk)
    transactions = _visible_inventory_transactions(
        request, item.transactions.select_related("created_by", "pick_ticket")
    ).order_by("-created_at")
    
    paginator = Paginator(transactions, 50)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    
    return render(request, "inventory/item_transaction_history.html", {
        "item": item,
        "page_obj": page_obj,
    })


@login_required
@any_perm_required("inventory.view_inventorytransaction")
def export_transactions_csv(request):
    """Export transaction history as CSV."""
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="warehouse_transactions.csv"'
    
    writer = csv.writer(response)
    writer.writerow(["Date", "Type", "Item", "Qty Delta", "User", "Pick Ticket", "Notes"])
    
    transactions = _visible_inventory_transactions(
        request,
        InventoryTransaction.objects.select_related("item", "created_by", "pick_ticket"),
    ).order_by("-created_at")
    
    for tx in transactions:
        writer.writerow([
            tx.created_at.strftime("%Y-%m-%d %H:%M"),
            tx.get_transaction_type_display(),
            f"{tx.item.name} ({tx.item.part_number})",
            tx.quantity_delta,
            tx.created_by.username if tx.created_by else "System",
            tx.pick_ticket.ticket_number if tx.pick_ticket else "",
            tx.notes or "",
        ])

    return response


# ============================================================================
# Approval Queue Views
# ============================================================================

@login_required
@any_perm_required("inventory.view_approvalrequest")
def approval_queue(request):
    """Manager review queue for pending workflow approvals."""
    status_filter = request.GET.get("status", ApprovalRequest.Status.PENDING)
    requests = ApprovalRequest.objects.select_related("requested_by", "reviewed_by")
    if status_filter in ApprovalRequest.Status.values:
        requests = requests.filter(status=status_filter)
    counts = {
        "pending": ApprovalRequest.objects.filter(status=ApprovalRequest.Status.PENDING).count(),
        "approved": ApprovalRequest.objects.filter(status=ApprovalRequest.Status.APPROVED).count(),
        "rejected": ApprovalRequest.objects.filter(status=ApprovalRequest.Status.REJECTED).count(),
    }
    return render(request, "inventory/approval_queue.html", {
        "approval_requests": requests,
        "active_status": status_filter,
        "counts": counts,
    })


@login_required
@any_perm_required("inventory.change_approvalrequest")
def approval_request_review(request, pk, action):
    """Approve or reject a pending workflow request."""
    if request.method != "POST":
        return redirect("approval_queue")
    approval = get_object_or_404(ApprovalRequest, pk=pk, status=ApprovalRequest.Status.PENDING)
    status = {
        "approve": ApprovalRequest.Status.APPROVED,
        "reject": ApprovalRequest.Status.REJECTED,
    }.get(action)
    if not status:
        messages.error(request, "Unknown approval action.")
        return redirect("approval_queue")
    approval.mark_reviewed(status, request.user, request.POST.get("review_notes", ""))
    messages.success(request, f"Request {approval.get_status_display().lower()}.")
    return redirect("approval_queue")


# ============================================================================
# Settings / User Management Views
# ============================================================================

@login_required
@settings_access_required
def settings(request):
    """Main settings page with tabs for user, security, and app settings."""
    active_tab = request.GET.get("tab", "general")
    context = {"active_tab": active_tab}
    if active_tab == "users" and request.user.is_superuser:
        context["users"] = User.objects.all().order_by("-is_superuser", "username")
    return render(request, "inventory/settings.html", context)


@login_required
@any_perm_required("inventory.manage_users")
def user_management(request):
    """User management list for superusers — shows groups per user."""
    users = User.objects.all().order_by("-is_superuser", "username")
    # Prefetch groups for display
    users = users.prefetch_related("groups")
    return render(request, "inventory/settings.html", {
        "active_tab": "users",
        "users": users,
    })


# ============================================================================
# Group Management Views
# ============================================================================

@login_required
@any_perm_required("inventory.manage_groups", "inventory.manage_group_permissions")
def group_management(request):
    """Redirect to user management — groups/permissions are now managed per-user."""
    return redirect("user_management")


@login_required
@any_perm_required("inventory.manage_groups")
def group_update(request, group_name):
    """Redirect to user management — groups/permissions are now managed per-user."""
    return redirect("user_management")


def _safe_settings_next(request):
    """Return an internal settings/user page redirect target after group CRUD."""
    next_url = request.POST.get("next") or request.GET.get("next") or reverse("user_management")
    if not next_url.startswith("/") or next_url.startswith("//"):
        return reverse("user_management")
    return next_url


@login_required
@any_perm_required("inventory.manage_groups")
def group_create(request):
    """Create a Django auth group from the merged user edit page."""
    if request.method != "POST":
        return redirect("user_management")
    name = request.POST.get("name", "").strip()
    if not name:
        messages.error(request, "Group name is required.")
        return redirect(_safe_settings_next(request))
    group, created = Group.objects.get_or_create(name=name)
    if created:
        messages.success(request, f"Group '{group.name}' created.")
    else:
        messages.info(request, f"Group '{group.name}' already exists.")
    return redirect(_safe_settings_next(request))


@login_required
@any_perm_required("inventory.manage_groups")
def group_rename(request, pk):
    """Rename an auth group from the merged user edit page."""
    if request.method != "POST":
        return redirect("user_management")
    group = get_object_or_404(Group, pk=pk)
    old_name = group.name
    form = GroupRenameForm(request.POST, instance=group)
    if not form.is_valid():
        messages.error(request, "Group could not be renamed: " + " ".join(form.errors.get("name", ["invalid name"])))
        return redirect(_safe_settings_next(request))
    try:
        group = form.save()
    except IntegrityError:
        messages.error(request, "A group with that name already exists.")
        return redirect(_safe_settings_next(request))
    messages.success(request, f"Group '{old_name}' renamed to '{group.name}'.")
    return redirect(_safe_settings_next(request))


@login_required
@any_perm_required("inventory.manage_groups")
def group_delete(request, pk):
    """Delete an auth group from the merged user edit page."""
    if request.method != "POST":
        return redirect("user_management")
    group = get_object_or_404(Group, pk=pk)
    name = group.name
    group.delete()
    messages.success(request, f"Group '{name}' deleted.")
    return redirect(_safe_settings_next(request))


def _group_inventory_permissions():
    """Return inventory app permissions grouped by model for settings pages."""
    all_perms = Permission.objects.filter(content_type__app_label="inventory").select_related("content_type").order_by(
        "content_type__model", "codename"
    )
    grouped_perms = {}
    for p in all_perms:
        grouped_perms.setdefault(p.content_type.model, []).append(p)
    return grouped_perms


@login_required
@any_perm_required("inventory.manage_group_permissions")
def group_permissions(request, pk):
    """Edit permissions assigned to a Django auth group."""
    group = get_object_or_404(Group, pk=pk)
    grouped_perms = _group_inventory_permissions()
    if request.method == "POST":
        perm_ids = request.POST.getlist("permissions")
        selected_inventory = Permission.objects.filter(pk__in=perm_ids, content_type__app_label="inventory")
        existing_other_apps = group.permissions.exclude(content_type__app_label="inventory")
        group.permissions.set([*existing_other_apps, *selected_inventory])
        messages.success(request, f"Permissions updated for group '{group.name}'.")
        return redirect("user_management")

    assigned_permission_ids = set(group.permissions.values_list("id", flat=True))
    return render(request, "inventory/group_permissions.html", {
        "group": group,
        "grouped_perms": grouped_perms,
        "assigned_permission_ids": assigned_permission_ids,
    })


@login_required
@any_perm_required("inventory.manage_users")
def user_create(request):
    """Create a new user with group and permission assignments."""
    all_groups = Group.objects.all().order_by("name")
    grouped_perms = _group_inventory_permissions()

    if request.method == "POST":
        form = WarehouseUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            # Assign groups
            group_ids = request.POST.getlist("groups")
            if group_ids:
                user.groups.set(Group.objects.filter(pk__in=group_ids))
            # Assign individual permissions
            perm_ids = request.POST.getlist("permissions")
            if perm_ids:
                user.user_permissions.set(Permission.objects.filter(pk__in=perm_ids))
            messages.success(request, f"User '{user.username}' created successfully.")
            return redirect("user_management")
    else:
        form = WarehouseUserCreationForm()
    return render(request, "inventory/user_edit_merged.html", {
        "form": form,
        "title": "Create User",
        "all_groups": all_groups,
        "user_group_ids": set(),
        "grouped_perms": grouped_perms,
        "user_perm_ids": set(),
    })


@login_required
@any_perm_required("inventory.manage_users")
def user_edit(request, pk):
    """Edit an existing user with group and permission assignments."""
    user = get_object_or_404(User, pk=pk)
    editing_self = user.pk == request.user.pk
    protected_status = (user.is_staff, user.is_superuser, user.is_active)
    all_groups = Group.objects.all().order_by("name")
    grouped_perms = _group_inventory_permissions()

    if request.method == "POST":
        form = WarehouseUserChangeForm(request.POST, instance=user)
        if form.is_valid():
            user = form.save(commit=False)
            # Disabled self-edit controls are not submitted by browsers. Preserve
            # them so changing your own password cannot deactivate/lock out you.
            if editing_self:
                user.is_staff, user.is_superuser, user.is_active = protected_status
            password = request.POST.get("password", "")
            if password:
                user.set_password(password)
            user.save()
            if editing_self and password:
                update_session_auth_hash(request, user)
            # Self-edit group controls are disabled, so preserve existing groups.
            if not editing_self:
                group_ids = request.POST.getlist("groups")
                user.groups.set(Group.objects.filter(pk__in=group_ids))
            # Assign individual permissions
            perm_ids = request.POST.getlist("permissions")
            user.user_permissions.set(Permission.objects.filter(pk__in=perm_ids))
            messages.success(request, f"User '{user.username}' updated successfully.")
            return redirect("user_management")
    else:
        form = WarehouseUserChangeForm(instance=user)

    user_group_ids = set(user.groups.values_list("id", flat=True))
    user_perm_ids = set(user.user_permissions.values_list("id", flat=True))

    return render(request, "inventory/user_edit_merged.html", {
        "form": form,
        "title": f"Edit User: {user.username}",
        "user_obj": user,
        "all_groups": all_groups,
        "user_group_ids": user_group_ids,
        "grouped_perms": grouped_perms,
        "user_perm_ids": user_perm_ids,
    })


@login_required
@any_perm_required("inventory.manage_users")
def user_delete(request, pk):
    """Delete a user."""
    user = get_object_or_404(User, pk=pk)
    if user == request.user:
        messages.error(request, "You cannot delete your own account.")
        return redirect("user_management")
    if request.method == "POST":
        username = user.username
        user.delete()
        messages.success(request, f"User '{username}' deleted.")
        return redirect("user_management")
    return render(request, "inventory/user_confirm_delete.html", {
        "user_obj": user,
    })


@login_required
def change_password(request):
    """Change user password."""
    if request.method == "POST":
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, "Password changed successfully.")
            return redirect("settings")
    else:
        form = PasswordChangeForm(request.user)
    return render(request, "inventory/change_password.html", {
        "form": form,
    })


@login_required
@any_perm_required("inventory.view_inventoryitem")
def item_images(request, item_id):
    item = get_object_or_404(InventoryItem, pk=item_id)
    return render(request, "inventory/item_images.html", {
        "item": item,
        "images": item.images.all().order_by("-is_primary", "-uploaded_at"),
    })


@login_required
@any_perm_required("inventory.change_inventoryitem")
def item_image_add(request, item_id):
    item = get_object_or_404(InventoryItem, pk=item_id)
    if request.method == "POST":
        image = request.FILES.get("image")
        caption = request.POST.get("caption", "")
        is_primary = request.POST.get("is_primary") == "on"
        error = _validate_uploaded_file(
            image,
            allowed_mime_types=django_settings.ITEM_IMAGE_ALLOWED_MIME_TYPES,
            max_bytes=django_settings.ITEM_IMAGE_MAX_BYTES,
        )
        if error:
            messages.error(request, error)
        else:
            if is_primary:
                item.images.update(is_primary=False)
            ItemImage.objects.create(
                item=item,
                image=image,
                caption=caption,
                is_primary=is_primary,
                content_type=(image.content_type or "").lower().split(";")[0].strip(),
                uploaded_by=request.user,
            )
            messages.success(request, "Image added.")
    return redirect("item_images", item_id=item.pk)


@login_required
@any_perm_required("inventory.change_inventoryitem")
def item_image_delete(request, item_id, image_id):
    item = get_object_or_404(InventoryItem, pk=item_id)
    image = get_object_or_404(ItemImage, pk=image_id, item=item)
    if request.method == "POST":
        image.delete()
        messages.success(request, "Image deleted.")
    return redirect("item_images", item_id=item.pk)


@login_required
@any_perm_required("inventory.change_inventoryitem")
def item_documents(request, item_id):
    item = get_object_or_404(InventoryItem, pk=item_id)
    return render(request, "inventory/item_documents.html", {
        "item": item,
        "documents": item.documents.all().order_by("-uploaded_at"),
    })


@login_required
@any_perm_required("inventory.change_inventoryitem")
def item_document_add(request, item_id):
    item = get_object_or_404(InventoryItem, pk=item_id)
    if request.method == "POST":
        document = request.FILES.get("document")
        description = request.POST.get("description", "")
        error = _validate_uploaded_file(
            document,
            allowed_mime_types=django_settings.ITEM_DOCUMENT_ALLOWED_MIME_TYPES,
            max_bytes=django_settings.ITEM_DOCUMENT_MAX_BYTES,
        )
        if error:
            messages.error(request, error)
        else:
            ItemDocument.objects.create(
                item=item,
                document=document,
                original_filename=document.name,
                description=description,
                content_type=(document.content_type or "").lower().split(";")[0].strip(),
                uploaded_by=request.user,
            )
            messages.success(request, "Document added.")
    return redirect("item_documents", item_id=item.pk)


@login_required
@any_perm_required("inventory.change_inventoryitem")
def item_document_delete(request, item_id, doc_id):
    item = get_object_or_404(InventoryItem, pk=item_id)
    document = get_object_or_404(ItemDocument, pk=doc_id, item=item)
    if request.method == "POST":
        document.delete()
        messages.success(request, "Document deleted.")
    return redirect("item_documents", item_id=item.pk)


@login_required
def export_center(request):
    categories = CategoryChoices.choices
    shippers = (
        InventoryItem.objects.exclude(shipper__isnull=True)
        .exclude(shipper__exact="")
        .order_by("shipper")
        .values_list("shipper", flat=True)
        .distinct()
    )

    available_fields = CategoryChoices.choices

    selected_category = (request.GET.get("category") or "").strip()
    selected_stock = (request.GET.get("stock") or "").strip()
    selected_active = (request.GET.get("active") or "").strip()
    selected_part_number = (request.GET.get("part_number") or "").strip()
    selected_po_number = (request.GET.get("po_number") or "").strip()
    selected_shipper = (request.GET.get("shipper") or "").strip()
    selected_fields = request.GET.getlist("fields")
    export_format = (request.GET.get("format") or "").strip().lower()

    if export_format in ("xlsx", "csv"):
        items = InventoryItem.objects.all()
        if selected_fields:
            items = items.filter(category__in=selected_fields)
        if selected_category and not selected_fields:
            items = items.filter(category=selected_category)
        if selected_part_number:
            items = items.filter(part_number__icontains=selected_part_number)
        if selected_po_number:
            items = items.filter(
                receivingline__ticket__po_number__icontains=selected_po_number
            ).distinct()
        if selected_shipper:
            items = items.filter(shipper__icontains=selected_shipper)
        if selected_stock == "low":
            items = items.filter(low_stock_threshold__gt=0, quantity_on_hand__lte=models.F("low_stock_threshold"))
        elif selected_stock == "zero":
            items = items.filter(quantity_on_hand=0)
        elif selected_stock == "negative":
            items = items.filter(quantity_on_hand__lt=0)
        elif selected_stock == "positive":
            items = items.filter(quantity_on_hand__gt=0)
        if selected_active == "yes":
            items = items.filter(active=True)
        elif selected_active == "no":
            items = items.filter(active=False)
        items = items.order_by("category", "part_number")

        export_headers = [
            "Part #",
            "Name",
            "Category",
            "Qty",
            "Unit",
            "Building/Room",
            "Rack",
            "Section",
            "Bin Location",
            "Shipper",
            "Active",
        ]
        export_fields = [
            "part_number",
            "name",
            "category",
            "quantity_on_hand",
            "unit",
            "building_room",
            "rack",
            "section",
            "bin_location",
            "shipper",
            "active",
        ]

        def _row(item):
            row = []
            for f in export_fields:
                if f == "name":
                    row.append(item.description or item.name)
                elif f == "active":
                    row.append("Yes" if item.active else "No")
                else:
                    value = getattr(item, f, "")
                    row.append(value if value is not None else "")
            return row

        if export_format == "xlsx":
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Inventory"
            ws.append(export_headers)
            from openpyxl.styles import Font
            header_font = Font(bold=True)
            for cell in ws[1]:
                cell.font = header_font
            for item in items:
                ws.append(_row(item))

            from openpyxl.worksheet.datavalidation import DataValidation
            rack_validation = DataValidation(type="list", formula1='"' + ','.join(value for value, _ in RACK_CHOICES) + '"', allow_blank=True)
            section_validation = DataValidation(type="list", formula1='"' + ','.join(value for value, _ in SECTION_CHOICES) + '"', allow_blank=True)
            bin_validation = DataValidation(type="list", formula1='"' + ','.join(value for value, _ in BIN_LOCATION_CHOICES) + '"', allow_blank=True)
            ws.add_data_validation(rack_validation)
            ws.add_data_validation(section_validation)
            ws.add_data_validation(bin_validation)
            rack_validation.add("G2:G1000")
            section_validation.add("H2:H1000")
            bin_validation.add("I2:I1000")

            for col in ws.columns:
                max_length = 0
                column = col[0].column_letter
                for cell in col:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except Exception:
                        pass
                ws.column_dimensions[column].width = min(max_length + 2, 50)

            response = HttpResponse(
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            response["Content-Disposition"] = 'attachment; filename="warehouse_export.xlsx"'
            wb.save(response)
            return response

        if export_format == "csv":
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = 'attachment; filename="warehouse_export.csv"'
            writer = csv.writer(response)
            writer.writerow(export_headers)
            for item in items:
                writer.writerow(_row(item))
            return response

    return render(request, "inventory/export_center.html", {
        "categories": categories,
        "shippers": shippers,
        "selected_category": selected_category,
        "selected_stock": selected_stock,
        "selected_active": selected_active,
        "selected_part_number": selected_part_number,
        "selected_po_number": selected_po_number,
        "selected_shipper": selected_shipper,
        "selected_fields": selected_fields,
        "available_fields": available_fields,
        "export_info": "",
    })


# ---------------------------------------------------------------------------
# Location-based views (per-rack/section/bin QR codes)
# ---------------------------------------------------------------------------

def _parse_location_key(key):
    """Parse and validate a location key like 'B-11-2'."""
    parts = key.strip().split("-")
    if len(parts) == 3:
        rack, section, bin_location = parts
        rack = rack.upper()
        section = section.zfill(2) if section.isdigit() else section
        bin_location = bin_location.zfill(2) if bin_location.isdigit() else bin_location
        valid_racks = {value for value, _label in RACK_CHOICES}
        valid_sections = {value for value, _label in SECTION_CHOICES}
        valid_bins = {value for value, _label in BIN_LOCATION_CHOICES}
        if rack in valid_racks and section in valid_sections and bin_location in valid_bins:
            return {"rack": rack, "section": section, "bin_location": bin_location}
    return None


@login_required
def location_list(request):
    """Show occupied rack sections; each printable section includes all six bins."""
    from django.db.models import Count
    locations_data = (
        InventoryItem.objects.filter(active=True)
        .exclude(rack="")
        .exclude(section="")
        .exclude(bin_location="")
        .values("rack", "section", "bin_location")
        .annotate(item_count=Count("id"))
        .order_by("rack", "section", "bin_location")
    )

    section_map = {}
    locations = []
    for loc in locations_data:
        rack = str(loc["rack"]).upper()
        section_number = str(loc["section"]).zfill(2)
        bin_number = str(loc["bin_location"]).zfill(2)
        section_key = f"{rack}-{section_number}"
        location_key = f"{section_key}-{bin_number}"
        location_url = f"https://bbx.rplwms.com/locations/{location_key}/"
        location = {
            "key": location_key,
            "rack": rack,
            "section": section_number,
            "bin_location": bin_number,
            "item_count": loc["item_count"],
            "location_url": location_url,
            "qr_url": location_url,
            "qr_b64": generate_qr_code(location_url),
        }
        locations.append(location)
        section = section_map.setdefault(section_key, {
            "key": section_key,
            "range_label": f"{section_key}-01 through {section_key}-06",
            "item_count": 0,
            "occupied_bin_count": 0,
            "bins": [],
        })
        section["item_count"] += loc["item_count"]
        section["occupied_bin_count"] += 1
        section["bins"].append({
            "key": location_key,
            "location_url": location_url,
            "item_count": loc["item_count"],
        })

    return render(request, "inventory/location_list.html", {
        "sections": list(section_map.values()),
        # Retained for integrations that consume the location-list context.
        "locations": locations,
    })


@login_required
def location_qr_print(request):
    """Render complete six-bin rack sections, one section per physical page."""
    selected_sections = []

    # Current UI submits rack sections. Accept old location links/bookmarks too,
    # expanding any selected bin to its complete six-position section.
    raw_values = request.GET.getlist("section") + request.GET.getlist("location")
    for raw_value in raw_values:
        for raw_key in raw_value.split(","):
            key = raw_key.strip().upper()
            parts = key.split("-")
            if len(parts) == 3:
                key = "-".join(parts[:2])
            parsed = _parse_location_key(f"{key}-01")
            if not parsed:
                continue
            section_key = f"{parsed['rack']}-{parsed['section']}"
            if section_key not in selected_sections:
                selected_sections.append(section_key)

    pages = []
    for page_number, section_key in enumerate(selected_sections, start=1):
        labels = []
        for bin_value, _label in BIN_LOCATION_CHOICES:
            location_key = f"{section_key}-{bin_value}"
            location_url = f"https://bbx.rplwms.com/locations/{location_key}/"
            labels.append({
                "key": location_key,
                "qr_b64": generate_qr_code(location_url),
            })
        pages.append({
            "number": page_number,
            "total": len(selected_sections),
            "labels": labels,
        })

    if not pages:
        messages.error(request, "Select at least one rack section to print.")
        return redirect("location_list")

    return render(request, "inventory/location_qr_print.html", {"pages": pages})


@login_required
def location_detail(request, location_key):
    """Show all items at a specific location. Lead+ sees add/remove controls."""
    parsed = _parse_location_key(location_key)
    if not parsed:
        messages.error(request, f"Invalid location: {location_key}")
        return redirect("location_list")

    items = InventoryItem.objects.filter(
        rack=parsed["rack"],
        section=parsed["section"],
        bin_location=parsed["bin_location"],
        active=True,
    ).order_by("part_number")

    is_lead = request.user.is_superuser or request.user.has_perm("inventory.manage_storage_locations")

    return render(request, "inventory/location_detail.html", {
        "location_key": location_key,
        "items": items,
        "is_lead": is_lead,
        "parsed": parsed,
        "qr_url": f"https://bbx.rplwms.com/locations/{location_key}/",
        "location_url": f"https://bbx.rplwms.com/locations/{location_key}/",
        "qr_b64": generate_qr_code(f"https://bbx.rplwms.com/locations/{location_key}/"),
        "STANDALONE_BIN_VALUES": set(),
    })


@login_required
@any_perm_required("inventory.manage_storage_locations")
def location_add_item(request, location_key):
    """Add an item to a location (Lead+ only) — assigns rack/section/bin to item."""
    is_authorized = request.user.is_superuser or request.user.has_perm("inventory.manage_storage_locations")
    if not is_authorized:
        messages.error(request, "Only Leads and Managers can manage location inventory.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    parsed = _parse_location_key(location_key)
    if not parsed:
        messages.error(request, f"Invalid location: {location_key}")
        return redirect("location_list")

    if request.method == "POST":
        item_id = request.POST.get("item_id")
        if item_id:
            item = get_object_or_404(InventoryItem, pk=item_id)
            item.rack = parsed["rack"]
            item.section = parsed["section"]
            item.bin_location = parsed["bin_location"]
            item.save(update_fields=["rack", "section", "bin_location", "updated_at"])
            messages.success(request, f"{item.part_number} — {item.name} assigned to {location_key}")
        return redirect("location_detail", location_key=location_key)

    assignable_items = InventoryItem.objects.filter(active=True).exclude(
        rack=parsed["rack"], section=parsed["section"], bin_location=parsed["bin_location"]
    ).order_by("part_number")

    return render(request, "inventory/location_manage.html", {
        "location_key": location_key,
        "assignable_items": assignable_items,
        "action": "add",
    })


@login_required
@any_perm_required("inventory.manage_storage_locations")
def location_remove_item(request, location_key, item_pk):
    """Remove an item from a location (Lead+ only) — clears rack/section/bin."""
    is_authorized = request.user.is_superuser or request.user.has_perm("inventory.manage_storage_locations")
    if not is_authorized:
        messages.error(request, "Only Leads and Managers can manage location inventory.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    parsed = _parse_location_key(location_key)
    if not parsed:
        messages.error(request, f"Invalid location: {location_key}")
        return redirect("location_list")

    item = get_object_or_404(InventoryItem, pk=item_pk)
    item.rack = ""
    item.section = ""
    item.bin_location = ""
    item.save(update_fields=["rack", "section", "bin_location", "updated_at"])
    messages.success(request, f"{item.part_number} — {item.name} removed from {location_key}")
    return redirect("location_detail", location_key=location_key)


# ---- Material request portal and WMS board ----


def _visible_material_requests(request, queryset=None):
    """Owner-scope requests unless the user has explicit warehouse-wide access."""
    queryset = queryset if queryset is not None else MaterialRequest.objects.all()
    if not request.user.has_perm("inventory.view_all_materialrequests"):
        queryset = queryset.filter(creator=request.user)
    return queryset


def _request_lines_from_formset(formset):
    return [
        {
            "item": form.cleaned_data["item"],
            "quantity": form.cleaned_data["quantity"],
            "notes": form.cleaned_data.get("notes", ""),
        }
        for form in formset.forms
        if form.cleaned_data and not form.cleaned_data.get("DELETE")
    ]


@login_required
@request_portal_access_required
@any_perm_required("inventory.view_materialrequest")
def material_request_board(request):
    requests = _visible_material_requests(
        request, MaterialRequest.objects.filter(archived_at__isnull=True)
    ).select_related("pick_ticket", "creator", "assigned_to").prefetch_related(
        "lines__item"
    ).annotate(
        line_count=Count("lines", distinct=True), total_quantity=Sum("lines__quantity")
    )
    is_warehouse = request.user.has_perm("inventory.view_all_materialrequests")
    requested_view = request.GET.get("view", "").lower()
    view_mode = "queue" if is_warehouse and requested_view not in {"kanban", "board"} else "kanban"
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").upper()
    if query:
        requests = requests.filter(
            Q(request_number__icontains=query)
            | Q(pick_ticket__ticket_number__icontains=query)
            | Q(requestor_name__icontains=query)
            | Q(building_room__icontains=query)
            | Q(location__icontains=query)
        )
    if status in PickTicket.Status.values:
        requests = requests.filter(pick_ticket__status=status)

    assignment_filter = request.GET.get("assignment", request.GET.get("assigned", "")).lower()
    focus_filter = request.GET.get("focus", "actionable").lower()
    warehouse_assignees = []
    if is_warehouse:
        warehouse_assignees = [
            user
            for user in User.objects.filter(is_active=True).order_by(
                "first_name", "last_name", "username"
            )
            if user.has_perm("inventory.view_all_materialrequests")
        ]
        if assignment_filter == "mine":
            requests = requests.filter(assigned_to=request.user)
        elif assignment_filter == "unassigned":
            requests = requests.filter(assigned_to__isnull=True)

        now = timezone.now()
        today = timezone.localdate(now)
        if view_mode == "queue":
            if not status:
                requests = requests.exclude(pick_ticket__status=PickTicket.Status.CLOSED)
            if focus_filter == "urgent":
                requests = requests.filter(urgent=True)
            elif focus_filter == "overdue":
                requests = requests.filter(delivery_at__lt=now)
            elif focus_filter == "due_today":
                requests = requests.filter(delivery_at__date=today)
            elif focus_filter == "awaiting_requester":
                requests = requests.filter(
                    pick_ticket__status=PickTicket.Status.RECEIVED,
                    delivery_acceptance_confirmed_at__isnull=True,
                    delivery_not_ready_at__isnull=True,
                )
            elif focus_filter == "not_ready":
                requests = requests.filter(delivery_not_ready_at__isnull=False)

            queue_rows = list(requests)
            for material_request in queue_rows:
                due = material_request.delivery_at
                if material_request.urgent:
                    priority, label = 0, "Urgent"
                elif material_request.delivery_not_ready_at:
                    priority, label = 1, "Needs reschedule"
                elif due and due < now:
                    priority, label = 2, "Overdue"
                elif due and timezone.localdate(due) == today:
                    priority, label = 3, "Due today"
                elif (
                    material_request.pick_ticket.status == PickTicket.Status.RECEIVED
                    and not material_request.delivery_acceptance_confirmed_at
                ):
                    priority, label = 4, "Awaiting requester"
                else:
                    priority, label = 5, "Scheduled"
                material_request.queue_priority = priority
                material_request.queue_priority_label = label
            far_future = datetime.max.replace(tzinfo=timezone.get_current_timezone())
            requests = sorted(
                queue_rows,
                key=lambda row: (
                    row.queue_priority,
                    row.delivery_at or far_future,
                    row.created_at,
                ),
            )

    context = {
        "material_requests": requests,
        "query": query,
        "status_filter": status,
        "statuses": PickTicket.Status.choices,
        "is_warehouse": is_warehouse,
        "view_mode": view_mode,
        "assignment_filter": assignment_filter,
        "focus_filter": focus_filter,
        "warehouse_assignees": warehouse_assignees,
    }
    if request.GET.get("partial") == "1":
        template = (
            "inventory/_material_request_queue.html"
            if view_mode == "queue"
            else "inventory/_material_request_columns.html"
        )
        response = render(request, template, context)
        response["Cache-Control"] = "no-store"
        return response
    return render(request, "inventory/material_request_board.html", context)


@login_required
@request_portal_access_required
@any_perm_required("inventory.view_all_materialrequests")
@require_POST
def material_request_assign(request, pk):
    from .services import assign_material_request

    material_request = get_object_or_404(
        MaterialRequest.objects.select_related("pick_ticket"), pk=pk
    )
    assignee_id = request.POST.get("assigned_to", "").strip()
    assignee = None
    if assignee_id:
        assignee = get_object_or_404(User.objects.filter(is_active=True), pk=assignee_id)
        if not assignee.has_perm("inventory.view_all_materialrequests"):
            raise PermissionDenied
    material_request, changed = assign_material_request(
        material_request, assignee=assignee, actor=request.user
    )
    if changed:
        messages.success(request, f"Assignment updated for {material_request.request_number}.")
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = reverse("material_request_board")
    return redirect(next_url)


@login_required
@request_portal_access_required
@any_perm_required("inventory.view_materialrequest")
def material_request_archive(request):
    archived = _visible_material_requests(
        request, MaterialRequest.objects.filter(archived_at__isnull=False)
    ).select_related("pick_ticket", "creator").annotate(
        line_count=Count("lines", distinct=True), total_quantity=Sum("lines__quantity")
    )
    query = request.GET.get("q", "").strip()
    if query:
        archived = archived.filter(
            Q(request_number__icontains=query)
            | Q(pick_ticket__ticket_number__icontains=query)
            | Q(requestor_name__icontains=query)
            | Q(building_room__icontains=query)
            | Q(location__icontains=query)
        )
    return render(request, "inventory/material_request_archive.html", {
        "material_requests": archived, "query": query,
    })


@login_required
@request_portal_access_required
@all_perms_required("inventory.add_materialrequest", "inventory.add_materialrequestline", "inventory.view_inventoryitem")
def material_request_create(request):
    from .services import create_material_request

    instance = MaterialRequest(creator=request.user)
    if request.method == "POST":
        form = MaterialRequestForm(request.POST, instance=instance)
        formset = MaterialRequestLineFormSet(request.POST, instance=instance)
        if form.is_valid() and formset.is_valid():
            try:
                material_request = create_material_request(
                    creator=request.user,
                    lines=_request_lines_from_formset(formset),
                    **form.cleaned_data,
                )
            except IntegrityError:
                form.add_error(
                    "delivery_at",
                    "That delivery time was just scheduled by another request. Please choose another time.",
                )
            else:
                messages.success(request, f"{material_request.request_number} submitted and converted to {material_request.pick_ticket.ticket_number}.")
                return redirect(f"{reverse('material_request_detail', kwargs={'pk': material_request.pk})}?created=1")
    else:
        display_name = request.user.get_full_name() or request.user.username
        form = MaterialRequestForm(instance=instance, initial={"requestor_name": display_name})
        formset = MaterialRequestLineFormSet(instance=instance)
    inventory_items = InventoryItem.objects.filter(active=True).order_by("category", "name", "part_number")
    return render(request, "inventory/material_request_form.html", {
        "form": form,
        "formset": formset,
        "mode": "create",
        "inventory_items": inventory_items,
        "category_choices": CategoryChoices.choices,
        "has_form_errors": request.method == "POST" and bool(
            form.errors or any(formset.errors) or formset.non_form_errors()
        ),
    })


@login_required
@request_portal_access_required
@any_perm_required("inventory.view_materialrequest")
def material_request_detail(request, pk):
    material_request = _visible_material_requests(
        request,
        MaterialRequest.objects.select_related("pick_ticket", "creator").prefetch_related("lines__item"),
    ).filter(pk=pk).first()
    if material_request is None:
        stale_redirect = _stale_warehouse_notification_redirect(request, MaterialRequest, pk)
        if stale_redirect is not None:
            return stale_redirect
        raise Http404
    response_form = DeliveryResponseForm(material_request=material_request)
    return render(request, "inventory/material_request_detail.html", {
        "material_request": material_request,
        "delivery_response_form": response_form,
    })


@login_required
@request_portal_access_required
@any_perm_required("inventory.view_materialrequest")
@require_POST
def material_request_delivery_response(request, pk):
    if not request.is_request_portal:
        raise PermissionDenied
    material_request = get_object_or_404(
        MaterialRequest.objects.select_related("pick_ticket", "creator"),
        pk=pk,
        creator=request.user,
    )

    form = DeliveryResponseForm(request.POST, material_request=material_request)
    if not form.is_valid():
        messages.error(request, "Please correct the delivery response details.")
        return redirect("material_request_detail", pk=pk)

    try:
        if form.cleaned_data["response"] == "ready":
            from .services import confirm_delivery_acceptance

            confirm_delivery_acceptance(material_request, user=request.user)
            messages.success(request, "Delivery readiness confirmed. The warehouse has been notified.")
        else:
            from .services import decline_delivery_readiness

            decline_delivery_readiness(
                material_request,
                user=request.user,
                delivery_at=form.cleaned_data.get("delivery_at"),
                note=form.cleaned_data.get("note", ""),
            )
            if form.cleaned_data.get("delivery_at"):
                messages.success(request, "Not ready response and new requested delivery time sent to the warehouse.")
            else:
                messages.success(request, "Not ready response sent to the warehouse.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    except IntegrityError:
        messages.error(request, "That delivery time is already reserved. Please choose another time.")
    return redirect("material_request_detail", pk=pk)


@csrf_exempt
@never_cache
def material_request_email_delivery_response(request):
    """Signed response page; the capability token protects cookie-less email POSTs."""
    from .delivery_email import read_delivery_response_token
    from .services import confirm_delivery_acceptance, decline_delivery_readiness

    def response(context, status=200):
        rendered = render(
            request, "inventory/material_request_email_response.html", context, status=status
        )
        rendered["Referrer-Policy"] = "no-referrer"
        return rendered

    if request.method == "GET" and request.GET.get("receipt") == "1":
        return response({"responded": True})

    token = request.POST.get("token") or request.GET.get("token", "")
    try:
        payload = read_delivery_response_token(token)
        event = MaterialRequestEvent.objects.select_related(
            "material_request__pick_ticket", "material_request__creator"
        ).get(
            pk=payload["event_id"], material_request_id=payload["request_id"],
            event_type=MaterialRequestEvent.EventType.STATUS_CHANGED,
            new_status=PickTicket.Status.RECEIVED,
        )
    except (signing.BadSignature, signing.SignatureExpired, KeyError,
            MaterialRequestEvent.DoesNotExist):
        return response({"invalid_link": True}, status=400)

    material_request = event.material_request
    expected_slot = material_request.delivery_at.isoformat() if material_request.delivery_at else ""
    stale = MaterialRequestEvent.objects.filter(
        material_request=material_request, pk__gt=event.pk,
        event_type__in=[
            MaterialRequestEvent.EventType.STATUS_CHANGED,
            MaterialRequestEvent.EventType.UPDATED,
        ],
    ).exists()
    if (not material_request or stale or
            material_request.pick_ticket.status != PickTicket.Status.RECEIVED or
            payload.get("email") != material_request.requestor_email or
            payload.get("delivery_at") != expected_slot):
        return response({"invalid_link": True}, status=400)

    responded = MaterialRequestEvent.objects.filter(
        material_request=material_request, pk__gt=event.pk,
        event_type__in=[
            MaterialRequestEvent.EventType.DELIVERY_ACCEPTANCE_CONFIRMED,
            MaterialRequestEvent.EventType.DELIVERY_NOT_READY,
        ],
    ).exists()
    form = DeliveryResponseForm(material_request=material_request)
    if request.method == "POST" and not responded:
        action = request.POST.get("response")
        try:
            if action == "ready":
                confirm_delivery_acceptance(
                    material_request, user=material_request.creator,
                    expected_ready_event_id=event.pk,
                )
                responded = True
            elif action == "not_ready":
                form = DeliveryResponseForm(request.POST, material_request=material_request)
                if form.is_valid():
                    decline_delivery_readiness(
                        material_request, user=material_request.creator,
                        delivery_at=form.cleaned_data.get("delivery_at"),
                        note=form.cleaned_data.get("note", ""),
                        expected_ready_event_id=event.pk,
                    )
                    responded = True
            else:
                form.add_error(None, "Choose a response.")
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        except IntegrityError:
            form.add_error(
                "delivery_at",
                "That delivery time was just scheduled. Please choose another time.",
            )
        if responded:
            return redirect(f"{request.path}?receipt=1")

    return response({
        "material_request": material_request, "token": token,
        "delivery_response_form": form, "responded": responded,
    })


@login_required
@request_portal_access_required
@all_perms_required("inventory.change_materialrequest", "inventory.change_materialrequestline", "inventory.view_inventoryitem")
def material_request_edit(request, pk):
    from .services import update_material_request

    material_request = get_object_or_404(
        _visible_material_requests(request, MaterialRequest.objects.prefetch_related("lines")),
        pk=pk,
    )
    if request.method == "POST":
        form = MaterialRequestForm(request.POST, instance=material_request)
        formset = MaterialRequestLineFormSet(request.POST, instance=material_request)
        if form.is_valid() and formset.is_valid():
            try:
                material_request = update_material_request(
                    material_request,
                    lines=_request_lines_from_formset(formset),
                    actor=request.user,
                    **form.cleaned_data,
                )
            except IntegrityError:
                form.add_error(
                    "delivery_at",
                    "That delivery time was just scheduled by another request. Please choose another time.",
                )
            else:
                messages.success(request, f"{material_request.request_number} and its pick ticket were synchronized.")
                return redirect("material_request_detail", pk=material_request.pk)
    else:
        form = MaterialRequestForm(instance=material_request)
        formset = MaterialRequestLineFormSet(instance=material_request)
    inventory_items = InventoryItem.objects.filter(active=True).order_by("category", "name", "part_number")
    return render(request, "inventory/material_request_form.html", {
        "form": form,
        "formset": formset,
        "mode": "edit",
        "material_request": material_request,
        "inventory_items": inventory_items,
        "category_choices": CategoryChoices.choices,
        "has_form_errors": request.method == "POST" and bool(
            form.errors or any(formset.errors) or formset.non_form_errors()
        ),
    })


@login_required
@request_portal_access_required
@all_perms_required("inventory.delete_materialrequest", "inventory.delete_materialrequestline")
def material_request_delete(request, pk):
    from .services import delete_material_request

    material_request = get_object_or_404(_visible_material_requests(request), pk=pk)
    if request.method == "POST":
        number = material_request.request_number
        delete_material_request(material_request, actor=request.user)
        messages.success(request, f"{number} and its linked pick ticket were deleted; inventory was restored.")
        return redirect("material_request_board")
    return render(request, "inventory/material_request_confirm_delete.html", {"material_request": material_request})


@login_required
@any_perm_required("inventory.view_all_materialrequests")
def material_request_events(request):
    """Return a shared, ordered cursor stream; no cursor means initialize silently."""
    if request.get_host().split(":", 1)[0].lower() != "bbx.rplwms.com":
        raise Http404
    cursor_value = request.GET.get("cursor")
    latest = MaterialRequestEvent.objects.order_by("-id").values_list("id", flat=True).first() or 0
    events = []
    cursor = latest
    if cursor_value is not None:
        try:
            supplied_cursor = max(0, int(cursor_value))
        except (TypeError, ValueError):
            supplied_cursor = latest
        rows = list(MaterialRequestEvent.objects.filter(
            id__gt=supplied_cursor,
            event_type__in=[
                MaterialRequestEvent.EventType.CREATED,
                MaterialRequestEvent.EventType.UPDATED,
                MaterialRequestEvent.EventType.DELETED,
                MaterialRequestEvent.EventType.DELIVERY_ACCEPTANCE_CONFIRMED,
                MaterialRequestEvent.EventType.DELIVERY_NOT_READY,
            ],
        ).select_related(
            "material_request", "material_request__pick_ticket"
        ).order_by("id")[:50])
        for event in rows:
            if event.event_type == MaterialRequestEvent.EventType.DELETED:
                events.append({
                    "id": event.id,
                    "event_type": event.event_type,
                    "title": f"{event.request_number_snapshot} deleted",
                    "body": (
                        f"{event.request_number_snapshot} and linked ticket "
                        f"{event.ticket_number_snapshot} were deleted by the requestor."
                    ),
                    "request_number": event.request_number_snapshot,
                    "ticket_number": event.ticket_number_snapshot,
                    "requestor": event.requestor_snapshot,
                    "url": reverse("material_request_board"),
                    "created_at": event.created_at.isoformat(),
                })
                continue
            mr = event.material_request
            if mr is None:
                # Historical non-deletion events may outlive a request. They cannot be
                # rendered safely, but must not poison the shared polling cursor.
                continue
            if event.event_type == MaterialRequestEvent.EventType.CREATED:
                title = f"New material request {mr.request_number}"
                body = f"{mr.request_number}: {mr.requestor_name or mr.creator.get_username()} submitted {mr.pick_ticket.ticket_number}."
            elif event.event_type == MaterialRequestEvent.EventType.UPDATED:
                title = f"{mr.request_number} updated"
                body = event.change_summary
            elif event.event_type == MaterialRequestEvent.EventType.DELIVERY_ACCEPTANCE_CONFIRMED:
                title = f"Delivery confirmed for {mr.request_number}"
                body = "The requester confirmed they are ready for delivery."
            elif event.event_type == MaterialRequestEvent.EventType.DELIVERY_NOT_READY:
                title = f"Requester not ready for {mr.request_number}"
                body = "The requester is not ready for delivery."
                if mr.delivery_at:
                    body += f" Requested slot: {timezone.localtime(mr.delivery_at).strftime('%b %-d, %Y at %-I:%M %p')}."
            else:
                title = f"{mr.request_number} status updated"
                status_labels = dict(PickTicket.Status.choices)
                body = f"{status_labels.get(event.old_status, event.old_status)} → {status_labels.get(event.new_status, event.new_status)}"
            events.append({
                "id": event.id,
                "event_type": event.event_type,
                "title": f"🚨 URGENT: {title}" if mr.urgent else title,
                "body": body,
                "request_number": mr.request_number,
                "ticket_number": mr.pick_ticket.ticket_number,
                "requestor": mr.requestor_name,
                "url": reverse("material_request_detail", kwargs={"pk": mr.pk}),
                "created_at": event.created_at.isoformat(),
                "urgent": mr.urgent,
                "require_interaction": mr.urgent,
            })
        cursor = rows[-1].id if len(rows) == 50 else latest
    response = JsonResponse({"cursor": cursor, "events": events})
    response["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# Cycle counting
# ---------------------------------------------------------------------------


def _cycle_count_categories():
    """Distinct category names present on active inventory, sorted."""
    # NOTE: cannot rely on .distinct() + .order_by(name) here — Django appends
    # `name` to the SELECT DISTINCT when sorting, which gives one row per
    # unique (category, name) pair instead of one per category. Dedup in Python.
    names = set(
        InventoryItem.objects.filter(active=True)
        .exclude(category__isnull=True)
        .exclude(category="")
        .values_list("category", flat=True)
    )
    return sorted(names)


def _parse_percent(value):
    """Coerce a percent value from a form post to an int in [1, 100]."""
    try:
        pct = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if pct < 1 or pct > 100:
        return None
    return pct


@login_required
@any_perm_required("inventory.perform_cycle_count", "inventory.manage_cycle_counts")
def cycle_count_list(request):
    """Dashboard of all cycle counts (most recent first)."""
    cycle_counts = CycleCount.objects.select_related("created_by").all()
    category_filter = request.GET.get("category", "").strip()
    if category_filter:
        cycle_counts = cycle_counts.filter(items__category=category_filter).distinct()

    summary = {
        "total": cycle_counts.count(),
        "open": cycle_counts.filter(status=CycleCount.Status.OPEN).count(),
        "in_progress": cycle_counts.filter(status=CycleCount.Status.IN_PROGRESS).count(),
        "completed": cycle_counts.filter(status=CycleCount.Status.COMPLETED).count(),
        "cancelled": cycle_counts.filter(status=CycleCount.Status.CANCELLED).count(),
    }

    context = {
        "cycle_counts": cycle_counts,
        "summary": summary,
        "category_filter": category_filter,
        "categories": _cycle_count_categories(),
    }
    return render(request, "inventory/cycle_count_list.html", context)


@login_required
@any_perm_required("inventory.manage_cycle_counts")
def cycle_count_create(request):
    """Create a new cycle count batch."""
    categories = _cycle_count_categories()

    def _category_rows(posted_percents):
        return [
            {
                "name": c,
                "total": InventoryItem.objects.filter(category=c, active=True).count(),
                "checked": bool(str(posted_percents.get(c, "")).strip()),
                "percent": str(posted_percents.get(c, "") or ""),
            }
            for c in categories
        ]

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        notes = request.POST.get("notes", "").strip()
        pairs = []
        seen_categories = set()
        for category in categories:
            percent = _parse_percent(request.POST.get(f"percent_{category}", ""))
            if percent is None:
                continue
            if category in seen_categories:
                continue
            seen_categories.add(category)
            pairs.append((category, percent))

        if not pairs:
            messages.error(
                request,
                "Pick at least one category and a percentage between 1 and 100.",
            )
            return render(
                request,
                "inventory/cycle_count_create.html",
                {
                    "categories": _category_rows(
                        {
                            c: request.POST.get(f"percent_{c}", "") for c in categories
                        }
                    ),
                    "form_values": {"name": name, "notes": notes},
                },
            )

        seed = request.POST.get("seed", "").strip() or None
        selections = pick_random_items_for_cycle_count(pairs, seed=seed)

        total_items = sum(len(items) for _, items in selections)
        if total_items == 0:
            messages.error(
                request,
                "No active inventory items match the selected categories.",
            )
            return redirect("cycle_count_create")

        with transaction.atomic():
            cycle_count = CycleCount.objects.create(
                name=name,
                notes=notes,
                created_by=request.user,
                status=CycleCount.Status.OPEN,
            )
            entries = []
            for category, items in selections:
                for item in items:
                    entries.append(
                        CycleCountItem(
                            cycle_count=cycle_count,
                            category=category,
                            item=item,
                            system_quantity=item.quantity_on_hand,
                        )
                    )
            CycleCountItem.objects.bulk_create(entries)

        messages.success(
            request,
            f"Created {cycle_count.display_name} with {total_items} items across "
            f"{len(pairs)} categor{'y' if len(pairs) == 1 else 'ies'}.",
        )
        return redirect("cycle_count_detail", pk=cycle_count.pk)

    context = {
        "categories": _category_rows({}),
        "form_values": {"name": "", "notes": ""},
    }
    return render(request, "inventory/cycle_count_create.html", context)


@login_required
@any_perm_required("inventory.perform_cycle_count", "inventory.manage_cycle_counts")
def cycle_count_detail(request, pk):
    """Record counts for a cycle count batch."""
    cycle_count = get_object_or_404(CycleCount, pk=pk)
    items = (
        cycle_count.items.select_related("item", "counted_by")
        .order_by("category", "item__name")
    )

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "cancel":
            if cycle_count.status in (
                CycleCount.Status.COMPLETED,
                CycleCount.Status.CANCELLED,
            ):
                messages.error(
                    request, "This cycle count is already finished."
                )
                return redirect("cycle_count_detail", pk=cycle_count.pk)
            cycle_count.status = CycleCount.Status.CANCELLED
            cycle_count.save(update_fields=["status"])
            messages.success(request, f"{cycle_count.display_name} cancelled.")
            return redirect("cycle_count_detail", pk=cycle_count.pk)

        if action == "reopen":
            cycle_count.status = CycleCount.Status.IN_PROGRESS
            cycle_count.save(update_fields=["status"])
            messages.success(request, f"{cycle_count.display_name} reopened.")
            return redirect("cycle_count_detail", pk=cycle_count.pk)

        if action == "complete":
            uncounted = cycle_count.items.filter(counted_quantity__isnull=True).count()
            if uncounted:
                messages.error(
                    request,
                    f"Cannot complete: {uncounted} item{'s' if uncounted != 1 else ''} "
                    f"still need a count.",
                )
                return redirect("cycle_count_detail", pk=cycle_count.pk)
            cycle_count.status = CycleCount.Status.COMPLETED
            cycle_count.completed_at = timezone.now()
            cycle_count.save(update_fields=["status", "completed_at"])
            messages.success(
                request, f"{cycle_count.display_name} marked complete."
            )
            return redirect("cycle_count_detail", pk=cycle_count.pk)

        if action == "record":
            now = timezone.now()
            updated = 0
            with transaction.atomic():
                for entry in cycle_count.items.select_for_update():
                    raw = request.POST.get(f"count_{entry.pk}", "").strip()
                    if raw == "":
                        entry.counted_quantity = None
                        entry.counted_by = None
                        entry.counted_at = None
                        entry.note = ""
                        entry.save(
                            update_fields=[
                                "counted_quantity",
                                "counted_by",
                                "counted_at",
                                "note",
                            ]
                        )
                        continue
                    try:
                        qty = int(raw)
                    except ValueError:
                        messages.error(
                            request,
                            f"{entry.item.part_number}: '{raw}' is not a number.",
                        )
                        return redirect("cycle_count_detail", pk=cycle_count.pk)
                    if qty < 0:
                        messages.error(
                            request,
                            f"{entry.item.part_number}: counts cannot be negative.",
                        )
                        return redirect("cycle_count_detail", pk=cycle_count.pk)
                    note = request.POST.get(f"note_{entry.pk}", "").strip()[:240]
                    entry.counted_quantity = qty
                    entry.counted_by = request.user
                    entry.counted_at = now
                    entry.note = note
                    entry.save(
                        update_fields=[
                            "counted_quantity",
                            "counted_by",
                            "counted_at",
                            "note",
                        ]
                    )
                    updated += 1
                if cycle_count.status == CycleCount.Status.OPEN:
                    cycle_count.status = CycleCount.Status.IN_PROGRESS
                    cycle_count.save(update_fields=["status"])

            messages.success(
                request,
                f"Recorded {updated} count{'s' if updated != 1 else ''} on "
                f"{cycle_count.display_name}.",
            )
            return redirect("cycle_count_detail", pk=cycle_count.pk)

        messages.error(request, "Unknown action.")
        return redirect("cycle_count_detail", pk=cycle_count.pk)

    context = {
        "cycle_count": cycle_count,
        "items": items,
        "uncounted_count": items.filter(counted_quantity__isnull=True).count(),
        "variance_count": sum(1 for item in items if item.has_variance),
    }
    return render(request, "inventory/cycle_count_detail.html", context)


@login_required
@any_perm_required("inventory.perform_cycle_count", "inventory.manage_cycle_counts")
def cycle_count_pdf(request, pk):
    """Render the printable count sheet as a PDF (no current quantities)."""
    from django.conf import settings as django_settings
    from django.template.loader import render_to_string
    from weasyprint import HTML

    cycle_count = get_object_or_404(CycleCount, pk=pk)
    context = _cycle_count_print_context(cycle_count, pdf_mode=True)
    html = render_to_string(
        "inventory/cycle_count_pdf.html",
        context,
        request=request,
    )
    pdf = HTML(string=html, base_url=str(django_settings.BASE_DIR)).write_pdf()
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'inline; filename="cycle-count-{cycle_count.pk:05d}.pdf"'
    )
    return response


@login_required
@any_perm_required("inventory.manage_cycle_counts")
def cycle_count_results_pdf(request, pk):
    """Render the final reconciliation PDF for a completed cycle count.

    Gated to ``manage_cycle_counts`` because this document is the audit-trail
    artifact — it must only be issued by someone authorized to close the
    count. The view refuses to render unless the cycle count is in the
    ``completed`` state, so partial counts cannot accidentally produce an
    authoritative-looking audit document.

    Includes system quantity, counted quantity, and variance for every row,
    a per-category breakdown, totals, populated signature blocks, and the
    notes the manager entered on the detail page.
    """
    from django.conf import settings as django_settings
    from django.template.loader import render_to_string
    from weasyprint import HTML

    cycle_count = get_object_or_404(CycleCount, pk=pk)
    if cycle_count.status != CycleCount.Status.COMPLETED:
        messages.error(
            request,
            "Reconciliation PDF is only available after the cycle count is marked complete.",
        )
        return redirect("cycle_count_detail", pk=cycle_count.pk)
    context = _cycle_count_print_context(cycle_count, pdf_mode=True, mode="results")
    html = render_to_string(
        "inventory/cycle_count_results_pdf.html",
        context,
        request=request,
    )
    pdf = HTML(string=html, base_url=str(django_settings.BASE_DIR)).write_pdf()
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'inline; filename="cycle-count-{cycle_count.pk:05d}-reconciliation.pdf"'
    )
    return response

def _cycle_count_print_context(cycle_count, *, pdf_mode=False, mode="blank"):
    """Build the template context for the cycle count print/PDF.

    Two modes:
    - ``"blank"``: count sheet for the picker. Count boxes are empty, system
      quantity hidden on purpose to prevent bias.
    - ``"results"``: reconciliation document for a *completed* count. Shows
      system quantity, counted quantity, and variance for every row, plus a
      populated signature block (who created it, who counted each item, who
      closed it).

    Splits items into landscape Letter pages of ``rows_per_page`` rows each.
    Each page knows if it is first/last so the template can render summary
    strips and the final reconciliation block exactly where they belong.
    Each row carries a global ``number`` so the counter can keep their place
    when the sheet spans multiple pages.
    """
    if mode not in ("blank", "results"):
        raise ValueError(f"Unknown cycle count print mode: {mode!r}")

    items = list(
        cycle_count.items.select_related("item", "counted_by")
        .order_by("category", "item__name")
    )
    # Twenty operational rows fit safely on a landscape Letter page while
    # preserving the page header, summary strip, and footer. Same number as
    # the pick ticket print so the look and feel matches exactly.
    rows_per_page = 20

    chunks = [
        items[index : index + rows_per_page]
        for index in range(0, len(items), rows_per_page)
    ] or [[]]
    total_pages = len(chunks)
    pages = []
    cursor = 0
    for index, rows in enumerate(chunks, start=1):
        numbered_rows = []
        for entry in rows:
            cursor += 1
            numbered_rows.append({"number": cursor, "entry": entry})
        pages.append(
            {
                "number": index,
                "rows": numbered_rows,
                "is_first": index == 1,
                "is_last": index == total_pages,
            }
        )
    # Per-category totals for the reconciliation block on the last page.
    category_summaries = []
    counted_count = sum(1 for entry in items if entry.counted_quantity is not None)
    variance_total = 0
    variance_count = 0
    for category, group in _group_items_by_category(items):
        cat_counted = sum(1 for entry in group if entry.counted_quantity is not None)
        cat_variance_total = 0
        cat_variance_count = 0
        for entry in group:
            if entry.counted_quantity is not None:
                diff = entry.counted_quantity - entry.system_quantity
                cat_variance_total += diff
                if diff != 0:
                    cat_variance_count += 1
        variance_total += cat_variance_total
        variance_count += cat_variance_count
        category_summaries.append(
            {
                "name": category,
                "total": len(group),
                "counted": cat_counted,
                "variance_total": cat_variance_total,
                "variance_count": cat_variance_count,
            }
        )
    context = {
        "cycle_count": cycle_count,
        "pages": pages,
        "total_pages": total_pages,
        "category_summaries": category_summaries,
        "generated_at": timezone.now(),
        "pdf_mode": pdf_mode,
        "mode": mode,
        "counted_count": counted_count,
        "variance_total": variance_total,
        "variance_count": variance_count,
    }
    if pdf_mode:
        logo_path = (
            django_settings.BASE_DIR
            / "inventory"
            / "static"
            / "inventory"
            / "img"
            / "blackbox-logo.png"
        )
        context["logo_uri"] = logo_path.as_uri()
    return context


def _group_items_by_category(items):
    """Yield ``(category, [items...])`` preserving first-seen order."""
    seen = {}
    for item in items:
        seen.setdefault(item.category, []).append(item)
    return list(seen.items())
