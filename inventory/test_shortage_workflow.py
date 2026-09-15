from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from .models import (
    InventoryItem,
    InventoryTransaction,
    MaterialBackorder,
    MaterialRequest,
    PickTicket,
    PickTicketLine,
    ProcurementRequisition,
)
from .services import (
    create_material_request,
    delete_material_request,
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

    def test_procurement_backed_request_delete_is_blocked_without_server_error(self):
        response = self.client.post(
            reverse("material_request_delete", args=[self.request.pk]),
            HTTP_HOST="requests.rplwms.com",
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot be deleted after backorder fulfillment or procurement")
        self.assertTrue(MaterialRequest.objects.filter(pk=self.request.pk).exists())
        self.assertTrue(PickTicket.objects.filter(pk=self.request.pick_ticket_id).exists())

    def test_procurement_backed_linked_ticket_delete_is_blocked_without_server_error(self):
        response = self.client.post(
            reverse("ticket_delete", args=[self.request.pick_ticket_id]),
            HTTP_HOST="bbx.rplwms.com",
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot be deleted after backorder fulfillment or procurement")
        self.assertTrue(MaterialRequest.objects.filter(pk=self.request.pk).exists())
        self.assertTrue(PickTicket.objects.filter(pk=self.request.pick_ticket_id).exists())

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
