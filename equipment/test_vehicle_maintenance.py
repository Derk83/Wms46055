from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.utils import timezone

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
