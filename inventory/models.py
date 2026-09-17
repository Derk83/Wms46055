from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from barcode import Code128
from barcode.writer import ImageWriter
import qrcode
import io
import base64
import math
import random
import uuid


_LEDGER_WRITE_TOKEN = object()
_AUDIT_WIPE_TOKEN = object()


def _combine_delete_results(results):
    total = 0
    details = {}
    for count, breakdown in results:
        total += count
        for label, value in breakdown.items():
            details[label] = details.get(label, 0) + value
    return total, details


class ReceivingLineQuerySet(models.QuerySet):
    """Keep posted receiving quantities on the audited instance paths."""

    _ledger_fields = {
        "ticket", "ticket_id", "item", "item_id", "quantity", "ledger_reference"
    }

    def update(self, **kwargs):
        if self._ledger_fields.intersection(kwargs):
            raise ValidationError(
                "Posted receiving ticket, item, quantity, and ledger reference cannot be updated in bulk."
            )
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        if self._ledger_fields.intersection(fields):
            raise ValidationError(
                "Posted receiving ticket, item, quantity, and ledger reference cannot be updated in bulk."
            )
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def bulk_create(self, *args, **kwargs):
        raise ValidationError("Receiving lines must be created individually so stock is audited.")

    def delete(self):
        with transaction.atomic():
            lines = list(self.select_for_update())
            return _combine_delete_results(line.delete() for line in lines)


class ReceivingTicketQuerySet(models.QuerySet):
    """Route bulk ticket deletion through stock-reversing instance deletion."""

    def delete(self):
        with transaction.atomic():
            tickets = list(self.select_for_update())
            return _combine_delete_results(ticket.delete() for ticket in tickets)


class ManagedGroupRole(models.Model):
    """Stable identity for application-managed groups whose display names are editable."""

    role_key = models.CharField(max_length=64, unique=True)
    group = models.OneToOneField("auth.Group", on_delete=models.CASCADE, related_name="managed_role")

    def __str__(self):
        return f"{self.role_key}: {self.group.name}"


class PortalAccessRequest(models.Model):
    """Privacy-limited application for a local material-request account."""

    class Status(models.TextChoices):
        UNVERIFIED = "unverified", "Awaiting email verification"
        PENDING_REVIEW = "pending_review", "Pending manager review"
        MORE_INFO = "more_info", "More information requested"
        APPROVED_SETUP = "approved_setup", "Approved; awaiting password setup"
        ACTIVE = "active", "Active"
        DENIED = "denied", "Denied"
        EXPIRED = "expired", "Expired"

    email = models.EmailField(unique=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    full_name = models.CharField(max_length=150)
    position = models.CharField(max_length=150)
    contact_number = models.CharField(max_length=40)
    department = models.CharField(max_length=150, blank=True)
    project_jobsite = models.CharField(max_length=200, blank=True)
    sponsor = models.CharField(max_length=150, blank=True)
    business_reason = models.TextField(blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.UNVERIFIED, db_index=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_portal_access_requests")
    review_notes = models.TextField(blank=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="portal_access_request")
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    access_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [("review_portalaccessrequest", "Can review portal access requests")]

    def __str__(self):
        return f"{self.email} ({self.get_status_display()})"


class PortalAccessToken(models.Model):
    class Purpose(models.TextChoices):
        VERIFY_EMAIL = "verify_email", "Verify email"
        MORE_INFO = "more_info", "Provide more information"
        SET_PASSWORD = "set_password", "Set password"

    request = models.ForeignKey(PortalAccessRequest, on_delete=models.CASCADE, related_name="tokens")
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    digest = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["purpose", "digest"])]


class PortalAccessAuditEvent(models.Model):
    request = models.ForeignKey(PortalAccessRequest, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events")
    event_type = models.CharField(max_length=48, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]


class PortalAccessThrottle(models.Model):
    """Database-backed fixed-window counter; keys are irreversibly digested."""

    action = models.CharField(max_length=32)
    key_digest = models.CharField(max_length=64)
    window_started_at = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["action", "key_digest"], name="unique_portal_throttle_key")]


def upload_to_item_images(instance, filename):
    """Generate upload path for item images."""
    return f"item_images/{instance.item.part_number}/{filename}"


def upload_to_receiving_docs(instance, filename):
    """Generate upload path for receiving documents."""
    return f"receiving_docs/{timezone.now().year}/{timezone.now().month:02d}/{filename}"


def generate_code128_barcode(value):
    """Generate a Code 128 barcode image as base64 encoded PNG."""
    buffer = io.BytesIO()
    barcode = Code128(value, writer=ImageWriter())
    barcode.write(buffer, options={
        'module_width': 0.3,
        'module_height': 15,
        'font_size': 10,
        'text_distance': 5,
        'quiet_zone': 2,
        'write_text': True,
    })
    buffer.seek(0)
    return base64.b64encode(buffer.getvalue()).decode('utf-8')


def generate_qr_code(data):
    """Generate a QR code image as base64 encoded PNG."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return base64.b64encode(buffer.getvalue()).decode('utf-8')


class CategoryChoices(models.TextChoices):
    OFCI = "OFCI", "OFCI"
    CFCI = "CFCI", "CFCI"
    CONSUMABLES = "Consumables", "Consumables"
    TOOLS = "Tools", "Tools"
    SAFETY = "Safety", "Safety"
    EQUIPMENT = "Equipment", "Equipment"
    SUPPLIES = "Supplies", "Supplies"
    MISC = "Misc", "Misc"


RACK_CHOICES = [(letter, letter) for letter in ("A", "B", "C", "D")]
SECTION_CHOICES = [(f"{i:02d}", f"{i:02d}") for i in range(1, 21)]
BIN_SLOT_CHOICES = [(f"{i:02d}", f"{i:02d}") for i in range(1, 7)]
STANDALONE_BIN_CHOICES = []
BIN_LOCATION_CHOICES = BIN_SLOT_CHOICES
STANDALONE_BIN_VALUES = {value for value, _label in STANDALONE_BIN_CHOICES}


class InventoryItem(models.Model):
    """An inventory item such as a specific warehouse product."""

    part_number = models.CharField(max_length=80)
    fb_part_number = models.CharField("FB Part #", max_length=120, blank=True, db_index=True)
    model_number = models.CharField("Model #", max_length=120, blank=True)
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=120, blank=True, choices=CategoryChoices.choices)
    description = models.TextField(blank=True)
    shipper = models.CharField(max_length=120, blank=True)
    quantity_on_hand = models.IntegerField(default=0)
    unit = models.CharField(max_length=40, default="each")
    building_room = models.CharField("BLDG/Room #", max_length=120, blank=True)
    rack = models.CharField(max_length=2, blank=True, choices=RACK_CHOICES)
    section = models.CharField(max_length=2, blank=True, choices=SECTION_CHOICES)
    bin_location = models.CharField(max_length=32, blank=True, default="", choices=BIN_LOCATION_CHOICES, help_text="Numeric rack bin from 1 to 6")
    low_stock_threshold = models.IntegerField(default=0)
    barcode_value = models.CharField(max_length=160, unique=True, null=True, blank=True)
    qr_code_value = models.CharField(max_length=160, unique=True, null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Item photos and documents
    photo = models.ImageField(upload_to="item_photos/", blank=True, null=True, help_text="Main product photo")
    document = models.FileField(upload_to="item_documents/", blank=True, null=True, help_text="Supporting document (PDF, spec sheet, etc.)")
    document_name = models.CharField(max_length=255, blank=True, help_text="Friendly name for the document")

    class Meta:
        ordering = ["category", "name"]
        permissions = [
            ("import_inventory", "Can import inventory spreadsheets"),
            ("export_inventory", "Can export inventory spreadsheets"),
            ("bulk_adjust_inventory", "Can bulk adjust inventory"),
            ("clear_inventory", "Can clear all inventory"),
            ("manage_storage_locations", "Can manage storage locations"),
            ("view_qr_codes", "Can view QR Codes page"),
            ("print_qr_codes", "Can print QR Codes"),
            ("view_app_qr_code", "Can view app login QR code"),
            ("scan_codes", "Can scan barcodes and QR codes"),
            ("manage_item_barcodes", "Can generate item barcodes and assign UPCs"),
            ("manage_users", "Can manage warehouse users"),
            ("manage_groups", "Can create and delete warehouse groups"),
            ("manage_group_permissions", "Can edit warehouse group permissions"),
            ("perform_cycle_count", "Can perform cycle counts"),
            ("manage_cycle_counts", "Can create and manage cycle counts"),
            ("archive_cycle_counts", "Can archive completed cycle counts"),
        ]

    def __str__(self):
        return f"{self.part_number} — {self.name}"

    def get_absolute_url(self):
        return reverse("item_detail", args=[self.pk])

    @property
    def is_low_stock(self):
        if self.low_stock_threshold and self.quantity_on_hand <= self.low_stock_threshold:
            return True
        if self.quantity_on_hand < 0:
            return True
        return False

    @property
    def stock_status(self):
        if self.quantity_on_hand < 0:
            return "negative"
        if self.low_stock_threshold and self.quantity_on_hand <= self.low_stock_threshold:
            return "low"
        return "ok"

    @property
    def storage_location(self):
        """Picker-facing storage location, e.g. A-03-06."""
        if self.bin_location in STANDALONE_BIN_VALUES:
            return self.bin_location
        if self.rack and self.section and self.bin_location:
            return f"{self.rack}-{self.section.zfill(2)}-{self.bin_location.zfill(2)}"
        return self.bin_location.zfill(2) if self.bin_location.isdigit() else (self.bin_location or self.building_room or "")

    @property
    def barcode_image_b64(self):
        """Return a base64-encoded Code 128 barcode PNG for this item."""
        return generate_code128_barcode(self.auto_barcode_value)

    @property
    def current_qr_code_value(self):
        """Current scannable QR payload; regenerates from live inventory quantity."""
        return f"ITEM:{self.part_number}|QTY:{self.quantity_on_hand}"

    @property
    def qr_code_image_b64(self):
        """Return a base64-encoded QR code PNG with current inventory level data."""
        return generate_qr_code(self.current_qr_code_value)

    def get_barcode_value_from_name(self):
        """Generate a barcode value from the product name."""
        # Use the name as a clean barcode value, removing special chars
        import re
        clean = re.sub(r'[^A-Z0-9]+', '', self.name.upper())
        if not clean:
            clean = self.part_number
        return clean

    @property
    def auto_barcode_value(self):
        """Get or generate a barcode value based on product name."""
        if self.barcode_value:
            return self.barcode_value
        return self.get_barcode_value_from_name()

    def adjust_quantity(
        self, delta, transaction_type, user=None, pick_ticket=None,
        receiving_line=None, receiving_reference=None, reverses_transaction=None,
        notes="",
    ):
        with transaction.atomic():
            locked = InventoryItem.objects.select_for_update().get(pk=self.pk)
            new_quantity = locked.quantity_on_hand + delta
            if transaction_type == InventoryTransaction.TransactionType.PICK and new_quantity < 0:
                raise ValidationError(
                    f"Insufficient stock for {locked.part_number}: {locked.quantity_on_hand} available."
                )
            locked.quantity_on_hand = new_quantity
            locked.save(update_fields=["quantity_on_hand", "updated_at"])
            self.quantity_on_hand = locked.quantity_on_hand
            ledger_entry = InventoryTransaction(
                item=locked,
                transaction_type=transaction_type,
                quantity_delta=delta,
                pick_ticket=pick_ticket,
                receiving_line=receiving_line,
                receiving_reference=receiving_reference,
                reverses_transaction=reverses_transaction,
                notes=notes,
                created_by=user,
            )
            ledger_entry.save(_ledger_write_token=_LEDGER_WRITE_TOKEN)
            return ledger_entry

    def save(self, *args, **kwargs):
        if self.barcode_value:
            self.barcode_value = self.barcode_value.strip().upper()
        if self.qr_code_value:
            self.qr_code_value = self.qr_code_value.strip()
        if self.bin_location:
            raw_bin = self.bin_location.strip()
            self.bin_location = raw_bin.zfill(2) if raw_bin.isdigit() else raw_bin.upper()
        if self.rack:
            self.rack = self.rack.strip().upper()
        if self.section:
            raw_section = self.section.strip()
            self.section = raw_section.zfill(2) if raw_section.isdigit() else raw_section
        super().save(*args, **kwargs)


class PickTicket(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        PICKED = "PICKED", "Picked"
        RECEIVED = "RECEIVED", "Ready for Delivery"
        CLOSED = "CLOSED", "Closed/Delivered"

    ticket_number = models.CharField(max_length=20, unique=True, blank=True)
    date = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    picked_by_name = models.CharField(max_length=120)
    picked_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="picked_tickets",
    )
    qa_checked_by_name = models.CharField(max_length=120, blank=True)
    qa_checked_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="qa_checked_tickets",
    )
    received_by_name = models.CharField(max_length=120)
    requested_by_name = models.CharField(max_length=120)
    building_room = models.CharField("BLDG/Room #", max_length=120)
    location = models.CharField(max_length=160)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_pick_tickets",
    )
    assigned_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="acknowledged_pick_tickets",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("print_pickticket", "Can print material issue tickets"),
        ]

    def __str__(self):
        return self.ticket_number or "New pick ticket"

    def save(self, *args, **kwargs):
        if self.pk or self.ticket_number:
            return super().save(*args, **kwargs)
        super().save(*args, **kwargs)
        self.ticket_number = f"PT-{self.pk:06d}"
        PickTicket.objects.filter(pk=self.pk).update(ticket_number=self.ticket_number)

    def delete(self, *args, **kwargs):
        """Delete the ticket and reverse inventory for all lines via their delete() methods."""
        # Delete all lines first (will reverse inventory via PickTicketLine.delete())
        for line in self.lines.all():
            line.delete()
        super().delete(*args, **kwargs)


class PickTicketLine(models.Model):
    ticket = models.ForeignKey(PickTicket, related_name="lines", on_delete=models.CASCADE)
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    picked_quantity = models.PositiveIntegerField(null=True, blank=True)
    pick_variance_reason = models.TextField(blank=True)
    pick_variance_transaction = models.OneToOneField(
        "InventoryTransaction",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pick_variance_line",
    )
    reservation_transaction = models.OneToOneField(
        "InventoryTransaction",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reserved_pick_line",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(picked_quantity__isnull=True)
                    | models.Q(picked_quantity=models.F("quantity"))
                    | ~models.Q(pick_variance_reason="")
                ),
                name="pick_variance_requires_reason",
            ),
        ]

    def __str__(self):
        return f"{self.ticket.ticket_number}: {self.item.name} x {self.quantity}"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        if is_new and self.picked_quantity is None and self.ticket.status != PickTicket.Status.OPEN:
            self.picked_quantity = self.quantity
        with transaction.atomic():
            super().save(*args, **kwargs)
            if is_new:
                reservation_transaction = self.item.adjust_quantity(
                    -self.quantity,
                    InventoryTransaction.TransactionType.PICK,
                    user=self.ticket.created_by,
                    pick_ticket=self.ticket,
                    notes=f"Picked on {self.ticket.ticket_number}",
                )
                PickTicketLine.objects.filter(pk=self.pk).update(
                    reservation_transaction=reservation_transaction
                )
                self.reservation_transaction = reservation_transaction

    @transaction.atomic
    def delete(self, *args, **kwargs):
        """Delete the line and restore stock without overwriting concurrent changes.

        This is a compensating workflow that removes the original PICK entry —
        allowed via the audited `_audit_wipe_token` bypass, mirroring the same
        pattern as `inventory_clear`.
        """
        self = PickTicketLine.objects.select_for_update().select_related(
            "item", "ticket"
        ).get(pk=self.pk)
        InventoryItem.objects.select_for_update().get(pk=self.item_id)
        transaction_ids = []
        if self.reservation_transaction_id:
            transaction_ids.append(self.reservation_transaction_id)
        else:
            # Legacy safety fallback. The migration backfills normal historical
            # rows; if one escaped it, remove only one matching reservation.
            legacy_reservation_id = (
                InventoryTransaction.objects.filter(
                    item=self.item,
                    transaction_type=InventoryTransaction.TransactionType.PICK,
                    pick_ticket=self.ticket,
                    notes=f"Picked on {self.ticket.ticket_number}",
                )
                .order_by("created_at", "pk")
                .values_list("pk", flat=True)
                .first()
            )
            if legacy_reservation_id:
                transaction_ids.append(legacy_reservation_id)
        if self.pick_variance_transaction_id:
            transaction_ids.append(self.pick_variance_transaction_id)
        if transaction_ids:
            InventoryTransaction.objects._audit_wipe_query().filter(
                pk__in=transaction_ids
            ).delete()
        InventoryItem.objects.filter(pk=self.item_id).update(
            quantity_on_hand=models.F("quantity_on_hand")
            + (self.picked_quantity if self.picked_quantity is not None else self.quantity),
            updated_at=timezone.now(),
        )
        super().delete(*args, **kwargs)


class InventoryTransactionQuerySet(models.QuerySet):
    """The full inventory transaction log is an immutable ledger.

    All transaction types — RECEIPT, REVERSAL, PICK, ADJUSTMENT, IMPORT — are
    immutable to history rewriting. Any change requires a compensating entry.
    Bulk operations are restricted to safe, audited paths so that production
    code can never rewrite history.

    The only allowed bypass is the explicit `inventory_clear` destructive
    operation, which requires operator confirmation and is gated by an internal
    `_audit_wipe_token` keyword. The token must never be exposed to views.
    """

    _immutable_types = {"RECEIPT", "REVERSAL", "PICK", "ADJUSTMENT", "IMPORT"}

    def _contains_immutable(self):
        return self.filter(transaction_type__in=self._immutable_types).exists()

    def update(self, **kwargs):
        if kwargs.pop("_audit_wipe_token", None) == _AUDIT_WIPE_TOKEN:
            return super().update(**{k: v for k, v in kwargs.items() if not k.startswith("_")})
        if self._contains_immutable() or kwargs.get("transaction_type") in self._immutable_types:
            raise ValidationError(
                "Inventory transactions are immutable; create a compensating entry instead."
            )
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        objs = list(objs)
        object_ids = [obj.pk for obj in objs if obj.pk is not None]
        if (
            self.model.objects.filter(
                pk__in=object_ids, transaction_type__in=self._immutable_types
            ).exists()
            or any(obj.transaction_type in self._immutable_types for obj in objs)
        ):
            raise ValidationError(
                "Inventory transactions are immutable; create a compensating entry instead."
            )
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def bulk_create(self, objs, **kwargs):
        objs = list(objs)
        if any(obj.transaction_type in self._immutable_types for obj in objs):
            raise ValidationError(
                "Inventory transactions must be created through an audited stock operation."
            )
        return super().bulk_create(objs, **kwargs)

    def _unlink_reversed_receiving_line(self):
        """Internal use after a compensating reversal has been recorded."""
        if self.exclude(transaction_type="RECEIPT").exists():
            raise ValidationError("Only receipt transactions can be unlinked from reversed lines.")
        return super().update(receiving_line=None)

    def delete(self):
        # Bypass for the audited `inventory_clear` operator workflow
        if getattr(self, "_audit_wipe", False):
            return super().delete()
        if self._contains_immutable():
            raise ValidationError(
                "Inventory transactions are immutable; create a compensating entry instead."
            )
        return super().delete()

    def _audit_wipe_query(self):
        """Return a clone that may be wiped by audited compensating workflows.

        Allowed callers (must each be reviewed for safety):
          - inventory_clear: full operator wipe (requires explicit confirmation)
          - PickTicketLine.delete: reverses the line's PICK entry
          - any future compensating action that must undo a single entry

        Do not use this for ad-hoc updates — that defeats the immutability
        contract.
        """
        clone = self._chain()
        clone._audit_wipe = True
        return clone

    def _clone(self, **kwargs):
        clone = super()._clone(**kwargs)
        if getattr(self, "_audit_wipe", False):
            clone._audit_wipe = True
        return clone

    def _filter_or_exclude(self, negate, *args, **kwargs):
        # Strip our marker kwarg before ORM sees it
        kwargs.pop("_audit_wipe", None)
        return super()._filter_or_exclude(negate, *args, **kwargs)


class InventoryTransactionManager(models.Manager.from_queryset(InventoryTransactionQuerySet)):
    """Manager exposing the audited `_audit_wipe_query` shortcut."""

    def _audit_wipe_query(self):
        return self.get_queryset()._audit_wipe_query()


class InventoryTransaction(models.Model):
    class TransactionType(models.TextChoices):
        PICK = "PICK", "Pick ticket"
        RECEIPT = "RECEIPT", "Stock received"
        REVERSAL = "REVERSAL", "Receipt reversal"
        ADJUSTMENT = "ADJUSTMENT", "Manual adjustment"
        IMPORT = "IMPORT", "Initial import"

    item = models.ForeignKey(InventoryItem, related_name="transactions", on_delete=models.PROTECT)
    transaction_type = models.CharField(max_length=20, choices=TransactionType.choices)
    quantity_delta = models.IntegerField()
    pick_ticket = models.ForeignKey(PickTicket, null=True, blank=True, on_delete=models.SET_NULL)
    receiving_line = models.OneToOneField(
        "ReceivingLine", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="receipt_transaction",
    )
    receiving_reference = models.UUIDField(null=True, blank=True, db_index=True)
    reverses_transaction = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT,
        related_name="reversal_transaction",
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = InventoryTransactionManager()

    class Meta:
        ordering = ["-created_at"]
        base_manager_name = "objects"

    def __str__(self):
        return f"{self.item.part_number} {self.quantity_delta:+d} ({self.transaction_type})"

    @property
    def activity_description(self):
        quantity = abs(self.quantity_delta)
        units = "unit" if quantity == 1 else "units"
        item_label = f"{self.item.part_number} — {self.item.name}"
        ticket_label = self.pick_ticket.ticket_number if self.pick_ticket_id else ""
        if self.transaction_type == self.TransactionType.PICK:
            action = "Removed" if self.quantity_delta < 0 else "Returned"
            description = f"{action} {quantity} {units} {'from' if self.quantity_delta < 0 else 'to'} inventory for {item_label}"
            if ticket_label:
                description += f" on pick ticket {ticket_label}"
            return description + "."
        if self.transaction_type == self.TransactionType.RECEIPT:
            return f"Received {quantity} {units} into inventory for {item_label}."
        if self.transaction_type == self.TransactionType.REVERSAL:
            return f"Reversed a receipt by removing {quantity} {units} from {item_label}."
        if self.transaction_type == self.TransactionType.IMPORT:
            return f"Imported an opening balance of {self.quantity_delta:+d} {units} for {item_label}."
        direction = "Increased" if self.quantity_delta > 0 else "Reduced"
        description = f"{direction} inventory by {quantity} {units} for {item_label}"
        if ticket_label:
            description += f" while reconciling pick ticket {ticket_label}"
        return description + "."

    def save(self, *args, **kwargs):
        ledger_write_token = kwargs.pop("_ledger_write_token", None)
        if (
            self.pk is None
            and self.transaction_type in InventoryTransactionQuerySet._immutable_types
            and ledger_write_token is not _LEDGER_WRITE_TOKEN
        ):
            raise ValidationError(
                "Inventory transactions must be created through an audited stock operation."
            )
        if self.pk is not None:
            original_type = InventoryTransaction.objects.values_list(
                "transaction_type", flat=True
            ).get(pk=self.pk)
            if (
                original_type in InventoryTransactionQuerySet._immutable_types
                or self.transaction_type in InventoryTransactionQuerySet._immutable_types
            ):
                raise ValidationError(
                    "Inventory transactions are immutable; create a compensating entry instead."
                )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.transaction_type in InventoryTransactionQuerySet._immutable_types:
            raise ValidationError(
                "Inventory transactions are immutable; create a compensating entry instead."
            )
        return super().delete(*args, **kwargs)

    @classmethod
    def record_receipt(cls, item, quantity, user=None, notes=""):
        if quantity <= 0:
            raise ValueError("Receipt quantity must be positive")
        return item.adjust_quantity(quantity, cls.TransactionType.RECEIPT, user=user, notes=notes)

    @classmethod
    def record_adjustment(cls, item, delta, user=None, notes=""):
        if delta == 0:
            raise ValueError("Adjustment delta cannot be zero")
        return item.adjust_quantity(delta, cls.TransactionType.ADJUSTMENT, user=user, notes=notes)


class ReceivingTicket(models.Model):
    """A receiving ticket that can contain multiple line items."""
    ticket_number = models.CharField(max_length=20, unique=True, blank=True)
    date = models.DateTimeField(default=timezone.now)
    po_number = models.CharField(max_length=80, blank=True)
    vendor = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ReceivingTicketQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        base_manager_name = "objects"
        permissions = [
            ("receive_stock", "Can receive stock"),
            ("view_receiving_log", "Can view receiving log"),
            ("print_receivingticket", "Can print receiving tickets"),
        ]

    def __str__(self):
        return self.ticket_number or f"Receiving #{self.pk}"

    def save(self, *args, **kwargs):
        if self.pk or self.ticket_number:
            return super().save(*args, **kwargs)
        super().save(*args, **kwargs)
        self.ticket_number = f"RC-{self.pk:06d}"
        ReceivingTicket.objects.filter(pk=self.pk).update(ticket_number=self.ticket_number)

    def delete(self, *args, **kwargs):
        """Atomically reverse every line before deleting the receiving ticket."""
        if self.pk is None:
            return (0, {})
        with transaction.atomic():
            locked_ticket = ReceivingTicket.objects.select_for_update().get(pk=self.pk)
            for line in locked_ticket.lines.select_for_update():
                line.delete()
            result = models.Model.delete(locked_ticket, *args, **kwargs)
            self.pk = None
            return result


class ReceivingLine(models.Model):
    """A line item within a receiving ticket."""
    ticket = models.ForeignKey(ReceivingTicket, related_name="lines", on_delete=models.CASCADE)
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    notes = models.TextField(blank=True)
    shipper = models.CharField(max_length=120, blank=True)
    ledger_reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ReceivingLineQuerySet.as_manager()

    class Meta:
        ordering = ["created_at"]
        base_manager_name = "objects"

    def __str__(self):
        return f"{self.ticket.ticket_number}: {self.item.name} x {self.quantity}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            original = ReceivingLine.objects.only(
                "ticket_id", "item_id", "quantity", "ledger_reference"
            ).get(pk=self.pk)
            if (
                original.ticket_id != self.ticket_id
                or original.item_id != self.item_id
                or original.quantity != self.quantity
                or original.ledger_reference != self.ledger_reference
            ):
                raise ValidationError(
                    "Received ticket, item, quantity, and ledger reference cannot be changed directly; replace the line instead."
                )
            return super().save(*args, **kwargs)

        with transaction.atomic():
            super().save(*args, **kwargs)
            self.item.adjust_quantity(
                self.quantity,
                InventoryTransaction.TransactionType.RECEIPT,
                user=self.ticket.created_by,
                receiving_line=self,
                receiving_reference=self.ledger_reference,
                notes=f"Received on {self.ticket.ticket_number}" + (f": {self.notes}" if self.notes else ""),
            )

    def delete(self, *args, **kwargs):
        """Atomically reverse this exact receipt while preserving ledger history."""
        if self.pk is None:
            return (0, {})
        with transaction.atomic():
            locked_line = ReceivingLine.objects.select_for_update().select_related(
                "item", "ticket"
            ).get(pk=self.pk)
            receipt = InventoryTransaction.objects.select_for_update().filter(
                receiving_line=locked_line,
                transaction_type=InventoryTransaction.TransactionType.RECEIPT,
            ).first()
            if receipt is None:
                raise ValidationError("Cannot reverse a receiving line without its receipt ledger entry.")
            if (
                receipt.item_id != locked_line.item_id
                or receipt.quantity_delta != locked_line.quantity
                or receipt.receiving_reference != locked_line.ledger_reference
            ):
                raise ValidationError("The receiving line does not match its receipt ledger entry.")
            locked_line.item.adjust_quantity(
                -locked_line.quantity,
                InventoryTransaction.TransactionType.REVERSAL,
                user=locked_line.ticket.created_by,
                receiving_reference=locked_line.ledger_reference,
                reverses_transaction=receipt,
                notes=(
                    f"Reversed receipt line {locked_line.pk} from "
                    f"{locked_line.ticket.ticket_number}"
                ),
            )
            InventoryTransaction.objects.filter(
                pk=receipt.pk
            )._unlink_reversed_receiving_line()
            result = models.Model.delete(locked_line, *args, **kwargs)
            self.pk = None
            return result


class ReceivingDocument(models.Model):
    """Document attached to a receiving event (delivery note, packing slip, photo, etc.)."""

    item = models.ForeignKey(InventoryItem, related_name="receiving_docs", on_delete=models.CASCADE)
    document = models.FileField(upload_to=upload_to_receiving_docs)
    original_filename = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)
    source = models.CharField(max_length=20, choices=[("file", "File Upload"), ("camera", "Phone Camera")], default="file")

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.original_filename} for {self.item.part_number}"

    def file_extension(self):
        return self.original_filename.split(".")[-1].lower() if "." in self.original_filename else ""


class ItemImage(models.Model):
    """Additional images for an inventory item (gallery)."""

    item = models.ForeignKey(InventoryItem, related_name="images", on_delete=models.CASCADE)
    image = models.ImageField(upload_to=upload_to_item_images)
    caption = models.CharField(max_length=255, blank=True)
    is_primary = models.BooleanField(default=False, help_text="Set as primary display image")
    content_type = models.CharField(max_length=64, blank=True, help_text="MIME type captured at upload time")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_primary", "uploaded_at"]

    def __str__(self):
        return f"Image for {self.item.part_number}"


class ApprovalRequest(models.Model):
    """Pending workflow item that can be approved or rejected by a manager."""

    class ActionType(models.TextChoices):
        INVENTORY_EDIT = "INVENTORY_EDIT", "Inventory edit"
        INVENTORY_DELETE = "INVENTORY_DELETE", "Inventory delete"
        PICK_TICKET_EDIT = "PICK_TICKET_EDIT", "Pick ticket edit"
        RECEIVING_EDIT = "RECEIVING_EDIT", "Receiving edit"
        BULK_ADJUSTMENT = "BULK_ADJUSTMENT", "Bulk adjustment"
        OTHER = "OTHER", "Other"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    action_type = models.CharField(max_length=40, choices=ActionType.choices, default=ActionType.OTHER)
    object_type = models.CharField(max_length=80, blank=True)
    object_id = models.CharField(max_length=80, blank=True)
    summary = models.CharField(max_length=255)
    details = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="approval_requests",
        on_delete=models.PROTECT,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="reviewed_approval_requests",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["status", "-requested_at"]

    def __str__(self):
        return f"{self.summary} ({self.get_status_display()})"

    @property
    def target_label(self):
        if self.object_type and self.object_id:
            return f"{self.object_type} #{self.object_id}"
        return self.object_type or "Workflow item"

    def mark_reviewed(self, status, reviewer, notes=""):
        if status not in {self.Status.APPROVED, self.Status.REJECTED}:
            raise ValueError("Approval status must be approved or rejected.")
        self.status = status
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.review_notes = notes
        self.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes"])


class ItemDocument(models.Model):
    """Additional documents for an inventory item (spec sheets, manuals, etc.)."""

    item = models.ForeignKey(InventoryItem, related_name="documents", on_delete=models.CASCADE)
    document = models.FileField(upload_to="item_documents/")
    original_filename = models.CharField(max_length=255)
    description = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=64, blank=True, help_text="MIME type captured at upload time")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.original_filename} for {self.item.part_number}"


class RecentWork(models.Model):
    """A user's latest successfully opened warehouse records."""

    class Kind(models.TextChoices):
        INVENTORY_ITEM = "inventory_item", "Inventory Item"
        PICK_TICKET = "pick_ticket", "Pick Ticket"
        MATERIAL_REQUEST = "material_request", "Material Request"
        RECEIVING_TICKET = "receiving_ticket", "Receiving Ticket"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="recent_work",
        on_delete=models.CASCADE,
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    object_id = models.PositiveBigIntegerField()
    label = models.CharField(max_length=255)
    url = models.CharField(max_length=500)
    viewed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-viewed_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "kind", "object_id"],
                name="unique_recent_work_per_user_object",
            )
        ]
        indexes = [models.Index(fields=["user", "-viewed_at"])]

    def __str__(self):
        return f"{self.user}: {self.label}"


class MaterialRequest(models.Model):
    """A customer request fulfilled by its automatically linked pick ticket."""

    request_number = models.CharField(max_length=20, unique=True, blank=True)
    requestor_name = models.CharField(max_length=120, blank=True)
    requestor_email = models.EmailField(blank=True)
    urgent = models.BooleanField(default=False)
    building_room = models.CharField("BLDG/Room #", max_length=120, blank=True)
    location = models.CharField(max_length=160, blank=True)
    delivery_at = models.DateTimeField("Delivery date/time", null=True, blank=False)
    notes = models.TextField(blank=True)
    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="material_requests", on_delete=models.PROTECT
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="assigned_material_requests",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    pick_ticket = models.OneToOneField(
        PickTicket, related_name="material_request", on_delete=models.PROTECT
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    delivery_acceptance_confirmed_at = models.DateTimeField(null=True, blank=True)
    delivery_acceptance_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="confirmed_material_request_deliveries",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    delivery_not_ready_at = models.DateTimeField(null=True, blank=True)
    delivery_not_ready_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="declined_material_request_deliveries",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    delivery_response_note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["delivery_at"],
                condition=models.Q(delivery_at__isnull=False),
                name="unique_material_request_delivery_slot",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        delivery_acceptance_confirmed_at__isnull=True,
                        delivery_acceptance_confirmed_by__isnull=True,
                    )
                    | models.Q(
                        delivery_acceptance_confirmed_at__isnull=False,
                        delivery_acceptance_confirmed_by__isnull=False,
                    )
                ),
                name="material_request_acceptance_fields_paired",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(delivery_not_ready_at__isnull=True, delivery_not_ready_by__isnull=True)
                    | models.Q(delivery_not_ready_at__isnull=False, delivery_not_ready_by__isnull=False)
                ),
                name="material_request_not_ready_fields_paired",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(delivery_acceptance_confirmed_at__isnull=True)
                    | models.Q(delivery_not_ready_at__isnull=True)
                ),
                name="material_request_delivery_response_exclusive",
            ),
        ]
        permissions = [
            ("access_material_request_portal", "Can access the material request portal"),
            ("view_all_materialrequests", "Can view and manage all material requests"),
        ]

    def __str__(self):
        return self.request_number or "New material request"

    def save(self, *args, **kwargs):
        if self.pk or self.request_number:
            return super().save(*args, **kwargs)
        super().save(*args, **kwargs)
        self.request_number = f"MR-{self.pk:06d}"
        MaterialRequest.objects.filter(pk=self.pk).update(request_number=self.request_number)

    @property
    def creation_event(self):
        return self.events.get(event_type=MaterialRequestEvent.EventType.CREATED)


class MaterialRequestLine(models.Model):
    class ShortageAction(models.TextChoices):
        AVAILABLE_ONLY = "available_only", "Use available stock and cancel the rest"
        BACKORDER = "backorder", "Request the rest when available"
        PROCUREMENT = "procurement", "Ask Procurement to purchase the rest"

    material_request = models.ForeignKey(
        MaterialRequest, related_name="lines", on_delete=models.CASCADE
    )
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    allocated_quantity = models.PositiveIntegerField(default=0)
    shortage_action = models.CharField(
        max_length=24, choices=ShortageAction.choices, blank=True, default=""
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["material_request", "item"], name="unique_item_per_material_request"
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="material_request_quantity_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(allocated_quantity__lte=models.F("quantity")),
                name="material_request_allocation_not_over_requested",
            ),
        ]

    def __str__(self):
        return f"{self.material_request.request_number}: {self.item.name} x {self.quantity}"

    @property
    def shortage_quantity(self):
        return max(0, self.quantity - self.allocated_quantity)


class MaterialBackorder(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        PARTIAL = "PARTIAL", "Partially fulfilled"
        READY = "READY", "Ready to fulfill"
        FULFILLED = "FULFILLED", "Fulfilled"
        CANCELLED = "CANCELLED", "Cancelled"

    backorder_number = models.CharField(max_length=20, unique=True, blank=True)
    line = models.OneToOneField(
        MaterialRequestLine, related_name="backorder", on_delete=models.CASCADE
    )
    quantity = models.PositiveIntegerField()
    fulfilled_quantity = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        permissions = [
            ("manage_backorders", "Can manage and fulfill material backorders"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="backorder_quantity_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(fulfilled_quantity__lte=models.F("quantity")),
                name="backorder_fulfilled_not_over_quantity",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk or self.backorder_number:
            return super().save(*args, **kwargs)
        super().save(*args, **kwargs)
        self.backorder_number = f"BO-{self.pk:06d}"
        MaterialBackorder.objects.filter(pk=self.pk).update(
            backorder_number=self.backorder_number
        )

    @property
    def remaining_quantity(self):
        return max(0, self.quantity - self.fulfilled_quantity)

    def __str__(self):
        return self.backorder_number or "New backorder"


class ProcurementRequisition(models.Model):
    class Status(models.TextChoices):
        NEW = "NEW", "New"
        REVIEW = "REVIEW", "Under review"
        ORDERED = "ORDERED", "Ordered"
        PARTIAL = "PARTIAL", "Partially received"
        RECEIVED = "RECEIVED", "Received"
        CLOSED = "CLOSED", "Closed"
        CANCELLED = "CANCELLED", "Cancelled"

    requisition_number = models.CharField(max_length=20, unique=True, blank=True)
    backorder = models.OneToOneField(
        MaterialBackorder, related_name="procurement_requisition", on_delete=models.CASCADE
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    vendor = models.CharField(max_length=160, blank=True)
    po_number = models.CharField(max_length=80, blank=True)
    ordered_quantity = models.PositiveIntegerField(default=0)
    received_quantity = models.PositiveIntegerField(default=0)
    expected_delivery_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="procurement_requisitions", on_delete=models.PROTECT
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="updated_procurement_requisitions",
        null=True, blank=True, on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status", "-created_at"]
        permissions = [
            ("manage_procurement_requisitions", "Can manage procurement requisitions"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(received_quantity__lte=models.F("ordered_quantity")),
                name="procurement_received_not_over_ordered",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk or self.requisition_number:
            return super().save(*args, **kwargs)
        super().save(*args, **kwargs)
        self.requisition_number = f"PRQ-{self.pk:06d}"
        ProcurementRequisition.objects.filter(pk=self.pk).update(
            requisition_number=self.requisition_number
        )

    def __str__(self):
        return self.requisition_number or "New procurement requisition"


class BackorderFulfillment(models.Model):
    backorder = models.ForeignKey(
        MaterialBackorder, related_name="fulfillments", on_delete=models.CASCADE
    )
    pick_ticket = models.OneToOneField(
        PickTicket, related_name="backorder_fulfillment", on_delete=models.PROTECT
    )
    quantity = models.PositiveIntegerField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.backorder.backorder_number}: {self.quantity}"


class MaterialRequestEvent(models.Model):
    """Durable notification event for a material-request workflow change."""

    class EventType(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        DELETED = "deleted", "Deleted"
        STATUS_CHANGED = "status_changed", "Status changed"
        DELIVERY_ACCEPTANCE_CONFIRMED = "delivery_acceptance_confirmed", "Delivery acceptance confirmed"
        DELIVERY_NOT_READY = "delivery_not_ready", "Delivery not ready"

    material_request = models.ForeignKey(
        MaterialRequest, related_name="events", null=True, blank=True, on_delete=models.SET_NULL
    )
    event_type = models.CharField(max_length=40, choices=EventType.choices, default=EventType.CREATED)
    request_number_snapshot = models.CharField(max_length=40, blank=True)
    ticket_number_snapshot = models.CharField(max_length=40, blank=True)
    requestor_snapshot = models.CharField(max_length=200, blank=True)
    old_status = models.CharField(max_length=16, blank=True)
    new_status = models.CharField(max_length=16, blank=True)
    change_summary = models.TextField(blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="material_request_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["material_request"],
                condition=models.Q(event_type="created"),
                name="unique_created_event_per_material_request",
            ),
        ]

    def __str__(self):
        request_number = self.request_number_snapshot
        if self.material_request_id:
            request_number = self.material_request.request_number
        return f"{self.get_event_type_display()}: {request_number or 'deleted request'}"


class PushSubscription(models.Model):
    """One browser push endpoint owned by one authenticated user."""

    class Audience(models.TextChoices):
        WMS = "wms", "Warehouse"
        REQUEST_PORTAL = "request_portal", "Request portal"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions"
    )
    endpoint = models.URLField(max_length=2048, unique=True)
    p256dh = models.CharField(max_length=512)
    auth = models.CharField(max_length=512)
    user_agent = models.CharField(max_length=512, blank=True)
    session_key = models.CharField(max_length=40, blank=True, db_index=True)
    audience = models.CharField(max_length=20, choices=Audience.choices, default=Audience.WMS)
    enabled = models.BooleanField(default=True)
    failure_count = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Push subscription for {self.user}"


class PushDelivery(models.Model):
    """Transactional outbox row for a material-request push notification."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RETRY = "retry", "Retry scheduled"
        PROCESSING = "processing", "Processing"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Subscription expired"

    event = models.ForeignKey(
        MaterialRequestEvent, on_delete=models.CASCADE, related_name="push_deliveries"
    )
    subscription = models.ForeignKey(
        PushSubscription, on_delete=models.SET_NULL, related_name="deliveries", null=True
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "subscription"],
                name="unique_push_delivery_per_event_subscription",
            )
        ]
        indexes = [models.Index(fields=["status", "next_attempt_at"])]

    def __str__(self):
        return f"Push delivery {self.pk} ({self.status})"


class CycleCount(models.Model):
    """A cycle count batch: one or more category/percentage pairs.

    The manager picks a random subset of items from each chosen category and
    freezes them as ``CycleCountItem`` rows so the same selection can be
    counted, reviewed, and reconciled later.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    name = models.CharField(max_length=160, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cycle_counts_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
    )
    notes = models.TextField(blank=True)
    # Archive support: a completed cycle count can be archived to remove it
    # from the active list while preserving the audit trail (PDFs, signatures,
    # reconciliation data). Mirrors MaterialRequest.archived_at.
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="cycle_counts_archived",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        # Note: cycle-count permissions are declared on InventoryItem.Meta.permissions
        # so they show up once in the group permissions UI. The views gate on
        # ``inventory.perform_cycle_count`` / ``inventory.manage_cycle_counts``.

    def __str__(self):
        label = self.name or f"CC-{self.pk:05d}"
        return f"{label} ({self.get_status_display()})"

    @property
    def display_name(self):
        return self.name or f"CC-{self.pk:05d}"

    @property
    def total_items(self):
        return self.items.count()

    @property
    def counted_items(self):
        return self.items.filter(counted_quantity__isnull=False).count()

    @property
    def variance_count(self):
        return self.items.exclude(
            counted_quantity__isnull=True,
        ).exclude(
            counted_quantity=models.F("system_quantity"),
        ).count()

    @property
    def is_archived(self):
        return self.archived_at is not None

    @property
    def can_be_archived(self):
        """Only completed counts are eligible for archiving — the audit trail
        is sealed at completion, so archiving is a pure UI housekeeping move
        (removes the row from the active list while preserving the data)."""
        return self.status == self.Status.COMPLETED and not self.is_archived


class CycleCountItem(models.Model):
    """A single inventory item picked for a cycle count.

    ``system_quantity`` is frozen at creation time so the picker can record
    what they actually counted without being biased by the live on-hand value.
    """

    cycle_count = models.ForeignKey(
        CycleCount,
        on_delete=models.CASCADE,
        related_name="items",
    )
    category = models.CharField(max_length=120)
    item = models.ForeignKey(
        InventoryItem,
        on_delete=models.PROTECT,
        related_name="cycle_count_entries",
    )
    system_quantity = models.IntegerField()
    counted_quantity = models.IntegerField(null=True, blank=True)
    counted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="cycle_count_entries_counted",
        null=True,
        blank=True,
    )
    counted_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["category", "item__name"]
        indexes = [
            models.Index(fields=["cycle_count", "category"]),
        ]

    def __str__(self):
        return f"{self.item.part_number} ({self.category})"

    @property
    def variance(self):
        if self.counted_quantity is None:
            return None
        return self.counted_quantity - self.system_quantity

    @property
    def has_variance(self):
        v = self.variance
        return v is not None and v != 0


def pick_random_items_for_cycle_count(percent_by_category, seed=None):
    """Return ``(category, [InventoryItem, ...])`` for each category.

    ``percent_by_category`` is an iterable of ``(category_name, percent_int)``
    pairs. Percent is clamped to 1–100. Items are picked uniformly at random
    from active items in the given category. An optional ``seed`` makes the
    selection reproducible for tests.
    """
    rng = random.Random(seed)
    selections = []
    for category, percent in percent_by_category:
        percent = max(1, min(100, int(percent)))
        items = list(
            InventoryItem.objects.filter(category=category, active=True).order_by("id")
        )
        if not items:
            selections.append((category, []))
            continue
        count = max(1, math.ceil(len(items) * percent / 100))
        rng.shuffle(items)
        selections.append((category, items[:count]))
    return selections
