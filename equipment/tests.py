from datetime import timedelta
from io import BytesIO
import os
from pathlib import Path
import unittest
import zipfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.staticfiles import finders
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook

from .importer import import_workbook
from .forms import AssetForm, ReservationForm
from .models import (
    ActiveCustody,
    Asset,
    AssetEvent,
    AssetIdentifier,
    Checkout,
    EquipmentCategory,
    EquipmentImportRow,
    EquipmentLocation,
    EquipmentParty,
    EquipmentVendor,
    MaintenanceWorkOrder,
    RentalAsset,
    RentalContract,
    Reservation,
    ReturnRecord,
)
from .services import (
    checkout_assets,
    complete_maintenance,
    create_reservation,
    open_maintenance,
    return_asset,
    set_reservation_status,
    update_asset,
)

WORKBOOK = Path(os.environ.get("EQUIPMENT_TEST_WORKBOOK", "/home/hermes/.hermes/profiles/wms/cache/documents/doc_f1db9e5906b3_Equipment & Asset Tracker RPL.xlsx"))


class EquipmentTestMixin:
    def setUp(self):
        self.user = get_user_model().objects.create_user("equipment-manager", email="equipment@example.com", password="safe-test-password")
        permissions = Permission.objects.filter(content_type__app_label="equipment")
        self.user.user_permissions.set(permissions)
        self.category = EquipmentCategory.objects.create(name="Scanners", code="SCANNER", requires_serial=True)
        self.location = EquipmentLocation.objects.create(name="Warehouse", code="WAREHOUSE")
        self.party = EquipmentParty.objects.create(display_name="Jordan Smith", department="Field")
        self.asset = Asset.objects.create(
            asset_tag="RPL-EQ-TEST01",
            category=self.category,
            name="Scanner",
            ownership=Asset.Ownership.OWNED,
            status=Asset.Status.AVAILABLE,
            condition=Asset.Condition.GOOD,
            home_location=self.location,
            current_location=self.location,
            created_by=self.user,
            updated_by=self.user,
        )


class EquipmentServiceTests(EquipmentTestMixin, TestCase):
    def test_checkout_and_return_preserve_immutable_history(self):
        checkout = checkout_assets(
            actor=self.user,
            borrower_id=self.party.pk,
            asset_ids=[self.asset.pk],
            due_at=timezone.now() + timedelta(days=2),
            destination_id=self.location.pk,
            purpose="Site deployment",
        )
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.CHECKED_OUT)
        self.assertEqual(self.asset.current_party, self.party)
        self.assertTrue(ActiveCustody.objects.filter(asset=self.asset).exists())
        record = return_asset(
            actor=self.user,
            asset_id=self.asset.pk,
            condition=Asset.Condition.GOOD,
            return_location_id=self.location.pk,
        )
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.AVAILABLE)
        self.assertIsNone(self.asset.current_party)
        self.assertFalse(ActiveCustody.objects.filter(asset=self.asset).exists())
        self.assertEqual(record.checkout_item.checkout, checkout)
        self.assertEqual(ReturnRecord.objects.count(), 1)
        self.assertEqual(AssetEvent.objects.filter(asset=self.asset).count(), 2)
        with self.assertRaises(TypeError):
            record.delete()
        with self.assertRaises(TypeError):
            Checkout.objects.filter(pk=checkout.pk).update(purpose="rewritten")

    def test_double_checkout_is_rejected_without_partial_history(self):
        checkout_assets(actor=self.user, borrower_id=self.party.pk, asset_ids=[self.asset.pk])
        with self.assertRaisesMessage(ValidationError, "Unavailable equipment"):
            checkout_assets(actor=self.user, borrower_id=self.party.pk, asset_ids=[self.asset.pk])
        self.assertEqual(Checkout.objects.count(), 1)
        self.assertEqual(ActiveCustody.objects.count(), 1)

    def test_damaged_return_creates_maintenance_atomically(self):
        checkout_assets(actor=self.user, borrower_id=self.party.pk, asset_ids=[self.asset.pk])
        return_asset(
            actor=self.user,
            asset_id=self.asset.pk,
            condition=Asset.Condition.DAMAGED,
            notes="Cracked housing",
            damage_reported=True,
        )
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.MAINTENANCE)
        order = MaintenanceWorkOrder.objects.get(asset=self.asset)
        self.assertEqual(order.priority, MaintenanceWorkOrder.Priority.HIGH)

    def test_reservation_conflict_is_rejected(self):
        start = timezone.now() + timedelta(days=1)
        create_reservation(
            actor=self.user,
            requestor_id=self.party.pk,
            asset_ids=[self.asset.pk],
            starts_at=start,
            ends_at=start + timedelta(hours=4),
        )
        with self.assertRaisesMessage(ValidationError, "Conflicts"):
            create_reservation(
                actor=self.user,
                requestor_id=self.party.pk,
                asset_ids=[self.asset.pk],
                starts_at=start + timedelta(hours=1),
                ends_at=start + timedelta(hours=2),
            )
        self.assertEqual(Reservation.objects.count(), 1)

    def test_approved_reservation_holds_and_fulfills_through_checkout(self):
        start = timezone.now() + timedelta(days=1)
        reservation = create_reservation(
            actor=self.user,
            requestor_id=self.party.pk,
            asset_ids=[self.asset.pk],
            starts_at=start,
            ends_at=start + timedelta(hours=4),
        )
        set_reservation_status(actor=self.user, reservation_id=reservation.pk, status=Reservation.Status.APPROVED)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.RESERVED)
        with self.assertRaisesMessage(ValidationError, "Unavailable equipment"):
            checkout_assets(actor=self.user, borrower_id=self.party.pk, asset_ids=[self.asset.pk])
        checkout = checkout_assets(
            actor=self.user,
            borrower_id=self.party.pk,
            asset_ids=[self.asset.pk],
            reservation_id=reservation.pk,
        )
        reservation.refresh_from_db()
        self.asset.refresh_from_db()
        self.assertEqual(checkout.reservation, reservation)
        self.assertEqual(reservation.status, Reservation.Status.FULFILLED)
        self.assertEqual(self.asset.status, Asset.Status.CHECKED_OUT)

    def test_cancelling_approved_reservation_releases_hold(self):
        start = timezone.now() + timedelta(days=1)
        reservation = create_reservation(
            actor=self.user,
            requestor_id=self.party.pk,
            asset_ids=[self.asset.pk],
            starts_at=start,
            ends_at=start + timedelta(hours=4),
        )
        set_reservation_status(actor=self.user, reservation_id=reservation.pk, status=Reservation.Status.APPROVED)
        set_reservation_status(actor=self.user, reservation_id=reservation.pk, status=Reservation.Status.CANCELLED)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.AVAILABLE)

    def test_self_service_reservation_is_bound_to_linked_party(self):
        requester = get_user_model().objects.create_user("equipment-user", password="safe-test-password")
        requester.user_permissions.add(
            Permission.objects.get(codename="add_reservation", content_type__app_label="equipment"),
            Permission.objects.get(codename="view_reservation", content_type__app_label="equipment"),
        )
        own_party = EquipmentParty.objects.create(display_name="Equipment User", user=requester)
        other_party = EquipmentParty.objects.create(display_name="Other Person")
        form = ReservationForm(user=requester)
        self.assertEqual(list(form.fields["requestor"].queryset), [own_party])
        start = timezone.now() + timedelta(days=1)
        with self.assertRaises(PermissionDenied):
            create_reservation(
                actor=requester,
                requestor_id=other_party.pk,
                asset_ids=[self.asset.pk],
                starts_at=start,
                ends_at=start + timedelta(hours=1),
            )

    def test_maintenance_blocks_checked_out_asset(self):
        checkout_assets(actor=self.user, borrower_id=self.party.pk, asset_ids=[self.asset.pk])
        with self.assertRaisesMessage(ValidationError, "must be available"):
            open_maintenance(actor=self.user, asset_id=self.asset.pk, title="Inspection")

    def test_maintenance_requires_reserved_asset_hold_to_be_cancelled(self):
        start = timezone.now() + timedelta(days=1)
        reservation = create_reservation(
            actor=self.user,
            requestor_id=self.party.pk,
            asset_ids=[self.asset.pk],
            starts_at=start,
            ends_at=start + timedelta(hours=4),
        )
        set_reservation_status(actor=self.user, reservation_id=reservation.pk, status=Reservation.Status.APPROVED)
        with self.assertRaisesMessage(ValidationError, "Cancel the approved reservation"):
            open_maintenance(actor=self.user, asset_id=self.asset.pk, title="Inspection")

    def test_maintenance_completion_returns_asset_to_available(self):
        order = open_maintenance(actor=self.user, asset_id=self.asset.pk, title="Annual inspection")
        complete_maintenance(actor=self.user, work_order_id=order.pk, work_performed="Passed")
        order.refresh_from_db()
        self.asset.refresh_from_db()
        self.assertEqual(order.status, MaintenanceWorkOrder.Status.COMPLETED)
        self.assertEqual(self.asset.status, Asset.Status.AVAILABLE)

    def test_second_open_maintenance_order_is_rejected(self):
        open_maintenance(actor=self.user, asset_id=self.asset.pk, title="Annual inspection")
        with self.assertRaisesMessage(ValidationError, "already has an open"):
            open_maintenance(actor=self.user, asset_id=self.asset.pk, title="Duplicate inspection")

    def test_stale_asset_update_is_rejected(self):
        version = self.asset.updated_at
        update_asset(actor=self.user, asset_id=self.asset.pk, expected_updated_at=version, name="Updated scanner")
        with self.assertRaisesMessage(ValidationError, "changed after the form was opened"):
            update_asset(actor=self.user, asset_id=self.asset.pk, expected_updated_at=version, name="Stale update")

    def test_service_enforces_permission(self):
        unauthorized = get_user_model().objects.create_user("ordinary", password="safe-test-password")
        with self.assertRaises(PermissionDenied):
            checkout_assets(actor=unauthorized, borrower_id=self.party.pk, asset_ids=[self.asset.pk])


class EquipmentHostAndViewTests(EquipmentTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client(HTTP_HOST="equipment.rplwms.com")

    def test_equipment_host_uses_isolated_urlconf(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)
        self.assertEqual(self.client.get("/inventory/").status_code, 404)
        self.assertEqual(self.client.get("/material-requests/").status_code, 404)

    def test_equipment_manifest_and_login_use_equipment_identity(self):
        manifest_response = self.client.get("/manifest.webmanifest", secure=True)
        self.assertEqual(manifest_response.status_code, 200)
        self.assertEqual(manifest_response["Content-Type"], "application/manifest+json")
        manifest = manifest_response.json()
        self.assertEqual(manifest["name"], "RPL Equipment")
        self.assertEqual({icon["sizes"] for icon in manifest["icons"]}, {"192x192", "512x512"})
        self.assertTrue(all("/equipment-" in icon["src"] for icon in manifest["icons"]))
        self.assertTrue(any(icon["purpose"] == "maskable" for icon in manifest["icons"]))
        for icon in manifest["icons"]:
            self.assertIsNotNone(finders.find(icon["src"].removeprefix("/static/")))

        worker = self.client.get("/service-worker.js", secure=True)
        self.assertEqual(worker.status_code, 200)
        self.assertEqual(worker["Content-Type"], "application/javascript")
        self.assertEqual(worker["Service-Worker-Allowed"], "/")
        self.assertIn(b"rpl-equipment-shell-v2", worker.content)
        self.assertContains(self.client.get("/offline/", secure=True), "RPL Equipment is offline")

        login = self.client.get("/login/", secure=True)
        self.assertContains(login, 'href="/manifest.webmanifest"', html=False)
        self.assertContains(login, "equipment/branding/equipment-mark.svg")
        self.assertContains(login, "equipment/icons/equipment-apple-touch-icon.png")
        self.assertContains(login, "equipment/css/equipment.css?v=")
        self.assertContains(login, "equipment/js/equipment.js?v=")
        self.assertContains(login, "app-mark-symbol")

    def test_authorized_dashboard_and_register_render(self):
        self.client.force_login(self.user)
        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, "Equipment control center")
        self.assertContains(dashboard, "Needs attention")
        self.assertContains(dashboard, "Active custody")
        self.assertContains(dashboard, "Upcoming reservations")
        self.assertContains(dashboard, "Equipment register")
        self.assertContains(dashboard, "Availability by category")
        self.assertContains(dashboard, 'data-pwa-install', count=2, html=False)
        self.assertContains(dashboard, 'data-equipment-theme-toggle', count=2, html=False)
        self.assertContains(dashboard, 'data-header-more-toggle', count=1, html=False)
        self.assertContains(dashboard, 'data-account-toggle', count=1, html=False)
        self.assertContains(dashboard, 'id="toast-region"', html=False)
        script = Path(finders.find("equipment/js/equipment.js")).read_text()
        self.assertIn("beforeinstallprompt", script)
        self.assertIn("Add to Home Screen", script)
        self.assertIn("closeHeaderMenus", script)
        register = self.client.get("/assets/?q=TEST01")
        self.assertEqual(register.status_code, 200)
        self.assertContains(register, self.asset.asset_tag)

    def test_dashboard_scopes_self_service_reservations_to_linked_party(self):
        requester = get_user_model().objects.create_user(
            "equipment-requester", email="requester@example.com", password="safe-test-password"
        )
        requester.user_permissions.add(
            Permission.objects.get(codename="access_equipment_portal", content_type__app_label="equipment"),
            Permission.objects.get(codename="view_asset", content_type__app_label="equipment"),
            Permission.objects.get(codename="view_reservation", content_type__app_label="equipment"),
        )
        own_party = EquipmentParty.objects.create(display_name="Own Requester", user=requester)
        other_party = EquipmentParty.objects.create(display_name="Other Requester")
        start = timezone.now() + timedelta(days=1)
        Reservation.objects.create(
            reservation_number="ER-OWN-001",
            requestor=own_party,
            starts_at=start,
            ends_at=start + timedelta(hours=2),
            purpose="Visible reservation",
            created_by=requester,
        )
        Reservation.objects.create(
            reservation_number="ER-OTHER-001",
            requestor=other_party,
            starts_at=start,
            ends_at=start + timedelta(hours=2),
            purpose="Private reservation",
            created_by=self.user,
        )

        self.client.force_login(requester)
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ER-OWN-001")
        self.assertNotContains(response, "ER-OTHER-001")
        self.assertEqual(response.context["reservation_total"], 1)

    def test_dashboard_attention_total_is_unsampled_and_includes_lost_assets(self):
        self.asset.status = Asset.Status.LOST
        self.asset.review_required = True
        self.asset.save(update_fields=("status", "review_required", "updated_at"))
        for index in range(7):
            Asset.objects.create(
                asset_tag=f"RPL-EQ-REVIEW{index:02d}",
                category=self.category,
                name=f"Review asset {index}",
                status=Asset.Status.AVAILABLE,
                condition=Asset.Condition.GOOD,
                review_required=True,
                created_by=self.user,
                updated_by=self.user,
            )

        self.client.force_login(self.user)
        response = self.client.get("/")

        self.assertEqual(response.context["attention_total"], 8)
        self.assertContains(response, "8 issues")
        self.assertContains(response, "Equipment is recorded as lost")

    def test_dashboard_rental_obligations_use_open_line_return_dates(self):
        vendor = EquipmentVendor.objects.create(name="Rental Vendor")
        contract = RentalContract.objects.create(
            contract_number="RENT-TEST-001",
            vendor=vendor,
            starts_on=timezone.localdate(),
            ends_on=timezone.localdate() + timedelta(days=60),
            status=RentalContract.Status.ACTIVE,
            created_by=self.user,
        )
        returned_asset = Asset.objects.create(
            asset_tag="RPL-EQ-RETURNED",
            category=self.category,
            name="Returned rental",
            status=Asset.Status.AVAILABLE,
            condition=Asset.Condition.GOOD,
            created_by=self.user,
            updated_by=self.user,
        )
        RentalAsset.objects.create(
            contract=contract,
            asset=self.asset,
            expected_return_on=timezone.localdate() + timedelta(days=2),
        )
        RentalAsset.objects.create(
            contract=contract,
            asset=returned_asset,
            expected_return_on=timezone.localdate() + timedelta(days=1),
            returned_on=timezone.localdate(),
        )

        self.client.force_login(self.user)
        response = self.client.get("/")
        rental_items = [item for item in response.context["attention_items"] if item["kind"] == "Rental"]

        self.assertEqual(response.context["rental_due_total"], 1)
        self.assertEqual([item["asset"] for item in rental_items], [self.asset])
        self.assertContains(response, "Rental return due under RENT-TEST-001")

    def test_dashboard_prioritizes_contract_fallback_deadline_before_rental_slice(self):
        vendor = EquipmentVendor.objects.create(name="Priority Rental Vendor")
        later_contract = RentalContract.objects.create(
            contract_number="RENT-LATER",
            vendor=vendor,
            starts_on=timezone.localdate(),
            ends_on=timezone.localdate() + timedelta(days=60),
            status=RentalContract.Status.ACTIVE,
            created_by=self.user,
        )
        for index in range(6):
            asset = Asset.objects.create(
                asset_tag=f"RPL-EQ-LATER{index}",
                category=self.category,
                name=f"Later rental {index}",
                status=Asset.Status.AVAILABLE,
                condition=Asset.Condition.GOOD,
                created_by=self.user,
                updated_by=self.user,
            )
            RentalAsset.objects.create(
                contract=later_contract,
                asset=asset,
                expected_return_on=timezone.localdate() + timedelta(days=14),
            )
        urgent_contract = RentalContract.objects.create(
            contract_number="RENT-URGENT",
            vendor=vendor,
            starts_on=timezone.localdate(),
            ends_on=timezone.localdate(),
            status=RentalContract.Status.ACTIVE,
            created_by=self.user,
        )
        RentalAsset.objects.create(contract=urgent_contract, asset=self.asset)

        self.client.force_login(self.user)
        response = self.client.get("/")
        rental_items = [item for item in response.context["attention_items"] if item["kind"] == "Rental"]

        self.assertEqual(response.context["rental_due_total"], 7)
        self.assertEqual(rental_items[0]["asset"], self.asset)
        self.assertContains(response, "Rental return due under RENT-URGENT")

    def test_user_without_portal_permission_is_forbidden(self):
        user = get_user_model().objects.create_user("no-equipment", password="safe-test-password")
        self.client.force_login(user)
        self.assertEqual(self.client.get("/").status_code, 403)

    def test_portal_access_does_not_bypass_resource_permissions(self):
        user = get_user_model().objects.create_user("limited-equipment", email="limited@example.com", password="safe-test-password")
        user.user_permissions.add(
            Permission.objects.get(codename="access_equipment_portal", content_type__app_label="equipment"),
            Permission.objects.get(codename="view_asset", content_type__app_label="equipment"),
        )
        self.client.force_login(user)
        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, "Equipment register")
        self.assertNotContains(dashboard, "Active custody")
        self.assertNotContains(dashboard, "Upcoming reservations")
        self.assertNotContains(dashboard, "Maintenance queue")
        self.assertNotContains(dashboard, "Active rentals")
        self.assertNotContains(dashboard, "Recent activity")
        self.assertEqual(self.client.get("/assets/").status_code, 200)
        self.assertEqual(self.client.get("/checkouts/").status_code, 403)
        self.assertEqual(self.client.get("/maintenance/").status_code, 403)
        self.assertEqual(self.client.get("/rentals/").status_code, 403)
        self.assertEqual(self.client.get("/reports/").status_code, 403)

    def test_manager_role_is_provisioned_with_equipment_permissions(self):
        group = Group.objects.get(name="Equipment Manager")
        codenames = set(
            group.permissions.filter(content_type__app_label="equipment").values_list(
                "codename", flat=True
            )
        )
        self.assertIn("access_equipment_portal", codenames)
        self.assertIn("checkout_asset", codenames)
        self.assertIn("view_asset_costs", codenames)

    def test_cost_fields_are_absent_without_sensitive_permission(self):
        form = AssetForm(instance=self.asset, include_costs=False)
        self.assertNotIn("purchase_cost", form.fields)
        self.assertNotIn("replacement_value", form.fields)
        self.assertIn("version", form.fields)
        self.assertTrue(form.fields["version"].required)

    def test_email_login_form_is_available_on_equipment_host(self):
        response = self.client.post("/login/", {"username": self.user.email, "password": "safe-test-password"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

    def test_costs_are_not_in_export_without_permission(self):
        self.user.user_permissions.remove(Permission.objects.get(codename="view_asset_costs", content_type__app_label="equipment"))
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        response = self.client.get("/reports/export.csv")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Purchase Cost", response.content.decode().splitlines()[0])

    def test_scan_lookup_accepts_internal_tag(self):
        self.client.force_login(self.user)
        response = self.client.get("/scan/", {"q": self.asset.asset_tag})
        self.assertRedirects(response, f"/assets/{self.asset.pk}/", fetch_redirect_response=False)

    def test_scan_lookup_fails_closed_for_ambiguous_identifier(self):
        other = Asset.objects.create(
            asset_tag="RPL-EQ-TEST02",
            category=self.category,
            name="Second scanner",
            created_by=self.user,
            updated_by=self.user,
        )
        AssetIdentifier.objects.create(asset=self.asset, kind=AssetIdentifier.Kind.SERIAL, namespace="A", value="DUPLICATE")
        AssetIdentifier.objects.create(asset=other, kind=AssetIdentifier.Kind.SERIAL, namespace="B", value="DUPLICATE")
        self.client.force_login(self.user)
        response = self.client.get("/api/scan/", {"q": "DUPLICATE"})
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["ambiguous"])

    def test_csv_formula_values_are_neutralized(self):
        self.asset.name = '=HYPERLINK("https://invalid.example")'
        self.asset.save(update_fields=("name", "updated_at"))
        self.client.force_login(self.user)
        response = self.client.get("/reports/export.csv")
        self.assertIn("'=HYPERLINK", response.content.decode())


class EquipmentWorkbookImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("import-manager", password="safe-test-password")
        cls.user.user_permissions.set(Permission.objects.filter(content_type__app_label="equipment"))

    def synthetic_workbook(self):
        workbook = Workbook()
        workbook.remove(workbook.worksheets[0])
        for title in ("Vehicles", "Rentals", "Scanners", "Radios", "Laser Rulers", "Testers&Splicers", "IT-IPADs", "Black Box Laptop", "Meta Items", "Meta Assets", "Maintanance Schedule"):
            workbook.create_sheet(title)
        workbook["Vehicles"].append(["Vendor", "VIN", "Year", "Make", "Plate", "Subtype", "Legacy"])
        workbook["Vehicles"].append(["Black Box", "TESTVIN001", 2026, "Ford", "TEST-1", "Transit", "VEH-1"])
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()

    def test_synthetic_workbook_import_is_portable_and_idempotent(self):
        content = self.synthetic_workbook()
        first, created = import_workbook(content, source_name="synthetic.xlsx", actor=self.user)
        self.assertTrue(created)
        self.assertEqual(first.summary["assets_created"], 1)
        self.assertEqual(Asset.objects.count(), Asset.objects.values("asset_tag").distinct().count())
        second, created = import_workbook(content, source_name="synthetic.xlsx", actor=self.user)
        self.assertFalse(created)
        self.assertEqual(first.pk, second.pk)

    @unittest.skipUnless(WORKBOOK.exists(), "Set EQUIPMENT_TEST_WORKBOOK to run source-workbook reconciliation")
    def test_workbook_import_preserves_provenance_and_expected_counts(self):
        self.assertTrue(WORKBOOK.exists())
        batch, created = import_workbook(WORKBOOK.read_bytes(), source_name=WORKBOOK.name, actor=self.user)
        self.assertTrue(created)
        self.assertEqual(batch.summary["assets_created"], 336)
        self.assertEqual(Asset.objects.count(), 336)
        self.assertEqual(Asset.objects.filter(review_required=True).count(), 14)
        self.assertEqual(batch.review_rows, 14)
        self.assertEqual(EquipmentImportRow.objects.filter(batch=batch, sheet_name="Testers&Splicers", messages__icontains="Continuation").count(), 15)
        self.assertEqual(Asset.objects.filter(source_namespace="IT-IPADs").count(), 2)
        self.assertEqual(Asset.objects.filter(source_namespace="Meta Assets").count(), 0)
        self.assertTrue(EquipmentImportRow.objects.filter(batch=batch, sheet_name="Meta Assets", status=EquipmentImportRow.Status.EXCLUDED).exists())
        self.assertEqual(MaintenanceWorkOrder.objects.filter(status=MaintenanceWorkOrder.Status.COMPLETED).count(), 1)
        self.assertEqual(ActiveCustody.objects.count(), Asset.objects.filter(status=Asset.Status.CHECKED_OUT).count())
        imported = Asset.objects.filter(status=Asset.Status.CHECKED_OUT).first()
        return_asset(actor=self.user, asset_id=imported.pk, condition=Asset.Condition.GOOD)
        imported.refresh_from_db()
        self.assertEqual(imported.status, Asset.Status.AVAILABLE)

    @unittest.skipUnless(WORKBOOK.exists(), "Set EQUIPMENT_TEST_WORKBOOK to run source-workbook reconciliation")
    def test_reimport_is_idempotent(self):
        content = WORKBOOK.read_bytes()
        first, created = import_workbook(content, source_name=WORKBOOK.name, actor=self.user)
        self.assertTrue(created)
        second, created = import_workbook(content, source_name=WORKBOOK.name, actor=self.user)
        self.assertFalse(created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Asset.objects.count(), 336)

    def test_unsafe_xlsx_compression_is_rejected_before_parsing(self):
        output = BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("xl/worksheets/sheet1.xml", "A" * 2_000_000)
        with self.assertRaisesMessage(ValueError, "unsafe compression ratio"):
            import_workbook(output.getvalue(), source_name="unsafe.xlsx", actor=self.user)
        self.assertEqual(Asset.objects.count(), 0)
