from datetime import timedelta

from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import InventoryItem, MaterialRequest, PickTicket
from .services import create_material_request


class MaterialRequestBoardArchiveTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("requestor-archive", password="pw")
        self.user.groups.add(Group.objects.get(name="Material Requests"))
        self.item = InventoryItem.objects.create(part_number="ARC-1", name="Archived item", quantity_on_hand=50)
        self.client.force_login(self.user)

    def make_request(self, status=PickTicket.Status.OPEN):
        request_obj = create_material_request(
            creator=self.user,
            requestor_name="Requestor",
            building_room="B1",
            location="Dock",
            notes="",
            delivery_at=timezone.now() + timedelta(days=1, minutes=15),
            lines=[{"item": self.item, "quantity": 1, "notes": ""}],
        )
        request_obj.pick_ticket.status = status
        request_obj.pick_ticket.save(update_fields=["status", "updated_at"])
        return request_obj

    def test_board_has_exactly_four_progress_blocks_and_live_refresh(self):
        response = self.client.get(reverse("material_request_board"), HTTP_HOST="requests.rplwms.com")
        self.assertEqual(response.status_code, 200)
        for label in ("Open", "Picked", "Ready for Delivery", "Closed/Delivered"):
            self.assertContains(response, f"<h2>{label}</h2>", count=1, html=False)
        self.assertNotContains(response, ">Received<", html=False)
        self.assertContains(response, 'id="material-request-live-region"', html=False)
        self.assertContains(response, "material-request-board-poll", html=False)

    def test_dual_role_user_gets_portal_safe_board_after_creating_request(self):
        self.user.user_permissions.add(
            Permission.objects.get(codename="view_all_materialrequests")
        )
        request_obj = self.make_request()

        response = self.client.get(
            reverse("material_request_board"), HTTP_HOST="requests.rplwms.com"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, request_obj.request_number)
        self.assertContains(response, 'aria-label="Material request status board"', html=False)
        self.assertNotContains(response, 'aria-label="Warehouse material request work queue"', html=False)

    def test_partial_board_response_is_available_on_both_hosts(self):
        self.make_request()
        for host in ("requests.rplwms.com", "bbx.rplwms.com"):
            response = self.client.get(reverse("material_request_board"), {"partial": "1"}, HTTP_HOST=host)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "MR-")
            self.assertNotContains(response, "<!doctype html", html=False)

    def test_archive_command_moves_only_closed_requests(self):
        closed = self.make_request(PickTicket.Status.CLOSED)
        ready = self.make_request(PickTicket.Status.RECEIVED)
        call_command("archive_closed_material_requests")
        closed.refresh_from_db()
        ready.refresh_from_db()
        self.assertIsNotNone(closed.archived_at)
        self.assertIsNone(ready.archived_at)
        board = self.client.get(reverse("material_request_board"), HTTP_HOST="requests.rplwms.com")
        self.assertNotContains(board, closed.request_number)
        self.assertContains(board, ready.request_number)
        archive = self.client.get(reverse("material_request_archive"), HTTP_HOST="requests.rplwms.com")
        self.assertContains(archive, closed.request_number)
        self.assertNotContains(archive, ready.request_number)
