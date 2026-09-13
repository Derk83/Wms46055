from django.contrib import admin
from django.db.models import Q

from .models import (
    InventoryItem, InventoryTransaction, MaterialRequest, MaterialRequestEvent,
    MaterialRequestLine, PickTicket, PickTicketLine, PushDelivery, PushSubscription,
)


class PickTicketLineInline(admin.TabularInline):
    model = PickTicketLine
    extra = 0
    readonly_fields = ("created_at",)

    def _request_managed(self, obj):
        return bool(obj and MaterialRequest.objects.filter(pick_ticket=obj).exists())

    def has_add_permission(self, request, obj=None):
        return False if self._request_managed(obj) else super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return False if self._request_managed(obj) else super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False if self._request_managed(obj) else super().has_delete_permission(request, obj)


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ("part_number", "name", "category", "quantity_on_hand", "storage_location", "is_low_stock", "active")
    list_filter = ("category", "active", "rack", "section", "bin_location")
    search_fields = ("part_number", "name", "barcode_value", "qr_code_value")
    readonly_fields = ("created_at", "updated_at")


@admin.register(PickTicket)
class PickTicketAdmin(admin.ModelAdmin):
    list_display = ("ticket_number", "date", "picked_by_name", "requested_by_name", "building_room", "location")
    search_fields = ("ticket_number", "picked_by_name", "received_by_name", "requested_by_name", "building_room", "location")
    list_filter = ("date", "location")
    inlines = [PickTicketLineInline]
    readonly_fields = ("ticket_number", "created_at", "updated_at")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.has_perm("inventory.view_all_materialrequests"):
            return queryset
        return queryset.filter(
            Q(material_request__isnull=True) | Q(material_request__creator=request.user)
        )

    def get_readonly_fields(self, request, obj=None):
        if obj and MaterialRequest.objects.filter(pick_ticket=obj).exists():
            return tuple(field.name for field in self.model._meta.fields)
        return super().get_readonly_fields(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and MaterialRequest.objects.filter(pick_ticket=obj).exists():
            return False
        return super().has_delete_permission(request, obj)


@admin.register(InventoryTransaction)
class InventoryTransactionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "item", "transaction_type", "quantity_delta", "pick_ticket", "created_by")
    list_filter = ("transaction_type", "created_at")
    search_fields = ("item__part_number", "item__name", "pick_ticket__ticket_number", "notes")
    readonly_fields = ("created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.has_perm("inventory.view_all_materialrequests"):
            return queryset
        return queryset.filter(
            Q(pick_ticket__material_request__isnull=True)
            | Q(pick_ticket__material_request__creator=request.user)
        )


class MaterialRequestLineInline(admin.TabularInline):
    model = MaterialRequestLine
    extra = 0
    readonly_fields = ("item", "quantity", "notes", "created_at")

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MaterialRequest)
class MaterialRequestAdmin(admin.ModelAdmin):
    list_display = ("request_number", "requestor_name", "building_room", "location", "pick_ticket", "created_at")
    list_filter = ("pick_ticket__status", "created_at")
    search_fields = ("request_number", "requestor_name", "building_room", "location", "pick_ticket__ticket_number")
    readonly_fields = tuple(field.name for field in MaterialRequest._meta.fields)
    inlines = [MaterialRequestLineInline]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.has_perm("inventory.view_all_materialrequests"):
            return queryset
        return queryset.filter(creator=request.user)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MaterialRequestEvent)
class MaterialRequestEventAdmin(admin.ModelAdmin):
    list_display = ("id", "material_request", "created_at")
    readonly_fields = tuple(field.name for field in MaterialRequestEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.has_perm("inventory.view_all_materialrequests"):
            return queryset
        return queryset.filter(material_request__creator=request.user)


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "enabled", "failure_count", "last_success_at", "updated_at")
    list_filter = ("enabled", "created_at", "updated_at")
    search_fields = ("user__username", "endpoint")
    readonly_fields = ("user", "endpoint", "user_agent", "created_at", "updated_at", "last_success_at", "failure_count")
    exclude = ("p256dh", "auth", "session_key", "last_error")

    def has_add_permission(self, request):
        return False


@admin.register(PushDelivery)
class PushDeliveryAdmin(admin.ModelAdmin):
    list_display = ("event", "subscription", "status", "attempts", "next_attempt_at", "sent_at")
    list_filter = ("status", "created_at")
    search_fields = ("event__material_request__request_number", "subscription__user__username")
    readonly_fields = tuple(field.name for field in PushDelivery._meta.fields)

    def has_add_permission(self, request):
        return False

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.has_perm("inventory.view_all_materialrequests"):
            return queryset
        return queryset.filter(event__material_request__creator=request.user)

    def has_change_permission(self, request, obj=None):
        return False
