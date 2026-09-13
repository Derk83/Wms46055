from django import forms
from django.contrib.auth.models import Group, User
from django.db.models import Q
from django.forms import BaseInlineFormSet, formset_factory, inlineformset_factory
from django.utils import timezone

from .models import (
    BIN_LOCATION_CHOICES,
    CategoryChoices,
    InventoryItem,
    MaterialRequest,
    MaterialRequestLine,
    PickTicket,
    PickTicketLine,
    RACK_CHOICES,
    ReceivingLine,
    ReceivingTicket,
    SECTION_CHOICES,
)


class GroupRenameForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ["name"]

    def clean_name(self):
        return self.cleaned_data["name"].strip()


DELIVERY_TIME_CHOICES = [("", "Select time")] + [
    (f"{hour:02d}:{minute:02d}", forms.TimeField().to_python(f"{hour:02d}:{minute:02d}").strftime("%-I:%M %p"))
    for hour in range(24)
    for minute in (0, 15, 30, 45)
]


class QuarterHourSplitDateTimeWidget(forms.SplitDateTimeWidget):
    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, date_format="%Y-%m-%d", time_format="%H:%M")
        self.widgets[0] = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
        self.widgets[1] = forms.Select(choices=DELIVERY_TIME_CHOICES)

    def decompress(self, value):
        if value:
            return [value.date(), value.strftime("%H:%M")]
        return [None, None]


class PaddedNumericChoiceField(forms.ChoiceField):
    def to_python(self, value):
        value = super().to_python(value)
        return value.zfill(2) if value and value.isdigit() else value


class MaterialRequestForm(forms.ModelForm):
    delivery_at = forms.SplitDateTimeField(
        required=True,
        label="Delivery date & time",
        input_date_formats=["%Y-%m-%d"],
        input_time_formats=["%H:%M"],
        widget=QuarterHourSplitDateTimeWidget(),
        error_messages={"required": "Delivery date and time are required."},
    )

    class Meta:
        model = MaterialRequest
        fields = ["requestor_name", "requestor_email", "building_room", "location", "delivery_at", "urgent", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        data = args[0] if args else kwargs.get("data")
        if data is not None and data.get("delivery_at") and not data.get("delivery_at_0"):
            copied = data.copy()
            legacy_value = str(data.get("delivery_at"))
            separator = "T" if "T" in legacy_value else " "
            date_part, _, time_part = legacy_value.partition(separator)
            copied["delivery_at_0"] = date_part
            copied["delivery_at_1"] = time_part[:5]
            if args:
                args = (copied, *args[1:])
            else:
                kwargs["data"] = copied
        super().__init__(*args, **kwargs)
        # Requestor Name, Email, and Location are required for every material
        # request so the warehouse always has a way to confirm or contact the
        # person who asked for the material and where it should be delivered.
        self.fields["requestor_name"].required = True
        self.fields["requestor_name"].error_messages["required"] = "Requestor name is required."
        self.fields["requestor_email"].required = True
        self.fields["requestor_email"].error_messages["required"] = "Requestor email is required so the warehouse can send the ready-for-delivery confirmation."
        self.fields["requestor_email"].help_text = (
            "Receives a ready-for-delivery email with secure response buttons."
        )
        self.fields["requestor_email"].widget.attrs.update({
            "autocomplete": "email", "placeholder": "requestor@example.com"
        })
        self.fields["location"].required = True
        self.fields["location"].error_messages["required"] = "Delivery location is required so the picker knows where to deliver the request."

    def clean_delivery_at(self):
        delivery_at = self.cleaned_data.get("delivery_at")
        if delivery_at and (delivery_at.minute % 15 or delivery_at.second or delivery_at.microsecond):
            raise forms.ValidationError("Choose a delivery time in a 15-minute increment.")
        if delivery_at:
            occupied = MaterialRequest.objects.filter(delivery_at=delivery_at)
            if self.instance.pk:
                occupied = occupied.exclude(pk=self.instance.pk)
            if occupied.exists():
                raise forms.ValidationError(
                    "That delivery time is already scheduled. Please choose another time."
                )
        return delivery_at


class MaterialRequestLineForm(forms.ModelForm):
    class Meta:
        model = MaterialRequestLine
        fields = ["item", "quantity", "notes"]
        widgets = {
            "item": forms.Select(attrs={"class": "request-item-select"}),
            "quantity": forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"}),
            "notes": forms.Textarea(attrs={"rows": 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["quantity"].min_value = 1
        self.fields["quantity"].widget.attrs["min"] = 1
        available = Q(active=True)
        if self.instance and self.instance.item_id:
            available |= Q(pk=self.instance.item_id)
        self.fields["item"].queryset = InventoryItem.objects.filter(available).order_by(
            "part_number"
        )


class BaseMaterialRequestLineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        item_ids = set()
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            item = form.cleaned_data.get("item")
            if item and item.pk in item_ids:
                raise forms.ValidationError("Duplicate items are not allowed in a material request.")
            if item:
                item_ids.add(item.pk)


MaterialRequestLineFormSet = inlineformset_factory(
    MaterialRequest,
    MaterialRequestLine,
    form=MaterialRequestLineForm,
    formset=BaseMaterialRequestLineFormSet,
    extra=2,
    can_delete=True,
    min_num=1,
    validate_min=True,
)


class DeliveryResponseForm(forms.Form):
    RESPONSE_CHOICES = (("ready", "Confirm ready for delivery"), ("not_ready", "Not ready"))

    response = forms.ChoiceField(choices=RESPONSE_CHOICES)
    delivery_at = forms.SplitDateTimeField(
        required=False,
        label="Reschedule delivery",
        help_text="Optional. Choose a new date and time if the current slot does not work.",
        widget=forms.SplitDateTimeWidget(
            date_attrs={"type": "date", "aria-label": "Reschedule delivery date"},
            time_attrs={"type": "time", "aria-label": "Reschedule delivery time", "step": "900"},
            date_format="%Y-%m-%d",
        ),
        input_date_formats=["%Y-%m-%d"],
        input_time_formats=["%H:%M"],
    )
    note = forms.CharField(
        required=False,
        max_length=500,
        label="Note for the warehouse",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Optional availability details"}),
    )

    def __init__(self, *args, material_request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.material_request = material_request

    def clean_delivery_at(self):
        delivery_at = self.cleaned_data.get("delivery_at")
        if not delivery_at:
            return delivery_at
        if delivery_at <= timezone.now():
            raise forms.ValidationError("Choose a future delivery time.")
        if delivery_at.minute % 15 or delivery_at.second or delivery_at.microsecond:
            raise forms.ValidationError("Delivery times must use 15-minute increments.")
        occupied = MaterialRequest.objects.filter(delivery_at=delivery_at)
        if self.material_request:
            occupied = occupied.exclude(pk=self.material_request.pk)
        if occupied.exists():
            raise forms.ValidationError("That delivery time is already reserved.")
        return delivery_at

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("response") == "ready":
            cleaned["delivery_at"] = None
            cleaned["note"] = ""
        return cleaned



class InventoryItemForm(forms.ModelForm):
    rack = forms.ChoiceField(choices=[("", "---------")] + RACK_CHOICES, required=False)
    section = PaddedNumericChoiceField(choices=[("", "---------")] + SECTION_CHOICES, required=False)
    bin_location = PaddedNumericChoiceField(
        choices=[("", "---------")] + BIN_LOCATION_CHOICES,
        required=False,
    )

    class Meta:
        model = InventoryItem
        fields = [
            "part_number",
            "fb_part_number",
            "model_number",
            "name",
            "category",
            "description",
            "shipper",
            "quantity_on_hand",
            "unit",
            "building_room",
            "rack",
            "section",
            "bin_location",
            "low_stock_threshold",
            "barcode_value",
            "qr_code_value",
            "active",
        ]


class PickTicketForm(forms.ModelForm):
    picked_by_name = forms.ChoiceField(label="Picked by")

    class Meta:
        model = PickTicket
        fields = [
            "date",
            "status",
            "picked_by_name",
            "received_by_name",
            "requested_by_name",
            "building_room",
            "location",
            "notes",
        ]
        widgets = {
            "date": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["received_by_name"].required = False
        self.fields["picked_by_name"].choices = self._user_choices()
        if self.instance.pk and self.instance.picked_by_name:
            selected_value = self._selected_user_value(self.instance.picked_by_name)
            if selected_value is None:
                selected_value = self.instance.picked_by_name
                self.fields["picked_by_name"].choices.append((selected_value, self.instance.picked_by_name))
            self.fields["picked_by_name"].initial = selected_value
        if not self.instance.pk:
            # Default new tickets to OPEN status
            self.fields["status"].initial = PickTicket.Status.OPEN

    def _user_choices(self):
        choices = [("", "Select user…")]
        users = User.objects.filter(is_active=True).order_by("first_name", "last_name", "username")
        for user in users:
            choices.append((str(user.pk), user.get_full_name() or user.get_username()))
        return choices

    def _selected_user_value(self, display_name):
        for user in User.objects.filter(is_active=True):
            if str(user.pk) == str(display_name) or user.get_username() == display_name or (user.get_full_name() or user.get_username()) == display_name:
                return str(user.pk)
        return None

    def clean_picked_by_name(self):
        value = self.cleaned_data["picked_by_name"]
        try:
            user = User.objects.get(pk=value, is_active=True)
        except (User.DoesNotExist, ValueError):
            return value
        return user.get_full_name() or user.get_username()


class PickTicketLineForm(forms.ModelForm):
    scan_code = forms.CharField(
        required=False,
        help_text="Scanned barcode / QR / part #",
        widget=forms.HiddenInput(attrs={"class": "scan-code-hidden"}),
    )

    class Meta:
        model = PickTicketLine
        fields = ["scan_code", "item", "quantity"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].required = False
        self.fields["item"].queryset = InventoryItem.objects.filter(active=True).order_by("category", "name", "part_number")
        self.fields["item"].widget.attrs.update({"class": "searchable-item-select", "data-placeholder": "Type to search items…"})

    def clean(self):
        cleaned = super().clean()
        scan_code = (cleaned.get("scan_code") or "").strip()
        item = cleaned.get("item")
        quantity = cleaned.get("quantity")
        has_quantity = quantity is not None

        # If all fields are empty, this is an empty extra form in the formset.
        if not scan_code and not item and not has_quantity:
            return cleaned

        if scan_code and not item:
            item = InventoryItem.objects.filter(barcode_value=scan_code).first()
            item = item or InventoryItem.objects.filter(qr_code_value=scan_code).first()
            item = item or InventoryItem.objects.filter(part_number=scan_code).first()
            if not item:
                raise forms.ValidationError(f"No item found for scan/code: {scan_code}")
            cleaned["item"] = item

        if not item:
            raise forms.ValidationError("Select an item or scan a barcode before submitting this line.")

        if not quantity:
            cleaned["quantity"] = 1
        return cleaned


PickTicketLineFormSet = inlineformset_factory(
    PickTicket,
    PickTicketLine,
    form=PickTicketLineForm,
    extra=5,
    can_delete=True,
    min_num=1,
    validate_min=True,
)


class ReceivingTicketEditForm(forms.ModelForm):
    class Meta:
        model = ReceivingTicket
        fields = ["date", "po_number", "vendor", "notes"]
        widgets = {
            "date": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].input_formats = ["%Y-%m-%dT%H:%M"]


class ReceivingLineForm(forms.ModelForm):
    class Meta:
        model = ReceivingLine
        fields = ["item", "quantity", "shipper", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 1})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = InventoryItem.objects.filter(active=True).order_by("category", "name", "part_number")
        self.fields["item"].widget.attrs.update({"class": "searchable-item-select"})


ReceivingLineFormSet = inlineformset_factory(
    ReceivingTicket,
    ReceivingLine,
    form=ReceivingLineForm,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True,
)


class ReceivingForm(forms.Form):
    """Form for receiving stock for a single item (inline with multiple items)."""
    item = forms.ModelChoiceField(
        queryset=InventoryItem.objects.filter(active=True),
        required=False,
        help_text="Select existing item or leave blank for new item"
    )
    part_number = forms.CharField(
        max_length=80,
        required=False,
        help_text="Required if creating new item"
    )
    name = forms.CharField(
        max_length=200,
        required=False,
        help_text="Required if creating new item"
    )
    quantity = forms.IntegerField(min_value=1)
    rack = forms.ChoiceField(choices=[("", "---------")] + RACK_CHOICES, required=False)
    section = PaddedNumericChoiceField(choices=[("", "---------")] + SECTION_CHOICES, required=False)
    bin_location = PaddedNumericChoiceField(
        choices=[("", "---------")] + BIN_LOCATION_CHOICES,
        required=False,
    )
    shipper = forms.CharField(max_length=120, required=False, help_text="Shipper / carrier")
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    document = forms.FileField(required=False, help_text="Upload delivery note, packing slip, or photo")
    source = forms.ChoiceField(choices=[("file", "File Upload"), ("camera", "Phone Camera")], initial="file", widget=forms.HiddenInput())
    po_number = forms.CharField(max_length=80, required=False, help_text="Purchase Order #")

    def clean(self):
        cleaned_data = super().clean()
        location_values = [
            cleaned_data.get("rack"),
            cleaned_data.get("section"),
            cleaned_data.get("bin_location"),
        ]
        if any(location_values) and not all(location_values):
            raise forms.ValidationError("Choose a rack, section, and bin together, or leave all three blank.")
        if not cleaned_data.get("item") and not all(location_values):
            raise forms.ValidationError("A complete destination location is required for a new item.")
        return cleaned_data


class ReceivingTicketForm(forms.Form):
    """Form for receiving ticket header (vendor, PO #, general notes)."""
    date = forms.DateTimeField(
        label="Ticket Date",
        help_text="Date/time of receipt (for backdating, choose past date/time)",
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M"],
    )
    po_number = forms.CharField(max_length=80, required=False, help_text="Purchase Order #")
    vendor = forms.CharField(max_length=120, required=False, help_text="Vendor / supplier name")
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False, help_text="General notes for this receiving ticket")


class BulkReceivingForm(forms.Form):
    """Form for bulk receiving from spreadsheet upload."""
    spreadsheet = forms.FileField(help_text="Upload Excel (.xlsx) file with receiving data")
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)


class BulkAdjustForm(forms.Form):
    """Form for adjusting multiple items at once."""
    item = forms.ModelChoiceField(
        queryset=InventoryItem.objects.filter(active=True),
        required=False,
        help_text="Leave blank for new item"
    )
    part_number = forms.CharField(
        max_length=80,
        required=False,
        help_text="Required for new items"
    )
    name = forms.CharField(
        max_length=200,
        required=False,
        help_text="Required for new items"
    )
    quantity_delta = forms.IntegerField(
        help_text="Positive to add, negative to remove"
    )
    notes = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}),
        required=False
    )


class InventoryBulkEditForm(forms.Form):
    """Form for bulk editing multiple inventory items inline."""
    items = forms.CharField(widget=forms.HiddenInput(), required=False)
    # Dynamic fields will be added in __init__
    
    def __init__(self, *args, **kwargs):
        item_data = kwargs.pop("item_data", [])
        super().__init__(*args, **kwargs)
        for item in item_data:
            prefix = f"item_{item['id']}"
            self.fields[f"{prefix}_name"] = forms.CharField(
                max_length=200, 
                initial=item["name"], 
                required=True,
                label="Name"
            )
            self.fields[f"{prefix}_shipper"] = forms.CharField(
                max_length=120,
                initial=item["shipper"] or "",
                required=False,
                label="Shipper"
            )
            existing_categories = list(
                InventoryItem.objects.exclude(category="")
                .values_list("category", flat=True)
                .distinct()
                .order_by("category")
            )
            category_choices = [("", "")] + list(CategoryChoices.choices)
            for cat in existing_categories:
                if cat and cat not in [c[0] for c in category_choices]:
                    category_choices.append((cat, cat))
            self.fields[f"{prefix}_category"] = forms.ChoiceField(
                choices=category_choices,
                initial=item["category"],
                required=False,
                label="Category",
                widget=forms.Select(attrs={"class": "category-select"}),
            )
            self.fields[f"{prefix}_quantity_on_hand"] = forms.IntegerField(
                initial=item["quantity_on_hand"], 
                required=True,
                label="Qty on Hand"
            )
            self.fields[f"{prefix}_building_room"] = forms.CharField(
                max_length=80, 
                initial=item["building_room"] or "", 
                required=False,
                label="Building/Room"
            )
            self.fields[f"{prefix}_rack"] = forms.ChoiceField(
                choices=[("", "")] + RACK_CHOICES,
                initial=item.get("rack") or "",
                required=False,
                label="Rack",
            )
            self.fields[f"{prefix}_section"] = PaddedNumericChoiceField(
                choices=[("", "")] + SECTION_CHOICES,
                initial=item.get("section") or "",
                required=False,
                label="Section",
            )
            self.fields[f"{prefix}_bin_location"] = PaddedNumericChoiceField(
                choices=[("", "")] + BIN_LOCATION_CHOICES,
                initial=item["bin_location"] or "",
                required=False,
                label="Bin",
            )
            self.fields[f"{prefix}_low_stock_threshold"] = forms.IntegerField(
                initial=item["low_stock_threshold"] or 0, 
                required=False,
                min_value=0,
                label="Low Stock Threshold"
            )
            self.fields[f"{prefix}_part_number"] = forms.CharField(
                max_length=80,
                initial=item["part_number"],
                required=False,
                widget=forms.HiddenInput(),
                label="Part #"
            )
            self.fields[f"{prefix}_id"] = forms.IntegerField(
                initial=item["id"],
                required=True,
                widget=forms.HiddenInput(),
                label="ID"
            )
