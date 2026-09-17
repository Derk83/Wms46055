import json
import logging
from urllib.parse import urlparse
from datetime import timedelta

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from pywebpush import webpush

from .access import MANAGER_GROUP_NAMES, user_is_manager
from .models import MaterialRequest, MaterialRequestEvent, PickTicket, PushDelivery, PushSubscription

logger = logging.getLogger(__name__)
MAX_DELIVERY_ATTEMPTS = 5
DEFAULT_PUSH_ENDPOINT_HOST_SUFFIXES = (
    "fcm.googleapis.com",
    "push.services.mozilla.com",
    "push.apple.com",
    "notify.windows.com",
)


def push_endpoint_is_allowed(endpoint):
    """Restrict browser-supplied endpoints to known Web Push providers."""
    try:
        parsed = urlparse(endpoint)
        host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
    except (TypeError, ValueError):
        return False
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        return False
    if port not in (None, 443):
        return False
    suffixes = getattr(
        settings, "WEB_PUSH_ALLOWED_ENDPOINT_HOST_SUFFIXES", DEFAULT_PUSH_ENDPOINT_HOST_SUFFIXES
    )
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in suffixes)


def _vapid_subject():
    subject = getattr(settings, "WEBPUSH_VAPID_SUBJECT", "")
    if subject.startswith(("http://", "https://")):
        return subject.rstrip("/")
    return subject


def push_is_configured():
    return bool(
        getattr(settings, "WEBPUSH_VAPID_PUBLIC_KEY", "")
        and getattr(settings, "WEBPUSH_VAPID_PRIVATE_KEY", "")
        and _vapid_subject()
    )


def _permission_subscriptions(codename):
    direct_permission = Q(
        user__user_permissions__content_type__app_label="inventory",
        user__user_permissions__codename=codename,
    )
    group_permission = Q(
        user__groups__permissions__content_type__app_label="inventory",
        user__groups__permissions__codename=codename,
    )
    return PushSubscription.objects.filter(
        Q(user__is_superuser=True) | direct_permission | group_permission,
        user__is_active=True,
        enabled=True,
    ).distinct()


def _authorized_subscriptions(event):
    if event.event_type == MaterialRequestEvent.EventType.STATUS_CHANGED:
        return _permission_subscriptions("access_material_request_portal").filter(
            user_id=event.material_request.creator_id,
            audience=PushSubscription.Audience.REQUEST_PORTAL,
        )
    subscriptions = _permission_subscriptions("view_all_materialrequests").filter(
        audience=PushSubscription.Audience.WMS
    )
    if event.event_type == MaterialRequestEvent.EventType.CREATED:
        subscriptions = subscriptions.filter(user__is_superuser=False).exclude(
            user__groups__name__in=MANAGER_GROUP_NAMES
        )
    return subscriptions.distinct()


def queue_material_request_push(event):
    """Create durable outbox rows inside the request transaction, then send after commit."""
    if not push_is_configured():
        return []
    deliveries = [
        PushDelivery(event=event, subscription=subscription)
        for subscription in _authorized_subscriptions(event)
    ]
    if not deliveries:
        return []
    created = PushDelivery.objects.bulk_create(deliveries, ignore_conflicts=True)
    delivery_ids = list(
        PushDelivery.objects.filter(event=event, status=PushDelivery.Status.PENDING)
        .values_list("pk", flat=True)
    )
    if delivery_ids:
        transaction.on_commit(lambda: deliver_push_deliveries(delivery_ids), robust=True)
    return created


def _payload(delivery):
    event = delivery.event
    if event.event_type == MaterialRequestEvent.EventType.DELETED:
        return {
            "title": f"{event.request_number_snapshot} deleted",
            "body": (
                f"{event.request_number_snapshot} and linked ticket "
                f"{event.ticket_number_snapshot} were deleted by the requestor."
            ),
            "url": reverse("material_request_board"),
            "eventId": event.pk,
            "tag": f"material-request-deleted-{event.pk}",
        }

    material_request = event.material_request
    payload = {
        "title": "New material request",
        "body": f"{material_request.request_number} from {material_request.requestor_name or 'a requestor'}",
        "url": reverse("material_request_detail", args=[material_request.pk]),
        "eventId": delivery.event_id,
        "requestId": material_request.pk,
        "urgent": material_request.urgent,
        "tag": f"material-request-{material_request.pk}",
    }
    claim_available = (
        event.event_type == MaterialRequestEvent.EventType.CREATED
        and MaterialRequest.objects.filter(
            pk=material_request.pk,
            assigned_to__isnull=True,
            pick_ticket__status=PickTicket.Status.OPEN,
        ).exists()
    )
    if (
        claim_available
        and delivery.subscription.user.has_perm("inventory.change_pickticket")
        and not user_is_manager(delivery.subscription.user)
    ):
        payload.update({
            "actions": [{"action": "accept-request", "title": "Accept request"}],
            "claimUrl": reverse("material_request_claim_push", args=[material_request.pk]),
            "claimToken": signing.dumps(
                {
                    "request_id": material_request.pk,
                    "user_id": delivery.subscription.user_id,
                    "event_id": event.pk,
                },
                salt="material-request-warehouse-claim",
                compress=True,
            ),
            "requireInteraction": True,
        })
    if event.event_type == MaterialRequestEvent.EventType.UPDATED:
        payload.update({
            "title": f"{material_request.request_number} updated",
            "body": event.change_summary[:500],
            "tag": f"material-request-{material_request.pk}-update-{event.pk}",
        })
        if material_request.assigned_to_id:
            payload.update({
                "claimedBy": (
                    material_request.assigned_to.get_full_name()
                    or material_request.assigned_to.get_username()
                ),
                "tag": f"material-request-{material_request.pk}",
            })
    elif event.event_type == MaterialRequestEvent.EventType.STATUS_CHANGED:
        status_label = dict(material_request.pick_ticket.Status.choices).get(
            event.new_status, event.new_status
        )
        payload.update({
            "title": "Order status updated",
            "body": f"{material_request.request_number} is now {status_label}.",
            "tag": f"material-request-{material_request.pk}-status",
        })
        if event.new_status == material_request.pick_ticket.Status.RECEIVED:
            payload.update({
                "title": "Order ready for delivery",
                "body": f"{material_request.request_number} is ready. Are you ready to accept delivery?",
                "actions": [{"action": "confirm-delivery", "title": "Yes, I'm ready"}],
                "confirmUrl": reverse("material_request_confirm_ready", args=[material_request.pk]),
                "confirmationToken": signing.dumps(
                    {
                        "request_id": material_request.pk,
                        "user_id": delivery.subscription.user_id,
                        "event_id": event.pk,
                    },
                    salt="material-request-delivery-readiness",
                    compress=True,
                ),
                "requireInteraction": True,
            })
    elif event.event_type == MaterialRequestEvent.EventType.DELIVERY_ACCEPTANCE_CONFIRMED:
        payload.update({
            "title": "Requester ready for delivery",
            "body": f"{material_request.request_number}: the requester confirmed they are ready to accept delivery.",
            "tag": f"material-request-{material_request.pk}-acceptance",
        })
    elif event.event_type == MaterialRequestEvent.EventType.DELIVERY_NOT_READY:
        body = f"{material_request.request_number}: the requester is not ready for delivery."
        if material_request.delivery_at:
            body += f" New requested slot: {timezone.localtime(material_request.delivery_at).strftime('%b %-d at %-I:%M %p')}."
        if material_request.delivery_response_note:
            body += f" Note: {material_request.delivery_response_note}"
        payload.update({
            "title": "Requester not ready",
            "body": body,
            "tag": f"material-request-{material_request.pk}-not-ready",
        })
    if material_request.urgent:
        payload["title"] = f"🚨 URGENT: {payload['title']}"
        payload["urgent"] = True
        payload["requireInteraction"] = True
    return payload


def _still_authorized(subscription, event):
    if (
        event.event_type == MaterialRequestEvent.EventType.CREATED
        and user_is_manager(subscription.user)
    ):
        return False
    if event.event_type == MaterialRequestEvent.EventType.STATUS_CHANGED:
        permission = "inventory.access_material_request_portal"
        intended_recipient = (
            subscription.user_id == event.material_request.creator_id
            and subscription.audience == PushSubscription.Audience.REQUEST_PORTAL
        )
    else:
        permission = "inventory.view_all_materialrequests"
        intended_recipient = subscription.audience == PushSubscription.Audience.WMS
    return (
        subscription.enabled
        and bool(subscription.session_key)
        and Session.objects.filter(
            session_key=subscription.session_key, expire_date__gt=timezone.now()
        ).exists()
        and subscription.user.is_active
        and intended_recipient
        and subscription.user.has_perm(permission)
    )


def deliver_push_deliveries(delivery_ids=None):
    """Claim and deliver outbox rows with a five-minute crash-recovery lease."""
    result = {"sent": 0, "failed": 0, "expired": 0}
    if not push_is_configured():
        return result

    if delivery_ids is None:
        delivery_ids = list(
            PushDelivery.objects.filter(
                status__in=[PushDelivery.Status.PENDING, PushDelivery.Status.RETRY, PushDelivery.Status.PROCESSING],
                next_attempt_at__lte=timezone.now(),
                attempts__lt=MAX_DELIVERY_ATTEMPTS,
            ).order_by("next_attempt_at", "pk").values_list("pk", flat=True)
        )

    for delivery_id in delivery_ids:
        now = timezone.now()
        claimable = (
            Q(status__in=[PushDelivery.Status.PENDING, PushDelivery.Status.RETRY], next_attempt_at__lte=now)
            | Q(status=PushDelivery.Status.PROCESSING, next_attempt_at__lte=now)
        )
        claimed = PushDelivery.objects.filter(pk=delivery_id, attempts__lt=MAX_DELIVERY_ATTEMPTS).filter(
            claimable
        ).update(status=PushDelivery.Status.PROCESSING, next_attempt_at=now + timedelta(minutes=5))
        if not claimed:
            continue

        try:
            delivery = PushDelivery.objects.select_related(
                "subscription__user", "event__material_request"
            ).get(pk=delivery_id)
        except PushDelivery.DoesNotExist:
            continue
        subscription = delivery.subscription
        if subscription is None:
            delivery.status = PushDelivery.Status.EXPIRED
            delivery.last_error = "Push subscription no longer exists."
            delivery.save(update_fields=["status", "last_error"])
            continue
        if not _still_authorized(subscription, delivery.event):
            delivery.status = PushDelivery.Status.CANCELLED
            delivery.last_error = "User is no longer authorized for material requests."
            delivery.save(update_fields=["status", "last_error"])
            continue
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                },
                data=json.dumps(_payload(delivery)),
                vapid_private_key=settings.WEBPUSH_VAPID_PRIVATE_KEY,
                vapid_claims={"sub": _vapid_subject()},
                ttl=300,
            )
        except Exception as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in (404, 410):
                logger.info("Removing expired Web Push endpoint for user %s", subscription.user_id)
                delivery.status = PushDelivery.Status.EXPIRED
                delivery.attempts += 1
                delivery.last_error = f"Push endpoint returned HTTP {status_code}."
                delivery.save(update_fields=["status", "attempts", "last_error"])
                subscription.delete()
                result["expired"] += 1
                continue
            delivery.attempts += 1
            delivery.last_error = str(exc)[:500]
            subscription.failure_count += 1
            subscription.last_error = str(exc)[:500]
            subscription.save(update_fields=["failure_count", "last_error", "updated_at"])
            if delivery.attempts >= MAX_DELIVERY_ATTEMPTS:
                delivery.status = PushDelivery.Status.FAILED
            else:
                delivery.status = PushDelivery.Status.RETRY
                delivery.next_attempt_at = timezone.now() + timedelta(
                    minutes=min(60, 2 ** delivery.attempts)
                )
            delivery.save(
                update_fields=["attempts", "last_error", "status", "next_attempt_at"]
            )
            result["failed"] += 1
            logger.warning("Web Push delivery %s failed: %s", delivery.pk, exc)
            continue

        delivery.status = PushDelivery.Status.SENT
        delivery.attempts += 1
        delivery.sent_at = timezone.now()
        delivery.last_error = ""
        delivery.save(update_fields=["status", "attempts", "sent_at", "last_error"])
        subscription.failure_count = 0
        subscription.last_error = ""
        subscription.last_success_at = timezone.now()
        subscription.save(
            update_fields=["failure_count", "last_error", "last_success_at", "updated_at"]
        )
        result["sent"] += 1
    return result
