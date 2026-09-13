from datetime import timedelta

from django.contrib.auth.models import Permission, User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import InventoryItem, MaterialRequestEvent, PickTicket
from .services import create_material_request


class WarehouseWorkQueueTests(TestCase):
    host = "bbx.rplwms.com"

    def setUp(self):
        self.owner = User.objects.create_user("queue-owner", password="pw")
        self.warehouse = User.objects.create_user(
            "queue-warehouse", password="pw", first_name="Casey", last_name="Picker"
        )
        self.worker = User.objects.create_user(
            "queue-worker", password="pw", first_name="Morgan", last_name="Lee"
        )
        warehouse_permissions = Permission.objects.filter(
            content_type__app_label="inventory",
            codename__in={
                "view_materialrequest",
                "change_materialrequest",
                "view_all_materialrequests",
            },
        )
        self.warehouse.user_permissions.add(*warehouse_permissions)
        self.worker.user_permissions.add(*warehouse_permissions)
        self.item = InventoryItem.objects.create(
            part_number="QUEUE-1", name="Queue gloves", quantity_on_hand=100
        )
        self.client.force_login(self.warehouse)

    def request_obj(self, name, *, delivery_at=None, urgent=False, status=PickTicket.Status.OPEN):
        obj = create_material_request(
            creator=self.owner,
            requestor_name=name,
            building_room="B1",
            location="Warehouse dock",
            delivery_at=delivery_at,
            urgent=urgent,
            notes="",
            lines=[{"item": self.item, "quantity": 1, "notes": ""}],
        )
        if status != PickTicket.Status.OPEN:
            obj.pick_ticket.status = status
            obj.pick_ticket.save(update_fields=["status", "updated_at"])
        return obj

    def test_warehouse_defaults_to_work_queue_and_kanban_remains_available(self):
        active = self.request_obj("Active request")
        closed = self.request_obj("Closed request", status=PickTicket.Status.CLOSED)

        response = self.client.get(reverse("material_request_board"), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Warehouse Work Queue")
        self.assertContains(response, active.request_number)
        self.assertNotContains(response, closed.request_number)
        self.assertContains(response, "Kanban")
        self.assertContains(response, "view=board")

        board = self.client.get(
            reverse("material_request_board"), {"view": "board"}, HTTP_HOST=self.host
        )
        self.assertContains(board, 'aria-label="Material request status board"', html=False)
        self.assertContains(board, closed.request_number)

    def test_requester_keeps_owner_scoped_kanban_without_assignment_controls(self):
        own = self.request_obj("Owner request")
        foreign_owner = User.objects.create_user("queue-foreign", password="pw")
        foreign = create_material_request(
            creator=foreign_owner,
            requestor_name="Foreign request",
            building_room="B2",
            location="Other dock",
            notes="",
            lines=[{"item": self.item, "quantity": 1, "notes": ""}],
        )
        self.owner.user_permissions.add(
            Permission.objects.get(codename="access_material_request_portal"),
            Permission.objects.get(codename="view_materialrequest"),
        )
        requester_client = Client()
        requester_client.force_login(self.owner)

        response = requester_client.get(
            reverse("material_request_board"), HTTP_HOST="requests.rplwms.com"
        )

        self.assertContains(response, 'aria-label="Material request status board"', html=False)
        self.assertContains(response, own.request_number)
        self.assertNotContains(response, foreign.request_number)
        self.assertNotContains(response, "Assign to")
        self.assertNotContains(response, "Warehouse Work Queue")

    def test_queue_orders_urgent_then_overdue_then_due_today_then_normal(self):
        now = timezone.now()
        urgent = self.request_obj("Urgent", delivery_at=now + timedelta(days=3), urgent=True)
        overdue = self.request_obj("Overdue", delivery_at=now - timedelta(hours=2))
        due_today = self.request_obj("Due today", delivery_at=now + timedelta(hours=2))
        normal = self.request_obj("Normal", delivery_at=now + timedelta(days=5))

        response = self.client.get(reverse("material_request_board"), HTTP_HOST=self.host)
        body = response.content.decode()

        positions = [body.index(obj.request_number) for obj in (urgent, overdue, due_today, normal)]
        self.assertEqual(positions, sorted(positions))
        self.assertContains(response, "Overdue")
        self.assertContains(response, "Due today")

    def test_assignment_is_warehouse_only_filterable_and_emits_one_event_for_one_change(self):
        assigned = self.request_obj("Assigned work")
        unassigned = self.request_obj("Unassigned work")
        url = reverse("material_request_assign", args=[assigned.pk])

        first = self.client.post(
            url,
            {"assigned_to": self.worker.pk},
            HTTP_HOST=self.host,
        )
        second = self.client.post(
            url,
            {"assigned_to": self.worker.pk},
            HTTP_HOST=self.host,
        )

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        assigned.refresh_from_db()
        self.assertEqual(assigned.assigned_to, self.worker)
        events = MaterialRequestEvent.objects.filter(
            material_request=assigned,
            event_type=MaterialRequestEvent.EventType.UPDATED,
            change_summary__startswith="Assigned to:",
        )
        self.assertEqual(events.count(), 1)
        self.assertIn("Morgan Lee", events.get().change_summary)

        mine = self.client.get(
            reverse("material_request_board"), {"assigned": "mine"}, HTTP_HOST=self.host
        )
        self.assertNotIn(assigned.pk, [row.pk for row in mine.context["material_requests"]])
        self.assertNotIn(unassigned.pk, [row.pk for row in mine.context["material_requests"]])

        self.client.force_login(self.worker)
        mine = self.client.get(
            reverse("material_request_board"), {"assigned": "mine"}, HTTP_HOST=self.host
        )
        self.assertIn(assigned.pk, [row.pk for row in mine.context["material_requests"]])
        self.assertNotIn(unassigned.pk, [row.pk for row in mine.context["material_requests"]])

        self.client.force_login(self.warehouse)
        unassigned_response = self.client.get(
            reverse("material_request_board"), {"assigned": "unassigned"}, HTTP_HOST=self.host
        )
        self.assertNotIn(
            assigned.pk, [row.pk for row in unassigned_response.context["material_requests"]]
        )
        self.assertIn(
            unassigned.pk, [row.pk for row in unassigned_response.context["material_requests"]]
        )

    def test_assignment_rejects_nonwarehouse_assignee_and_unsafe_redirect(self):
        obj = self.request_obj("Guarded assignment")
        outsider = User.objects.create_user("outsider", password="pw")
        url = reverse("material_request_assign", args=[obj.pk])

        rejected = self.client.post(
            url,
            {"assigned_to": outsider.pk},
            HTTP_HOST=self.host,
        )
        self.assertEqual(rejected.status_code, 403)
        obj.refresh_from_db()
        self.assertIsNone(obj.assigned_to)

        accepted = self.client.post(
            url,
            {"assigned_to": self.worker.pk, "next": "https://evil.example/steal"},
            HTTP_HOST=self.host,
        )
        self.assertRedirects(
            accepted,
            reverse("material_request_board"),
            fetch_redirect_response=False,
        )

    def test_unassignment_is_audited_once(self):
        obj = self.request_obj("Unassign work")
        obj.assigned_to = self.worker
        obj.save(update_fields=["assigned_to"])

        response = self.client.post(
            reverse("material_request_assign", args=[obj.pk]),
            {"assigned_to": ""},
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 302)
        obj.refresh_from_db()
        self.assertIsNone(obj.assigned_to)
        event = MaterialRequestEvent.objects.get(
            material_request=obj,
            event_type=MaterialRequestEvent.EventType.UPDATED,
        )
        self.assertIn("Morgan Lee → Unassigned", event.change_summary)

    def test_requester_cannot_assign_even_their_own_request(self):
        obj = self.request_obj("Private request")
        self.owner.user_permissions.add(
            Permission.objects.get(codename="access_material_request_portal"),
            Permission.objects.get(codename="view_materialrequest"),
            Permission.objects.get(codename="change_materialrequest"),
        )
        requester_client = Client()
        requester_client.force_login(self.owner)

        response = requester_client.post(
            reverse("material_request_assign", args=[obj.pk]),
            {"assigned_to": self.worker.pk},
            HTTP_HOST="requests.rplwms.com",
        )

        self.assertIn(response.status_code, {403, 404})
        obj.refresh_from_db()
        self.assertIsNone(obj.assigned_to)
