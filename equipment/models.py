import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ImmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise TypeError("Equipment history is append-only.")

    def delete(self):
        raise TypeError("Equipment history is append-only.")


class ImmutableModel(models.Model):
    objects = ImmutableQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise TypeError("Equipment history is append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise TypeError("Equipment history is append-only.")


class EquipmentCategory(TimestampedModel):
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=32, unique=True)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    requires_serial = models.BooleanField(default=False)
    default_checkout_days = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ("name",)
        verbose_name_plural = "equipment categories"

    def __str__(self):
        return self.name


class EquipmentLocation(TimestampedModel):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=160)
    building = models.CharField(max_length=120, blank=True)
    room = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name


class EquipmentParty(TimestampedModel):
    class Kind(models.TextChoices):
        PERSON = "PERSON", "Person"
        TEAM = "TEAM", "Team"
        DEPARTMENT = "DEPARTMENT", "Department"
        PROJECT = "PROJECT", "Project"
        EXTERNAL = "EXTERNAL", "External"

    kind = models.CharField(max_length=16, choices=Kind, default=Kind.PERSON)
    display_name = models.CharField(max_length=200)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="equipment_party",
    )
    department = models.CharField(max_length=160, blank=True)
    email = models.EmailField(blank=True)
    external_reference = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("display_name",)
        verbose_name_plural = "equipment parties"

    def __str__(self):
        return self.display_name


class EquipmentVendor(TimestampedModel):
    name = models.CharField(max_length=180, unique=True)
    vendor_code = models.CharField(max_length=60, blank=True)
    contact_name = models.CharField(max_length=160, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=60, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name


class Asset(TimestampedModel):
    class Ownership(models.TextChoices):
        OWNED = "OWNED", "Owned"
        RENTED = "RENTED", "Rented"
        LEASED = "LEASED", "Leased"
        CUSTOMER = "CUSTOMER", "Customer owned"
        UNKNOWN = "UNKNOWN", "Unknown"

    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        RESERVED = "RESERVED", "Reserved"
        CHECKED_OUT = "CHECKED_OUT", "Checked out"
        IN_TRANSIT = "IN_TRANSIT", "In transit"
        MAINTENANCE = "MAINTENANCE", "Maintenance"
        OUT_OF_SERVICE = "OUT_OF_SERVICE", "Out of service"
        LOST = "LOST", "Lost"
        RETIRED = "RETIRED", "Retired"
        RETURNED_VENDOR = "RETURNED_VENDOR", "Returned to vendor"
        UNKNOWN = "UNKNOWN", "Unknown"

    class Condition(models.TextChoices):
        NEW = "NEW", "New"
        GOOD = "GOOD", "Good"
        FAIR = "FAIR", "Fair"
        DAMAGED = "DAMAGED", "Damaged"
        UNSERVICEABLE = "UNSERVICEABLE", "Unserviceable"
        UNKNOWN = "UNKNOWN", "Unknown"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset_tag = models.CharField(max_length=64, unique=True)
    legacy_tag = models.CharField(max_length=120, blank=True)
    source_namespace = models.CharField(max_length=120, blank=True)
    category = models.ForeignKey(EquipmentCategory, on_delete=models.PROTECT, related_name="assets")
    name = models.CharField(max_length=220)
    manufacturer = models.CharField(max_length=120, blank=True)
    model_number = models.CharField(max_length=160, blank=True)
    description = models.TextField(blank=True)
    ownership = models.CharField(max_length=16, choices=Ownership, default=Ownership.UNKNOWN)
    status = models.CharField(max_length=24, choices=Status, default=Status.AVAILABLE)
    condition = models.CharField(max_length=20, choices=Condition, default=Condition.UNKNOWN)
    quantity = models.PositiveIntegerField(default=1)
    current_party = models.ForeignKey(
        EquipmentParty, null=True, blank=True, on_delete=models.PROTECT, related_name="assigned_assets"
    )
    home_location = models.ForeignKey(
        EquipmentLocation, null=True, blank=True, on_delete=models.PROTECT, related_name="home_assets"
    )
    current_location = models.ForeignKey(
        EquipmentLocation, null=True, blank=True, on_delete=models.PROTECT, related_name="current_assets"
    )
    acquired_on = models.DateField(null=True, blank=True)
    purchase_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    replacement_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    attributes = models.JSONField(default=dict, blank=True)
    review_required = models.BooleanField(default=False)
    review_notes = models.TextField(blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="archived_equipment_assets"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="created_equipment_assets"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="updated_equipment_assets"
    )

    class Meta:
        ordering = ("asset_tag",)
        indexes = [
            models.Index(fields=("status", "category")),
            models.Index(fields=("source_namespace", "legacy_tag")),
            models.Index(fields=("updated_at",)),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gte=1), name="equipment_asset_quantity_positive"),
            models.CheckConstraint(
                condition=(Q(archived_at__isnull=True, archived_by__isnull=True) | Q(archived_at__isnull=False, archived_by__isnull=False)),
                name="equipment_asset_archive_pair",
            ),
        ]
        permissions = [
            ("access_equipment_portal", "Can access equipment portal"),
            ("manage_equipment", "Can manage equipment registry"),
            ("checkout_asset", "Can check out equipment"),
            ("return_asset", "Can return equipment"),
            ("manage_reservations", "Can manage equipment reservations"),
            ("manage_maintenance", "Can manage equipment maintenance"),
            ("manage_rentals", "Can manage equipment rentals"),
            ("import_equipment", "Can import equipment"),
            ("export_equipment", "Can export equipment"),
            ("view_asset_costs", "Can view equipment costs"),
            ("view_equipment_audit", "Can view equipment audit history"),
            ("print_asset_labels", "Can print equipment labels"),
        ]

    def clean(self):
        if self.status in {self.Status.RETIRED, self.Status.RETURNED_VENDOR} and self.current_party_id:
            raise ValidationError({"status": "Retired or vendor-returned equipment cannot have active custody."})

    def save(self, *args, **kwargs):
        self.asset_tag = self.asset_tag.strip().upper()
        self.legacy_tag = self.legacy_tag.strip()
        super().save(*args, **kwargs)

    @property
    def primary_serial(self):
        identifier = self.identifiers.filter(kind=AssetIdentifier.Kind.SERIAL, is_primary=True).first()
        return identifier.value if identifier else ""

    def __str__(self):
        return f"{self.asset_tag} · {self.name}"


class AssetIdentifier(TimestampedModel):
    class Kind(models.TextChoices):
        SERIAL = "SERIAL", "Serial number"
        BASE_SERIAL = "BASE_SERIAL", "Base serial"
        VIN = "VIN", "VIN"
        LICENSE_PLATE = "LICENSE_PLATE", "License plate"
        IMEI = "IMEI", "IMEI"
        GPS_SERIAL = "GPS_SERIAL", "GPS serial"
        EQUIPMENT_NUMBER = "EQUIPMENT_NUMBER", "Equipment number"
        BARCODE = "BARCODE", "Barcode"
        TRACKING = "TRACKING", "Tracking"
        OTHER = "OTHER", "Other"

    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="identifiers")
    kind = models.CharField(max_length=32, choices=Kind)
    namespace = models.CharField(max_length=120, default="global")
    value = models.CharField(max_length=220)
    normalized_value = models.CharField(max_length=220, editable=False)
    is_primary = models.BooleanField(default=False)

    class Meta:
        ordering = ("kind", "value")
        constraints = [
            models.UniqueConstraint(fields=("namespace", "kind", "normalized_value"), name="equipment_identifier_unique"),
            models.UniqueConstraint(
                fields=("asset", "kind"), condition=Q(is_primary=True), name="equipment_one_primary_identifier"
            ),
        ]

    def save(self, *args, **kwargs):
        self.value = self.value.strip()
        self.normalized_value = " ".join(self.value.upper().split())
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_kind_display()}: {self.value}"


class AssetComponent(TimestampedModel):
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="components")
    role = models.CharField(max_length=120)
    serial_number = models.CharField(max_length=220, blank=True)
    model_number = models.CharField(max_length=160, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    notes = models.TextField(blank=True)
    source_row = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ("asset", "role", "id")
        constraints = [models.CheckConstraint(condition=Q(quantity__gte=1), name="equipment_component_quantity_positive")]


class Reservation(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"
        FULFILLED = "FULFILLED", "Fulfilled"
        EXPIRED = "EXPIRED", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reservation_number = models.CharField(max_length=24, unique=True)
    requestor = models.ForeignKey(EquipmentParty, on_delete=models.PROTECT, related_name="reservations")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status, default=Status.PENDING)
    purpose = models.TextField(blank=True)
    destination = models.ForeignKey(EquipmentLocation, null=True, blank=True, on_delete=models.PROTECT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="equipment_reservations")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="approved_equipment_reservations"
    )
    assets = models.ManyToManyField(Asset, through="ReservationAsset", related_name="reservations")

    class Meta:
        ordering = ("-starts_at",)
        constraints = [models.CheckConstraint(condition=Q(ends_at__gt=models.F("starts_at")), name="equipment_reservation_dates")]

    def __str__(self):
        return self.reservation_number


class ReservationAsset(models.Model):
    reservation = models.ForeignKey(Reservation, on_delete=models.PROTECT)
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("reservation", "asset"), name="equipment_reservation_asset_unique")]


class Checkout(ImmutableModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    checkout_number = models.CharField(max_length=24, unique=True)
    borrower = models.ForeignKey(EquipmentParty, on_delete=models.PROTECT, related_name="checkouts")
    borrower_snapshot = models.CharField(max_length=240)
    destination = models.ForeignKey(EquipmentLocation, null=True, blank=True, on_delete=models.PROTECT)
    purpose = models.TextField(blank=True)
    checked_out_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="equipment_checkouts")
    reservation = models.ForeignKey(Reservation, null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-checked_out_at",)

    def __str__(self):
        return self.checkout_number


class CheckoutItem(ImmutableModel):
    checkout = models.ForeignKey(Checkout, on_delete=models.PROTECT, related_name="items")
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="checkout_items")
    asset_tag_snapshot = models.CharField(max_length=64)
    asset_name_snapshot = models.CharField(max_length=220)
    condition_out = models.CharField(max_length=20, choices=Asset.Condition)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("checkout", "asset"), name="equipment_checkout_asset_unique")]


class ActiveCustody(models.Model):
    asset = models.OneToOneField(Asset, primary_key=True, on_delete=models.PROTECT, related_name="active_custody")
    checkout_item = models.OneToOneField(CheckoutItem, on_delete=models.PROTECT, related_name="active_custody")
    borrower = models.ForeignKey(EquipmentParty, on_delete=models.PROTECT)
    due_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ReturnRecord(ImmutableModel):
    checkout_item = models.OneToOneField(CheckoutItem, on_delete=models.PROTECT, related_name="return_record")
    returned_at = models.DateTimeField(default=timezone.now)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    return_location = models.ForeignKey(EquipmentLocation, null=True, blank=True, on_delete=models.PROTECT)
    condition_in = models.CharField(max_length=20, choices=Asset.Condition)
    notes = models.TextField(blank=True)
    damage_reported = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class MaintenanceWorkOrder(TimestampedModel):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        SCHEDULED = "SCHEDULED", "Scheduled"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        WAITING_PARTS = "WAITING_PARTS", "Waiting for parts"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        NORMAL = "NORMAL", "Normal"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    work_order_number = models.CharField(max_length=24, unique=True)
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="maintenance_work_orders")
    title = models.CharField(max_length=220)
    problem_description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status, default=Status.OPEN)
    priority = models.CharField(max_length=12, choices=Priority, default=Priority.NORMAL)
    out_of_service = models.BooleanField(default=True)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    work_performed = models.TextField(blank=True)
    vendor = models.ForeignKey(EquipmentVendor, null=True, blank=True, on_delete=models.PROTECT)
    cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="opened_equipment_work")
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="completed_equipment_work"
    )

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return self.work_order_number


class RentalContract(TimestampedModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        ENDED = "ENDED", "Ended"
        PENDING = "PENDING", "Pending"
        CANCELLED = "CANCELLED", "Cancelled"

    contract_number = models.CharField(max_length=80, unique=True)
    vendor = models.ForeignKey(EquipmentVendor, on_delete=models.PROTECT, related_name="contracts")
    po_number = models.CharField(max_length=80, blank=True)
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status, default=Status.ACTIVE)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        ordering = ("-starts_on", "contract_number")


class RentalAsset(TimestampedModel):
    class RatePeriod(models.TextChoices):
        DAY = "DAY", "Day"
        WEEK = "WEEK", "Week"
        FOUR_WEEK = "FOUR_WEEK", "Four weeks"
        MONTH = "MONTH", "Month"

    contract = models.ForeignKey(RentalContract, on_delete=models.PROTECT, related_name="lines")
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="rental_lines")
    vendor_equipment_number = models.CharField(max_length=120, blank=True)
    rate_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    rate_period = models.CharField(max_length=16, choices=RatePeriod, default=RatePeriod.FOUR_WEEK)
    expected_return_on = models.DateField(null=True, blank=True)
    returned_on = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("contract", "asset"), name="equipment_rental_asset_unique")]


class AssetDocument(TimestampedModel):
    class Classification(models.TextChoices):
        INTERNAL = "INTERNAL", "Internal"
        RESTRICTED = "RESTRICTED", "Restricted"

    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="documents")
    file = models.FileField(upload_to="equipment/documents/%Y/%m/")
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120)
    size_bytes = models.PositiveBigIntegerField()
    description = models.CharField(max_length=255, blank=True)
    classification = models.CharField(max_length=16, choices=Classification, default=Classification.INTERNAL)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)


class AssetEvent(ImmutableModel):
    class Type(models.TextChoices):
        IMPORTED = "IMPORTED", "Imported"
        CREATED = "CREATED", "Created"
        UPDATED = "UPDATED", "Updated"
        CHECKED_OUT = "CHECKED_OUT", "Checked out"
        RETURNED = "RETURNED", "Returned"
        RESERVED = "RESERVED", "Reserved"
        RESERVATION_CHANGED = "RESERVATION_CHANGED", "Reservation changed"
        MAINTENANCE_OPENED = "MAINTENANCE_OPENED", "Maintenance opened"
        MAINTENANCE_COMPLETED = "MAINTENANCE_COMPLETED", "Maintenance completed"
        STATUS_CHANGED = "STATUS_CHANGED", "Status changed"
        REVIEWED = "REVIEWED", "Reviewed"

    asset = models.ForeignKey(Asset, null=True, blank=True, on_delete=models.PROTECT, related_name="events")
    event_type = models.CharField(max_length=32, choices=Type)
    occurred_at = models.DateTimeField(default=timezone.now)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    summary = models.CharField(max_length=255)
    metadata = models.JSONField(default=dict, blank=True)
    correlation_id = models.UUIDField(default=uuid.uuid4, editable=False)

    class Meta:
        ordering = ("-occurred_at", "-id")
        indexes = [models.Index(fields=("asset", "occurred_at"))]


class EquipmentMutationLock(models.Model):
    """Single-row mutex used to serialize equipment mutations on SQLite."""

    key = models.CharField(max_length=32, primary_key=True)
    touched_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "equipment mutation lock"


class EquipmentRequest(TimestampedModel):
    """Requester-authored need; concrete inventory remains manager-selected."""

    class Status(models.TextChoices):
        SUBMITTED = "SUBMITTED", "Submitted"
        REVIEWING = "REVIEWING", "Under review"
        APPROVED = "APPROVED", "Approved"
        READY = "READY", "Ready"
        FULFILLED = "FULFILLED", "Fulfilled"
        DECLINED = "DECLINED", "Declined"
        CANCELLED = "CANCELLED", "Cancelled"

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        NORMAL = "NORMAL", "Normal"
        HIGH = "HIGH", "High"
        URGENT = "URGENT", "Urgent"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_number = models.CharField(max_length=24, unique=True, editable=False)
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="equipment_requests")
    requestor_party = models.ForeignKey(EquipmentParty, on_delete=models.PROTECT, related_name="equipment_requests")
    status = models.CharField(max_length=16, choices=Status, default=Status.SUBMITTED)
    priority = models.CharField(max_length=12, choices=Priority, default=Priority.NORMAL)
    needed_from = models.DateField()
    needed_until = models.DateField(null=True, blank=True)
    destination = models.CharField(max_length=220)
    purpose = models.TextField()
    project = models.CharField(max_length=180, blank=True)
    accepts_substitutes = models.BooleanField(default=True)
    requester_notes = models.TextField(blank=True)
    manager_notes = models.TextField(blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="assigned_equipment_requests",
    )
    reservation = models.OneToOneField(
        Reservation, null=True, blank=True, on_delete=models.PROTECT, related_name="equipment_request"
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="cancelled_equipment_requests",
    )

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("status", "needed_from")), models.Index(fields=("requester", "created_at"))]
        constraints = [models.CheckConstraint(
            condition=Q(needed_until__isnull=True) | Q(needed_until__gte=models.F("needed_from")),
            name="equipment_request_needed_dates",
        )]
        permissions = [
            ("access_equipment_requests", "Can access equipment requests portal"),
            ("manage_equipment_requests", "Can manage equipment requests"),
        ]

    def __str__(self):
        return self.request_number


class EquipmentRequestLine(TimestampedModel):
    request = models.ForeignKey(EquipmentRequest, on_delete=models.PROTECT, related_name="lines")
    category = models.ForeignKey(
        EquipmentCategory, null=True, blank=True, on_delete=models.PROTECT, related_name="request_lines"
    )
    unlisted_equipment = models.CharField(max_length=220, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("id",)
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gte=1), name="equipment_request_line_quantity_positive"),
            models.CheckConstraint(
                condition=Q(category__isnull=False) | ~Q(unlisted_equipment=""),
                name="equipment_request_line_has_equipment",
            ),
        ]

    @property
    def description(self):
        return self.category.name if self.category_id else self.unlisted_equipment


class EquipmentRequestAllocation(models.Model):
    line = models.ForeignKey(EquipmentRequestLine, on_delete=models.PROTECT, related_name="allocations")
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="request_allocations")
    quantity = models.PositiveIntegerField(default=1)
    allocated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")
        constraints = [
            models.UniqueConstraint(fields=("line", "asset"), name="equipment_request_allocation_unique"),
            models.CheckConstraint(condition=Q(quantity__gte=1), name="equipment_request_allocation_quantity_positive"),
        ]


class EquipmentRequestEvent(ImmutableModel):
    class Type(models.TextChoices):
        CREATED = "CREATED", "Created"
        UPDATED = "UPDATED", "Updated"
        CANCELLED = "CANCELLED", "Cancelled"
        ASSIGNED = "ASSIGNED", "Assigned"
        ALLOCATED = "ALLOCATED", "Allocated"
        STATUS_CHANGED = "STATUS_CHANGED", "Status changed"

    request = models.ForeignKey(EquipmentRequest, on_delete=models.PROTECT, related_name="events")
    event_type = models.CharField(max_length=24, choices=Type)
    occurred_at = models.DateTimeField(default=timezone.now)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    from_status = models.CharField(max_length=16, blank=True)
    to_status = models.CharField(max_length=16, blank=True)
    message = models.CharField(max_length=255)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-occurred_at", "-id")
        indexes = [models.Index(fields=("request", "occurred_at"))]


class EquipmentImportBatch(TimestampedModel):
    class Status(models.TextChoices):
        UPLOADED = "UPLOADED", "Uploaded"
        VALIDATED = "VALIDATED", "Validated"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_name = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=16, choices=Status, default=Status.UPLOADED)
    total_rows = models.PositiveIntegerField(default=0)
    accepted_rows = models.PositiveIntegerField(default=0)
    review_rows = models.PositiveIntegerField(default=0)
    excluded_rows = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    completed_at = models.DateTimeField(null=True, blank=True)
    summary = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-created_at",)


class EquipmentImportRow(TimestampedModel):
    class Status(models.TextChoices):
        ACCEPTED = "ACCEPTED", "Accepted"
        REVIEW = "REVIEW", "Needs review"
        EXCLUDED = "EXCLUDED", "Excluded"
        ARTIFACT = "ARTIFACT", "Artifact/template"
        ERROR = "ERROR", "Error"

    batch = models.ForeignKey(EquipmentImportBatch, on_delete=models.PROTECT, related_name="rows")
    sheet_name = models.CharField(max_length=120)
    row_number = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status)
    raw_data = models.JSONField(default=dict)
    normalized_data = models.JSONField(default=dict)
    messages = models.JSONField(default=list)
    asset = models.ForeignKey(Asset, null=True, blank=True, on_delete=models.PROTECT, related_name="import_rows")

    class Meta:
        ordering = ("sheet_name", "row_number")
        constraints = [models.UniqueConstraint(fields=("batch", "sheet_name", "row_number"), name="equipment_import_row_unique")]
