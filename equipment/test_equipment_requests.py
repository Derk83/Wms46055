from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.utils import timezone

from .models import (
    ActiveCustody,
    Asset,
    AssetEvent,
    EquipmentCategory,
    EquipmentParty,
    EquipmentRequest,
    EquipmentRequestDeletion,
    EquipmentRequestEvent,
    EquipmentRequestLine,
    Reservation,
)
from .request_services import (
    allocate_request_assets,
    assign_equipment_request,
    create_equipment_request,
    delete_equipment_request,
    transition_equipment_request,
    update_equipment_request,
)
from .services import checkout_assets, set_reservation_status


class EquipmentRequestTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.requester = User.objects.create_user("requester", email="requester@example.com", password="test-password")
        self.other = User.objects.create_user("other", password="test-password")
        self.manager = User.objects.create_user("manager", password="test-password")
        requester_permission = Permission.objects.get(
            content_type__app_label="equipment", codename="access_equipment_requests"
        )
        self.requester.user_permissions.add(requester_permission)
        self.other.user_permissions.add(requester_permission)
        self.manager.user_permissions.set(Permission.objects.filter(content_type__app_label="equipment"))
        self.category = EquipmentCategory.objects.create(name="Radios", code="RADIO")
        self.asset = Asset.objects.create(
            asset_tag="RPL-EQ-RADIO01", category=self.category, name="Portable radio",
            status=Asset.Status.AVAILABLE, condition=Asset.Condition.GOOD,
            created_by=self.manager, updated_by=self.manager,
        )

    def values(self):
        return {
            "needed_from": timezone.localdate() + timedelta(days=2),
            "needed_until": timezone.localdate() + timedelta(days=4),
            "destination": "Project site A",
            "purpose": "Field commissioning",
            "project": "RPL-100",
            "priority": EquipmentRequest.Priority.HIGH,
            "accepts_substitutes": False,
            "requester_notes": "Call on arrival",
        }

    def make_request(self, actor=None, lines=None):
        return create_equipment_request(
            actor=actor or self.requester,
            values=self.values(),
            lines=lines or [{"category": self.category, "quantity": 1}],
        )

    def test_equipment_requester_role_has_no_manager_portal_access(self):
        group = Group.objects.get(name="Equipment Requester")
        codenames = set(group.permissions.values_list("codename", flat=True))
        self.assertEqual(codenames, {"access_equipment_requests"})
        self.assertNotIn("access_equipment_portal", codenames)
        self.assertNotIn("manage_equipment_requests", codenames)

    def test_unlisted_request_creation_creates_party_and_audit_event(self):
        equipment_request = self.make_request(lines=[{
            "category": None, "unlisted_equipment": "Special fiber test set", "quantity": 2,
        }])
        self.assertRegex(equipment_request.request_number, r"^ER-\d{4}-[A-F0-9]{8}$")
        self.assertEqual(equipment_request.lines.get().unlisted_equipment, "Special fiber test set")
        self.assertEqual(equipment_request.lines.get().quantity, 2)
        self.assertEqual(equipment_request.requestor_party.user, self.requester)
        event = equipment_request.events.get()
        self.assertEqual(event.event_type, EquipmentRequestEvent.Type.CREATED)
        with self.assertRaises(TypeError):
            event.delete()

    def test_owner_scoping_and_forged_detail_edit_cancel(self):
        equipment_request = self.make_request()
        client = Client(HTTP_HOST="eqreq.rplwms.com")
        client.force_login(self.other)
        self.assertNotContains(client.get("/"), equipment_request.request_number)
        self.assertEqual(client.get(f"/requests/{equipment_request.pk}/").status_code, 404)
        self.assertEqual(client.get(f"/requests/{equipment_request.pk}/edit/").status_code, 404)
        self.assertEqual(client.post(f"/requests/{equipment_request.pk}/cancel/").status_code, 403)
        equipment_request.refresh_from_db()
        self.assertEqual(equipment_request.status, EquipmentRequest.Status.SUBMITTED)

    def test_host_routes_are_isolated_both_ways(self):
        requester_client = Client(HTTP_HOST="eqreq.rplwms.com")
        requester_client.force_login(self.requester)
        self.assertEqual(requester_client.get("/").status_code, 200)
        self.assertEqual(requester_client.get("/assets/").status_code, 404)
        self.assertEqual(requester_client.get("/requests/").status_code, 404)
        equipment_client = Client(HTTP_HOST="equipment.rplwms.com")
        equipment_client.force_login(self.manager)
        self.assertEqual(equipment_client.get("/requests/").status_code, 200)
        self.assertEqual(equipment_client.get("/new/").status_code, 404)

    def test_requester_has_no_direct_manager_or_equipment_access(self):
        client = Client(HTTP_HOST="equipment.rplwms.com")
        client.force_login(self.requester)
        self.assertEqual(client.get("/").status_code, 403)
        self.assertEqual(client.get("/requests/").status_code, 403)
        self.assertEqual(client.get("/assets/").status_code, 403)

    def test_equipment_options_requires_requester_permission(self):
        self.other.groups.clear()
        self.other.user_permissions.clear()
        client = Client(HTTP_HOST="eqreq.rplwms.com")
        client.force_login(self.other)
        self.assertEqual(
            client.get("/api/equipment-options/", {"category": self.category.pk}).status_code,
            403,
        )

    def test_request_form_filters_preferred_assets_and_marks_unavailable_choices(self):
        other_category = EquipmentCategory.objects.create(name="Lifts", code="LIFT")
        checked_out = Asset.objects.create(
            asset_tag="RPL-EQ-RADIO02", category=self.category, name="Checked-out radio",
            status=Asset.Status.CHECKED_OUT, condition=Asset.Condition.GOOD,
            created_by=self.manager, updated_by=self.manager,
        )
        other_asset = Asset.objects.create(
            asset_tag="RPL-EQ-LIFT01", category=other_category, name="Scissor lift",
            status=Asset.Status.AVAILABLE, condition=Asset.Condition.GOOD,
            created_by=self.manager, updated_by=self.manager,
        )
        client = Client(HTTP_HOST="eqreq.rplwms.com")
        client.force_login(self.requester)

        response = client.get("/new/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="lines-0-requested_asset"')
        self.assertNotContains(response, "Checked-out radio")
        self.assertNotContains(response, "Scissor lift")

        response = client.get("/api/equipment-options/", {"category": self.category.pk})
        self.assertEqual(response.status_code, 200)
        assets = {asset["id"]: asset for asset in response.json()["assets"]}
        self.assertEqual(set(assets), {str(self.asset.pk), str(checked_out.pk)})
        self.assertTrue(assets[str(self.asset.pk)]["available"])
        self.assertFalse(assets[str(checked_out.pk)]["available"])
        self.assertIn("Checked-out radio", assets[str(checked_out.pk)]["label"])
        self.assertIn("Checked out", assets[str(checked_out.pk)]["label"])
        self.assertNotIn(str(other_asset.pk), assets)

    def test_request_persists_available_preferred_asset(self):
        equipment_request = self.make_request(lines=[{
            "category": self.category, "requested_asset": self.asset, "quantity": 1,
        }])
        line = equipment_request.lines.select_related("requested_asset").get()
        self.assertEqual(line.requested_asset, self.asset)

    def test_request_rejects_unavailable_or_cross_category_preferred_asset(self):
        checked_out = Asset.objects.create(
            asset_tag="RPL-EQ-RADIO02", category=self.category, name="Checked-out radio",
            status=Asset.Status.CHECKED_OUT, condition=Asset.Condition.GOOD,
            created_by=self.manager, updated_by=self.manager,
        )
        other_category = EquipmentCategory.objects.create(name="Lifts", code="LIFT")
        with self.assertRaisesMessage(ValidationError, "selected equipment is not available"):
            self.make_request(lines=[{
                "category": self.category, "requested_asset": checked_out, "quantity": 1,
            }])
        with self.assertRaisesMessage(ValidationError, "does not belong to the selected category"):
            self.make_request(lines=[{
                "category": other_category, "requested_asset": self.asset, "quantity": 1,
            }])

    def test_model_rejects_cross_category_preferred_asset(self):
        equipment_request = self.make_request()
        other_category = EquipmentCategory.objects.create(name="Lifts", code="LIFT")
        line = EquipmentRequestLine(
            request=equipment_request, category=other_category,
            requested_asset=self.asset, quantity=1,
        )
        with self.assertRaisesMessage(ValidationError, "does not belong to this category"):
            line.full_clean()

    def test_edit_audit_preserves_old_preferred_asset(self):
        equipment_request = self.make_request(lines=[{
            "category": self.category, "requested_asset": self.asset, "quantity": 1,
        }])
        update_equipment_request(
            actor=self.requester, request_id=equipment_request.pk, values=self.values(),
            lines=[{"category": self.category, "quantity": 1}],
        )
        event = equipment_request.events.get(event_type=EquipmentRequestEvent.Type.UPDATED)
        self.assertEqual(
            event.metadata["lines"]["from"][0]["requested_asset_id"], str(self.asset.pk),
        )
        self.assertIsNone(event.metadata["lines"]["to"][0]["requested_asset_id"])

    def test_web_form_creates_unlisted_request(self):
        client = Client(HTTP_HOST="eqreq.rplwms.com")
        client.force_login(self.requester)
        data = {
            "needed_from": self.values()["needed_from"].isoformat(),
            "needed_until": self.values()["needed_until"].isoformat(),
            "destination": "Remote site",
            "purpose": "Testing",
            "project": "P-42",
            "priority": "NORMAL",
            "accepts_substitutes": "on",
            "requester_notes": "",
            "lines-TOTAL_FORMS": "3", "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "1", "lines-MAX_NUM_FORMS": "20",
            "lines-0-category": "", "lines-0-unlisted_equipment": "Unlisted analyzer",
            "lines-0-quantity": "3", "lines-0-notes": "Calibrated",
            "lines-1-category": "", "lines-1-unlisted_equipment": "", "lines-1-quantity": "", "lines-1-notes": "",
            "lines-2-category": "", "lines-2-unlisted_equipment": "", "lines-2-quantity": "", "lines-2-notes": "",
        }
        response = client.post("/new/", data)
        self.assertEqual(response.status_code, 302)
        created = EquipmentRequest.objects.get(requester=self.requester)
        self.assertEqual(created.lines.get().unlisted_equipment, "Unlisted analyzer")

    def test_allocation_approval_ready_audits_and_uses_reservation(self):
        equipment_request = self.make_request()
        allocate_request_assets(
            actor=self.manager, request_id=equipment_request.pk,
            line_id=equipment_request.lines.get().pk, asset_ids=[self.asset.pk],
        )
        equipment_request.refresh_from_db()
        self.assertEqual(equipment_request.status, EquipmentRequest.Status.REVIEWING)
        self.assertIsNotNone(equipment_request.reservation_id)
        self.assertEqual(equipment_request.reservation.status, Reservation.Status.PENDING)
        self.assertEqual(list(equipment_request.reservation.assets.all()), [self.asset])
        transition_equipment_request(
            actor=self.manager, request_id=equipment_request.pk,
            status=EquipmentRequest.Status.APPROVED, expected_status=EquipmentRequest.Status.REVIEWING,
        )
        equipment_request.refresh_from_db()
        self.asset.refresh_from_db()
        self.assertEqual(equipment_request.reservation.status, Reservation.Status.APPROVED)
        self.assertEqual(self.asset.status, Asset.Status.RESERVED)
        transition_equipment_request(
            actor=self.manager, request_id=equipment_request.pk,
            status=EquipmentRequest.Status.READY, expected_status=EquipmentRequest.Status.APPROVED,
        )
        self.assertEqual(
            list(equipment_request.events.values_list("event_type", flat=True)),
            [EquipmentRequestEvent.Type.STATUS_CHANGED, EquipmentRequestEvent.Type.STATUS_CHANGED,
             EquipmentRequestEvent.Type.ALLOCATED, EquipmentRequestEvent.Type.CREATED],
        )

    def test_stale_double_manager_transition_is_rejected(self):
        equipment_request = self.make_request()
        transition_equipment_request(
            actor=self.manager, request_id=equipment_request.pk,
            status=EquipmentRequest.Status.REVIEWING, expected_status=EquipmentRequest.Status.SUBMITTED,
        )
        with self.assertRaisesMessage(ValidationError, "changed after the page was opened"):
            transition_equipment_request(
                actor=self.manager, request_id=equipment_request.pk,
                status=EquipmentRequest.Status.DECLINED, expected_status=EquipmentRequest.Status.SUBMITTED,
            )
        equipment_request.refresh_from_db()
        self.assertEqual(equipment_request.status, EquipmentRequest.Status.REVIEWING)

    def test_linked_checkout_atomically_fulfills_request_with_event(self):
        equipment_request = self.make_request()
        allocate_request_assets(actor=self.manager, request_id=equipment_request.pk,
                                line_id=equipment_request.lines.get().pk, asset_ids=[self.asset.pk])
        transition_equipment_request(actor=self.manager, request_id=equipment_request.pk,
                                     status=EquipmentRequest.Status.APPROVED,
                                     expected_status=EquipmentRequest.Status.REVIEWING)
        transition_equipment_request(actor=self.manager, request_id=equipment_request.pk,
                                     status=EquipmentRequest.Status.READY,
                                     expected_status=EquipmentRequest.Status.APPROVED)
        equipment_request.refresh_from_db()
        checkout = checkout_assets(actor=self.manager, borrower_id=equipment_request.requestor_party_id,
                                   asset_ids=[self.asset.pk], reservation_id=equipment_request.reservation_id)
        equipment_request.refresh_from_db()
        self.assertEqual(equipment_request.status, EquipmentRequest.Status.FULFILLED)
        event = equipment_request.events.first()
        self.assertEqual(event.to_status, EquipmentRequest.Status.FULFILLED)
        self.assertEqual(event.metadata["checkout_number"], checkout.checkout_number)

    def test_generic_reservation_mutation_rejects_linked_request(self):
        equipment_request = self.make_request()
        allocate_request_assets(actor=self.manager, request_id=equipment_request.pk,
                                line_id=equipment_request.lines.get().pk, asset_ids=[self.asset.pk])
        equipment_request.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "managed from the equipment request"):
            set_reservation_status(actor=self.manager, reservation_id=equipment_request.reservation_id,
                                   status=Reservation.Status.APPROVED)

    def test_direct_fulfillment_requires_checkout(self):
        equipment_request = self.make_request()
        allocate_request_assets(actor=self.manager, request_id=equipment_request.pk,
                                line_id=equipment_request.lines.get().pk, asset_ids=[self.asset.pk])
        transition_equipment_request(actor=self.manager, request_id=equipment_request.pk,
                                     status=EquipmentRequest.Status.APPROVED,
                                     expected_status=EquipmentRequest.Status.REVIEWING)
        transition_equipment_request(actor=self.manager, request_id=equipment_request.pk,
                                     status=EquipmentRequest.Status.READY,
                                     expected_status=EquipmentRequest.Status.APPROVED)
        with self.assertRaisesMessage(ValidationError, "completed by checking out"):
            transition_equipment_request(actor=self.manager, request_id=equipment_request.pk,
                                         status=EquipmentRequest.Status.FULFILLED,
                                         expected_status=EquipmentRequest.Status.READY)

    def test_duplicate_asset_across_request_lines_is_clean_validation_error(self):
        equipment_request = self.make_request(lines=[
            {"category": self.category, "quantity": 1}, {"category": self.category, "quantity": 1},
        ])
        first, second = equipment_request.lines.all()
        allocate_request_assets(actor=self.manager, request_id=equipment_request.pk,
                                line_id=first.pk, asset_ids=[self.asset.pk])
        with self.assertRaisesMessage(ValidationError, "already allocated to this request"):
            allocate_request_assets(actor=self.manager, request_id=equipment_request.pk,
                                    line_id=second.pk, asset_ids=[self.asset.pk])

    def test_audit_events_capture_bounded_edit_assignment_and_note_deltas(self):
        equipment_request = self.make_request()
        values = self.values()
        values["purpose"] = "B" * 700
        update_equipment_request(actor=self.requester, request_id=equipment_request.pk, values=values,
                                 lines=[{"category": self.category, "quantity": 1, "notes": "changed"}])
        updated = equipment_request.events.first()
        self.assertEqual(updated.metadata["changes"]["purpose"]["from"], "Field commissioning")
        self.assertLessEqual(len(updated.metadata["changes"]["purpose"]["to"]), 501)
        assign_equipment_request(actor=self.manager, request_id=equipment_request.pk,
                                 assigned_to=self.manager, manager_notes="private manager note")
        assigned = equipment_request.events.first()
        self.assertEqual(assigned.metadata["changes"]["assigned_to_id"]["to"], self.manager.pk)
        self.assertEqual(assigned.metadata["changes"]["manager_notes"]["to"], "private manager note")
        client = Client(HTTP_HOST="eqreq.rplwms.com")
        client.force_login(self.requester)
        self.assertNotContains(client.get(f"/requests/{equipment_request.pk}/"), "private manager note")

    def test_forged_formset_over_maximum_is_rejected(self):
        client = Client(HTTP_HOST="eqreq.rplwms.com")
        client.force_login(self.requester)
        values = self.values()
        data = {**values, "needed_from": values["needed_from"].isoformat(),
                "needed_until": values["needed_until"].isoformat(),
                "lines-TOTAL_FORMS": "41", "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "1", "lines-MAX_NUM_FORMS": "20"}
        for index in range(41):
            data[f"lines-{index}-category"] = str(self.category.pk)
            data[f"lines-{index}-quantity"] = "1"
        response = client.post("/new/", data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "at most 20 forms")
        self.assertFalse(EquipmentRequest.objects.filter(requester=self.requester).exists())

    def test_past_needed_date_is_rejected_on_create_and_expired_approval(self):
        values = self.values()
        values["needed_from"] = timezone.localdate() - timedelta(days=1)
        values["needed_until"] = timezone.localdate()
        with self.assertRaisesMessage(ValidationError, "cannot be in the past"):
            create_equipment_request(actor=self.requester, values=values,
                                     lines=[{"category": self.category, "quantity": 1}])
        equipment_request = self.make_request()
        allocate_request_assets(actor=self.manager, request_id=equipment_request.pk,
                                line_id=equipment_request.lines.get().pk, asset_ids=[self.asset.pk])
        EquipmentRequest.objects.filter(pk=equipment_request.pk).update(
            needed_from=timezone.localdate() - timedelta(days=2),
            needed_until=timezone.localdate() - timedelta(days=1))
        with self.assertRaisesMessage(ValidationError, "window has expired"):
            transition_equipment_request(actor=self.manager, request_id=equipment_request.pk,
                                         status=EquipmentRequest.Status.APPROVED,
                                         expected_status=EquipmentRequest.Status.REVIEWING)

    def test_manager_can_permanently_delete_unfulfilled_request_with_tombstone(self):
        equipment_request = self.make_request()
        request_id = equipment_request.pk
        request_number = equipment_request.request_number
        long_notes = "N" * 700
        equipment_request.requester_notes = long_notes
        equipment_request.manager_notes = "Manager-only detail"
        equipment_request.assigned_to = self.manager
        equipment_request.save(update_fields=("requester_notes", "manager_notes", "assigned_to", "updated_at"))
        client = Client(HTTP_HOST="equipment.rplwms.com")
        client.force_login(self.manager)

        detail = client.get(f"/requests/{request_id}/")
        self.assertContains(detail, "Delete request")
        confirmation = client.get(f"/requests/{request_id}/delete/")
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, request_number)
        self.assertContains(confirmation, "Permanently delete request")

        response = client.post(
            f"/requests/{request_id}/delete/",
            {"confirmation": request_number},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Request {request_number} was permanently deleted")
        self.assertFalse(EquipmentRequest.objects.filter(pk=request_id).exists())
        tombstone = EquipmentRequestDeletion.objects.get(request_id=request_id)
        self.assertEqual(tombstone.request_number, request_number)
        self.assertEqual(tombstone.deleted_by, self.manager)
        self.assertEqual(tombstone.requester_label, self.requester.username)
        self.assertEqual(tombstone.snapshot["line_count"], 1)
        self.assertEqual(tombstone.snapshot["event_count"], 1)
        self.assertEqual(tombstone.snapshot["requester_notes"], long_notes)
        self.assertEqual(tombstone.snapshot["manager_notes"], "Manager-only detail")
        self.assertEqual(tombstone.snapshot["assigned_to"]["id"], self.manager.pk)
        self.assertEqual(tombstone.snapshot["events"][0]["type"], EquipmentRequestEvent.Type.CREATED)
        with self.assertRaises(TypeError):
            tombstone.delete()

    def test_request_delete_requires_exact_confirmation_and_manager_permission(self):
        equipment_request = self.make_request()
        csrf_client = Client(HTTP_HOST="equipment.rplwms.com", enforce_csrf_checks=True)
        csrf_client.force_login(self.manager)
        csrf_client.get(f"/requests/{equipment_request.pk}/delete/")
        denied = csrf_client.post(
            f"/requests/{equipment_request.pk}/delete/",
            {"confirmation": equipment_request.request_number},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertTrue(EquipmentRequest.objects.filter(pk=equipment_request.pk).exists())

        client = Client(HTTP_HOST="equipment.rplwms.com")
        client.force_login(self.manager)

        wrong = client.post(
            f"/requests/{equipment_request.pk}/delete/",
            {"confirmation": "wrong"},
            follow=True,
        )
        self.assertContains(wrong, f"Enter {equipment_request.request_number} exactly")
        self.assertTrue(EquipmentRequest.objects.filter(pk=equipment_request.pk).exists())
        self.assertFalse(EquipmentRequestDeletion.objects.exists())

        with self.assertRaises(PermissionDenied):
            delete_equipment_request(
                actor=self.requester,
                request_id=equipment_request.pk,
                confirmation=equipment_request.request_number,
            )
        requester_client = Client(HTTP_HOST="equipment.rplwms.com")
        requester_client.force_login(self.requester)
        self.assertEqual(
            requester_client.post(
                f"/requests/{equipment_request.pk}/delete/",
                {"confirmation": equipment_request.request_number},
            ).status_code,
            403,
        )

    def test_request_with_fulfillment_history_deletes_request_and_preserves_reservation(self):
        equipment_request = self.make_request()
        allocate_request_assets(
            actor=self.manager,
            request_id=equipment_request.pk,
            line_id=equipment_request.lines.get().pk,
            asset_ids=[self.asset.pk],
        )
        equipment_request.refresh_from_db()
        request_id = equipment_request.pk
        reservation_id = equipment_request.reservation_id
        client = Client(HTTP_HOST="equipment.rplwms.com")
        client.force_login(self.manager)

        confirmation = client.get(f"/requests/{request_id}/delete/")
        self.assertContains(confirmation, "Linked equipment history will be preserved")
        self.assertContains(confirmation, "Permanently delete request")
        response = client.post(
            f"/requests/{request_id}/delete/",
            {"confirmation": equipment_request.request_number},
            follow=True,
        )

        self.assertContains(response, "was permanently deleted")
        self.assertFalse(EquipmentRequest.objects.filter(pk=request_id).exists())
        reservation = Reservation.objects.get(pk=reservation_id)
        self.assertEqual(reservation.status, Reservation.Status.CANCELLED)
        tombstone = EquipmentRequestDeletion.objects.get(request_id=request_id)
        self.assertEqual(tombstone.snapshot["reservation"]["id"], str(reservation_id))
        self.assertEqual(tombstone.snapshot["reservation"]["status_before"], Reservation.Status.PENDING)
        self.assertEqual(tombstone.snapshot["reservation"]["status_after"], Reservation.Status.CANCELLED)
        self.assertEqual(
            tombstone.snapshot["lines"][0]["allocations"][0]["asset_tag"],
            self.asset.asset_tag,
        )
        self.assertEqual(
            tombstone.snapshot["lines"][0]["allocations"][0]["allocated_by_id"],
            self.manager.pk,
        )

    def test_deleting_approved_request_cancels_reservation_and_releases_asset(self):
        equipment_request = self.make_request()
        allocate_request_assets(
            actor=self.manager, request_id=equipment_request.pk,
            line_id=equipment_request.lines.get().pk, asset_ids=[self.asset.pk],
        )
        transition_equipment_request(
            actor=self.manager, request_id=equipment_request.pk,
            status=EquipmentRequest.Status.APPROVED,
            expected_status=EquipmentRequest.Status.REVIEWING,
        )
        equipment_request.refresh_from_db()
        reservation_id = equipment_request.reservation_id
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.RESERVED)

        delete_equipment_request(
            actor=self.manager,
            request_id=equipment_request.pk,
            confirmation=equipment_request.request_number,
        )

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.AVAILABLE)
        self.assertEqual(
            Reservation.objects.get(pk=reservation_id).status,
            Reservation.Status.CANCELLED,
        )
        release_event = AssetEvent.objects.filter(
            asset=self.asset,
            event_type=AssetEvent.Type.RESERVATION_CHANGED,
            metadata__to_status=Reservation.Status.CANCELLED,
        ).get()
        self.assertEqual(release_event.actor, self.manager)
        self.assertEqual(release_event.metadata["from_status"], Reservation.Status.APPROVED)
        asset_snapshot = EquipmentRequestDeletion.objects.get(
            request_id=equipment_request.pk
        ).snapshot["reservation"]["assets"][0]
        self.assertEqual(asset_snapshot["status_before"], Asset.Status.RESERVED)
        self.assertEqual(asset_snapshot["status_after"], Asset.Status.AVAILABLE)

    def test_delete_preserves_nonreserved_asset_state_in_reservation_snapshot(self):
        equipment_request = self.make_request()
        allocate_request_assets(
            actor=self.manager, request_id=equipment_request.pk,
            line_id=equipment_request.lines.get().pk, asset_ids=[self.asset.pk],
        )
        transition_equipment_request(
            actor=self.manager, request_id=equipment_request.pk,
            status=EquipmentRequest.Status.APPROVED,
            expected_status=EquipmentRequest.Status.REVIEWING,
        )
        Asset.objects.filter(pk=self.asset.pk).update(status=Asset.Status.MAINTENANCE)

        delete_equipment_request(
            actor=self.manager,
            request_id=equipment_request.pk,
            confirmation=equipment_request.request_number,
        )

        asset_snapshot = EquipmentRequestDeletion.objects.get(
            request_id=equipment_request.pk
        ).snapshot["reservation"]["assets"][0]
        self.assertEqual(asset_snapshot["status_before"], Asset.Status.MAINTENANCE)
        self.assertEqual(asset_snapshot["status_after"], Asset.Status.MAINTENANCE)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.MAINTENANCE)

    def test_delete_rolls_back_reservation_release_if_request_removal_fails(self):
        equipment_request = self.make_request()
        allocate_request_assets(
            actor=self.manager, request_id=equipment_request.pk,
            line_id=equipment_request.lines.get().pk, asset_ids=[self.asset.pk],
        )
        transition_equipment_request(
            actor=self.manager, request_id=equipment_request.pk,
            status=EquipmentRequest.Status.APPROVED,
            expected_status=EquipmentRequest.Status.REVIEWING,
        )
        equipment_request.refresh_from_db()
        queryset_type = type(equipment_request.events.all())

        with patch.object(queryset_type, "_raw_delete", side_effect=RuntimeError("forced failure")):
            with self.assertRaisesMessage(RuntimeError, "forced failure"):
                delete_equipment_request(
                    actor=self.manager,
                    request_id=equipment_request.pk,
                    confirmation=equipment_request.request_number,
                )

        self.assertTrue(EquipmentRequest.objects.filter(pk=equipment_request.pk).exists())
        self.assertFalse(EquipmentRequestDeletion.objects.filter(request_id=equipment_request.pk).exists())
        self.assertEqual(
            Reservation.objects.get(pk=equipment_request.reservation_id).status,
            Reservation.Status.APPROVED,
        )
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.RESERVED)

    def test_deleting_fulfilled_request_preserves_checkout_and_custody(self):
        equipment_request = self.make_request()
        allocate_request_assets(
            actor=self.manager, request_id=equipment_request.pk,
            line_id=equipment_request.lines.get().pk, asset_ids=[self.asset.pk],
        )
        transition_equipment_request(
            actor=self.manager, request_id=equipment_request.pk,
            status=EquipmentRequest.Status.APPROVED,
            expected_status=EquipmentRequest.Status.REVIEWING,
        )
        transition_equipment_request(
            actor=self.manager, request_id=equipment_request.pk,
            status=EquipmentRequest.Status.READY,
            expected_status=EquipmentRequest.Status.APPROVED,
        )
        equipment_request.refresh_from_db()
        reservation_id = equipment_request.reservation_id
        checkout = checkout_assets(
            actor=self.manager,
            borrower_id=equipment_request.requestor_party_id,
            asset_ids=[self.asset.pk],
            reservation_id=reservation_id,
        )
        equipment_request.refresh_from_db()

        delete_equipment_request(
            actor=self.manager,
            request_id=equipment_request.pk,
            confirmation=equipment_request.request_number,
        )

        self.assertTrue(type(checkout).objects.filter(pk=checkout.pk).exists())
        self.assertEqual(
            Reservation.objects.get(pk=reservation_id).status,
            Reservation.Status.FULFILLED,
        )
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.CHECKED_OUT)
        self.assertEqual(self.asset.current_party_id, equipment_request.requestor_party_id)
        self.assertEqual(
            ActiveCustody.objects.get(asset=self.asset).borrower_id,
            equipment_request.requestor_party_id,
        )

    def test_role_provisioning_removes_stale_request_management_permissions(self):
        from .roles import provision_equipment_roles

        stale = Permission.objects.get(content_type__app_label="equipment", codename="manage_equipment_requests")
        requester = Group.objects.get(name="Equipment Requester")
        logistics = Group.objects.get(name="Logistics Manager")
        requester.permissions.add(stale, Permission.objects.get(
            content_type__app_label="equipment", codename="access_equipment_portal"))
        logistics.permissions.add(stale)
        provision_equipment_roles(sender=type("Sender", (), {"label": "equipment"})())
        self.assertEqual(set(requester.permissions.filter(content_type__app_label="equipment")
                             .values_list("codename", flat=True)), {"access_equipment_requests"})
        self.assertFalse(logistics.permissions.filter(codename="manage_equipment_requests",
                                                       content_type__app_label="equipment").exists())
