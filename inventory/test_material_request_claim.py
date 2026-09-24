import json
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from .models import (
    InventoryItem,
    MaterialRequestEvent,
    PickTicket,
    PushDelivery,
    PushSubscription,
)
from .push import _authorized_subscriptions, _payload
from .services import assign_material_request, create_material_request


class MaterialRequestClaimTests(TestCase):
    def setUp(self):
        permissions = Permission.objects.filter(
            codename__in=["view_materialrequest", "view_all_materialrequests", "change_pickticket"]
        )
        self.first = User.objects.create_user("first-specialist", password="pw", first_name="First")
        self.second = User.objects.create_user("second-specialist", password="pw", first_name="Second")
        self.first.user_permissions.add(*permissions)
        self.second.user_permissions.add(*permissions)
        self.requester = User.objects.create_user("claim-requester", password="pw")
        self.item = InventoryItem.objects.create(
            part_number="CLAIM-1", name="Claim item", quantity_on_hand=20
        )
        with patch("inventory.push.deliver_push_deliveries"):
            self.material_request = create_material_request(
                creator=self.requester,
                requestor_name="Claim Requester",
                building_room="B1",
                location="Dock",
                notes="",
                lines=[{"item": self.item, "quantity": 2, "notes": ""}],
            )

    def _client(self, user):
        client = Client()
        client.force_login(user)
        return client

    @patch("inventory.push.deliver_push_deliveries")
    def test_first_accept_wins_and_assigns_linked_ticket(self, deliver):
        url = reverse("material_request_claim", args=[self.material_request.pk])
        first = self._client(self.first).post(url, HTTP_HOST="bbx.rplwms.com")
        second = self._client(self.second).post(url, HTTP_HOST="bbx.rplwms.com")

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["claimed"])
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["claimed_by"], "First")
        self.material_request.refresh_from_db()
        self.material_request.pick_ticket.refresh_from_db()
        self.assertEqual(self.material_request.assigned_to, self.first)
        self.assertEqual(self.material_request.pick_ticket.assigned_to, self.first)
        self.assertEqual(self.material_request.pick_ticket.picked_by_user, self.first)
        self.assertEqual(self.material_request.pick_ticket.picked_by_name, "First")
        self.assertIsNotNone(self.material_request.pick_ticket.assigned_at)
        self.assertIsNone(self.material_request.pick_ticket.acknowledged_at)
        accepted = self.material_request.events.filter(
            event_type=MaterialRequestEvent.EventType.UPDATED,
            change_summary="Accepted by First",
        )
        self.assertEqual(accepted.count(), 1)
        deliver.assert_not_called()

    def test_user_without_fulfillment_permission_cannot_claim(self):
        viewer = User.objects.create_user("claim-viewer", password="pw")
        viewer.user_permissions.add(Permission.objects.get(codename="view_all_materialrequests"))
        response = self._client(viewer).post(
            reverse("material_request_claim", args=[self.material_request.pk]),
            HTTP_HOST="bbx.rplwms.com",
        )
        self.assertEqual(response.status_code, 403)
        self.material_request.refresh_from_db()
        self.assertIsNone(self.material_request.assigned_to)

    def test_initial_event_poll_returns_actionable_unclaimed_request(self):
        response = self._client(self.first).get(
            reverse("material_request_events"), HTTP_HOST="bbx.rplwms.com"
        )
        self.assertEqual(response.status_code, 200)
        event = response.json()["events"][0]
        self.assertEqual(event["request_id"], self.material_request.pk)
        self.assertEqual(
            event["claim_url"], reverse("material_request_claim", args=[self.material_request.pk])
        )
        self.assertTrue(event["require_interaction"])

    @patch("inventory.push.deliver_push_deliveries")
    def test_claim_event_removes_accept_action_for_other_tabs(self, deliver):
        cursor = self.material_request.creation_event.pk
        self._client(self.first).post(
            reverse("material_request_claim", args=[self.material_request.pk]),
            HTTP_HOST="bbx.rplwms.com",
        )
        response = self._client(self.second).get(
            reverse("material_request_events"), {"cursor": cursor}, HTTP_HOST="bbx.rplwms.com"
        )
        self.assertEqual(response.status_code, 200)
        event = response.json()["events"][0]
        self.assertEqual(event["request_id"], self.material_request.pk)
        self.assertEqual(event["claim_url"], "")
        self.assertEqual(event["claimed_by"], "First")

    @patch("inventory.push.deliver_push_deliveries")
    def test_signed_push_accept_action_claims_for_intended_user(self, deliver):
        client = self._client(self.first)
        subscription = PushSubscription.objects.create(
            user=self.first,
            endpoint="https://push.example.test/send/claim",
            p256dh="claim-key",
            auth="claim-auth",
            session_key=client.session.session_key,
        )
        delivery = PushDelivery.objects.create(
            event=self.material_request.creation_event,
            subscription=subscription,
        )
        payload = _payload(delivery)
        self.assertEqual(payload["actions"][0]["action"], "accept-request")
        self.assertTrue(payload["requireInteraction"])
        response = client.post(
            payload["claimUrl"],
            json.dumps({"token": payload["claimToken"]}),
            content_type="application/json",
            HTTP_HOST="bbx.rplwms.com",
        )
        self.assertEqual(response.status_code, 200)
        self.material_request.refresh_from_db()
        self.assertEqual(self.material_request.assigned_to, self.first)

    def test_managers_do_not_receive_claim_popup_or_push_action(self):
        manager = User.objects.create_user("claim-manager", password="pw")
        manager.user_permissions.add(*Permission.objects.filter(
            codename__in=["view_materialrequest", "view_all_materialrequests", "change_pickticket"]
        ))
        manager.groups.add(Group.objects.get(name="Logistics Manager"))
        client = self._client(manager)
        response = client.get(
            reverse("material_request_events"), {"cursor": 0}, HTTP_HOST="bbx.rplwms.com"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["events"], [])

        specialist_subscription = PushSubscription.objects.create(
            user=self.first,
            endpoint="https://push.example.test/send/specialist",
            p256dh="specialist-key",
            auth="specialist-auth",
        )
        manager_subscription = PushSubscription.objects.create(
            user=manager,
            endpoint="https://push.example.test/send/manager",
            p256dh="manager-key",
            auth="manager-auth",
        )
        recipients = list(_authorized_subscriptions(self.material_request.creation_event))
        self.assertIn(specialist_subscription, recipients)
        self.assertNotIn(manager_subscription, recipients)
        delivery = PushDelivery.objects.create(
            event=self.material_request.creation_event,
            subscription=manager_subscription,
        )
        self.assertNotIn("actions", _payload(delivery))

    @patch("inventory.push.deliver_push_deliveries")
    def test_only_manager_can_override_accepted_picker_and_override_is_audited(self, _deliver):
        self._client(self.first).post(
            reverse("material_request_claim", args=[self.material_request.pk]),
            HTTP_HOST="bbx.rplwms.com",
        )
        ticket = self.material_request.pick_ticket
        ticket.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "Only a manager"):
            assign_material_request(
                self.material_request, assignee=self.second, actor=self.first
            )

        manager = User.objects.create_user("picker-manager", password="pw")
        manager.groups.add(Group.objects.get(name="Sr. Logistics Manager"))
        assign_material_request(
            self.material_request, assignee=self.second, actor=manager
        )
        ticket.refresh_from_db()
        self.material_request.refresh_from_db()
        self.assertEqual(ticket.status, PickTicket.Status.OPEN)
        self.assertEqual(ticket.assigned_to, self.second)
        self.assertEqual(ticket.picked_by_user, self.second)
        self.assertIsNone(ticket.acknowledged_at)
        self.assertEqual(self.material_request.assigned_to, self.second)
        self.assertTrue(self.material_request.events.filter(
            event_type=MaterialRequestEvent.EventType.UPDATED,
            change_summary="Assigned to: First → Second",
            actor=manager,
        ).exists())

    def test_service_worker_handles_accept_action(self):
        source = self._client(self.first).get(
            "/service-worker.js", HTTP_HOST="bbx.rplwms.com"
        ).content.decode()
        self.assertIn("event.action === 'accept-request'", source)
        self.assertIn("claimToken", source)
        self.assertIn("credentials: 'same-origin'", source)
        self.assertIn("Request already assigned", source)
        self.assertIn("silent: false", source)
        self.assertIn("vibrate: [180, 80, 180]", source)

    def test_claim_and_event_endpoints_reject_non_warehouse_host(self):
        client = self._client(self.first)
        claim = client.post(
            reverse("material_request_claim", args=[self.material_request.pk]),
            HTTP_HOST="localhost",
        )
        events = client.get(reverse("material_request_events"), HTTP_HOST="localhost")
        subscription = PushSubscription.objects.create(
            user=self.first,
            endpoint="https://push.example.test/send/wrong-host",
            p256dh="wrong-host-key",
            auth="wrong-host-auth",
            session_key=client.session.session_key,
        )
        delivery = PushDelivery.objects.create(
            event=self.material_request.creation_event,
            subscription=subscription,
        )
        payload = _payload(delivery)
        push_claim = client.post(
            payload["claimUrl"],
            json.dumps({"token": payload["claimToken"]}),
            content_type="application/json",
            HTTP_HOST="localhost",
        )
        self.assertEqual(claim.status_code, 404)
        self.assertEqual(events.status_code, 404)
        self.assertEqual(push_claim.status_code, 403)
        self.material_request.refresh_from_db()
        self.assertIsNone(self.material_request.assigned_to)

    def test_client_merges_push_claim_data_and_scopes_cursor_per_user(self):
        root = Path(__file__).resolve().parent
        app_source = (root / "static/inventory/js/app.js").read_text()
        push_source = (root / "static/inventory/js/push.js").read_text()
        self.assertIn("item.claim_url || item.claimUrl || existing.claim_url", app_source)
        self.assertIn("location.host}:${userScope}", app_source)
        self.assertIn("document.hidden || polling", app_source)
        self.assertIn("playClaimAlert", app_source)
        self.assertIn("navigator.vibrate([140, 70, 140])", app_source)
        self.assertIn("claim_url: payload.claimUrl", push_source)

    def test_pick_ticket_ui_includes_mobile_cards_and_bin_location(self):
        root = Path(__file__).resolve().parent
        detail_source = (root / "templates/inventory/ticket_detail.html").read_text()
        print_source = (root / "templates/inventory/ticket_print.html").read_text()
        css_source = (root / "static/inventory/css/app.css").read_text()
        self.assertIn("pick-ticket-lines", detail_source)
        self.assertIn("line.item.storage_location", detail_source)
        self.assertIn("Locked to the specialist who accepted", detail_source)
        self.assertIn("BIN LOCATION", print_source)
        self.assertIn("Mobile pick-ticket workflow", css_source)

    @patch("inventory.push.deliver_push_deliveries")
    def test_delayed_push_does_not_offer_accept_after_request_is_claimed(self, deliver):
        client = self._client(self.first)
        subscription = PushSubscription.objects.create(
            user=self.second,
            endpoint="https://push.example.test/send/stale-claim",
            p256dh="stale-key",
            auth="stale-auth",
            session_key=self._client(self.second).session.session_key,
        )
        client.post(
            reverse("material_request_claim", args=[self.material_request.pk]),
            HTTP_HOST="bbx.rplwms.com",
        )
        delivery = PushDelivery.objects.create(
            event=self.material_request.creation_event,
            subscription=subscription,
        )
        payload = _payload(delivery)
        self.assertNotIn("claimUrl", payload)
        self.assertNotIn("claimToken", payload)
        self.assertNotIn("actions", payload)
