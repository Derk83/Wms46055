import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from django.contrib.auth.models import Permission, User
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import InventoryItem, MaterialRequestEvent, PickTicket, PushDelivery, PushSubscription
from .services import create_material_request


class PWAInstallabilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("pwa-user", password="pw")

    def test_manifest_is_installable_and_host_specific_on_both_domains(self):
        for host, expected_name in (
            ("bbx.rplwms.com", "RPL Warehouse"),
            ("requests.rplwms.com", "RPL Warehouse — Material Requests"),
        ):
            response = self.client.get("/manifest.webmanifest", HTTP_HOST=host, secure=True)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "application/manifest+json")
            manifest = response.json()
            self.assertEqual(manifest["name"], expected_name)
            self.assertEqual(manifest["start_url"], "/")
            self.assertEqual(manifest["scope"], "/")
            self.assertEqual(manifest["display"], "standalone")
            self.assertEqual({icon["sizes"] for icon in manifest["icons"]}, {"192x192", "512x512"})
            self.assertTrue(any("maskable" in icon.get("purpose", "") for icon in manifest["icons"]))

    def test_service_worker_is_root_scoped_and_never_intercepts_writes(self):
        for host in ("bbx.rplwms.com", "requests.rplwms.com"):
            response = self.client.get("/service-worker.js", HTTP_HOST=host, secure=True)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response["Content-Type"].startswith("application/javascript"))
            self.assertEqual(response["Service-Worker-Allowed"], "/")
            self.assertIn("no-cache", response["Cache-Control"])
            source = response.content.decode()
            self.assertIn("request.method !== 'GET'", source)
            self.assertNotIn("sync.add", source)
            self.assertNotIn("indexedDB", source)
            self.assertNotIn("/api/", source)
            self.assertIn("request.mode === 'navigate'", source)
            self.assertIn("/offline/", source)
            self.assertIn("const CACHE_VERSION = 'bbx-shell-v4';", source)
        self.assertEqual(self.client.head("/service-worker.js", HTTP_HOST="bbx.rplwms.com").status_code, 200)
        self.assertEqual(self.client.head("/manifest.webmanifest", HTTP_HOST="bbx.rplwms.com").status_code, 200)

    def test_offline_fallback_contains_no_authenticated_warehouse_data(self):
        response = self.client.get("/offline/", HTTP_HOST="bbx.rplwms.com", secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertContains(response, "You're offline")
        self.assertNotContains(response, "Inventory")
        self.assertNotContains(response, "Material Requests")

    def test_authenticated_shell_registers_pwa_and_exposes_install_control(self):
        client = Client(HTTP_HOST="bbx.rplwms.com")
        client.force_login(self.user)
        response = client.get("/", secure=True)
        self.assertContains(response, 'rel="manifest"', html=False)
        self.assertContains(response, 'href="/manifest.webmanifest"', html=False)
        self.assertContains(response, 'data-pwa-install', html=False)
        self.assertContains(response, "inventory/js/pwa.js")

    def test_mutating_inventory_routes_remain_server_only(self):
        client = Client(HTTP_HOST="bbx.rplwms.com")
        client.force_login(self.user)
        service_worker = client.get("/service-worker.js", secure=True).content.decode()
        for route in ("/inventory/", "/material-requests/", "/tickets/", "/api/"):
            self.assertNotIn(f"'{route}'", service_worker)
        self.assertNotIn("Background Sync", service_worker)
        self.assertNotIn("self.addEventListener('sync'", service_worker)
        pwa_client = (Path(__file__).parent / "static/inventory/js/pwa.js").read_text()
        self.assertIn("navigator.onLine", pwa_client)
        self.assertIn("form.method", pwa_client)
        self.assertNotIn("indexedDB", pwa_client)
        self.assertNotIn("localStorage.setItem('pending", pwa_client)


VAPID_SETTINGS = {
    "WEBPUSH_VAPID_PUBLIC_KEY": "test-public-key",
    "WEBPUSH_VAPID_PRIVATE_KEY": "test-private-key",
    "WEBPUSH_VAPID_SUBJECT": "mailto:warehouse@example.com",
}


@override_settings(**VAPID_SETTINGS)
class PushSubscriptionEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("warehouse", password="pw")
        self.user.user_permissions.add(
            Permission.objects.get(codename="view_materialrequest"),
            Permission.objects.get(codename="view_all_materialrequests"),
        )
        self.client.force_login(self.user)
        self.subscription = {
            "endpoint": "https://updates.push.services.mozilla.com/wpush/v2/device-1",
            "keys": {"p256dh": "p256dh-value", "auth": "auth-value"},
        }

    def test_config_and_subscription_lifecycle_supports_wms_and_request_portal(self):
        config = self.client.get(reverse("push_config"), HTTP_HOST="bbx.rplwms.com", secure=True)
        self.assertEqual(config.status_code, 200)
        self.assertJSONEqual(config.content, {"enabled": True, "publicKey": "test-public-key", "subscribed": False})
        response = self.client.post(
            reverse("push_subscribe"), json.dumps(self.subscription), content_type="application/json",
            HTTP_HOST="bbx.rplwms.com", secure=True,
        )
        self.assertEqual(response.status_code, 201)
        saved = PushSubscription.objects.get()
        self.assertEqual((saved.user, saved.endpoint, saved.p256dh), (self.user, self.subscription["endpoint"], "p256dh-value"))
        self.assertTrue(saved.enabled)
        self.assertEqual(saved.session_key, self.client.session.session_key)
        self.assertTrue(self.client.get(reverse("push_config"), HTTP_HOST="bbx.rplwms.com").json()["subscribed"])
        response = self.client.post(
            reverse("push_unsubscribe"), json.dumps({"endpoint": self.subscription["endpoint"]}),
            content_type="application/json", HTTP_HOST="bbx.rplwms.com",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PushSubscription.objects.exists())
        self.user.user_permissions.add(Permission.objects.get(codename="access_material_request_portal"))
        portal = self.client.post(
            "/api/push/subscribe/", json.dumps(self.subscription), content_type="application/json",
            HTTP_HOST="requests.rplwms.com",
        )
        self.assertEqual(portal.status_code, 201)

    def test_subscription_requires_auth_permission_valid_json_and_csrf(self):
        anonymous = Client().post(
            reverse("push_subscribe"), json.dumps(self.subscription), content_type="application/json",
            HTTP_HOST="bbx.rplwms.com",
        )
        self.assertEqual(anonymous.status_code, 302)
        self.user.user_permissions.clear()
        denied = self.client.post(
            reverse("push_subscribe"), json.dumps(self.subscription), content_type="application/json",
            HTTP_HOST="bbx.rplwms.com",
        )
        self.assertEqual(denied.status_code, 403)
        self.user.user_permissions.add(
            Permission.objects.get(codename="view_materialrequest"),
            Permission.objects.get(codename="view_all_materialrequests"),
        )
        malformed = self.client.post(
            reverse("push_subscribe"), "not-json", content_type="application/json", HTTP_HOST="bbx.rplwms.com",
        )
        self.assertEqual(malformed.status_code, 400)
        too_large = dict(self.subscription)
        too_large["endpoint"] = "https://push.example.test/" + ("x" * 2050)
        response = self.client.post(
            reverse("push_subscribe"), json.dumps(too_large), content_type="application/json",
            HTTP_HOST="bbx.rplwms.com",
        )
        self.assertEqual(response.status_code, 400)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        csrf_denied = csrf_client.post(
            reverse("push_subscribe"), json.dumps(self.subscription), content_type="application/json",
            HTTP_HOST="bbx.rplwms.com",
        )
        self.assertEqual(csrf_denied.status_code, 403)

    def test_subscription_rejects_unapproved_or_confusing_endpoints(self):
        rejected_endpoints = (
            "http://updates.push.services.mozilla.com/wpush/device-1",
            "https://updates.push.services.mozilla.com.evil.test/wpush/device-1",
            "https://push.services.mozilla.com@evil.test/wpush/device-1",
            "https://user:pass@updates.push.services.mozilla.com/wpush/device-1",
            "https://updates.push.services.mozilla.com:8443/wpush/device-1",
            "not-a-url",
        )
        for endpoint in rejected_endpoints:
            payload = {**self.subscription, "endpoint": endpoint}
            with self.subTest(endpoint=endpoint):
                response = self.client.post(
                    reverse("push_subscribe"), json.dumps(payload), content_type="application/json",
                    HTTP_HOST="bbx.rplwms.com", secure=True,
                )
                self.assertEqual(response.status_code, 400)
        self.assertFalse(PushSubscription.objects.exists())

    def test_view_only_requester_cannot_use_wms_notification_surfaces(self):
        requester = User.objects.create_user("view-only-requester", password="pw")
        requester.user_permissions.add(Permission.objects.get(codename="view_materialrequest"))
        client = Client()
        client.force_login(requester)
        config = client.get(reverse("push_config"), HTTP_HOST="bbx.rplwms.com")
        subscribe = client.post(
            reverse("push_subscribe"), json.dumps(self.subscription),
            content_type="application/json", HTTP_HOST="bbx.rplwms.com",
        )
        events = client.get(reverse("material_request_events"), HTTP_HOST="bbx.rplwms.com")
        self.assertEqual(config.status_code, 403)
        self.assertEqual(subscribe.status_code, 403)
        self.assertIn(events.status_code, {302, 403})

    def test_same_browser_subscription_moves_to_current_user(self):
        other = User.objects.create_user("other", password="pw")
        other.user_permissions.add(Permission.objects.get(codename="view_materialrequest"))
        PushSubscription.objects.create(
            user=other, endpoint=self.subscription["endpoint"], p256dh="old", auth="old", enabled=True,
        )
        response = self.client.post(
            reverse("push_subscribe"), json.dumps(self.subscription), content_type="application/json",
            HTTP_HOST="bbx.rplwms.com",
        )
        self.assertEqual(response.status_code, 200)
        saved = PushSubscription.objects.get()
        self.assertEqual(saved.user, self.user)
        self.assertEqual(saved.p256dh, "p256dh-value")


@override_settings(**VAPID_SETTINGS)
class PushDeliveryTests(TestCase):
    def setUp(self):
        self.creator = User.objects.create_user("requestor", password="pw")
        self.recipient = User.objects.create_user("recipient", password="pw")
        self.recipient.user_permissions.add(
            Permission.objects.get(codename="view_materialrequest"),
            Permission.objects.get(codename="view_all_materialrequests"),
        )
        self.recipient_client = Client()
        self.recipient_client.force_login(self.recipient)
        self.recipient_session_key = self.recipient_client.session.session_key
        self.unauthorized = User.objects.create_user("unauthorized", password="pw")
        self.item = InventoryItem.objects.create(part_number="PUSH-1", name="Push item", quantity_on_hand=20)
        self.subscription = PushSubscription.objects.create(
            user=self.recipient, endpoint="https://push.example.test/send/recipient",
            p256dh="key", auth="secret", enabled=True, session_key=self.recipient_session_key,
        )
        PushSubscription.objects.create(
            user=self.unauthorized, endpoint="https://push.example.test/send/unauthorized",
            p256dh="key2", auth="secret2", enabled=True,
        )

    def create_request(self):
        return create_material_request(
            creator=self.creator, requestor_name="Requester", building_room="B1", location="Dock",
            notes="", lines=[{"item": self.item, "quantity": 1, "notes": ""}],
        )

    @patch("inventory.push.deliver_push_deliveries")
    def test_request_commit_queues_only_authorized_active_subscriptions(self, deliver):
        with self.captureOnCommitCallbacks(execute=True):
            material_request = self.create_request()
        deliveries = PushDelivery.objects.select_related("subscription").all()
        self.assertEqual(deliveries.count(), 1)
        self.assertEqual(deliveries.get().subscription, self.subscription)
        self.assertEqual(deliveries.get().event, material_request.creation_event)
        deliver.assert_called_once()

    @patch("inventory.push.webpush")
    def test_successful_delivery_uses_vapid_and_marks_sent(self, webpush):
        with patch("inventory.push.deliver_push_deliveries"):
            material_request = self.create_request()
        delivery = PushDelivery.objects.get(event=material_request.creation_event, subscription=self.subscription)
        from .push import deliver_push_deliveries
        result = deliver_push_deliveries([delivery.pk])
        delivery.refresh_from_db()
        self.assertEqual(result, {"sent": 1, "failed": 0, "expired": 0})
        self.assertEqual(delivery.status, PushDelivery.Status.SENT)
        self.assertIsNotNone(delivery.sent_at)
        kwargs = webpush.call_args.kwargs
        payload = json.loads(kwargs["data"])
        self.assertEqual(payload["url"], reverse("material_request_detail", args=[material_request.pk]))
        self.assertEqual(payload["eventId"], material_request.creation_event.pk)
        self.assertNotIn("notes", payload)
        self.assertEqual(kwargs["vapid_claims"]["sub"], "mailto:warehouse@example.com")

    @patch("inventory.push.webpush")
    def test_urgent_request_push_is_prominent_and_persistent(self, webpush):
        from .push import deliver_push_deliveries

        with patch("inventory.push.deliver_push_deliveries"):
            material_request = create_material_request(
                creator=self.creator, requestor_name="Requester", building_room="B1", location="Dock",
                notes="", urgent=True,
                lines=[{"item": self.item, "quantity": 1, "notes": ""}],
            )
        delivery = PushDelivery.objects.get(
            event=material_request.creation_event, subscription=self.subscription
        )
        deliver_push_deliveries([delivery.pk])
        payload = json.loads(webpush.call_args.kwargs["data"])
        self.assertEqual(payload["title"], "🚨 URGENT: New material request")
        self.assertTrue(payload["urgent"])
        self.assertTrue(payload["requireInteraction"])

    @override_settings(WEBPUSH_VAPID_SUBJECT="https://bbx.rplwms.com/")
    @patch("inventory.push.webpush")
    def test_delivery_normalizes_trailing_slash_in_https_vapid_subject(self, webpush):
        with patch("inventory.push.deliver_push_deliveries"):
            material_request = self.create_request()
        delivery = PushDelivery.objects.get(event=material_request.creation_event, subscription=self.subscription)
        from .push import deliver_push_deliveries

        result = deliver_push_deliveries([delivery.pk])

        self.assertEqual(result, {"sent": 1, "failed": 0, "expired": 0})
        self.assertEqual(webpush.call_args.kwargs["vapid_claims"]["sub"], "https://bbx.rplwms.com")

    @patch("inventory.push.webpush")
    def test_gone_subscription_is_removed_without_retry(self, webpush):
        error = Exception("gone")
        error.response = Mock(status_code=410)
        webpush.side_effect = error
        with patch("inventory.push.deliver_push_deliveries"):
            material_request = self.create_request()
        delivery = PushDelivery.objects.get(event=material_request.creation_event, subscription=self.subscription)
        from .push import deliver_push_deliveries
        result = deliver_push_deliveries([delivery.pk])
        self.assertEqual(result["expired"], 1)
        self.assertFalse(PushSubscription.objects.filter(pk=self.subscription.pk).exists())
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, PushDelivery.Status.EXPIRED)
        self.assertIsNone(delivery.subscription)

    @patch("inventory.push.webpush")
    def test_transient_failure_is_bounded_and_scheduled_for_retry(self, webpush):
        webpush.side_effect = RuntimeError("temporary outage")
        with patch("inventory.push.deliver_push_deliveries"):
            material_request = self.create_request()
        delivery = PushDelivery.objects.get(event=material_request.creation_event, subscription=self.subscription)
        from .push import deliver_push_deliveries
        result = deliver_push_deliveries([delivery.pk])
        delivery.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(result["failed"], 1)
        self.assertEqual(delivery.status, PushDelivery.Status.RETRY)
        self.assertEqual(delivery.attempts, 1)
        self.assertGreater(delivery.next_attempt_at, timezone.now())
        self.assertEqual(self.subscription.failure_count, 1)

    @patch("inventory.push.webpush")
    def test_in_flight_delivery_lease_prevents_duplicate_send(self, webpush):
        with patch("inventory.push.deliver_push_deliveries"):
            material_request = self.create_request()
        delivery = PushDelivery.objects.get(event=material_request.creation_event, subscription=self.subscription)
        delivery.status = PushDelivery.Status.PROCESSING
        delivery.next_attempt_at = timezone.now() + timedelta(minutes=5)
        delivery.save(update_fields=["status", "next_attempt_at"])
        from .push import deliver_push_deliveries
        result = deliver_push_deliveries([delivery.pk])
        self.assertEqual(result, {"sent": 0, "failed": 0, "expired": 0})
        webpush.assert_not_called()

    @patch("inventory.push.webpush")
    def test_delivery_rechecks_permission_before_disclosing_request(self, webpush):
        with patch("inventory.push.deliver_push_deliveries"):
            material_request = self.create_request()
        self.recipient.user_permissions.clear()
        self.recipient.user_permissions.add(Permission.objects.get(codename="view_materialrequest"))
        delivery = PushDelivery.objects.get(event=material_request.creation_event, subscription=self.subscription)
        from .push import deliver_push_deliveries
        deliver_push_deliveries([delivery.pk])
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, PushDelivery.Status.CANCELLED)
        webpush.assert_not_called()

    @patch("inventory.push.webpush")
    def test_expired_login_session_cancels_delivery(self, webpush):
        with patch("inventory.push.deliver_push_deliveries"):
            material_request = self.create_request()
        Session.objects.filter(session_key=self.recipient_session_key).delete()
        delivery = PushDelivery.objects.get(event=material_request.creation_event, subscription=self.subscription)
        from .push import deliver_push_deliveries
        deliver_push_deliveries([delivery.pk])
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, PushDelivery.Status.CANCELLED)
        webpush.assert_not_called()

    @patch("inventory.push.deliver_push_deliveries")
    def test_retry_command_delivers_due_rows_and_removes_old_disabled_endpoints(self, deliver):
        with patch("inventory.push.deliver_push_deliveries"):
            material_request = self.create_request()
        delivery = PushDelivery.objects.get(event=material_request.creation_event, subscription=self.subscription)
        stale = PushSubscription.objects.create(
            user=self.recipient, endpoint="https://push.example.test/send/stale",
            p256dh="stale-key", auth="stale-auth", enabled=False,
        )
        PushSubscription.objects.filter(pk=stale.pk).update(updated_at=timezone.now() - timedelta(days=31))
        call_command("process_push_notifications", limit=25)
        deliver.assert_called_once_with([delivery.pk])
        self.assertFalse(PushSubscription.objects.filter(pk=stale.pk).exists())

    def test_service_worker_handles_push_and_safe_same_origin_clicks(self):
        source = self.client.get("/service-worker.js", HTTP_HOST="bbx.rplwms.com").content.decode()
        self.assertIn("self.addEventListener('push'", source)
        self.assertIn("self.addEventListener('notificationclick'", source)
        self.assertIn("url.origin !== self.location.origin", source)


@override_settings(**VAPID_SETTINGS)
class MaterialRequestStatusNotificationTests(TestCase):
    def setUp(self):
        self.requester = User.objects.create_user("delivery-requester", password="pw")
        self.requester.user_permissions.add(
            Permission.objects.get(codename="access_material_request_portal"),
            Permission.objects.get(codename="view_materialrequest"),
        )
        self.warehouse = User.objects.create_user("warehouse-status", password="pw")
        self.warehouse.user_permissions.add(
            Permission.objects.get(codename="view_materialrequest"),
            Permission.objects.get(codename="view_all_materialrequests"),
        )
        self.requester_client = Client()
        self.requester_client.force_login(self.requester)
        self.warehouse_client = Client()
        self.warehouse_client.force_login(self.warehouse)
        self.requester_subscription = PushSubscription.objects.create(
            user=self.requester, endpoint="https://push.example.test/send/requester-status",
            p256dh="requester-key", auth="requester-auth",
            session_key=self.requester_client.session.session_key,
            audience=PushSubscription.Audience.REQUEST_PORTAL,
        )
        self.warehouse_subscription = PushSubscription.objects.create(
            user=self.warehouse, endpoint="https://push.example.test/send/warehouse-status",
            p256dh="warehouse-key", auth="warehouse-auth",
            session_key=self.warehouse_client.session.session_key,
        )
        item = InventoryItem.objects.create(part_number="STATUS-1", name="Status item", quantity_on_hand=10)
        with patch("inventory.push.deliver_push_deliveries"):
            self.material_request = create_material_request(
                creator=self.requester, requestor_name="Delivery Requester", building_room="B1",
                location="Dock", notes="", lines=[{"item": item, "quantity": 1, "notes": ""}],
            )

    @patch("inventory.push.deliver_push_deliveries")
    def test_each_real_status_transition_queues_one_requester_notification(self, deliver):
        from .services import update_pick_ticket_status

        for old_status, new_status in (
            (PickTicket.Status.OPEN, PickTicket.Status.PICKED),
            (PickTicket.Status.PICKED, PickTicket.Status.RECEIVED),
            (PickTicket.Status.RECEIVED, PickTicket.Status.CLOSED),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                event = update_pick_ticket_status(self.material_request.pick_ticket, new_status, actor=self.warehouse)
            self.assertEqual((event.old_status, event.new_status), (old_status, new_status))
            self.assertEqual(
                list(PushDelivery.objects.filter(event=event).values_list("subscription_id", flat=True)),
                [self.requester_subscription.pk],
            )
        self.assertEqual(deliver.call_count, 3)

    @patch("inventory.push.deliver_push_deliveries")
    def test_reposting_current_status_does_not_duplicate_an_event(self, deliver):
        from .services import update_pick_ticket_status

        event = update_pick_ticket_status(
            self.material_request.pick_ticket, PickTicket.Status.OPEN, actor=self.warehouse
        )
        self.assertIsNone(event)
        self.assertEqual(self.material_request.events.filter(
            event_type=MaterialRequestEvent.EventType.STATUS_CHANGED
        ).count(), 0)
        deliver.assert_not_called()

    @patch("inventory.push.webpush")
    def test_ready_for_delivery_payload_asks_requester_to_confirm(self, webpush):
        from .push import deliver_push_deliveries
        from .services import update_pick_ticket_status

        with patch("inventory.push.deliver_push_deliveries"):
            event = update_pick_ticket_status(
                self.material_request.pick_ticket, PickTicket.Status.RECEIVED, actor=self.warehouse
            )
        delivery = PushDelivery.objects.get(event=event, subscription=self.requester_subscription)
        deliver_push_deliveries([delivery.pk])
        payload = json.loads(webpush.call_args.kwargs["data"])
        self.assertEqual(payload["title"], "Order ready for delivery")
        self.assertIn("ready to accept", payload["body"].lower())
        self.assertEqual(payload["actions"][0]["action"], "confirm-delivery")
        self.assertTrue(payload["confirmUrl"].endswith("/confirm-ready/"))
        self.assertTrue(payload["confirmationToken"])
        self.assertTrue(payload["requireInteraction"])

    @patch("inventory.push.deliver_push_deliveries")
    def test_notification_confirmation_records_readiness_once_and_notifies_warehouse(self, deliver):
        from .push import _payload
        from .services import update_pick_ticket_status

        with patch("inventory.push.deliver_push_deliveries"):
            ready_event = update_pick_ticket_status(
                self.material_request.pick_ticket, PickTicket.Status.RECEIVED, actor=self.warehouse
            )
        payload = _payload(PushDelivery.objects.get(
            event=ready_event, subscription=self.requester_subscription
        ))
        request_args = (
            payload["confirmUrl"], json.dumps({"token": payload["confirmationToken"]})
        )
        response = self.requester_client.post(
            *request_args, content_type="application/json", HTTP_HOST="requests.rplwms.com"
        )
        self.assertEqual(response.status_code, 200)
        self.material_request.refresh_from_db()
        self.assertIsNotNone(self.material_request.delivery_acceptance_confirmed_at)
        confirmation_event = self.material_request.events.get(
            event_type=MaterialRequestEvent.EventType.DELIVERY_ACCEPTANCE_CONFIRMED
        )
        self.assertEqual(
            PushDelivery.objects.get(event=confirmation_event).subscription,
            self.warehouse_subscription,
        )
        duplicate = self.requester_client.post(
            *request_args, content_type="application/json", HTTP_HOST="requests.rplwms.com"
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(self.material_request.events.filter(
            event_type=MaterialRequestEvent.EventType.DELIVERY_ACCEPTANCE_CONFIRMED
        ).count(), 1)

    @patch("inventory.push.deliver_push_deliveries")
    def test_stale_ready_token_is_rejected_after_a_new_ready_cycle(self, deliver):
        from .push import _payload
        from .services import update_pick_ticket_status

        with patch("inventory.push.deliver_push_deliveries"):
            first_ready = update_pick_ticket_status(
                self.material_request.pick_ticket, PickTicket.Status.RECEIVED, actor=self.warehouse
            )
            first_payload = _payload(PushDelivery.objects.get(
                event=first_ready, subscription=self.requester_subscription
            ))
            update_pick_ticket_status(
                self.material_request.pick_ticket, PickTicket.Status.CLOSED, actor=self.warehouse
            )
            second_ready = update_pick_ticket_status(
                self.material_request.pick_ticket, PickTicket.Status.RECEIVED, actor=self.warehouse
            )
            second_payload = _payload(PushDelivery.objects.get(
                event=second_ready, subscription=self.requester_subscription
            ))

        stale = self.requester_client.post(
            first_payload["confirmUrl"],
            json.dumps({"token": first_payload["confirmationToken"]}),
            content_type="application/json",
            HTTP_HOST="requests.rplwms.com",
        )
        self.assertEqual(stale.status_code, 400)
        self.material_request.refresh_from_db()
        self.assertIsNone(self.material_request.delivery_acceptance_confirmed_at)

        current = self.requester_client.post(
            second_payload["confirmUrl"],
            json.dumps({"token": second_payload["confirmationToken"]}),
            content_type="application/json",
            HTTP_HOST="requests.rplwms.com",
        )
        self.assertEqual(current.status_code, 200)
        self.material_request.pick_ticket.refresh_from_db()
        self.assertEqual(self.material_request.pick_ticket.status, PickTicket.Status.RECEIVED)

    @patch("inventory.push.deliver_push_deliveries")
    def test_new_request_portal_subscription_catches_up_currently_ready_order(self, deliver):
        from .services import update_pick_ticket_status

        self.requester_subscription.delete()
        with patch("inventory.push.deliver_push_deliveries"):
            ready_event = update_pick_ticket_status(
                self.material_request.pick_ticket, PickTicket.Status.RECEIVED, actor=self.warehouse
            )
        self.assertFalse(PushDelivery.objects.filter(event=ready_event).exists())

        payload = {
            "endpoint": "https://updates.push.services.mozilla.com/wpush/v2/catch-up-device",
            "keys": {"p256dh": "catch-up-key", "auth": "catch-up-auth"},
        }
        with self.captureOnCommitCallbacks(execute=True):
            response = self.requester_client.post(
                "/api/push/subscribe/", json.dumps(payload), content_type="application/json",
                HTTP_HOST="requests.rplwms.com", secure=True,
            )
        self.assertEqual(response.status_code, 201)
        subscription = PushSubscription.objects.get(endpoint=payload["endpoint"])
        self.assertEqual(subscription.audience, PushSubscription.Audience.REQUEST_PORTAL)
        self.assertTrue(PushDelivery.objects.filter(event=ready_event, subscription=subscription).exists())
        deliver.assert_called_once()

    def test_service_worker_posts_confirmation_action_and_keeps_normal_click_navigation(self):
        source = self.requester_client.get(
            "/service-worker.js", HTTP_HOST="requests.rplwms.com"
        ).content.decode()
        self.assertIn("event.action === 'confirm-delivery'", source)
        self.assertIn("credentials: 'same-origin'", source)
        self.assertIn("confirmationToken", source)
        self.assertIn("actions: Array.isArray(data.actions)", source)

    def test_service_worker_displays_push_even_when_app_is_visible(self):
        source = self.requester_client.get(
            "/service-worker.js", HTTP_HOST="requests.rplwms.com"
        ).content.decode()
        self.assertIn("self.registration.showNotification", source)
        self.assertNotIn("visibilityState === 'visible'", source)

    @patch("inventory.push.deliver_push_deliveries")
    def test_request_edit_notifies_warehouse_with_change_summary(self, deliver):
        from .push import _payload
        from .services import update_material_request

        with self.captureOnCommitCallbacks(execute=True):
            updated = update_material_request(
                self.material_request,
                requestor_name="Updated Requester",
                building_room="B2",
                location="North Dock",
                notes="Handle carefully",
                delivery_at=self.material_request.delivery_at,
                lines=[{
                    "item": self.material_request.lines.get().item,
                    "quantity": 3,
                    "notes": "Bring cart",
                }],
                actor=self.requester,
            )

        event = updated.events.get(event_type=MaterialRequestEvent.EventType.UPDATED)
        self.assertIn("Requestor: Delivery Requester → Updated Requester", event.change_summary)
        self.assertIn("Building / room: B1 → B2", event.change_summary)
        self.assertIn("Location: Dock → North Dock", event.change_summary)
        self.assertIn("STATUS-1 quantity: 1 → 3", event.change_summary)
        deliveries = PushDelivery.objects.filter(event=event)
        self.assertEqual(list(deliveries.values_list("subscription_id", flat=True)), [self.warehouse_subscription.pk])
        payload = _payload(deliveries.get())
        self.assertEqual(payload["title"], f"{updated.request_number} updated")
        self.assertIn("Location: Dock → North Dock", payload["body"])
        deliver.assert_called_once()

    @patch("inventory.push.deliver_push_deliveries")
    def test_wms_event_feed_excludes_requester_status_notifications(self, deliver):
        cursor = MaterialRequestEvent.objects.order_by("-pk").values_list("pk", flat=True).first() or 0
        from .services import update_pick_ticket_status

        event = update_pick_ticket_status(
            self.material_request.pick_ticket, PickTicket.Status.PICKED, actor=self.warehouse
        )
        response = self.warehouse_client.get(
            reverse("material_request_events"), {"cursor": cursor}, HTTP_HOST="bbx.rplwms.com"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["events"], [])
        self.assertEqual(response.json()["cursor"], event.pk)

    @patch("inventory.push.deliver_push_deliveries")
    def test_delete_notifies_warehouse_and_preserves_the_outbox_event(self, deliver):
        from .models import MaterialRequest
        from .push import _payload
        from .services import delete_material_request

        request_pk = self.material_request.pk
        request_number = self.material_request.request_number
        ticket_number = self.material_request.pick_ticket.ticket_number
        cursor = MaterialRequestEvent.objects.order_by("-pk").values_list("pk", flat=True).first() or 0
        with self.captureOnCommitCallbacks(execute=True):
            event = delete_material_request(self.material_request, actor=self.requester)

        self.assertFalse(MaterialRequest.objects.filter(pk=request_pk).exists())
        event.refresh_from_db()
        self.assertEqual(event.event_type, MaterialRequestEvent.EventType.DELETED)
        self.assertIsNone(event.material_request_id)
        self.assertEqual(event.request_number_snapshot, request_number)
        self.assertEqual(event.ticket_number_snapshot, ticket_number)
        deliveries = PushDelivery.objects.filter(event=event)
        self.assertEqual(
            list(deliveries.values_list("subscription_id", flat=True)),
            [self.warehouse_subscription.pk],
        )
        payload = _payload(deliveries.get())
        self.assertEqual(payload["title"], f"{request_number} deleted")
        self.assertIn(ticket_number, payload["body"])
        response = self.warehouse_client.get(
            reverse("material_request_events"), {"cursor": cursor}, HTTP_HOST="bbx.rplwms.com"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["events"][0]["event_type"], MaterialRequestEvent.EventType.DELETED)
        self.assertEqual(response.json()["events"][0]["request_number"], request_number)
        deliver.assert_called_once()
