from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.utils import timezone

from .models import (
    Asset,
    EquipmentCategory,
    EquipmentParty,
    EquipmentRequest,
    EquipmentRequestLine,
    MaintenanceWorkOrder,
)


class EquipmentRequestUIReviewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.requester = User.objects.create_user("ui-requester", email="ui@example.com", password="test")
        self.manager = User.objects.create_user("ui-manager", password="test")
        self.requester.user_permissions.add(Permission.objects.get(
            content_type__app_label="equipment", codename="access_equipment_requests"
        ))
        self.manager.user_permissions.set(Permission.objects.filter(content_type__app_label="equipment"))
        self.party = EquipmentParty.objects.create(user=self.requester, display_name="UI Requester")
        self.category = EquipmentCategory.objects.create(name="Radios", code="RADIO-UI")

    def make_request(self, number, status=EquipmentRequest.Status.SUBMITTED):
        item = EquipmentRequest.objects.create(
            request_number=number,
            requester=self.requester,
            requestor_party=self.party,
            status=status,
            needed_from=timezone.localdate() + timedelta(days=2),
            destination="Site A",
            purpose="UI review",
        )
        EquipmentRequestLine.objects.create(request=item, category=self.category, quantity=1)
        return item

    def test_requester_shell_theme_navigation_and_dynamic_formset(self):
        client = Client(HTTP_HOST="eqreq.rplwms.com")
        client.force_login(self.requester)
        response = client.get("/new/")
        self.assertContains(response, "eqreq-init.js")
        self.assertContains(response, "eqreq.js")
        self.assertContains(response, 'data-eqreq-theme-toggle')
        self.assertContains(response, 'href="/help/"')
        self.assertContains(response, 'href="/account/"')
        self.assertContains(response, 'name="lines-TOTAL_FORMS" value="1"')
        self.assertContains(response, "data-empty-line")
        self.assertContains(response, "data-add-line")
        self.assertNotContains(response, '<html lang="en" data-theme="dark"')
        self.assertEqual(client.get("/help/").status_code, 200)
        account = client.get("/account/")
        self.assertContains(account, "ui@example.com")
        self.assertNotContains(account, "Request queue")

    def test_requester_pagination_page_two_and_status_filter_are_preserved(self):
        for index in range(21):
            self.make_request(f"ER-UI-{index:03d}")
        client = Client(HTTP_HOST="eqreq.rplwms.com")
        client.force_login(self.requester)
        first = client.get("/?status=SUBMITTED")
        self.assertContains(first, "status=SUBMITTED&amp;page=2")
        second = client.get("/?status=SUBMITTED&page=2")
        self.assertEqual(len(second.context["page"].object_list), 1)
        self.assertContains(second, "ER-UI-000")

    def test_manager_queue_pagination_and_responsive_cells(self):
        for index in range(51):
            self.make_request(f"ER-MGR-{index:03d}", EquipmentRequest.Status.REVIEWING)
        client = Client(HTTP_HOST="equipment.rplwms.com")
        client.force_login(self.manager)
        first = client.get("/requests/?status=REVIEWING")
        self.assertContains(first, "equipment-workspace-heading")
        self.assertContains(first, "responsive-records")
        self.assertContains(first, 'data-label="Requester"')
        self.assertContains(first, "status=REVIEWING&amp;page=2")
        second = client.get("/requests/?status=REVIEWING&page=2")
        self.assertEqual(len(second.context["page"].object_list), 1)
        self.assertContains(second, "ER-MGR-000")

    def test_manager_detail_only_renders_service_allowed_actions(self):
        submitted = self.make_request("ER-ACTIONS-OPEN")
        closed = self.make_request("ER-ACTIONS-CLOSED", EquipmentRequest.Status.CANCELLED)
        client = Client(HTTP_HOST="equipment.rplwms.com")
        client.force_login(self.manager)
        open_response = client.get(f"/requests/{submitted.pk}/")
        self.assertContains(open_response, 'value="REVIEWING"')
        self.assertContains(open_response, 'value="DECLINED"')
        self.assertNotContains(open_response, 'value="APPROVED"')
        closed_response = client.get(f"/requests/{closed.pk}/")
        self.assertContains(closed_response, "Request closed")
        self.assertNotContains(closed_response, "Save assignment")
        self.assertNotContains(closed_response, "Allocate assets")

    def test_maintenance_actions_match_status_and_schedule_is_required(self):
        asset = Asset.objects.create(
            asset_tag="UI-ASSET", category=self.category, name="UI asset",
            status=Asset.Status.MAINTENANCE, created_by=self.manager, updated_by=self.manager,
        )
        order = MaintenanceWorkOrder.objects.create(
            work_order_number="EQ-WO-UI", asset=asset, title="Review actions", opened_by=self.manager,
        )
        client = Client(HTTP_HOST="equipment.rplwms.com")
        client.force_login(self.manager)
        response = client.get("/maintenance/")
        self.assertContains(response, "Schedule date")
        self.assertContains(response, "Start work")
        self.assertContains(response, "Cancel work order")
        self.assertNotContains(response, "Wait for parts")
        invalid = client.post(f"/maintenance/{order.pk}/status/", {"status": "SCHEDULED"}, follow=True)
        self.assertContains(invalid, "Scheduled for: A scheduled service date is required.")
        order.status = MaintenanceWorkOrder.Status.COMPLETED
        order.save(update_fields=("status", "updated_at"))
        closed = client.get("/maintenance/?tab=history")
        self.assertNotContains(closed, f'/maintenance/{order.pk}/complete/')
        complete = client.get(f"/maintenance/{order.pk}/complete/")
        self.assertRedirects(complete, "/maintenance/")
