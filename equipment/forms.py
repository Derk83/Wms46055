from django import forms
from django.utils import timezone

from .models import (
    Asset,
    EquipmentCategory,
    EquipmentLocation,
    EquipmentParty,
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
