from django import forms
from django.db.models import Q
from django.utils import timezone

from .models import (
    Asset,
    EquipmentCategory,
    EquipmentLocation,
    EquipmentParty,
    EquipmentRequest,
    EquipmentRequestLine,
    MaintenanceWorkOrder,
    Reservation,
)


class DateTimeLocalInput(forms.DateTimeInput):
    input_type = "datetime-local"


class AssetForm(forms.ModelForm):
    version = forms.DateTimeField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Asset
        fields = (
            "asset_tag",
            "legacy_tag",
            "source_namespace",
            "category",
            "name",
            "manufacturer",
            "model_number",
            "ownership",
            "condition",
            "quantity",
            "home_location",
            "current_location",
            "acquired_on",
            "purchase_cost",
            "replacement_value",
            "notes",
            "review_required",
            "review_notes",
        )
        widgets = {"acquired_on": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 3}), "review_notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, include_costs=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not include_costs:
            self.fields.pop("purchase_cost", None)
            self.fields.pop("replacement_value", None)
        if self.instance and self.instance.pk:
            self.fields.pop("current_location", None)
            self.fields["version"].required = True
            self.fields["version"].initial = self.instance.updated_at


class CheckoutForm(forms.Form):
    assets = forms.ModelMultipleChoiceField(
        queryset=Asset.objects.none(),
        widget=forms.SelectMultiple(attrs={"size": 12}),
        help_text="Select one or more available assets. Use Ctrl/Cmd to select multiple.",
    )
    borrower = forms.ModelChoiceField(queryset=EquipmentParty.objects.none())
    reservation = forms.ModelChoiceField(
        queryset=Reservation.objects.none(), required=False,
        help_text="Required when issuing equipment held by an approved reservation.",
    )
    destination = forms.ModelChoiceField(queryset=EquipmentLocation.objects.none(), required=False)
    due_at = forms.DateTimeField(required=False, widget=DateTimeLocalInput(format="%Y-%m-%dT%H:%M"))
    purpose = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assets"].queryset = Asset.objects.filter(status__in=(Asset.Status.AVAILABLE, Asset.Status.RESERVED), archived_at__isnull=True).select_related("category")
        self.fields["borrower"].queryset = EquipmentParty.objects.filter(active=True)
        self.fields["reservation"].queryset = Reservation.objects.filter(status=Reservation.Status.APPROVED).select_related("requestor")
        self.fields["destination"].queryset = EquipmentLocation.objects.filter(active=True)

    def clean_due_at(self):
        value = self.cleaned_data.get("due_at")
        if value and value <= timezone.now():
            raise forms.ValidationError("Due date must be in the future.")
        return value


class ReturnForm(forms.Form):
    condition = forms.ChoiceField(choices=Asset.Condition)
    return_location = forms.ModelChoiceField(queryset=EquipmentLocation.objects.none(), required=False)
    damage_reported = forms.BooleanField(required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["return_location"].queryset = EquipmentLocation.objects.filter(active=True)


class ReservationForm(forms.Form):
    assets = forms.ModelMultipleChoiceField(
        queryset=Asset.objects.none(), widget=forms.SelectMultiple(attrs={"size": 12})
    )
    requestor = forms.ModelChoiceField(queryset=EquipmentParty.objects.none())
    starts_at = forms.DateTimeField(widget=DateTimeLocalInput(format="%Y-%m-%dT%H:%M"))
    ends_at = forms.DateTimeField(widget=DateTimeLocalInput(format="%Y-%m-%dT%H:%M"))
    destination = forms.ModelChoiceField(queryset=EquipmentLocation.objects.none(), required=False)
    purpose = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assets"].queryset = Asset.objects.exclude(status__in=(Asset.Status.RETIRED, Asset.Status.LOST, Asset.Status.RETURNED_VENDOR)).filter(archived_at__isnull=True)
        requestors = EquipmentParty.objects.filter(active=True)
        if user and not user.is_superuser and not user.has_perm("equipment.manage_reservations"):
            requestors = requestors.filter(user=user)
        self.fields["requestor"].queryset = requestors
        self.fields["destination"].queryset = EquipmentLocation.objects.filter(active=True)

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("starts_at"), cleaned.get("ends_at")
        if start and end and end <= start:
            self.add_error("ends_at", "End must be after the start.")
        return cleaned


class ReservationStatusForm(forms.Form):
    status = forms.ChoiceField(
        choices=(
            (Reservation.Status.APPROVED, "Approve"),
            (Reservation.Status.REJECTED, "Reject"),
            (Reservation.Status.CANCELLED, "Cancel"),
            (Reservation.Status.FULFILLED, "Mark fulfilled"),
        )
    )


class MaintenanceForm(forms.Form):
    asset = forms.ModelChoiceField(queryset=Asset.objects.none())
    title = forms.CharField(max_length=220)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}))
    priority = forms.ChoiceField(choices=MaintenanceWorkOrder.Priority)
    due_at = forms.DateTimeField(required=False, widget=DateTimeLocalInput(format="%Y-%m-%dT%H:%M"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["asset"].queryset = Asset.objects.filter(status=Asset.Status.AVAILABLE, archived_at__isnull=True)


class MaintenanceCompleteForm(forms.Form):
    work_performed = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))
    condition = forms.ChoiceField(choices=Asset.Condition, initial=Asset.Condition.GOOD)
    cost = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=12)

    def __init__(self, *args, include_costs=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not include_costs:
            self.fields.pop("cost")


class ImportUploadForm(forms.Form):
    workbook = forms.FileField(help_text="Excel .xlsx workbook, maximum 25 MB.")

    def clean_workbook(self):
        workbook = self.cleaned_data["workbook"]
        if not workbook.name.lower().endswith(".xlsx"):
            raise forms.ValidationError("Upload an .xlsx workbook.")
        if workbook.size > 25 * 1024 * 1024:
            raise forms.ValidationError("Workbook exceeds 25 MB.")
        return workbook


class EquipmentRequestForm(forms.ModelForm):
    class Meta:
        model = EquipmentRequest
        fields = (
            "needed_from", "needed_until", "destination", "purpose", "project",
            "priority", "accepts_substitutes", "requester_notes",
        )
        widgets = {
            "needed_from": forms.DateInput(attrs={"type": "date"}),
            "needed_until": forms.DateInput(attrs={"type": "date"}),
            "purpose": forms.Textarea(attrs={"rows": 3}),
            "requester_notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("needed_from"), cleaned.get("needed_until")
        if start and end and end < start:
            self.add_error("needed_until", "The end date cannot be before the start date.")
        return cleaned


class EquipmentRequestLineForm(forms.ModelForm):
    class Meta:
        model = EquipmentRequestLine
        fields = ("category", "unlisted_equipment", "quantity", "notes")
        widgets = {"notes": forms.TextInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = EquipmentCategory.objects.filter(active=True)
        self.fields["category"].required = False
        self.fields["quantity"].required = False
        if not self.instance.pk:
            self.fields["quantity"].initial = None

    def clean(self):
        cleaned = super().clean()
        category = cleaned.get("category")
        unlisted = (cleaned.get("unlisted_equipment") or "").strip()
        if not category and not unlisted:
            if any(self.data.get(self.add_prefix(name)) for name in ("quantity", "notes")):
                raise forms.ValidationError("Choose a category or describe unlisted equipment.")
            return cleaned
        cleaned["unlisted_equipment"] = unlisted
        if not cleaned.get("quantity"):
            cleaned["quantity"] = 1
        return cleaned


EquipmentRequestLineFormSet = forms.formset_factory(
    EquipmentRequestLineForm, extra=3, min_num=1, validate_min=True, max_num=20
)


class EquipmentRequestAssignmentForm(forms.Form):
    assigned_to = forms.ModelChoiceField(queryset=None, required=False)
    manager_notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission
        super().__init__(*args, **kwargs)
        permission = Permission.objects.filter(
            content_type__app_label="equipment", codename="manage_equipment_requests"
        ).first()
        users = get_user_model().objects.filter(is_active=True)
        if permission:
            users = users.filter(Q(is_superuser=True) | Q(user_permissions=permission) | Q(groups__permissions=permission)).distinct()
        self.fields["assigned_to"].queryset = users.order_by("first_name", "last_name", "username")


class EquipmentRequestStatusForm(forms.Form):
    status = forms.ChoiceField(choices=EquipmentRequest.Status)
    expected_status = forms.CharField(widget=forms.HiddenInput)
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class EquipmentAllocationForm(forms.Form):
    line = forms.ModelChoiceField(queryset=EquipmentRequestLine.objects.none())
    assets = forms.ModelMultipleChoiceField(
        queryset=Asset.objects.none(), widget=forms.SelectMultiple(attrs={"size": 10})
    )

    def __init__(self, *args, equipment_request=None, **kwargs):
        super().__init__(*args, **kwargs)
        if equipment_request:
            self.fields["line"].queryset = equipment_request.lines.all()
        self.fields["assets"].queryset = Asset.objects.filter(
            status=Asset.Status.AVAILABLE, archived_at__isnull=True
        ).select_related("category").order_by("category__name", "asset_tag")
