from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse

from .models import InventoryItem, MaterialRequest


class MaterialRequestInventoryPickerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("request-admin", "", "pw")
        self.client.force_login(self.user)
        self.active = InventoryItem.objects.create(
            part_number="CAT-100",
            name="Cut resistant gloves",
            category="Safety",
            quantity_on_hand=42,
            unit="pair",
            building_room="BLDG 1",
            rack="A",
            section="2",
            bin_location="3",
            active=True,
        )
        self.inactive = InventoryItem.objects.create(
            part_number="OLD-200",
            name="Retired item",
            quantity_on_hand=7,
            active=False,
        )

    def test_create_page_contains_searchable_inventory_picker_without_leaving_form(self):
        response = self.client.get(
            reverse("material_request_create"),
            HTTP_HOST="requests.rplwms.com",
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="inventory-picker"', html=False)
        self.assertContains(response, 'id="inventory-search"', html=False)
        self.assertContains(response, f'data-item-id="{self.active.pk}"', html=False)
        self.assertContains(response, "CAT-100")
        self.assertContains(response, "Cut resistant gloves")
        self.assertContains(response, "42 pair")
        self.assertContains(response, "A-02-03")
        self.assertContains(response, 'min="1"', html=False)
        self.assertNotContains(response, "OLD-200")
        self.assertContains(response, 'data-material-request-draft="create"', html=False)
        self.assertContains(response, 'data-has-errors="false"', html=False)

    def test_request_creator_without_inventory_view_permission_cannot_enumerate_stock(self):
        user = User.objects.create_user("request-only", password="pw")
        for codename in ("add_materialrequest", "add_materialrequestline", "access_material_request_portal"):
            user.user_permissions.add(Permission.objects.get(content_type__app_label="inventory", codename=codename))
        self.client.force_login(user)
        response = self.client.get(
            reverse("material_request_create"), HTTP_HOST="requests.rplwms.com", secure=True,
        )
        self.assertEqual(response.status_code, 302)

    def test_inactive_item_already_on_request_remains_editable(self):
        self.client.post(
            reverse("material_request_create"),
            {
                "requestor_name": "Picker Tester", "requestor_email": "picker@example.com",
                "building_room": "BLDG", "location": "Dock", "notes": "",
                "delivery_at": "2026-09-15T10:00",
                "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "1", "lines-MAX_NUM_FORMS": "1000",
                "lines-0-item": str(self.active.pk), "lines-0-quantity": "2", "lines-0-notes": "",
            },
            HTTP_HOST="requests.rplwms.com", secure=True,
        )
        request_obj = MaterialRequest.objects.get()
        line = request_obj.lines.get()
        self.active.active = False
        self.active.save(update_fields=["active"])
        response = self.client.get(
            reverse("material_request_edit", args=[request_obj.pk]),
            HTTP_HOST="requests.rplwms.com", secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{line.item_id}" selected')

    def test_invalid_submission_keeps_inventory_picker_and_entered_header_values(self):
        response = self.client.post(
            reverse("material_request_create"),
            {
                "requestor_name": "Pat Requestor",
                "building_room": "BLDG 9 / 12",
                "location": "West staging",
                "notes": "Keep this work",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "1",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-item": "",
                "lines-0-quantity": "",
                "lines-0-notes": "",
            },
            HTTP_HOST="requests.rplwms.com",
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="inventory-picker"', html=False)
        self.assertContains(response, "Pat Requestor")
        self.assertContains(response, "BLDG 9 / 12")
        self.assertContains(response, "Keep this work")


class GroupRenameTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin", "", "pw")
        self.client.force_login(self.admin)

    def test_renaming_group_preserves_memberships_permissions_and_effective_access(self):
        group = Group.objects.create(name="Material Request Team")
        permission = Permission.objects.get(
            content_type__app_label="inventory",
            codename="view_inventoryitem",
        )
        group.permissions.add(permission)
        member = User.objects.create_user("member", password="pw")
        member.groups.add(group)

        response = self.client.post(
            reverse("group_rename", args=[group.pk]),
            {"name": "Logistics Request Team", "next": reverse("user_edit", args=[member.pk])},
            secure=True,
        )

        self.assertRedirects(
            response,
            reverse("user_edit", args=[member.pk]),
            fetch_redirect_response=False,
        )
        group.refresh_from_db()
        member = User.objects.get(pk=member.pk)
        self.assertEqual(group.name, "Logistics Request Team")
        self.assertTrue(member.groups.filter(pk=group.pk).exists())
        self.assertTrue(group.permissions.filter(pk=permission.pk).exists())
        self.assertTrue(member.has_perm("inventory.view_inventoryitem"))

    def test_user_edit_page_exposes_group_rename_control(self):
        group = Group.objects.create(name="Warehouse Operators")
        member = User.objects.create_user("operator", password="pw")

        response = self.client.get(reverse("user_edit", args=[member.pk]), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("group_rename", args=[group.pk]))
        self.assertContains(response, f'value="{group.name}"', html=False)

    def test_renamed_material_request_group_is_not_recreated_by_provisioning(self):
        from django.apps import apps
        from .signals import provision_material_requests_group

        group = Group.objects.get(name="Material Requests")
        response = self.client.post(
            reverse("group_rename", args=[group.pk]),
            {"name": "Request Creators"},
            secure=True,
        )
        self.assertEqual(response.status_code, 302)

        provision_material_requests_group(sender=apps.get_app_config("inventory"))

        group.refresh_from_db()
        self.assertEqual(group.name, "Request Creators")
        self.assertFalse(Group.objects.filter(name="Material Requests").exists())
        self.assertTrue(group.permissions.filter(codename="access_material_request_portal").exists())

    def test_renaming_role_group_does_not_change_permission_based_location_access(self):
        group = Group.objects.create(name="Lead")
        group.permissions.add(Permission.objects.get(
            content_type__app_label="inventory", codename="manage_storage_locations",
        ))
        member = User.objects.create_user("lead-user", password="pw")
        member.groups.add(group)
        InventoryItem.objects.create(
            part_number="LOC-1", name="Located item", quantity_on_hand=1,
            building_room="BLDG", rack="A", section="1", bin_location="2",
        )
        group.name = "Warehouse Coordinators"
        group.save(update_fields=["name"])
        self.client.force_login(member)
        response = self.client.get(reverse("location_detail", args=["A-1-2"]), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_lead"])

    def test_group_permission_edit_preserves_permissions_from_other_apps(self):
        group = Group.objects.create(name="Cross App")
        external = Permission.objects.get(content_type__app_label="auth", codename="view_user")
        inventory_perm = Permission.objects.get(
            content_type__app_label="inventory", codename="view_inventoryitem",
        )
        group.permissions.add(external)
        response = self.client.post(
            reverse("group_permissions", args=[group.pk]),
            {"permissions": [inventory_perm.pk]}, secure=True,
        )
        self.assertEqual(response.status_code, 302)
        self.assertSetEqual(
            set(group.permissions.values_list("pk", flat=True)),
            {external.pk, inventory_perm.pk},
        )


class GroupPermissionsCheckAllTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("check-all-admin", "", "pw")
        self.client.force_login(self.admin)
        self.group = Group.objects.create(name="Check All Group")

    def test_group_permissions_page_exposes_check_all_toggle(self):
        response = self.client.get(reverse("group_permissions", args=[self.group.pk]), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="check-all-permissions"', html=False)
        self.assertContains(response, "Check all", html=False)
        self.assertContains(response, 'data-check-all-target=".permission-grid"', html=False)

    def test_check_all_toggle_selects_every_permission(self):
        perms = Permission.objects.filter(
            content_type__app_label="inventory",
            codename__in=[
                "view_inventoryitem",
                "add_inventoryitem",
                "change_pickticket",
                "view_pickticket",
            ],
        )
        response = self.client.post(
            reverse("group_permissions", args=[self.group.pk]),
            {"permissions": [p.pk for p in perms]},
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        self.group.refresh_from_db()
        self.assertSetEqual(
            set(self.group.permissions.values_list("pk", flat=True)),
            {p.pk for p in perms},
        )

    def test_unchecking_check_all_deselects_every_permission(self):
        perms = list(Permission.objects.filter(
            content_type__app_label="inventory",
            codename__in=["view_inventoryitem", "change_pickticket"],
        ))
        self.group.permissions.add(*perms)

        response = self.client.post(
            reverse("group_permissions", args=[self.group.pk]),
            {"permissions": []},
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        self.group.refresh_from_db()
        self.assertEqual(self.group.permissions.count(), 0)


class LogisticsSpecialistVisibilityTests(TestCase):
    """Logistics Specialist should see all material requests / pick tickets regardless of assignee."""

    def setUp(self):
        from .models import PickTicket

        self.specialist = User.objects.create_user("ls-specialist", password="pw")
        specialist_group, _ = Group.objects.get_or_create(name="Logistics Specialist")
        specialist_group.permissions.add(Permission.objects.get(
            content_type__app_label="inventory",
            codename="view_all_materialrequests",
        ))
        self.specialist.groups.add(specialist_group)
        self.specialist.refresh_from_db()

        self.other_user = User.objects.create_user("ls-other", password="pw")

        def _make_request(name, creator, building, *, assigned_to=None):
            ticket = PickTicket.objects.create(
                picked_by_name="", received_by_name="", requested_by_name=name,
                building_room=building, location="Dock",
                created_by=self.other_user,
            )
            return MaterialRequest.objects.create(
                requestor_name=name, creator=creator,
                building_room=building, location="Dock",
                assigned_to=assigned_to, pick_ticket=ticket,
            )

        self.request_mine = _make_request("Specialist", self.specialist, "BLDG 1")
        self.request_others = _make_request("Other", self.other_user, "BLDG 2")
        self.request_assigned_to_other = _make_request(
            "Other", self.other_user, "BLDG 3", assigned_to=self.other_user,
        )
        self.request_assigned_to_specialist = _make_request(
            "Other", self.other_user, "BLDG 4", assigned_to=self.specialist,
        )

        self.ticket_unassigned = self.request_others.pick_ticket
        self.ticket_assigned_to_other = self.request_assigned_to_other.pick_ticket
        self.ticket_assigned_to_specialist = self.request_assigned_to_specialist.pick_ticket

        self.client.force_login(self.specialist)

    def test_specialist_sees_all_material_requests_regardless_of_creator(self):
        from .views import _visible_material_requests

        visible = _visible_material_requests(
            type("R", (), {"user": self.specialist})()
        )
        ids = set(visible.values_list("pk", flat=True))
        self.assertSetEqual(
            ids,
            {
                self.request_mine.pk,
                self.request_others.pk,
                self.request_assigned_to_other.pk,
                self.request_assigned_to_specialist.pk,
            },
        )

    def test_specialist_sees_all_pick_tickets_regardless_of_assignee(self):
        from .views import _visible_pick_tickets

        visible = _visible_pick_tickets(
            type("R", (), {"user": self.specialist})()
        )
        ids = set(visible.values_list("pk", flat=True))
        self.assertIn(self.ticket_unassigned.pk, ids)
        self.assertIn(self.ticket_assigned_to_other.pk, ids)
