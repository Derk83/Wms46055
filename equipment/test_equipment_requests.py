from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.utils import timezone

from .models import (
    Asset,
    EquipmentCategory,
    EquipmentParty,
    EquipmentRequest,
    EquipmentRequestEvent,
    Reservation,
)
from .request_services import (
    allocate_request_assets,
    create_equipment_request,
    transition_equipment_request,
)


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
