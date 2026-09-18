from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.utils import timezone

from .forms import MaintenanceCompleteForm, MaintenancePlanForm
from .models import (
    ActiveCustody, Asset, EquipmentCategory, EquipmentLocation, EquipmentParty,
    MaintenancePlan, MaintenanceWorkOrder, VehicleMeterReading,
)
from .services import (
    checkout_assets, complete_maintenance, forecast_plan_due, generate_due_maintenance,
    record_meter_reading, save_maintenance_plan, transition_maintenance,
)


class VehicleMaintenanceTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user("fleet-manager", password="test-password")
        self.manager.user_permissions.set(Permission.objects.filter(content_type__app_label="equipment"))
        self.vehicle_category = EquipmentCategory.objects.create(name="Vehicles", code="VEHICLE")
        self.other_category = EquipmentCategory.objects.create(name="Scanners", code="SCANNER")
        self.location = EquipmentLocation.objects.create(name="Yard", code="YARD")
        self.vehicle = Asset.objects.create(
            asset_tag="TRUCK-001", category=self.vehicle_category, name="Service truck",
            status=Asset.Status.AVAILABLE, condition=Asset.Condition.GOOD,
            current_location=self.location, created_by=self.manager, updated_by=self.manager,
        )
        self.other = Asset.objects.create(
            asset_tag="SCAN-001", category=self.other_category, name="Scanner",
            status=Asset.Status.AVAILABLE, condition=Asset.Condition.GOOD,
            created_by=self.manager, updated_by=self.manager,
        )

    def plan(self, **overrides):
        values = {
            "asset": self.vehicle,
            "service_title": "Oil and filter service",
            "calendar_interval_months": 6,
            "meter_type": VehicleMeterReading.MeterType.ODOMETER,
            "meter_interval": Decimal("5000"),
            "lead_days": 14,
            "lead_meter": Decimal("250"),
            "last_service_date": date(2026, 1, 31),
            "last_service_meter": Decimal("10000"),
        }
        values.update(overrides)
        return save_maintenance_plan(actor=self.manager, **values)

    def test_plan_is_vehicle_only_and_requires_a_trigger(self):
        with self.assertRaises(ValidationError):
            save_maintenance_plan(
                actor=self.manager, asset=self.other, service_title="Invalid", calendar_interval_months=1
            )
        with self.assertRaises(ValidationError):
            save_maintenance_plan(actor=self.manager, asset=self.vehicle, service_title="No trigger")
        with self.assertRaises(ValidationError):
            save_maintenance_plan(
                actor=self.manager, asset=self.vehicle, service_title="Meter mismatch",
                meter_interval=Decimal("1000"),
            )

    def test_calendar_month_and_meter_next_due_calculation(self):
        plan = self.plan()
        self.assertEqual(plan.next_due_date, date(2026, 7, 31))
        self.assertEqual(plan.next_due_meter, Decimal("15000"))

    def test_meter_readings_are_immutable_and_cannot_decrease(self):
        reading = record_meter_reading(
            actor=self.manager, asset_id=self.vehicle.pk,
            meter_type=VehicleMeterReading.MeterType.ODOMETER, value="12000", reading_on=date(2026, 1, 1),
        )
        with self.assertRaisesMessage(ValidationError, "cannot decrease"):
            record_meter_reading(
                actor=self.manager, asset_id=self.vehicle.pk,
                meter_type=VehicleMeterReading.MeterType.ODOMETER, value="11999",
            )
        with self.assertRaises(TypeError):
            reading.save()
        with self.assertRaises(TypeError):
            VehicleMeterReading.objects.filter(pk=reading.pk).delete()

    def test_whichever_first_forecasting_and_idempotent_generation(self):
        plan = self.plan(last_service_date=date(2026, 1, 1))
        record_meter_reading(
            actor=self.manager, asset_id=self.vehicle.pk,
            meter_type=VehicleMeterReading.MeterType.ODOMETER, value="14800",
        )
        forecast = forecast_plan_due(plan, as_of=date(2026, 2, 1))
        self.assertTrue(forecast["meter_due"])
        self.assertFalse(forecast["date_due"])
        first = generate_due_maintenance(actor=self.manager, as_of=date(2026, 2, 1), plan_ids=[plan.pk])
        second = generate_due_maintenance(actor=self.manager, as_of=date(2026, 2, 1), plan_ids=[plan.pk])
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(MaintenanceWorkOrder.objects.filter(plan=plan).count(), 1)

    def test_completion_reschedules_both_baselines(self):
        plan = self.plan(last_service_date=date(2025, 1, 1))
        order = generate_due_maintenance(actor=self.manager, as_of=date(2026, 1, 1), plan_ids=[plan.pk])[0]
        complete_maintenance(
            actor=self.manager, work_order_id=order.pk, work_performed="Changed oil",
            meter_at_completion=Decimal("15100"), completed_on=date(2026, 2, 28),
        )
        plan.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(plan.last_service_date, date(2026, 2, 28))
        self.assertEqual(plan.next_due_date, date(2026, 8, 28))
        self.assertEqual(plan.last_service_meter, Decimal("15100"))
        self.assertEqual(plan.next_due_meter, Decimal("20100"))
        self.assertEqual(order.meter_at_completion, Decimal("15100"))
        self.assertEqual(VehicleMeterReading.objects.get(asset=self.vehicle).value, Decimal("15100"))

    def test_status_progression_requires_schedule_and_preserves_custody_guard(self):
        plan = self.plan(last_service_date=date(2025, 1, 1))
        order = generate_due_maintenance(actor=self.manager, as_of=date(2026, 1, 1), plan_ids=[plan.pk])[0]
        with self.assertRaisesMessage(ValidationError, "scheduled service date"):
            transition_maintenance(actor=self.manager, work_order_id=order.pk, status=MaintenanceWorkOrder.Status.SCHEDULED)
        scheduled = timezone.make_aware(datetime(2026, 1, 15, 9, 0))
        transition_maintenance(
            actor=self.manager, work_order_id=order.pk,
            status=MaintenanceWorkOrder.Status.SCHEDULED, scheduled_for=scheduled,
        )
        party = EquipmentParty.objects.create(display_name="Driver")
        checkout_assets(actor=self.manager, borrower_id=party.pk, asset_ids=[self.vehicle.pk])
        with self.assertRaisesMessage(ValidationError, "must be available"):
            transition_maintenance(actor=self.manager, work_order_id=order.pk, status=MaintenanceWorkOrder.Status.IN_PROGRESS)
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.status, Asset.Status.CHECKED_OUT)
        self.assertTrue(ActiveCustody.objects.filter(asset=self.vehicle).exists())

    def test_generated_work_does_not_break_reserved_or_custody_state(self):
        plan = self.plan(last_service_date=date(2025, 1, 1))
        party = EquipmentParty.objects.create(display_name="Driver")
        checkout_assets(actor=self.manager, borrower_id=party.pk, asset_ids=[self.vehicle.pk])
        order = generate_due_maintenance(actor=self.manager, as_of=date(2026, 1, 1), plan_ids=[plan.pk])[0]
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.status, Asset.Status.CHECKED_OUT)
        complete_maintenance(actor=self.manager, work_order_id=order.pk, work_performed="Mobile service")
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.status, Asset.Status.CHECKED_OUT)
        self.assertTrue(ActiveCustody.objects.filter(asset=self.vehicle).exists())

    def test_permission_denial_for_services_and_manager_ui(self):
        requester = get_user_model().objects.create_user("requester", password="test-password")
        requester.user_permissions.add(
            Permission.objects.get(content_type__app_label="equipment", codename="access_equipment_portal"),
            Permission.objects.get(content_type__app_label="equipment", codename="view_asset"),
            Permission.objects.get(content_type__app_label="equipment", codename="view_maintenanceworkorder"),
        )
        with self.assertRaises(PermissionDenied):
            record_meter_reading(
                actor=requester, asset_id=self.vehicle.pk,
                meter_type=VehicleMeterReading.MeterType.ODOMETER, value=1,
            )
        client = Client(HTTP_HOST="equipment.rplwms.com")
        client.force_login(requester)
        self.assertEqual(client.get("/maintenance/?tab=plans").status_code, 403)
        self.assertEqual(client.get(f"/assets/{self.vehicle.pk}/meter-readings/new/").status_code, 403)

    def test_manager_vehicle_panel_and_tabs_render(self):
        self.plan()
        client = Client(HTTP_HOST="equipment.rplwms.com")
        client.force_login(self.manager)
        detail = client.get(f"/assets/{self.vehicle.pk}/")
        self.assertContains(detail, "Maintenance plan & meters")
        maintenance = client.get("/maintenance/?tab=plans")
        self.assertContains(maintenance, "Recurring plans")
        self.assertContains(maintenance, "Oil and filter service")

    def test_vehicle_classification_uses_controlled_codes_not_substrings(self):
        misleading = EquipmentCategory.objects.create(name="Vehicle accessories", code="NONVEHICLE")
        asset = Asset.objects.create(
            asset_tag="ACC-001", category=misleading, name="Accessory", created_by=self.manager, updated_by=self.manager,
        )
        self.assertFalse(asset.is_vehicle)
        self.assertNotIn(asset, MaintenancePlanForm().fields["asset"].queryset)
        normalized = EquipmentCategory.objects.create(name="Fleet", code=" fleet-vehicle ")
        vehicle = Asset.objects.create(
            asset_tag="TRUCK-002", category=normalized, name="Truck", created_by=self.manager, updated_by=self.manager,
        )
        self.assertTrue(vehicle.is_vehicle)
        self.assertIn(vehicle, MaintenancePlanForm().fields["asset"].queryset)

    def test_direct_meter_create_enforces_nondecreasing_and_bulk_create_is_blocked(self):
        VehicleMeterReading.objects.create(
            asset=self.vehicle, meter_type=VehicleMeterReading.MeterType.ODOMETER,
            value=Decimal("100"), reading_on=date(2026, 1, 1), recorded_by=self.manager,
        )
        with self.assertRaisesMessage(ValidationError, "cannot decrease"):
            VehicleMeterReading.objects.create(
                asset=self.vehicle, meter_type=VehicleMeterReading.MeterType.ODOMETER,
                value=Decimal("99"), reading_on=date(2026, 1, 2), recorded_by=self.manager,
            )
        with self.assertRaises(TypeError):
            VehicleMeterReading.objects.bulk_create([VehicleMeterReading(
                asset=self.vehicle, meter_type=VehicleMeterReading.MeterType.ODOMETER,
                value=Decimal("101"), recorded_by=self.manager,
            )])

    def test_plan_asset_is_immutable_and_schedule_edits_wait_for_closed_work(self):
        plan = self.plan(last_service_date=date(2025, 1, 1))
        order = generate_due_maintenance(actor=self.manager, as_of=date(2026, 1, 1), plan_ids=[plan.pk])[0]
        with self.assertRaisesMessage(ValidationError, "Schedule-defining"):
            save_maintenance_plan(actor=self.manager, plan_id=plan.pk, meter_interval=Decimal("6000"))
        second = Asset.objects.create(
            asset_tag="TRUCK-003", category=self.vehicle_category, name="Truck 3",
            created_by=self.manager, updated_by=self.manager,
        )
        with self.assertRaisesMessage(ValidationError, "cannot be changed"):
            save_maintenance_plan(actor=self.manager, plan_id=plan.pk, asset=second)
        transition_maintenance(actor=self.manager, work_order_id=order.pk, status=MaintenanceWorkOrder.Status.CANCELLED)
        updated = save_maintenance_plan(actor=self.manager, plan_id=plan.pk, meter_interval=Decimal("6000"))
        self.assertEqual(updated.meter_interval, Decimal("6000"))
        with self.assertRaisesMessage(ValidationError, "cannot be changed"):
            save_maintenance_plan(actor=self.manager, plan_id=plan.pk, asset=second)

    def test_completion_rejects_mismatched_asset_and_stale_occurrence(self):
        plan = self.plan(last_service_date=date(2025, 1, 1))
        order = generate_due_maintenance(actor=self.manager, as_of=date(2026, 1, 1), plan_ids=[plan.pk])[0]
        MaintenanceWorkOrder.objects.filter(pk=order.pk).update(occurrence_key="stale")
        with self.assertRaisesMessage(ValidationError, "current maintenance occurrence"):
            complete_maintenance(actor=self.manager, work_order_id=order.pk, work_performed="No")
        order.refresh_from_db()
        self.assertEqual(order.status, MaintenanceWorkOrder.Status.OPEN)
        MaintenanceWorkOrder.objects.filter(pk=order.pk).update(occurrence_key=f"maintenance-plan:{plan.pk}:date:{plan.next_due_date}:meter:{plan.next_due_meter}")
        MaintenanceWorkOrder.objects.filter(pk=order.pk).update(asset=self.other)
        with self.assertRaisesMessage(ValidationError, "does not match"):
            complete_maintenance(actor=self.manager, work_order_id=order.pk, work_performed="No")
        plan.refresh_from_db()
        self.assertEqual(plan.last_service_date, date(2025, 1, 1))

    def test_completion_preserves_nonmaintenance_states_and_requires_safe_release(self):
        protected = (
            Asset.Status.RETIRED, Asset.Status.LOST, Asset.Status.OUT_OF_SERVICE,
            Asset.Status.RETURNED_VENDOR, Asset.Status.RESERVED, Asset.Status.CHECKED_OUT,
            Asset.Status.IN_TRANSIT, Asset.Status.UNKNOWN,
        )
        for index, status in enumerate(protected):
            self.vehicle.status = status
            self.vehicle.save(update_fields=("status", "updated_at"))
            order = MaintenanceWorkOrder.objects.create(
                work_order_number=f"EQ-WO-P{index}", asset=self.vehicle, title="State test", opened_by=self.manager,
            )
            complete_maintenance(actor=self.manager, work_order_id=order.pk, work_performed="Done")
            self.vehicle.refresh_from_db()
            self.assertEqual(self.vehicle.status, status)

        self.vehicle.status = Asset.Status.MAINTENANCE
        self.vehicle.save(update_fields=("status", "updated_at"))
        first = MaintenanceWorkOrder.objects.create(
            work_order_number="EQ-WO-SAFE1", asset=self.vehicle, title="First", opened_by=self.manager,
        )
        MaintenanceWorkOrder.objects.create(
            work_order_number="EQ-WO-SAFE2", asset=self.vehicle, title="Other", opened_by=self.manager,
        )
        complete_maintenance(actor=self.manager, work_order_id=first.pk, work_performed="Done")
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.status, Asset.Status.MAINTENANCE)

        safe_asset = Asset.objects.create(
            asset_tag="TRUCK-SAFE", category=self.vehicle_category, name="Safe release",
            status=Asset.Status.MAINTENANCE, created_by=self.manager, updated_by=self.manager,
        )
        safe_order = MaintenanceWorkOrder.objects.create(
            work_order_number="EQ-WO-SAFE3", asset=safe_asset, title="Only work", opened_by=self.manager,
        )
        complete_maintenance(actor=self.manager, work_order_id=safe_order.pk, work_performed="Done")
        safe_asset.refresh_from_db()
        self.assertEqual(safe_asset.status, Asset.Status.AVAILABLE)

    def test_generation_skips_archived_and_terminal_assets(self):
        statuses = (Asset.Status.RETIRED, Asset.Status.LOST, Asset.Status.RETURNED_VENDOR)
        for index, status in enumerate(statuses):
            vehicle = Asset.objects.create(
                asset_tag=f"TERM-{index}", category=self.vehicle_category, name="Terminal",
                status=Asset.Status.AVAILABLE, created_by=self.manager, updated_by=self.manager,
            )
            plan = save_maintenance_plan(
                actor=self.manager, asset=vehicle, service_title="Service", calendar_interval_months=1,
                last_service_date=date(2025, 1, 1),
            )
            vehicle.status = status
            vehicle.save(update_fields=("status", "updated_at"))
            self.assertEqual(generate_due_maintenance(actor=self.manager, as_of=date(2026, 1, 1), plan_ids=[plan.pk]), [])
        archived = Asset.objects.create(
            asset_tag="ARCH-1", category=self.vehicle_category, name="Archived",
            created_by=self.manager, updated_by=self.manager,
        )
        plan = save_maintenance_plan(
            actor=self.manager, asset=archived, service_title="Service", calendar_interval_months=1,
            last_service_date=date(2025, 1, 1),
        )
        archived.archived_at = timezone.now()
        archived.archived_by = self.manager
        archived.save(update_fields=("archived_at", "archived_by", "updated_at"))
        self.assertEqual(generate_due_maintenance(actor=self.manager, as_of=date(2026, 1, 1), plan_ids=[plan.pk]), [])

    def test_future_completion_date_rejected_by_form_and_service(self):
        future = timezone.localdate() + timedelta(days=1)
        form = MaintenanceCompleteForm(data={
            "work_performed": "Done", "condition": Asset.Condition.GOOD,
            "completed_on": future.isoformat(),
        })
        self.assertFalse(form.is_valid())
        self.assertIn("future", form.errors["completed_on"][0])
        order = MaintenanceWorkOrder.objects.create(
            work_order_number="EQ-WO-FUTURE", asset=self.vehicle, title="Future", opened_by=self.manager,
        )
        with self.assertRaisesMessage(ValidationError, "cannot be in the future"):
            complete_maintenance(
                actor=self.manager, work_order_id=order.pk, work_performed="Done", completed_on=future,
            )
        order.refresh_from_db()
        self.assertEqual(order.status, MaintenanceWorkOrder.Status.OPEN)

    def test_nonnegative_validators_and_database_checks(self):
        plan = self.plan()
        for field_name in ("lead_meter", "last_service_meter", "next_due_meter"):
            field = MaintenancePlan._meta.get_field(field_name)
            with self.assertRaises(ValidationError):
                field.clean(Decimal("-1"), plan)
        order = MaintenanceWorkOrder(
            work_order_number="EQ-WO-NEG", asset=self.vehicle, title="Negative", opened_by=self.manager,
        )
        for field_name in ("meter_due", "meter_at_completion"):
            with self.assertRaises(ValidationError):
                MaintenanceWorkOrder._meta.get_field(field_name).clean(Decimal("-1"), order)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MaintenanceWorkOrder.objects.create(
                work_order_number="EQ-WO-DBNEG", asset=self.vehicle, title="Negative",
                meter_due=Decimal("-1"), opened_by=self.manager,
            )

    def test_invalid_as_of_command_is_a_failure(self):
        with self.assertRaisesMessage(CommandError, "YYYY-MM-DD"):
            call_command("process_vehicle_maintenance", as_of="not-a-date")
