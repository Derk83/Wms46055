from django.contrib import admin

from .models import (
    Asset,
    AssetComponent,
    AssetEvent,
    AssetIdentifier,
    Checkout,
    CheckoutItem,
    EquipmentCategory,
    EquipmentImportBatch,
    EquipmentImportRow,
    EquipmentLocation,
    EquipmentParty,
    EquipmentVendor,
    MaintenancePlan,
    MaintenanceWorkOrder,
    RentalAsset,
    RentalContract,
    Reservation,
    ReturnRecord,
    VehicleMeterReading,
)


class AssetIdentifierInline(admin.TabularInline):
    model = AssetIdentifier
    extra = 0


class AssetComponentInline(admin.TabularInline):
    model = AssetComponent
    extra = 0


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("asset_tag", "name", "category", "status", "current_party", "review_required")
    list_filter = ("status", "ownership", "category", "review_required")
    search_fields = ("asset_tag", "legacy_tag", "name", "identifiers__value")
    inlines = (AssetIdentifierInline, AssetComponentInline)
    readonly_fields = (
        "status",
        "current_party",
        "current_location",
        "archived_at",
        "archived_by",
        "created_at",
        "updated_at",
    )

    def get_exclude(self, request, obj=None):
        if request.user.has_perm("equipment.view_asset_costs"):
            return ()
        return ("purchase_cost", "replacement_value")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Checkout)
class CheckoutAdmin(admin.ModelAdmin):
    list_display = ("checkout_number", "borrower", "checked_out_at", "due_at", "created_by")
    readonly_fields = [field.name for field in Checkout._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AssetEvent, CheckoutItem, ReturnRecord, VehicleMeterReading)
class ImmutableHistoryAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(EquipmentCategory)
admin.site.register(EquipmentLocation)
admin.site.register(EquipmentParty)
admin.site.register(EquipmentVendor)


class WorkflowReadOnlyAdmin(admin.ModelAdmin):
    """Expose operational records for inspection without bypassing services."""

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Reservation)
class ReservationAdmin(WorkflowReadOnlyAdmin):
    list_display = ("reservation_number", "requestor", "starts_at", "ends_at", "status")


@admin.register(MaintenancePlan)
class MaintenancePlanAdmin(WorkflowReadOnlyAdmin):
    list_display = ("asset", "service_title", "active", "next_due_date", "next_due_meter")


@admin.register(MaintenanceWorkOrder)
class MaintenanceWorkOrderAdmin(WorkflowReadOnlyAdmin):
    list_display = ("work_order_number", "asset", "status", "priority", "due_at")

    def get_exclude(self, request, obj=None):
        return () if request.user.has_perm("equipment.view_asset_costs") else ("cost",)


@admin.register(RentalContract)
class RentalContractAdmin(WorkflowReadOnlyAdmin):
    list_display = ("contract_number", "vendor", "starts_on", "ends_on", "status")


@admin.register(RentalAsset)
class RentalAssetAdmin(WorkflowReadOnlyAdmin):
    list_display = ("contract", "asset", "vendor_equipment_number", "expected_return_on", "returned_on")

    def get_exclude(self, request, obj=None):
        return () if request.user.has_perm("equipment.view_asset_costs") else ("rate_amount",)


@admin.register(EquipmentImportBatch, EquipmentImportRow)
class ImmutableImportAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm(f"equipment.view_{self.model._meta.model_name}")

    def has_delete_permission(self, request, obj=None):
        return False
