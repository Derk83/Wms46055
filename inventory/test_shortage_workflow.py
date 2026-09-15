from unittest.mock import patch

from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from .models import (
    BackorderFulfillment,
    InventoryItem,
    InventoryTransaction,
    MaterialBackorder,
    MaterialRequest,
    MaterialRequestEvent,
    PickTicket,
    PickTicketLine,
    ProcurementRequisition,
)
from .services import (
    create_material_request,
    delete_material_request,
    delete_material_request_cascade,
    fulfill_material_backorder,
    update_material_request,
    update_procurement_requisition,
)


class MaterialShortageWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("warehouse", password="test-only")
        self.item = InventoryItem.objects.create(
            part_number="SHORT-1", name="Short item", quantity_on_hand=40, unit="ea"
        )

    def create_request(self, quantity=50, action=""):
        return create_material_request(
            creator=self.user,
            requestor_name="Requester",
            requestor_email="requester@example.com",
            building_room="100",
            location="Dock",
            notes="",
            lines=[{
                "item": self.item,
                "quantity": quantity,
                "notes": "",
                "shortage_action": action,
            }],
        )

    def test_short_request_requires_explicit_decision_and_rolls_back(self):
        with self.assertRaisesMessage(ValidationError, "Choose what should happen"):
            self.create_request()
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, 40)
        self.assertFalse(MaterialRequest.objects.exists())
        self.assertFalse(PickTicket.objects.exists())

    def test_available_only_allocates_stock_without_backorder(self):
        request = self.create_request(action="available_only")
        line = request.lines.get()
        self.item.refresh_from_db()
        self.assertEqual(line.quantity, 50)
        self.assertEqual(line.allocated_quantity, 40)
        self.assertEqual(line.shortage_quantity, 10)
        self.assertEqual(request.pick_ticket.lines.get().quantity, 40)
        self.assertEqual(self.item.quantity_on_hand, 0)
        self.assertFalse(MaterialBackorder.objects.exists())

    def test_backorder_decision_creates_linked_demand_without_negative_stock(self):
        request = self.create_request(action="backorder")
        line = request.lines.get()
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, 0)
        self.assertEqual(line.backorder.quantity, 10)
        self.assertEqual(line.backorder.fulfilled_quantity, 0)
        self.assertTrue(line.backorder.backorder_number.startswith("BO-"))
        self.assertFalse(ProcurementRequisition.objects.exists())

    def test_procurement_decision_creates_requisition_linked_to_backorder(self):
        request = self.create_request(action="procurement")
        line = request.lines.get()
        requisition = line.backorder.procurement_requisition
        self.assertEqual(line.backorder.quantity, 10)
        self.assertEqual(requisition.ordered_quantity, 10)
        self.assertEqual(requisition.created_by, self.user)
        self.assertTrue(requisition.requisition_number.startswith("PRQ-"))

    def test_zero_stock_can_backorder_full_quantity(self):
        self.item.quantity_on_hand = 0
        self.item.save(update_fields=["quantity_on_hand"])
        request = self.create_request(quantity=12, action="backorder")
        line = request.lines.get()
        self.assertEqual(line.allocated_quantity, 0)
        self.assertEqual(line.backorder.quantity, 12)
        self.assertFalse(request.pick_ticket.lines.exists())

    def test_pick_ledger_refuses_negative_stock_and_rolls_back_line(self):
        ticket = PickTicket.objects.create(created_by=self.user)
        with self.assertRaisesMessage(ValidationError, "Insufficient stock"):
            PickTicketLine.objects.create(ticket=ticket, item=self.item, quantity=41)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, 40)
        self.assertFalse(ticket.lines.exists())
        self.assertFalse(InventoryTransaction.objects.exists())

    def test_backorder_can_be_partially_then_fully_fulfilled(self):
        request = self.create_request(action="backorder")
        backorder = request.lines.get().backorder
        self.item.adjust_quantity(
            10, InventoryTransaction.TransactionType.RECEIPT, user=self.user, notes="Test receipt"
        )
        backorder, first_ticket = fulfill_material_backorder(
            backorder, quantity=6, actor=self.user
        )
        self.assertEqual(backorder.fulfilled_quantity, 6)
        self.assertEqual(backorder.status, MaterialBackorder.Status.PARTIAL)
        self.assertEqual(first_ticket.lines.get().quantity, 6)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, 4)

        backorder, second_ticket = fulfill_material_backorder(
            backorder, quantity=4, actor=self.user
        )
        self.assertEqual(backorder.fulfilled_quantity, 10)
        self.assertEqual(backorder.status, MaterialBackorder.Status.FULFILLED)
        self.assertIsNotNone(backorder.fulfilled_at)
        self.assertNotEqual(first_ticket.pk, second_ticket.pk)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, 0)

    def test_fulfillment_rechecks_stock_and_remaining_demand(self):
        request = self.create_request(action="backorder")
        backorder = request.lines.get().backorder
        with self.assertRaisesMessage(ValidationError, "available"):
            fulfill_material_backorder(backorder, quantity=1, actor=self.user)
        self.assertFalse(backorder.fulfillments.exists())

    def test_edit_restores_old_allocation_then_recalculates_shortage(self):
        request = self.create_request(quantity=30)
        request = update_material_request(
            request,
            requestor_name=request.requestor_name,
            requestor_email=request.requestor_email,
            building_room=request.building_room,
            location=request.location,
            delivery_at=request.delivery_at,
            urgent=request.urgent,
            notes=request.notes,
            actor=self.user,
            lines=[{
                "item": self.item,
                "quantity": 50,
                "notes": "changed",
                "shortage_action": "backorder",
            }],
        )
        line = request.lines.get()
        self.item.refresh_from_db()
        self.assertEqual(line.allocated_quantity, 40)
        self.assertEqual(line.backorder.quantity, 10)
        self.assertEqual(request.pick_ticket.lines.get().quantity, 40)
        self.assertEqual(self.item.quantity_on_hand, 0)

    def test_request_edit_is_blocked_after_backorder_fulfillment(self):
        request = self.create_request(action="backorder")
        backorder = request.lines.get().backorder
        self.item.adjust_quantity(
            1, InventoryTransaction.TransactionType.RECEIPT, user=self.user, notes="Test receipt"
        )
        fulfill_material_backorder(backorder, quantity=1, actor=self.user)
        with self.assertRaisesMessage(ValidationError, "cannot be edited"):
            update_material_request(
                request,
                requestor_name=request.requestor_name,
                requestor_email=request.requestor_email,
                building_room=request.building_room,
                location=request.location,
                delivery_at=request.delivery_at,
                urgent=request.urgent,
                notes=request.notes,
                actor=self.user,
                lines=[{
                    "item": self.item,
                    "quantity": 50,
                    "notes": "",
                    "shortage_action": "backorder",
                }],
            )

    def test_procurement_request_is_immutable_even_while_requisition_is_new(self):
        request = self.create_request(action="procurement")
        with self.assertRaisesMessage(ValidationError, "cannot be edited"):
            update_material_request(
                request,
                requestor_name=request.requestor_name,
                requestor_email=request.requestor_email,
                building_room=request.building_room,
                location=request.location,
                delivery_at=request.delivery_at,
                urgent=request.urgent,
                notes="changed",
                actor=self.user,
                lines=[{
                    "item": self.item,
                    "quantity": 50,
                    "notes": "",
                    "shortage_action": "procurement",
                }],
            )
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            delete_material_request(request, actor=self.user)
        self.assertTrue(MaterialRequest.objects.filter(pk=request.pk).exists())
        self.assertTrue(ProcurementRequisition.objects.filter(backorder__line__material_request=request).exists())

    def test_manager_cascade_rolls_back_complete_graph_and_stock_on_failure(self):
        request = self.create_request(action="procurement")
        backorder = request.lines.get().backorder
        self.item.adjust_quantity(
            10,
            InventoryTransaction.TransactionType.RECEIPT,
            user=self.user,
            notes="Rollback test receipt",
        )
        _, supplemental_ticket = fulfill_material_backorder(
            backorder, quantity=10, actor=self.user
        )
        main_ticket_id = request.pick_ticket_id
        request_id = request.pk
        backorder_id = backorder.pk
        requisition_id = backorder.procurement_requisition.pk
        supplemental_ticket_id = supplemental_ticket.pk
        baseline_deleted_events = MaterialRequestEvent.objects.filter(
            event_type=MaterialRequestEvent.EventType.DELETED
        ).count()
        original_delete = PickTicket.delete

        def fail_on_main_ticket(ticket, *args, **kwargs):
            if ticket.pk == main_ticket_id:
                raise RuntimeError("injected cascade failure")
            return original_delete(ticket, *args, **kwargs)

        with patch.object(PickTicket, "delete", new=fail_on_main_ticket):
            with self.assertRaisesMessage(RuntimeError, "injected cascade failure"):
                delete_material_request_cascade(request, actor=self.user)

        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, 0)
        self.assertTrue(MaterialRequest.objects.filter(pk=request_id).exists())
        self.assertTrue(MaterialBackorder.objects.filter(pk=backorder_id).exists())
        self.assertTrue(ProcurementRequisition.objects.filter(pk=requisition_id).exists())
        self.assertTrue(BackorderFulfillment.objects.filter(pick_ticket_id=supplemental_ticket_id).exists())
        self.assertEqual(
            PickTicket.objects.filter(pk__in=[main_ticket_id, supplemental_ticket_id]).count(),
            2,
        )
        self.assertEqual(
            MaterialRequestEvent.objects.filter(
                event_type=MaterialRequestEvent.EventType.DELETED
            ).count(),
            baseline_deleted_events,
        )

    def test_procurement_received_cannot_exceed_ordered(self):
        requisition = self.create_request(action="procurement").lines.get().backorder.procurement_requisition
        with self.assertRaisesMessage(ValidationError, "cannot exceed"):
            update_procurement_requisition(
                requisition,
                actor=self.user,
                ordered_quantity=10,
                received_quantity=11,
            )
        requisition.refresh_from_db()
        self.assertEqual(requisition.received_quantity, 0)


class MaterialShortageViewsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin", "admin@example.com", "test-only")
        self.client = Client()
        self.client.force_login(self.admin)
        self.item = InventoryItem.objects.create(
            part_number="SHORT-VIEW", name="Short view item", quantity_on_hand=1
        )
        self.request = create_material_request(
            creator=self.admin,
            requestor_name="Requester",
            requestor_email="requester@example.com",
            building_room="100",
            location="Dock",
            notes="",
            lines=[{
                "item": self.item,
                "quantity": 2,
                "notes": "",
                "shortage_action": "procurement",
            }],
        )

    def test_backorder_and_procurement_pages_render(self):
        backorder_response = self.client.get(reverse("backorder_list"))
        procurement_response = self.client.get(reverse("procurement_requisition_list"))
        detail_response = self.client.get(
            reverse(
                "procurement_requisition_detail",
                args=[self.request.lines.get().backorder.procurement_requisition.pk],
            )
        )
        self.assertEqual(backorder_response.status_code, 200)
        self.assertContains(backorder_response, "BO-")
        self.assertEqual(procurement_response.status_code, 200)
        self.assertContains(procurement_response, "PRQ-")
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "FB Part #")

    def test_request_detail_shows_allocation_and_shortage(self):
        response = self.client.get(reverse("material_request_detail", args=[self.request.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Allocated now")
        self.assertContains(response, "Ask Procurement to purchase the rest")

    def test_manager_delete_dialog_lists_linked_records_and_requires_checkbox(self):
        backorder = self.request.lines.get().backorder
        requisition = backorder.procurement_requisition
        response = self.client.get(
            reverse("material_request_delete", args=[self.request.pk]),
            HTTP_HOST="bbx.rplwms.com",
        )
        self.assertEqual(response.status_code, 200)
        for number in (
            self.request.pick_ticket.ticket_number,
            backorder.backorder_number,
            requisition.requisition_number,
        ):
            self.assertContains(response, number)
        self.assertContains(response, 'name="confirm_linked_deletion"')
        self.assertContains(response, 'target="_blank"', count=3)
        portal_response = self.client.get(
            reverse("material_request_delete", args=[self.request.pk]),
            HTTP_HOST="requests.rplwms.com",
        )
        self.assertEqual(portal_response.status_code, 200)
        self.assertContains(portal_response, "https://bbx.rplwms.com/tickets/")
        self.assertContains(portal_response, "https://bbx.rplwms.com/procurement/")

        response = self.client.post(
            reverse("material_request_delete", args=[self.request.pk]),
            HTTP_HOST="bbx.rplwms.com",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Check the confirmation box")
        self.assertTrue(MaterialRequest.objects.filter(pk=self.request.pk).exists())
        self.assertTrue(PickTicket.objects.filter(pk=self.request.pick_ticket_id).exists())

    def test_linked_pick_ticket_delete_routes_to_manager_cascade_dialog(self):
        response = self.client.get(
            reverse("ticket_delete", args=[self.request.pick_ticket_id]),
            HTTP_HOST="bbx.rplwms.com",
        )
        self.assertRedirects(
            response,
            reverse("material_request_delete", args=[self.request.pk]),
            fetch_redirect_response=False,
        )

    def test_manager_confirmed_delete_removes_entire_graph_and_restores_stock(self):
        backorder = self.request.lines.get().backorder
        requisition_id = backorder.procurement_requisition.pk
        self.item.adjust_quantity(
            1,
            InventoryTransaction.TransactionType.RECEIPT,
            user=self.admin,
            notes="Cascade test receipt",
        )
        _, supplemental_ticket = fulfill_material_backorder(
            backorder, quantity=1, actor=self.admin
        )
        main_ticket_id = self.request.pick_ticket_id
        supplemental_ticket_id = supplemental_ticket.pk
        request_id = self.request.pk
        backorder_id = backorder.pk

        unrelated_item = InventoryItem.objects.create(
            part_number="UNRELATED", name="Unrelated", quantity_on_hand=3
        )
        unrelated_request = create_material_request(
            creator=self.admin,
            requestor_name="Other requester",
            requestor_email="other@example.com",
            building_room="200",
            location="Office",
            notes="",
            lines=[{
                "item": unrelated_item,
                "quantity": 1,
                "notes": "",
                "shortage_action": "",
            }],
        )

        response = self.client.get(
            reverse("material_request_delete", args=[request_id]),
            HTTP_HOST="bbx.rplwms.com",
        )
        self.assertContains(response, supplemental_ticket.ticket_number)
        self.assertContains(response, 'target="_blank"', count=4)
        response = self.client.post(
            reverse("material_request_delete", args=[request_id]),
            {"confirm_linked_deletion": "yes"},
            HTTP_HOST="bbx.rplwms.com",
        )
        self.assertRedirects(
            response,
            reverse("material_request_board"),
            fetch_redirect_response=False,
        )

        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, 2)
        self.assertFalse(MaterialRequest.objects.filter(pk=request_id).exists())
        self.assertFalse(MaterialBackorder.objects.filter(pk=backorder_id).exists())
        self.assertFalse(ProcurementRequisition.objects.filter(pk=requisition_id).exists())
        self.assertFalse(BackorderFulfillment.objects.filter(pick_ticket_id=supplemental_ticket_id).exists())
        self.assertFalse(PickTicket.objects.filter(pk__in=[main_ticket_id, supplemental_ticket_id]).exists())
        self.assertTrue(MaterialRequest.objects.filter(pk=unrelated_request.pk).exists())
        deletion_event = MaterialRequestEvent.objects.get(
            event_type=MaterialRequestEvent.EventType.DELETED,
            request_number_snapshot=self.request.request_number,
        )
        self.assertIn("Manager cascade deleted linked records", deletion_event.change_summary)

    def test_non_manager_with_delete_permissions_cannot_use_cascade(self):
        non_manager = User.objects.create_user("delete-operator")
        permission_names = [
            "delete_materialrequest",
            "delete_materialrequestline",
            "delete_pickticket",
            "delete_pickticketline",
            "delete_materialbackorder",
            "delete_procurementrequisition",
            "delete_backorderfulfillment",
        ]
        non_manager.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="inventory",
                codename__in=permission_names,
            )
        )
        self.request.creator = non_manager
        self.request.save(update_fields=["creator"])
        self.client.force_login(non_manager)
        response = self.client.post(
            reverse("material_request_delete", args=[self.request.pk]),
            {"confirm_linked_deletion": "yes"},
            HTTP_HOST="bbx.rplwms.com",
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(MaterialRequest.objects.filter(pk=self.request.pk).exists())

    def test_manager_roles_receive_complete_cascade_permissions(self):
        manager = User.objects.create_user("logistics-manager")
        manager.groups.add(Group.objects.get(name="Logistics Manager"))
        self.assertTrue(manager.has_perms((
            "inventory.delete_materialrequest",
            "inventory.delete_materialrequestline",
            "inventory.delete_pickticket",
            "inventory.delete_pickticketline",
            "inventory.delete_materialbackorder",
            "inventory.delete_procurementrequisition",
            "inventory.delete_backorderfulfillment",
        )))

    def test_shortage_copy_and_supply_links_are_clear_and_prominent(self):
        form_response = self.client.get(reverse("material_request_create"))
        self.assertEqual(form_response.status_code, 200)
        for copy in (
            "Not enough stock — choose what happens next",
            "Use available stock and cancel the rest",
            "Request the rest when available",
            "Ask Procurement to purchase the rest",
        ):
            self.assertContains(form_response, copy)

        dashboard_response = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard_response.status_code, 200)
        html = dashboard_response.content.decode()
        desktop_nav = html.split('<nav class="desktop-priority-nav"', 1)[1].split("</nav>", 1)[0]
        primary_links, more_menu = desktop_nav.split('<div class="header-more">', 1)
        self.assertIn("Backorders", primary_links)
        self.assertIn("Procurement", primary_links)
        self.assertNotIn("Backorders", more_menu)
        self.assertNotIn("Procurement", more_menu)

    def test_global_search_includes_permission_scoped_backorders(self):
        backorder = self.request.lines.get().backorder
        response = self.client.get(
            reverse("global_search"),
            {"q": backorder.backorder_number},
            HTTP_HOST="bbx.rplwms.com",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Backorders")
        self.assertContains(response, backorder.backorder_number)
        self.assertContains(response, f"#backorder-{backorder.pk}")

        no_backorder_access = User.objects.create_user("search-no-backorders")
        self.client.force_login(no_backorder_access)
        response = self.client.get(
            reverse("global_search"),
            {"q": backorder.backorder_number},
            HTTP_HOST="bbx.rplwms.com",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f"#backorder-{backorder.pk}")
        self.assertNotContains(response, "<h2>Backorders</h2>", html=True)

    def test_request_form_requires_shortage_choice_before_submit(self):
        from datetime import timedelta
        from django.utils import timezone

        second_item = InventoryItem.objects.create(
            part_number="SHORT-FORM", name="Short form item", quantity_on_hand=2
        )
        delivery_at = timezone.localtime() + timedelta(days=2)
        delivery_at = delivery_at.replace(
            minute=(delivery_at.minute // 15) * 15, second=0, microsecond=0
        )
        data = {
            "requestor_name": "Form Requester",
            "requestor_email": "form@example.com",
            "building_room": "B100",
            "location": "Dock",
            "delivery_at": delivery_at.strftime("%Y-%m-%dT%H:%M"),
            "notes": "",
            "lines-TOTAL_FORMS": "1",
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "1",
            "lines-MAX_NUM_FORMS": "1000",
            "lines-0-item": str(second_item.pk),
            "lines-0-quantity": "5",
            "lines-0-notes": "",
            "lines-0-shortage_action": "",
        }
        response = self.client.post(reverse("material_request_create"), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose what should happen to the remaining 3")
        second_item.refresh_from_db()
        self.assertEqual(second_item.quantity_on_hand, 2)

        data["lines-0-shortage_action"] = "backorder"
        response = self.client.post(reverse("material_request_create"), data)
        self.assertEqual(response.status_code, 302)
        second_item.refresh_from_db()
        self.assertEqual(second_item.quantity_on_hand, 0)
        created = MaterialRequest.objects.exclude(pk=self.request.pk).get(lines__item=second_item)
        self.assertEqual(created.lines.get().allocated_quantity, 2)
        self.assertEqual(created.lines.get().backorder.quantity, 3)

    def test_unprivileged_user_cannot_open_supply_queues(self):
        user = User.objects.create_user("no-supply-access")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("backorder_list")).status_code, 302)
        self.assertEqual(self.client.get(reverse("procurement_requisition_list")).status_code, 302)

    def _ticket_post_data(self, rows, *, ticket=None):
        from django.utils import timezone

        data = {
            "date": timezone.localtime(ticket.date if ticket else timezone.now()).strftime("%Y-%m-%dT%H:%M"),
            "status": ticket.status if ticket else PickTicket.Status.OPEN,
            "picked_by_name": str(self.admin.pk),
            "received_by_name": "",
            "requested_by_name": "Requester",
            "building_room": "B100",
            "location": "Dock",
            "notes": "",
            "lines-TOTAL_FORMS": str(len(rows)),
            "lines-INITIAL_FORMS": str(len(ticket.lines.all()) if ticket else 0),
            "lines-MIN_NUM_FORMS": "1",
            "lines-MAX_NUM_FORMS": "1000",
        }
        existing = list(ticket.lines.order_by("pk")) if ticket else []
        for index, (item, quantity) in enumerate(rows):
            data[f"lines-{index}-item"] = str(item.pk)
            data[f"lines-{index}-quantity"] = str(quantity)
            data[f"lines-{index}-scan_code"] = ""
            if index < len(existing):
                data[f"lines-{index}-id"] = str(existing[index].pk)
        return data

    def test_manual_ticket_create_rolls_back_all_lines_when_one_is_short(self):
        first = InventoryItem.objects.create(part_number="MANUAL-1", name="First", quantity_on_hand=3)
        second = InventoryItem.objects.create(part_number="MANUAL-2", name="Second", quantity_on_hand=1)
        baseline = PickTicket.objects.count()
        response = self.client.post(
            reverse("ticket_create"), self._ticket_post_data([(first, 2), (second, 2)])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Only 1 of MANUAL-2 is available")
        self.assertEqual(PickTicket.objects.count(), baseline)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.quantity_on_hand, second.quantity_on_hand), (3, 1))

    def test_manual_ticket_edit_rolls_back_original_lines_when_replacement_is_short(self):
        first = InventoryItem.objects.create(part_number="EDIT-1", name="First", quantity_on_hand=5)
        second = InventoryItem.objects.create(part_number="EDIT-2", name="Second", quantity_on_hand=1)
        ticket = PickTicket.objects.create(created_by=self.admin)
        PickTicketLine.objects.create(ticket=ticket, item=first, quantity=2)
        response = self.client.post(
            reverse("ticket_edit", args=[ticket.pk]),
            self._ticket_post_data([(first, 1), (second, 2)], ticket=ticket),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Only 1 of EDIT-2 is available")
        self.assertEqual(list(ticket.lines.values_list("item_id", "quantity")), [(first.pk, 2)])
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.quantity_on_hand, second.quantity_on_hand), (3, 1))

    def test_supplemental_ticket_is_immutable_through_generic_routes(self):
        backorder = self.request.lines.get().backorder
        self.item.adjust_quantity(
            1, InventoryTransaction.TransactionType.RECEIPT, user=self.admin, notes="Test receipt"
        )
        _, ticket = fulfill_material_backorder(backorder, quantity=1, actor=self.admin)
        detail = self.client.get(reverse("ticket_detail", args=[ticket.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "its lines cannot be edited or deleted")
        self.assertNotContains(detail, "Edit Pick Ticket")
        self.assertNotContains(detail, "Delete Ticket")
        self.assertRedirects(
            self.client.get(reverse("ticket_edit", args=[ticket.pk])),
            reverse("ticket_detail", args=[ticket.pk]),
        )
        self.assertRedirects(
            self.client.get(reverse("ticket_delete", args=[ticket.pk])),
            reverse("ticket_detail", args=[ticket.pk]),
        )
        self.assertRedirects(
            self.client.post(reverse("ticket_delete", args=[ticket.pk])),
            reverse("ticket_detail", args=[ticket.pk]),
        )
        self.assertTrue(PickTicket.objects.filter(pk=ticket.pk).exists())
        backorder.refresh_from_db()
        self.assertEqual(backorder.fulfilled_quantity, 1)
