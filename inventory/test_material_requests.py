from datetime import datetime, timezone as datetime_timezone

from django.contrib.auth.models import Group, Permission, User
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from .models import InventoryItem, MaterialRequestEvent, PickTicket


class MaterialRequestWorkflowTests(TestCase):
    def setUp(self):
        from .models import MaterialRequest

        self.MaterialRequest = MaterialRequest
        self.user = User.objects.create_user("requestor", password="pw", first_name="Rae", last_name="User")
        self.item_a = InventoryItem.objects.create(part_number="A", name="Gloves", quantity_on_hand=20)
        self.item_b = InventoryItem.objects.create(part_number="B", name="Glasses", quantity_on_hand=30)

    def test_service_creates_number_ticket_lines_inventory_and_event_on_commit(self):
        from .models import MaterialRequestEvent
        from .services import create_material_request

        with self.captureOnCommitCallbacks(execute=True):
            material_request = create_material_request(
                creator=self.user,
                requestor_name="Rae User",
                building_room="BLDG 1 / 204",
                location="North desk",
                notes="Urgent",
                lines=[{"item": self.item_a, "quantity": 3, "notes": "Large"}],
            )
        self.assertEqual(material_request.request_number, f"MR-{material_request.pk:06d}")
        self.assertEqual(material_request.pick_ticket.status, PickTicket.Status.OPEN)
        self.assertEqual(material_request.pick_ticket.requested_by_name, "Rae User")
        self.assertEqual(material_request.pick_ticket.lines.get().quantity, 3)
        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.quantity_on_hand, 17)
        self.assertEqual(MaterialRequestEvent.objects.get().material_request, material_request)

    def test_update_atomically_rebuilds_ticket_and_applies_exact_stock_delta(self):
        from .services import create_material_request, update_material_request

        request_obj = create_material_request(
            creator=self.user, requestor_name="Rae", building_room="B1", location="L1",
            notes="", lines=[{"item": self.item_a, "quantity": 4, "notes": "old"}],
        )
        request_obj.pick_ticket.status = PickTicket.Status.PICKED
        request_obj.pick_ticket.save(update_fields=["status"])
        update_material_request(
            request_obj,
            requestor_name="Rae Updated", building_room="B2", location="L2", notes="new",
            lines=[{"item": self.item_a, "quantity": 1, "notes": "one"}, {"item": self.item_b, "quantity": 5, "notes": "five"}],
        )
        self.item_a.refresh_from_db(); self.item_b.refresh_from_db()
        request_obj.refresh_from_db(); request_obj.pick_ticket.refresh_from_db()
        self.assertEqual((self.item_a.quantity_on_hand, self.item_b.quantity_on_hand), (19, 25))
        self.assertEqual(request_obj.pick_ticket.status, PickTicket.Status.PICKED)
        self.assertEqual(request_obj.pick_ticket.requested_by_name, "Rae Updated")
        self.assertEqual(request_obj.pick_ticket.lines.count(), 2)

    def test_delete_restores_inventory_and_deletes_linked_ticket(self):
        from .services import create_material_request, delete_material_request

        request_obj = create_material_request(
            creator=self.user, requestor_name="Rae", building_room="B", location="L", notes="",
            lines=[{"item": self.item_a, "quantity": 6, "notes": ""}],
        )
        ticket_pk = request_obj.pick_ticket_id
        delete_material_request(request_obj)
        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.quantity_on_hand, 20)
        self.assertFalse(PickTicket.objects.filter(pk=ticket_pk).exists())
        self.assertFalse(self.MaterialRequest.objects.exists())

    def test_linked_pick_ticket_edit_stays_canonical_and_delete_requires_manager(self):
        from .services import create_material_request

        request_obj = create_material_request(
            creator=self.user, requestor_name="Rae", building_room="B", location="L", notes="",
            lines=[{"item": self.item_a, "quantity": 2, "notes": ""}],
        )
        self.user.user_permissions.add(
            Permission.objects.get(codename="change_pickticket"),
            Permission.objects.get(codename="delete_pickticket"),
            Permission.objects.get(codename="delete_materialrequest"),
            Permission.objects.get(codename="delete_materialrequestline"),
        )
        client = Client()
        client.force_login(self.user)
        edit = client.get(reverse("ticket_edit", args=[request_obj.pick_ticket_id]), HTTP_HOST="bbx.rplwms.com")
        delete = client.get(reverse("ticket_delete", args=[request_obj.pick_ticket_id]), HTTP_HOST="bbx.rplwms.com")
        self.assertRedirects(
            edit,
            reverse("material_request_edit", args=[request_obj.pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(delete.status_code, 403)

        deleted = client.post(
            reverse("ticket_delete", args=[request_obj.pick_ticket_id]),
            HTTP_HOST="bbx.rplwms.com",
        )
        self.assertEqual(deleted.status_code, 403)
        self.assertTrue(self.MaterialRequest.objects.filter(pk=request_obj.pk).exists())
        self.assertTrue(PickTicket.objects.filter(pk=request_obj.pick_ticket_id).exists())
        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.quantity_on_hand, 18)

    def test_duplicate_items_have_form_and_database_validation(self):
        from .forms import MaterialRequestLineFormSet
        from .services import create_material_request

        parent = self.MaterialRequest(creator=self.user)
        data = {
            "lines-TOTAL_FORMS": "2", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "1", "lines-MAX_NUM_FORMS": "1000",
            "lines-0-item": str(self.item_a.pk), "lines-0-quantity": "1", "lines-0-notes": "",
            "lines-1-item": str(self.item_a.pk), "lines-1-quantity": "2", "lines-1-notes": "",
        }
        formset = MaterialRequestLineFormSet(data, instance=parent)
        self.assertFalse(formset.is_valid())
        self.assertIn("duplicate", str(formset.non_form_errors()).lower())
        request_obj = create_material_request(
            creator=self.user, requestor_name="R", building_room="B", location="L", notes="",
            lines=[{"item": self.item_a, "quantity": 1, "notes": ""}],
        )
        from .models import MaterialRequestLine
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MaterialRequestLine.objects.create(material_request=request_obj, item=self.item_a, quantity=2)

    def test_request_header_fields_are_optional_and_delivery_slot_is_saved(self):
        from .forms import MaterialRequestForm
        from .services import create_material_request

        delivery_at = datetime(2026, 9, 15, 14, 30, tzinfo=datetime_timezone.utc)
        form = MaterialRequestForm({
            "requestor_name": "Derek", "requestor_email": "derek@example.com",
            "building_room": "", "location": "Job Site A", "notes": "",
            "delivery_at": "2026-09-15T14:30",
        })
        self.assertTrue(form.is_valid(), form.errors)
        request_obj = create_material_request(
            creator=self.user, requestor_name="Derek", requestor_email="derek@example.com",
            building_room="", location="Job Site A", notes="",
            delivery_at=delivery_at,
            lines=[{"item": self.item_a, "quantity": 1, "notes": ""}],
        )
        self.assertEqual(request_obj.delivery_at, delivery_at)
        self.assertEqual(request_obj.pick_ticket.requested_by_name, "Derek")

    def test_urgent_checkbox_is_persisted_and_defaults_to_not_urgent(self):
        from .forms import MaterialRequestForm
        from .services import create_material_request

        default_request = create_material_request(
            creator=self.user, requestor_name="Normal",
            requestor_email="normal@example.com",
            building_room="", location="Job Site A", notes="",
            delivery_at=datetime(2026, 9, 15, 14, 30, tzinfo=datetime_timezone.utc),
            lines=[{"item": self.item_a, "quantity": 1, "notes": ""}],
        )
        self.assertFalse(default_request.urgent)

        form = MaterialRequestForm({
            "requestor_name": "Urgent", "requestor_email": "urgent@example.com",
            "building_room": "", "location": "Job Site A", "notes": "",
            "delivery_at": "2026-09-15T14:45", "urgent": "on",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.cleaned_data["urgent"])

        urgent_request = create_material_request(
            creator=self.user, requestor_name="Urgent", building_room="", location="", notes="",
            urgent=True,
            delivery_at=datetime(2026, 9, 15, 14, 45, tzinfo=datetime_timezone.utc),
            lines=[{"item": self.item_b, "quantity": 1, "notes": ""}],
        )
        self.assertTrue(urgent_request.urgent)

    def test_delivery_slot_cannot_be_used_by_two_material_requests(self):
        from .forms import MaterialRequestForm
        from .services import create_material_request

        delivery_at = datetime(2026, 9, 15, 14, 30, tzinfo=datetime_timezone.utc)
        create_material_request(
            creator=self.user, requestor_name="First", building_room="", location="", notes="",
            delivery_at=delivery_at,
            lines=[{"item": self.item_a, "quantity": 1, "notes": ""}],
        )
        form = MaterialRequestForm({
            "requestor_name": "Second", "building_room": "", "location": "", "notes": "",
            "delivery_at": "2026-09-15T14:30",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("already scheduled", str(form.errors).lower())

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.MaterialRequest.objects.create(
                    requestor_name="Race", creator=self.user,
                    pick_ticket=PickTicket.objects.create(
                        picked_by_name="", received_by_name="", requested_by_name="",
                        building_room="", location="", created_by=self.user,
                    ),
                    delivery_at=delivery_at,
                )


class MaterialRequestAccessAndHostTests(TestCase):
    def setUp(self):
        self.group = Group.objects.get(name="Material Requests")
        self.user = User.objects.create_user("portal", password="pw")
        self.user.groups.add(self.group)
        self.item = InventoryItem.objects.create(part_number="PPE-1", name="Vest", quantity_on_hand=10)
        self.client.force_login(self.user)

    def test_empty_board_uses_compact_kanban_columns(self):
        response = self.client.get(
            reverse("material_request_board"), HTTP_HOST="requests.rplwms.com"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="kanban kanban-empty"', html=False)
        self.assertContains(response, "No requests found")

        self._request_for(self.user, "Portal User")
        populated = self.client.get(
            reverse("material_request_board"), HTTP_HOST="requests.rplwms.com"
        )
        self.assertContains(populated, 'class="kanban"', html=False)
        self.assertNotContains(populated, 'class="kanban kanban-empty"', html=False)

    def _post_data(self, qty="2"):
        return {
            "requestor_name": "Portal User", "requestor_email": "portal@example.com",
            "building_room": "B1", "location": "Room 2", "notes": "Need it",
            "delivery_at": "2026-09-16T10:15",
            "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "1", "lines-MAX_NUM_FORMS": "1000",
            "lines-0-item": str(self.item.pk), "lines-0-quantity": qty, "lines-0-notes": "Medium",
        }

    def test_urgent_request_form_persists_checkbox_and_shows_badges(self):
        from .models import MaterialRequest

        data = self._post_data()
        data["urgent"] = "on"
        response = self.client.post(
            reverse("material_request_create"), data, HTTP_HOST="requests.rplwms.com"
        )
        self.assertEqual(response.status_code, 302)
        obj = MaterialRequest.objects.get()
        self.assertTrue(obj.urgent)
        detail = self.client.get(response.url, HTTP_HOST="requests.rplwms.com")
        self.assertContains(detail, 'class="badge urgent-badge"', html=False)
        board = self.client.get(reverse("material_request_board"), HTTP_HOST="requests.rplwms.com")
        self.assertContains(board, 'class="badge urgent-badge"', html=False)

    def test_group_is_provisioned_idempotently_with_exact_required_access(self):
        expected = {
            "add_materialrequest", "view_materialrequest", "change_materialrequest", "delete_materialrequest",
            "add_materialrequestline", "view_materialrequestline", "change_materialrequestline", "delete_materialrequestline",
            "view_inventoryitem", "access_material_request_portal",
        }
        actual = set(self.group.permissions.values_list("codename", flat=True))
        self.assertTrue(expected.issubset(actual), expected - actual)
        self.assertNotIn("add_pickticket", actual)
        self.assertNotIn("view_pickticket", actual)
        self.assertNotIn("print_pickticket", actual)

    def test_portal_create_edit_detail_and_manager_only_delete(self):
        from .models import MaterialRequest

        response = self.client.post(reverse("material_request_create"), self._post_data(), HTTP_HOST="requests.rplwms.com")
        self.assertEqual(response.status_code, 302)
        obj = MaterialRequest.objects.get()
        self.assertIn(str(obj.pk), response.url)
        detail = self.client.get(response.url, HTTP_HOST="requests.rplwms.com")
        self.assertContains(detail, obj.request_number)
        edit_url = reverse("material_request_edit", args=[obj.pk])
        edit_page = self.client.get(edit_url, HTTP_HOST="requests.rplwms.com")
        self.assertEqual(edit_page.status_code, 200)
        self.assertContains(edit_page, f"Edit {obj.request_number}")
        board = self.client.get(reverse("material_request_board"), HTTP_HOST="requests.rplwms.com")
        self.assertEqual(board.status_code, 200)
        self.assertContains(board, obj.request_number)
        edit_data = self._post_data(qty="4")
        edit_data.update({"lines-INITIAL_FORMS": "1", "lines-0-id": str(obj.lines.get().pk)})
        edited = self.client.post(edit_url, edit_data, HTTP_HOST="requests.rplwms.com")
        self.assertEqual(edited.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, 6)
        deleted = self.client.post(reverse("material_request_delete", args=[obj.pk]), HTTP_HOST="requests.rplwms.com")
        self.assertEqual(deleted.status_code, 403)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, 6)
        self.assertTrue(MaterialRequest.objects.filter(pk=obj.pk).exists())

    def _request_for(self, creator, name):
        from .services import create_material_request

        return create_material_request(
            creator=creator,
            requestor_name=name,
            building_room="B1",
            location="Room 2",
            notes="",
            lines=[{"item": self.item, "quantity": 1, "notes": ""}],
        )

    def test_portal_board_only_lists_requests_owned_by_authenticated_user(self):
        own = self._request_for(self.user, "Portal User")
        other = User.objects.create_user("other-owner", password="pw")
        foreign = self._request_for(other, "Other User")

        response = self.client.get(
            reverse("material_request_board"), HTTP_HOST="requests.rplwms.com"
        )

        self.assertContains(response, own.request_number)
        self.assertNotContains(response, foreign.request_number)

    def test_portal_archive_only_lists_requests_owned_by_authenticated_user(self):
        from django.utils import timezone

        own = self._request_for(self.user, "Portal User")
        other = User.objects.create_user("other-archive-owner", password="pw")
        foreign = self._request_for(other, "Other User")
        own.archived_at = timezone.now()
        own.save(update_fields=["archived_at"])
        foreign.archived_at = timezone.now()
        foreign.save(update_fields=["archived_at"])

        response = self.client.get(
            reverse("material_request_archive"), HTTP_HOST="requests.rplwms.com"
        )

        self.assertContains(response, own.request_number)
        self.assertNotContains(response, foreign.request_number)

    def test_portal_cannot_view_edit_or_delete_another_users_request(self):
        other = User.objects.create_user("other-private-owner", password="pw")
        foreign = self._request_for(other, "Other User")
        routes = (
            reverse("material_request_detail", args=[foreign.pk]),
            reverse("material_request_edit", args=[foreign.pk]),
            reverse("material_request_delete", args=[foreign.pk]),
        )

        for route in routes:
            with self.subTest(route=route, method="GET"):
                self.assertEqual(
                    self.client.get(route, HTTP_HOST="requests.rplwms.com").status_code,
                    404,
                )
        for route in routes[1:]:
            with self.subTest(route=route, method="POST"):
                self.assertEqual(
                    self.client.post(route, HTTP_HOST="requests.rplwms.com").status_code,
                    404,
                )

        self.assertTrue(type(foreign).objects.filter(pk=foreign.pk).exists())

    def test_requester_is_owner_scoped_even_on_wms_host(self):
        other = User.objects.create_user("other-wms-owner", password="pw")
        foreign = self._request_for(other, "Other User")
        own = self._request_for(self.user, "Portal User")
        self.user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="inventory",
            codename__in={
                "view_pickticket", "change_pickticket", "print_pickticket",
                "delete_pickticket", "view_inventorytransaction",
            },
        ))

        board = self.client.get(
            reverse("material_request_board"), HTTP_HOST="bbx.rplwms.com"
        )
        detail = self.client.get(
            reverse("material_request_detail", args=[foreign.pk]),
            HTTP_HOST="bbx.rplwms.com",
        )
        edit = self.client.get(
            reverse("material_request_edit", args=[foreign.pk]),
            HTTP_HOST="bbx.rplwms.com",
        )
        delete = self.client.post(
            reverse("material_request_delete", args=[foreign.pk]),
            HTTP_HOST="bbx.rplwms.com",
        )
        ticket_list = self.client.get(reverse("ticket_list"), HTTP_HOST="bbx.rplwms.com")
        ticket_detail = self.client.get(
            reverse("ticket_detail", args=[foreign.pick_ticket_id]),
            HTTP_HOST="bbx.rplwms.com",
        )
        ticket_print = self.client.get(
            reverse("ticket_print", args=[foreign.pick_ticket_id]),
            HTTP_HOST="bbx.rplwms.com",
        )
        ticket_pdf = self.client.get(
            reverse("ticket_print_pdf", args=[foreign.pick_ticket_id]),
            HTTP_HOST="bbx.rplwms.com",
        )
        ticket_status = self.client.post(
            reverse("ticket_status_update", args=[foreign.pick_ticket_id]),
            {"status": PickTicket.Status.PICKED}, HTTP_HOST="bbx.rplwms.com",
        )
        ticket_export = self.client.get(reverse("export_tickets_csv"), HTTP_HOST="bbx.rplwms.com")
        transaction_history = self.client.get(reverse("transaction_history"), HTTP_HOST="bbx.rplwms.com")
        transaction_export = self.client.get(reverse("export_transactions_csv"), HTTP_HOST="bbx.rplwms.com")

        self.assertNotContains(board, foreign.request_number)
        self.assertEqual(detail.status_code, 404)
        self.assertEqual(edit.status_code, 404)
        self.assertEqual(delete.status_code, 404)
        self.assertEqual(ticket_list.status_code, 200)
        self.assertContains(ticket_list, own.pick_ticket.ticket_number)
        self.assertNotContains(ticket_list, foreign.pick_ticket.ticket_number)
        self.assertEqual(ticket_detail.status_code, 404)
        self.assertEqual(ticket_print.status_code, 404)
        self.assertEqual(ticket_pdf.status_code, 404)
        self.assertEqual(ticket_status.status_code, 404)
        self.assertContains(ticket_export, own.pick_ticket.ticket_number)
        self.assertNotContains(ticket_export, foreign.pick_ticket.ticket_number)
        self.assertContains(transaction_history, own.pick_ticket.ticket_number)
        self.assertNotContains(transaction_history, foreign.pick_ticket.ticket_number)
        self.assertContains(transaction_export, own.pick_ticket.ticket_number)
        self.assertNotContains(transaction_export, foreign.pick_ticket.ticket_number)
        foreign.pick_ticket.refresh_from_db()
        self.assertEqual(foreign.pick_ticket.status, PickTicket.Status.OPEN)
        self.assertTrue(type(foreign).objects.filter(pk=foreign.pk).exists())

    def test_wms_dashboard_top_picked_is_owner_scoped(self):
        from .services import create_material_request, update_pick_ticket_status

        other = User.objects.create_user("dashboard-foreign-owner", password="pw")
        own_item = InventoryItem.objects.create(
            part_number="OWN-DASH-ITEM", name="Owner dashboard item", quantity_on_hand=20
        )
        foreign_item = InventoryItem.objects.create(
            part_number="FOREIGN-DASH-ITEM", name="Foreign dashboard item", quantity_on_hand=20
        )
        own = create_material_request(
            creator=self.user, requestor_name="Portal User", building_room="B1",
            location="Room 2", notes="", lines=[{"item": own_item, "quantity": 2, "notes": ""}],
        )
        foreign = create_material_request(
            creator=other, requestor_name="Other User", building_room="B2",
            location="Room 3", notes="", lines=[{"item": foreign_item, "quantity": 9, "notes": ""}],
        )
        update_pick_ticket_status(own.pick_ticket, PickTicket.Status.PICKED, actor=self.user)
        update_pick_ticket_status(foreign.pick_ticket, PickTicket.Status.PICKED, actor=other)

        response = self.client.get(reverse("dashboard"), HTTP_HOST="bbx.rplwms.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, own_item.part_number)
        self.assertNotContains(response, foreign_item.part_number)

    def test_explicit_warehouse_permission_retains_warehouse_wide_access(self):
        warehouse = User.objects.create_user("warehouse", password="pw")
        warehouse.user_permissions.add(
            *self.group.permissions.all(),
            Permission.objects.get(
                content_type__app_label="inventory", codename="view_all_materialrequests"
            ),
        )
        other = User.objects.create_user("warehouse-other-owner", password="pw")
        foreign = self._request_for(other, "Other User")
        self.client.force_login(warehouse)

        board = self.client.get(
            reverse("material_request_board"), HTTP_HOST="bbx.rplwms.com"
        )
        detail = self.client.get(
            reverse("material_request_detail", args=[foreign.pk]),
            HTTP_HOST="bbx.rplwms.com",
        )
        edit = self.client.get(
            reverse("material_request_edit", args=[foreign.pk]),
            HTTP_HOST="bbx.rplwms.com",
        )
        delete = self.client.get(
            reverse("material_request_delete", args=[foreign.pk]),
            HTTP_HOST="bbx.rplwms.com",
        )

        self.assertContains(board, foreign.request_number)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(edit.status_code, 200)
        self.assertEqual(delete.status_code, 403)

    def test_admin_and_linked_ticket_surfaces_are_owner_scoped_without_global_permission(self):
        own = self._request_for(self.user, "Portal User")
        foreign = self._request_for(
            User.objects.create_user("admin-foreign-owner", password="pw"),
            "Foreign Admin Request",
        )
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="inventory",
            codename__in={
                "view_materialrequestevent", "view_pickticket", "view_inventorytransaction"
            },
        ))
        request_list = self.client.get(
            reverse("admin:inventory_materialrequest_changelist"), HTTP_HOST="bbx.rplwms.com"
        )
        ticket_list = self.client.get(
            reverse("admin:inventory_pickticket_changelist"), HTTP_HOST="bbx.rplwms.com"
        )
        event_list = self.client.get(
            reverse("admin:inventory_materialrequestevent_changelist"), HTTP_HOST="bbx.rplwms.com"
        )
        foreign_change = self.client.get(
            reverse("admin:inventory_materialrequest_change", args=[foreign.pk]),
            HTTP_HOST="bbx.rplwms.com",
        )
        for response in (request_list, event_list):
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, own.request_number)
            self.assertNotContains(response, foreign.request_number)
        self.assertEqual(ticket_list.status_code, 200)
        self.assertContains(ticket_list, own.pick_ticket.ticket_number)
        self.assertNotContains(ticket_list, foreign.pick_ticket.ticket_number)
        self.assertNotEqual(foreign_change.status_code, 200)

    def test_request_host_root_is_board_and_exposes_read_only_inventory(self):
        response = self.client.get("/", HTTP_HOST="requests.rplwms.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Material Requests")
        inventory = self.client.get("/inventory/", HTTP_HOST="requests.rplwms.com")
        self.assertEqual(inventory.status_code, 200)
        self.assertContains(inventory, self.item.part_number)
        self.assertContains(inventory, "Inventory")
        self.assertNotContains(inventory, "Add Item")
        self.assertNotContains(inventory, "Adjust Inventory")
        detail = self.client.get(f"/inventory/{self.item.pk}/", HTTP_HOST="requests.rplwms.com")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, self.item.part_number)
        self.assertNotContains(detail, "Edit Item")
        self.assertNotContains(detail, "Adjust Inventory")
        self.assertEqual(self.client.get("/inventory/", HTTP_HOST="requests.rplwms.com.").status_code, 200)
        self.assertEqual(self.client.get("/admin/", HTTP_HOST="requests.rplwms.com").status_code, 404)

    def test_request_portal_inventory_requires_inventory_view_permission(self):
        limited = User.objects.create_user("portal-no-inventory", password="pw")
        limited.user_permissions.add(Permission.objects.get(
            content_type__app_label="inventory", codename="access_material_request_portal"
        ))
        client = Client()
        client.force_login(limited)
        response = client.get("/inventory/", HTTP_HOST="requests.rplwms.com")
        self.assertEqual(response.status_code, 302)

    def test_request_portal_inventory_is_read_only_even_for_superuser(self):
        admin = User.objects.create_superuser("portal-admin", password="pw")
        client = Client()
        client.force_login(admin)
        inventory = client.get("/inventory/", HTTP_HOST="requests.rplwms.com")
        self.assertEqual(inventory.status_code, 200)
        self.assertNotContains(inventory, "Add Item")
        self.assertNotContains(inventory, "Import Spreadsheet")
        detail = client.get(f"/inventory/{self.item.pk}/", HTTP_HOST="requests.rplwms.com")
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, "Adjust Inventory")
        self.assertNotContains(detail, "Edit Item")

    def test_request_host_requires_explicit_portal_access_permission(self):
        limited = User.objects.create_user("limited", password="pw")
        limited.user_permissions.add(Permission.objects.get(codename="view_materialrequest"))
        client = Client()
        client.force_login(limited)
        self.assertEqual(client.get("/", HTTP_HOST="requests.rplwms.com").status_code, 403)
        self.assertEqual(
            client.get(reverse("material_request_board"), HTTP_HOST="bbx.rplwms.com").status_code,
            200,
        )

    def test_wms_board_available_but_arbitrary_ticket_creation_forbidden(self):
        self.assertEqual(self.client.get(reverse("material_request_board"), HTTP_HOST="bbx.rplwms.com").status_code, 200)
        response = self.client.get(reverse("ticket_create"), HTTP_HOST="bbx.rplwms.com")
        self.assertEqual(response.status_code, 302)

    def test_item_search_requires_login_and_inventory_permission(self):
        anonymous = Client().get(reverse("item_search_api"), {"q": "PPE"}, HTTP_HOST="bbx.rplwms.com")
        self.assertEqual(anonymous.status_code, 302)
        no_perm = User.objects.create_user("none", password="pw")
        denied = Client(); denied.force_login(no_perm)
        self.assertEqual(denied.get(reverse("item_search_api"), {"q": "PPE"}, HTTP_HOST="bbx.rplwms.com").status_code, 302)
        self.assertEqual(self.client.get(reverse("item_search_api"), {"q": "PPE"}, HTTP_HOST="bbx.rplwms.com").status_code, 200)

    def test_status_update_requires_change_pickticket(self):
        from .services import create_material_request
        obj = create_material_request(creator=self.user, requestor_name="U", building_room="B", location="L", notes="", lines=[{"item": self.item, "quantity": 1, "notes": ""}])
        response = self.client.post(reverse("ticket_status_update", args=[obj.pick_ticket_id]), {"status": "PICKED"}, HTTP_HOST="bbx.rplwms.com")
        self.assertEqual(response.status_code, 302)
        obj.pick_ticket.refresh_from_db()
        self.assertEqual(obj.pick_ticket.status, PickTicket.Status.OPEN)

    def _ready_request(self):
        from .services import create_material_request, update_pick_ticket_status

        obj = create_material_request(
            creator=self.user, requestor_name="Portal User", building_room="B1", location="Room 2",
            notes="", lines=[{"item": self.item, "quantity": 1, "notes": ""}],
            delivery_at=datetime(2026, 9, 16, 10, 15, tzinfo=datetime_timezone.utc),
        )
        update_pick_ticket_status(obj.pick_ticket, PickTicket.Status.RECEIVED, actor=self.user)
        obj.refresh_from_db()
        return obj

    def test_ready_request_detail_has_web_response_controls_for_creator(self):
        obj = self._ready_request()
        response = self.client.get(
            reverse("material_request_detail", args=[obj.pk]), HTTP_HOST="requests.rplwms.com"
        )
        self.assertContains(response, "Confirm ready for delivery")
        self.assertContains(response, "Not ready")
        self.assertContains(response, "Reschedule delivery")

    def test_creator_can_confirm_ready_from_web_without_closing_ticket(self):
        obj = self._ready_request()
        response = self.client.post(
            reverse("material_request_delivery_response", args=[obj.pk]),
            {"response": "ready"}, HTTP_HOST="requests.rplwms.com",
        )
        self.assertEqual(response.status_code, 302)
        obj.refresh_from_db(); obj.pick_ticket.refresh_from_db()
        self.assertIsNotNone(obj.delivery_acceptance_confirmed_at)
        self.assertEqual(obj.pick_ticket.status, PickTicket.Status.RECEIVED)

    def test_creator_can_mark_not_ready_and_optionally_reschedule(self):
        obj = self._ready_request()
        response = self.client.post(
            reverse("material_request_delivery_response", args=[obj.pk]),
            {
                "response": "not_ready", "delivery_at_0": "2026-09-17",
                "delivery_at_1": "14:30", "note": "Available tomorrow afternoon",
            }, HTTP_HOST="requests.rplwms.com",
        )
        self.assertEqual(response.status_code, 302)
        obj.refresh_from_db(); obj.pick_ticket.refresh_from_db()
        self.assertIsNotNone(obj.delivery_not_ready_at)
        self.assertEqual(obj.delivery_not_ready_by, self.user)
        self.assertEqual(obj.delivery_response_note, "Available tomorrow afternoon")
        self.assertEqual(obj.delivery_at.astimezone(datetime_timezone.utc).strftime("%Y-%m-%d %H:%M"), "2026-09-17 14:30")
        self.assertEqual(obj.pick_ticket.status, PickTicket.Status.RECEIVED)
        self.assertEqual(obj.events.order_by("-id").first().event_type, MaterialRequestEvent.EventType.DELIVERY_NOT_READY)
        board = self.client.get(reverse("material_request_board"), HTTP_HOST="requests.rplwms.com")
        self.assertEqual(board.status_code, 200)
        self.assertContains(board, obj.request_number)

    def test_other_user_cannot_submit_delivery_response(self):
        obj = self._ready_request()
        other = User.objects.create_user("other-portal", password="pw")
        other.groups.add(self.group)
        client = Client(); client.force_login(other)
        response = client.post(
            reverse("material_request_delivery_response", args=[obj.pk]),
            {"response": "ready"}, HTTP_HOST="requests.rplwms.com",
        )
        self.assertEqual(response.status_code, 404)
        obj.refresh_from_db()
        self.assertIsNone(obj.delivery_acceptance_confirmed_at)

    def test_response_rejects_wrong_host_and_invalid_reschedule_time(self):
        obj = self._ready_request()
        wrong_host = self.client.post(
            reverse("material_request_delivery_response", args=[obj.pk]),
            {"response": "ready"}, HTTP_HOST="bbx.rplwms.com",
        )
        self.assertEqual(wrong_host.status_code, 403)
        invalid = self.client.post(
            reverse("material_request_delivery_response", args=[obj.pk]),
            {"response": "not_ready", "delivery_at_0": "2026-09-17", "delivery_at_1": "14:07"},
            HTTP_HOST="requests.rplwms.com",
        )
        self.assertEqual(invalid.status_code, 302)
        obj.refresh_from_db()
        self.assertIsNone(obj.delivery_not_ready_at)


class MaterialRequestNotificationTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user("wms1", password="pw")
        self.user2 = User.objects.create_user("wms2", password="pw")
        view_permission = Permission.objects.get(codename="view_materialrequest")
        global_permission = Permission.objects.get(codename="view_all_materialrequests")
        self.user1.user_permissions.add(view_permission, global_permission)
        self.user2.user_permissions.add(view_permission, global_permission)
        self.item = InventoryItem.objects.create(part_number="X", name="Mask", quantity_on_hand=10)
        self.client1 = Client(); self.client1.force_login(self.user1)
        self.client2 = Client(); self.client2.force_login(self.user2)

    def test_initial_cursor_suppresses_history_then_two_clients_see_new_event_once(self):
        from .services import create_material_request
        from .models import MaterialRequestEvent

        with self.captureOnCommitCallbacks(execute=True):
            create_material_request(creator=self.user1, requestor_name="First", building_room="B", location="L", notes="", lines=[{"item": self.item, "quantity": 1, "notes": ""}])
        initial = self.client1.get(reverse("material_request_events"), HTTP_HOST="bbx.rplwms.com")
        self.assertEqual(initial.json()["events"], [])
        cursor = initial.json()["cursor"]
        with self.captureOnCommitCallbacks(execute=True):
            second = create_material_request(creator=self.user2, requestor_name="Second", building_room="B2", location="L2", notes="", lines=[{"item": self.item, "quantity": 1, "notes": ""}])
        for client in (self.client1, self.client2):
            seen = client.get(reverse("material_request_events"), {"cursor": cursor}, HTTP_HOST="bbx.rplwms.com")
            self.assertEqual([event["request_number"] for event in seen.json()["events"]], [second.request_number])
            advanced = seen.json()["cursor"]
            repeat = client.get(reverse("material_request_events"), {"cursor": advanced}, HTTP_HOST="bbx.rplwms.com")
            self.assertEqual(repeat.json()["events"], [])
            self.assertEqual(repeat["Cache-Control"], "no-store")
        self.assertEqual(MaterialRequestEvent.objects.count(), 2)

    def test_event_feed_skips_orphaned_historical_events_without_crashing(self):
        orphan = MaterialRequestEvent.objects.create(
            event_type=MaterialRequestEvent.EventType.UPDATED,
            change_summary="Historical request no longer exists",
        )

        response = self.client1.get(
            reverse("material_request_events"),
            {"cursor": orphan.pk - 1},
            HTTP_HOST="bbx.rplwms.com",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["events"], [])
        self.assertEqual(response.json()["cursor"], orphan.pk)

    def test_deleted_notification_targets_return_warehouse_to_work_queue(self):
        from .services import create_material_request, delete_material_request

        self.user1.user_permissions.add(
            Permission.objects.get(codename="view_pickticket")
        )
        obj = create_material_request(
            creator=self.user1,
            requestor_name="Rae",
            building_room="B",
            location="L",
            notes="",
            lines=[{"item": self.item, "quantity": 1, "notes": ""}],
        )
        request_pk = obj.pk
        ticket_pk = obj.pick_ticket_id
        delete_material_request(obj)

        for route_name, pk in (
            ("material_request_detail", request_pk),
            ("ticket_detail", ticket_pk),
        ):
            with self.subTest(route_name=route_name):
                response = self.client1.get(
                    reverse(route_name, args=[pk]),
                    HTTP_HOST="bbx.rplwms.com",
                )
                self.assertRedirects(
                    response,
                    reverse("material_request_board"),
                    fetch_redirect_response=False,
                )

    def test_events_are_wms_host_only_and_portal_does_not_load_poller(self):
        response = self.client1.get(reverse("material_request_events"), HTTP_HOST="requests.rplwms.com")
        self.assertEqual(response.status_code, 404)
        self.user1.groups.add(Group.objects.get(name="Material Requests"))
        portal = self.client1.get("/", HTTP_HOST="requests.rplwms.com")
        self.assertNotContains(portal, "material-request-events")
        wms = self.client1.get("/", HTTP_HOST="bbx.rplwms.com")
        self.assertContains(wms, "material-request-events")

    def test_event_feed_has_notification_center_copy(self):
        self.user1.user_permissions.add(Permission.objects.get(codename="view_materialrequest"))
        from .services import create_material_request

        initial = self.client1.get(reverse("material_request_events"), HTTP_HOST="bbx.rplwms.com")
        obj = create_material_request(
            creator=self.user1, requestor_name="Rae", building_room="B", location="L", notes="",
            lines=[{"item": self.item, "quantity": 1, "notes": ""}],
        )
        payload = self.client1.get(
            reverse("material_request_events"), {"cursor": initial.json()["cursor"]},
            HTTP_HOST="bbx.rplwms.com",
        ).json()["events"][0]
        self.assertEqual(payload["event_type"], "created")
        self.assertIn("New material request", payload["title"])
        self.assertIn(obj.request_number, payload["body"])

    def test_urgent_event_feed_is_flagged_and_requires_attention(self):
        from .services import create_material_request

        initial = self.client1.get(reverse("material_request_events"), HTTP_HOST="bbx.rplwms.com")
        obj = create_material_request(
            creator=self.user1, requestor_name="Rae", building_room="B", location="L", notes="",
            urgent=True, lines=[{"item": self.item, "quantity": 1, "notes": ""}],
        )
        payload = self.client1.get(
            reverse("material_request_events"), {"cursor": initial.json()["cursor"]},
            HTTP_HOST="bbx.rplwms.com",
        ).json()["events"][0]
        self.assertEqual(payload["title"], f"🚨 URGENT: New material request {obj.request_number}")
        self.assertTrue(payload["urgent"])
        self.assertTrue(payload["require_interaction"])


class MaterialRequestDesignTests(TestCase):
    def test_base_has_central_assets_theme_accessibility_and_print_rules(self):
        user = User.objects.create_superuser("admin", password="pw")
        self.client.force_login(user)
        response = self.client.get("/", HTTP_HOST="bbx.rplwms.com")
        self.assertContains(response, "inventory/css/app.css")
        self.assertContains(response, "inventory/js/app.js")
        self.assertContains(response, 'id="theme-toggle"', html=False)
        self.assertContains(response, "aria-label")
        self.assertContains(response, "app-mark-symbol")
        self.assertContains(response, "WAREHOUSE")
        self.assertContains(response, 'data-notification-toggle', html=False)
        self.assertContains(response, 'data-notification-count', html=False)
        self.assertContains(response, 'data-notification-panel', html=False)
