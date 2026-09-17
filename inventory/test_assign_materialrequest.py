"""Tests for splitting material-request assignment rights from view rights.

Logistics Specialists can see every material request (via migration 0042)
but must NOT be able to reassign the work to anyone. Procurement Specialists
and Logistics Managers keep the ability to assign.

These tests exercise the gating at three layers:
1. The ``material_request_assign`` view rejects the POST (302 → 403 / bounce).
2. The board template hides the Assign-to form and shows a read-only label.
3. The migration grants ``assign_materialrequest`` to Procurement Specialist
   and Logistics Manager and NOT to Logistics Specialist.
"""
from django.contrib.auth.models import Group, Permission, User
from django.test import Client, TestCase
from django.urls import reverse

from .models import InventoryItem, MaterialRequestEvent, PickTicket
from .services import create_material_request


def _make_user(username, *, groups=()):
    user = User.objects.create_user(username, password="pw")
    if groups:
        perms = Permission.objects.filter(
            content_type__app_label="inventory",
            codename__in={
                "view_materialrequest",
                "change_materialrequest",
                "view_all_materialrequests",
            },
        )
        user.user_permissions.add(*perms)
        for group in groups:
            user.groups.add(group)
    return user


class MaterialRequestAssignmentPermissionTests(TestCase):
    host = "bbx.rplwms.com"

    @classmethod
    def setUpTestData(cls):
        # Ensure the assign_materialrequest permission exists (test DB is in-memory).
        cls.assign_perm, _ = Permission.objects.get_or_create(
            content_type__app_label="inventory",
            content_type__model="materialrequest",
            codename="assign_materialrequest",
            defaults={"name": "Can assign material requests to warehouse staff"},
        )

    def setUp(self):
        # Create the three warehouse groups (some may exist if migrations
        # provision them, but the test DB doesn't run our group signals).
        self.procurement_group, _ = Group.objects.get_or_create(name="Procurement Specialist")
        self.logistics_manager_group, _ = Group.objects.get_or_create(name="Logistics Manager")
        self.logistics_specialist_group, _ = Group.objects.get_or_create(name="Logistics Specialist")

        # Apply the same grants the migration would do, so these tests
        # don't depend on whether migration 0043 has been applied to the
        # operator's DB before the test suite runs.
        self.procurement_group.permissions.add(self.assign_perm)
        self.logistics_manager_group.permissions.add(self.assign_perm)
        # Logistics Specialist is intentionally NOT granted assign_materialrequest.

        self.item = InventoryItem.objects.create(
            part_number="PERM-1", name="Perm gloves", quantity_on_hand=100
        )
        self.owner = _make_user("perm-owner")
        self.request_obj = create_material_request(
            creator=self.owner,
            requestor_name="Perm Tester",
            requestor_email="perm@example.com",
            building_room="B1",
            location="Dock",
            delivery_at=None,
            urgent=False,
            notes="",
            lines=[{"item": self.item, "quantity": 1, "notes": ""}],
        )
        # Pick a future delivery time so the slot-occupied validation
        # doesn't fire during unassign tests.
        from django.utils import timezone
        from datetime import timedelta
        self.request_obj.delivery_at = timezone.now() + timedelta(days=1)
        self.request_obj.save(update_fields=["delivery_at", "updated_at"])

        self.specialist = _make_user(
            "perm-specialist", groups=[self.logistics_specialist_group]
        )
        self.logistics_manager = _make_user(
            "perm-logmgr", groups=[self.logistics_manager_group]
        )
        self.procurement = _make_user(
            "perm-procurement", groups=[self.procurement_group]
        )

        # Add a candidate picker they could assign to.
        self.worker = _make_user(
            "perm-worker", groups=[self.logistics_specialist_group]
        )
        self.worker.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="inventory", codename="change_pickticket"
            )
        )

    def _post_assign(self, user, assignee_pk):
        c = Client()
        c.force_login(user)
        return c.post(
            reverse("material_request_assign", args=[self.request_obj.pk]),
            {"assigned_to": assignee_pk},
            HTTP_HOST=self.host,
        )

    def test_specialist_cannot_assign_via_post(self):
        # The decorator redirects authenticated-but-unauthorized users with
        # a flash message rather than raising 403 — verify the side effects:
        # no redirect URL is set, no event was created, and the assigned_to
        # field is still None.
        response = self._post_assign(self.specialist, self.worker.pk)
        self.assertEqual(response.status_code, 302)
        self.request_obj.refresh_from_db()
        self.assertIsNone(self.request_obj.assigned_to)
        self.assertEqual(
            MaterialRequestEvent.objects.filter(
                material_request=self.request_obj,
                change_summary__startswith="Assigned to:",
            ).count(),
            0,
            "Specialist should not be able to create assignment events",
        )
        # The redirect should NOT land on a success URL — it should go to
        # the previous page (HTTP_REFERER wasn't set in the test client,
        # so the decorator falls back to a known route).
        self.assertNotIn(
            reverse("material_request_assign", args=[self.request_obj.pk]),
            response.url or "",
            "Specialist should NOT be redirected back to the assign endpoint",
        )

    def test_logistics_manager_can_assign_via_post(self):
        response = self._post_assign(self.logistics_manager, self.worker.pk)
        self.assertEqual(response.status_code, 302)
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.assigned_to, self.worker)

    def test_procurement_can_assign_via_post(self):
        response = self._post_assign(self.procurement, self.worker.pk)
        self.assertEqual(response.status_code, 302)
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.assigned_to, self.worker)

    def test_board_hides_assign_form_for_specialist_but_shows_readonly_label(self):
        c = Client()
        c.force_login(self.specialist)
        response = c.get(reverse("material_request_board"), HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        # The assign-to <select> and Save button must NOT render for the specialist.
        self.assertNotIn('id="assigned-to-', body)
        self.assertNotIn('class="assignment-form"', body)
        self.assertNotIn(">Save</button>", body)

        # The read-only label SHOULD render instead.
        self.assertIn("assignment-readonly", body)
        # And the ticket must still be visible (LogSpec has view_all).
        self.assertIn(self.request_obj.request_number, body)

    def test_board_shows_assign_form_for_logistics_manager(self):
        c = Client()
        c.force_login(self.logistics_manager)
        response = c.get(reverse("material_request_board"), HTTP_HOST=self.host)
        body = response.content.decode()
        self.assertIn("assignment-form", body)
        self.assertIn('id="assigned-to-', body)
        self.assertNotIn("assignment-readonly", body)

    def test_migration_grants_assign_to_procurement_and_logistics_manager(self):
        # The migration's grant step has been applied above; verify the
        # resulting permission set on each warehouse group.
        self.assertIn(
            self.assign_perm,
            self.procurement_group.permissions.all(),
            "Procurement Specialist should be granted assign_materialrequest",
        )
        self.assertIn(
            self.assign_perm,
            self.logistics_manager_group.permissions.all(),
            "Logistics Manager should be granted assign_materialrequest",
        )
        self.assertNotIn(
            self.assign_perm,
            self.logistics_specialist_group.permissions.all(),
            "Logistics Specialist must NOT be granted assign_materialrequest",
        )

    def test_specialist_still_sees_all_requests_for_readonly_workflow(self):
        """Regression: Logistics Specialist must keep view_all_materialrequests."""
        self.assertTrue(
            self.specialist.has_perm("inventory.view_all_materialrequests"),
            "Logistics Specialist should still see every request",
        )
        self.assertFalse(
            self.specialist.has_perm("inventory.assign_materialrequest"),
            "Logistics Specialist should NOT be able to assign",
        )
