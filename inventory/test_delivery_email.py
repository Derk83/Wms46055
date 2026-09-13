import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.core import mail
from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import InventoryItem, MaterialRequestEvent, PickTicket, PushDelivery, PushSubscription
from .services import create_material_request, update_pick_ticket_status


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="RPL Warehouse <no-reply@rplwms.com>",
    WEBPUSH_VAPID_PUBLIC_KEY="public",
    WEBPUSH_VAPID_PRIVATE_KEY="private",
    WEBPUSH_VAPID_SUBJECT="mailto:warehouse@example.com",
)
class DeliveryEmailWorkflowTests(TestCase):
    def test_email_smtp_does_not_use_ipv6_loopback(self):
        self.assertNotEqual(settings.EMAIL_HOST, "::1")

    def setUp(self):
        self.requester = User.objects.create_user("requester-email", password="pw")
        self.requester.user_permissions.add(
            Permission.objects.get(codename="access_material_request_portal"),
            Permission.objects.get(codename="view_materialrequest"),
        )
        self.warehouse = User.objects.create_user("warehouse-email", password="pw")
        self.warehouse.user_permissions.add(
            Permission.objects.get(codename="view_materialrequest"),
            Permission.objects.get(codename="view_all_materialrequests"),
        )
        self.requester_client = Client()
        self.requester_client.force_login(self.requester)
        self.warehouse_client = Client()
        self.warehouse_client.force_login(self.warehouse)
        self.requester_subscription = PushSubscription.objects.create(
            user=self.requester,
            endpoint="https://push.example.test/requester-email",
            p256dh="key", auth="auth",
            audience=PushSubscription.Audience.REQUEST_PORTAL,
            session_key=self.requester_client.session.session_key,
        )
        self.warehouse_subscription = PushSubscription.objects.create(
            user=self.warehouse,
            endpoint="https://push.example.test/warehouse-email",
            p256dh="wkey", auth="wauth",
            audience=PushSubscription.Audience.WMS,
            session_key=self.warehouse_client.session.session_key,
        )
        self.item = InventoryItem.objects.create(
            part_number="EMAIL-1", name="Email item", quantity_on_hand=10
        )
        with patch("inventory.push.deliver_push_deliveries"):
            self.material_request = create_material_request(
                creator=self.requester,
                requestor_name="Email Requester",
                requestor_email="requester@example.com",
                building_room="B1",
                location="Dock",
                delivery_at=timezone.now() + timedelta(hours=1),
                notes="",
                lines=[{"item": self.item, "quantity": 1, "notes": ""}],
            )

    def _mark_ready(self):
        with patch("inventory.push.deliver_push_deliveries"):
            with self.captureOnCommitCallbacks(execute=True):
                return update_pick_ticket_status(
                    self.material_request.pick_ticket, PickTicket.Status.RECEIVED, actor=self.warehouse
                )

    def test_request_form_includes_required_requester_email(self):
        from .forms import MaterialRequestForm

        self.assertIn("requestor_email", MaterialRequestForm().fields)
        self.assertTrue(MaterialRequestForm().fields["requestor_email"].required)

    def test_detail_does_not_prefill_optional_reschedule_with_existing_slot(self):
        response = self.requester_client.get(
            reverse("material_request_detail", args=[self.material_request.pk]),
            HTTP_HOST="requests.rplwms.com",
        )
        self.assertEqual(response.status_code, 200)
        form = response.context["delivery_response_form"]
        self.assertIsNone(form.initial.get("delivery_at"))

    def test_not_ready_without_reschedule_creates_one_warehouse_delivery(self):
        self._mark_ready()
        with patch("inventory.push.deliver_push_deliveries") as deliver:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.requester_client.post(
                    reverse("material_request_delivery_response", args=[self.material_request.pk]),
                    {"response": "not_ready", "note": "Please call first"},
                    HTTP_HOST="requests.rplwms.com",
                )
        self.assertEqual(response.status_code, 302)
        event = self.material_request.events.get(
            event_type=MaterialRequestEvent.EventType.DELIVERY_NOT_READY
        )
        self.assertEqual(
            list(PushDelivery.objects.filter(event=event).values_list("subscription_id", flat=True)),
            [self.warehouse_subscription.pk],
        )
        deliver.assert_called_once()

    def test_ready_status_sends_email_with_safe_response_link(self):
        self._mark_ready()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["requester@example.com"])
        self.assertIn("ready for delivery", message.subject.lower())
        self.assertIn("Confirm ready for delivery", message.body)
        self.assertIn("Request a different delivery time", message.body)
        self.assertIn("https://requests.rplwms.com/material-requests/", message.body)

        url = re.search(r"https://requests\.rplwms\.com\S+", message.body).group(0)
        path = url.removeprefix("https://requests.rplwms.com")
        anonymous = Client()
        page = anonymous.get(path, HTTP_HOST="requests.rplwms.com")
        self.assertEqual(page.status_code, 200)
        self.material_request.refresh_from_db()
        self.assertIsNone(self.material_request.delivery_acceptance_confirmed_at)

    def test_urgent_ready_email_has_urgent_subject(self):
        self.material_request.urgent = True
        self.material_request.save(update_fields=["urgent"])
        self._mark_ready()
        self.assertTrue(mail.outbox[0].subject.startswith("🚨 URGENT:"))

    def test_signed_email_confirm_post_notifies_warehouse_once(self):
        self._mark_ready()
        url = re.search(r"https://requests\.rplwms\.com\S+", mail.outbox[0].body).group(0)
        path = url.removeprefix("https://requests.rplwms.com")
        anonymous = Client()
        with patch("inventory.push.deliver_push_deliveries"):
            with self.captureOnCommitCallbacks(execute=True):
                response = anonymous.post(path, {"response": "ready"}, HTTP_HOST="requests.rplwms.com")
        self.assertRedirects(response, "/material-requests/email-response/?receipt=1")
        event = self.material_request.events.get(
            event_type=MaterialRequestEvent.EventType.DELIVERY_ACCEPTANCE_CONFIRMED
        )
        self.assertEqual(PushDelivery.objects.filter(event=event).count(), 1)
        duplicate = anonymous.post(path, {"response": "ready"}, HTTP_HOST="requests.rplwms.com")
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(self.material_request.events.filter(
            event_type=MaterialRequestEvent.EventType.DELIVERY_ACCEPTANCE_CONFIRMED
        ).count(), 1)

    def test_signed_email_confirm_post_works_without_csrf_cookie(self):
        """Email capability links must work in privacy browsers that discard cookies."""
        self._mark_ready()
        url = re.search(r"https://requests\.rplwms\.com\S+", mail.outbox[0].body).group(0)
        path = url.removeprefix("https://requests.rplwms.com")
        privacy_client = Client(enforce_csrf_checks=True)

        with patch("inventory.push.deliver_push_deliveries"):
            with self.captureOnCommitCallbacks(execute=True):
                response = privacy_client.post(
                    path,
                    {"response": "ready"},
                    HTTP_HOST="requests.rplwms.com",
                    HTTP_ORIGIN="https://requests.rplwms.com",
                )

        self.assertRedirects(response, "/material-requests/email-response/?receipt=1")
        self.assertTrue(self.material_request.events.filter(
            event_type=MaterialRequestEvent.EventType.DELIVERY_ACCEPTANCE_CONFIRMED
        ).exists())

    def test_signed_email_reschedule_notifies_warehouse(self):
        self._mark_ready()
        url = re.search(r"https://requests\.rplwms\.com\S+", mail.outbox[0].body).group(0)
        path = url.removeprefix("https://requests.rplwms.com")
        new_slot = timezone.localtime(timezone.now() + timedelta(days=2)).replace(
            hour=14, minute=30, second=0, microsecond=0
        )
        with patch("inventory.push.deliver_push_deliveries"):
            with self.captureOnCommitCallbacks(execute=True):
                response = Client().post(path, {
                    "response": "not_ready",
                    "delivery_at_0": new_slot.strftime("%Y-%m-%d"),
                    "delivery_at_1": new_slot.strftime("%H:%M"),
                    "note": "Afternoon is better",
                }, HTTP_HOST="requests.rplwms.com")
        self.assertRedirects(response, "/material-requests/email-response/?receipt=1")
        self.material_request.refresh_from_db()
        self.assertEqual(
            timezone.localtime(self.material_request.delivery_at),
            new_slot,
        )
        event = self.material_request.events.get(
            event_type=MaterialRequestEvent.EventType.DELIVERY_NOT_READY
        )
        self.assertEqual(PushDelivery.objects.filter(event=event).count(), 1)
