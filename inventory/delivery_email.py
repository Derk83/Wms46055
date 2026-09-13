import logging
from urllib.parse import urlencode

from django.conf import settings
from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import MaterialRequestEvent

logger = logging.getLogger(__name__)
TOKEN_SALT = "inventory.ready-delivery-email.v1"
TOKEN_MAX_AGE = 7 * 24 * 60 * 60


def make_delivery_response_token(event):
    request = event.material_request
    return signing.dumps(
        {
            "request_id": request.pk,
            "event_id": event.pk,
            "email": request.requestor_email,
            "delivery_at": request.delivery_at.isoformat() if request.delivery_at else "",
        },
        salt=TOKEN_SALT,
        compress=True,
    )


def read_delivery_response_token(token):
    return signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)


def delivery_response_url(event):
    token = make_delivery_response_token(event)
    base_url = settings.REQUEST_PORTAL_BASE_URL.rstrip("/")
    path = reverse("material_request_email_delivery_response")
    return f"{base_url}{path}?{urlencode({'token': token})}"


def send_ready_for_delivery_email(event_id):
    event = MaterialRequestEvent.objects.select_related(
        "material_request__pick_ticket"
    ).get(pk=event_id)
    request = event.material_request
    if not request or not request.requestor_email:
        return False

    action_url = delivery_response_url(event)
    scheduled = (
        timezone.localtime(request.delivery_at).strftime("%b %-d, %Y at %-I:%M %p")
        if request.delivery_at else "Not scheduled"
    )
    subject = f"{'🚨 URGENT: ' if request.urgent else ''}{request.request_number} is ready for delivery"
    text = (
        f"Your material request {request.request_number} is ready for delivery.\n\n"
        f"Scheduled delivery: {scheduled}\n\n"
        "Use the secure link below to Confirm ready for delivery or to "
        "Request a different delivery time:\n"
        f"{action_url}\n\nThis secure link expires in 7 days and can be used once."
    )
    html = f"""
      <p>Your material request <strong>{request.request_number}</strong> is ready for delivery.</p>
      <p><strong>Scheduled delivery:</strong> {scheduled}</p>
      <p><a href="{action_url}" style="display:inline-block;padding:12px 18px;background:#1769aa;color:#fff;text-decoration:none;border-radius:6px">Confirm or request a different delivery time</a></p>
      <p>This secure link expires in 7 days and can be used once.</p>
    """
    message = EmailMultiAlternatives(
        subject,
        text,
        settings.DEFAULT_FROM_EMAIL,
        [request.requestor_email],
    )
    message.attach_alternative(html, "text/html")
    try:
        return bool(message.send(fail_silently=False))
    except Exception:
        logger.exception("Ready-for-delivery email failed for event %s", event_id)
        return False


def queue_ready_for_delivery_email(event):
    transaction.on_commit(lambda event_id=event.pk: send_ready_for_delivery_email(event_id))
