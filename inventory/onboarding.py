"""Security-sensitive service layer for material-request portal onboarding."""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import (
    ManagedGroupRole,
    PortalAccessAuditEvent,
    PortalAccessRequest,
    PortalAccessThrottle,
    PortalAccessToken,
)

logger = logging.getLogger(__name__)


def normalize_corporate_email(value: str) -> str:
    email = (value or "").strip().casefold()
    local, separator, domain = email.rpartition("@")
    if not separator or not local or domain != "blackbox.com" or len(email) > 150:
        raise ValueError("A valid @blackbox.com email address is required.")
    return email


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def audit(access_request, event_type, *, actor=None, **detail):
    return PortalAccessAuditEvent.objects.create(
        request=access_request, event_type=event_type, actor=actor, detail=detail
    )


def issue_token(access_request, purpose, *, ttl=None):
    now = timezone.now()
    if ttl is None:
        if purpose == PortalAccessToken.Purpose.VERIFY_EMAIL:
            hours = getattr(settings, "PORTAL_VERIFY_TOKEN_HOURS", 24)
        elif purpose == PortalAccessToken.Purpose.MORE_INFO:
            hours = getattr(settings, "PORTAL_MORE_INFO_TOKEN_HOURS", 72)
        else:
            hours = getattr(settings, "PORTAL_SETUP_TOKEN_HOURS", 48)
        ttl = timedelta(hours=hours)
    PortalAccessToken.objects.filter(
        request=access_request, purpose=purpose, used_at__isnull=True, revoked_at__isnull=True
    ).update(revoked_at=now)
    raw = secrets.token_urlsafe(32)
    PortalAccessToken.objects.create(
        request=access_request, purpose=purpose, digest=digest(raw), expires_at=now + ttl
    )
    audit(access_request, "token_issued", purpose=purpose)
    return raw


def valid_token(raw, purpose, *, lock=False):
    queryset = PortalAccessToken.objects.select_related("request")
    if lock:
        queryset = queryset.select_for_update()
    return queryset.filter(
        digest=digest(raw), purpose=purpose, used_at__isnull=True,
        revoked_at__isnull=True, expires_at__gt=timezone.now(),
    ).first()


def consume_token(raw, purpose):
    token = valid_token(raw, purpose, lock=True)
    if token is None:
        return None
    token.used_at = timezone.now()
    token.save(update_fields=["used_at"])
    return token


def email_context(access_request, raw=None):
    return {
        "access_request": access_request,
        "token": raw,
        "request_portal_url": settings.REQUEST_PORTAL_BASE_URL.rstrip("/"),
        "wms_url": getattr(settings, "APP_URL", "https://bbx.rplwms.com").rstrip("/"),
    }


def send_template(subject, recipient, template, context, *, access_request=None):
    try:
        send_mail(
            subject,
            render_to_string(f"inventory/email/{template}.txt", context),
            settings.DEFAULT_FROM_EMAIL,
            [recipient],
        )
    except Exception:
        logger.exception("Portal onboarding email delivery failed: template=%s", template)
        if access_request is not None:
            audit(access_request, "email_delivery_failed", template=template)
        return False
    return True


def send_verification(access_request, raw):
    context = email_context(access_request, raw)
    context["verification_url"] = context["request_portal_url"] + reverse(
        "portal_access_verify", kwargs={"token": raw}, urlconf="inventory.request_urls"
    )
    send_template(
        "Verify your RPL Material Request access", access_request.email,
        "portal_verify", context, access_request=access_request,
    )


def send_setup(access_request, raw, *, reminder=False):
    context = email_context(access_request, raw)
    context["setup_url"] = context["request_portal_url"] + reverse(
        "portal_access_setup", kwargs={"token": raw}, urlconf="inventory.request_urls"
    )
    subject = (
        "Reminder: set up your RPL Material Request account"
        if reminder else "Set up your RPL Material Request account"
    )
    template = "portal_reminder" if reminder else "portal_setup"
    return send_template(
        subject, access_request.email,
        template, context, access_request=access_request,
    )


def send_more_info(access_request, raw):
    context = email_context(access_request, raw)
    context["response_url"] = context["request_portal_url"] + reverse(
        "portal_access_more_info", kwargs={"token": raw}, urlconf="inventory.request_urls"
    )
    context["message"] = "A manager needs more information before reviewing your request."
    context["notes"] = access_request.review_notes
    send_template(
        "More information is needed for your RPL Material Request access request",
        access_request.email, "portal_decision", context, access_request=access_request,
    )


def reviewer_emails():
    permission = Permission.objects.get(
        content_type__app_label="inventory", codename="review_portalaccessrequest"
    )
    direct = User.objects.filter(is_active=True, user_permissions=permission)
    grouped = User.objects.filter(is_active=True, groups__permissions=permission)
    return list((direct | grouped).exclude(email="").values_list("email", flat=True).distinct())


def notify_reviewers(access_request):
    recipients = reviewer_emails()
    if not recipients:
        audit(access_request, "manager_notification_skipped", reason="no_recipients")
        return False
    try:
        send_mail(
            "Material portal access awaiting manager review",
            "A verified access request is waiting in the WMS manager queue.",
            settings.DEFAULT_FROM_EMAIL,
            recipients,
        )
    except Exception:
        logger.exception("Portal onboarding manager notification failed")
        audit(access_request, "email_delivery_failed", template="manager_notification")
        return False
    return True


def submit_access_request(
    *, email, full_name, position="", contact_number="", department="",
    project_jobsite="", sponsor="", business_reason="", acknowledge=True,
    website="",
):
    normalized = normalize_corporate_email(email)
    if User.objects.filter(username__iexact=normalized).exists() or User.objects.filter(email__iexact=normalized).exists():
        return None
    with transaction.atomic():
        access_request, created = PortalAccessRequest.objects.select_for_update().get_or_create(
            email=normalized,
            defaults={
                "acknowledged_at": timezone.now() if acknowledge else None,
                "full_name": full_name.strip(),
                "position": position.strip(),
                "contact_number": contact_number.strip(),
                "department": department.strip(),
                "project_jobsite": project_jobsite.strip(),
                "sponsor": sponsor.strip(),
                "business_reason": business_reason.strip(),
            },
        )
        raw = None
        if access_request.status == PortalAccessRequest.Status.EXPIRED and not access_request.user_id:
            # This is a fresh lifecycle. Reset the timestamp used by the
            # unverified expiry job so reapplications receive the full window.
            access_request.created_at = timezone.now()
            access_request.status = PortalAccessRequest.Status.UNVERIFIED
            access_request.verified_at = None
            access_request.reviewed_at = None
            access_request.reviewed_by = None
            access_request.review_notes = ""
            access_request.reminder_sent_at = None
            access_request.access_expires_at = None
        if access_request.status == PortalAccessRequest.Status.UNVERIFIED:
            if not created:
                access_request.acknowledged_at = timezone.now() if acknowledge else None
                access_request.full_name = full_name.strip()
                access_request.position = position.strip()
                access_request.contact_number = contact_number.strip()
                access_request.department = department.strip()
                access_request.project_jobsite = project_jobsite.strip()
                access_request.sponsor = sponsor.strip()
                access_request.business_reason = business_reason.strip()
                access_request.save(update_fields=[
                    "acknowledged_at", "full_name", "position", "contact_number", "department",
                    "project_jobsite", "sponsor", "business_reason", "status", "verified_at",
                    "reviewed_at", "reviewed_by", "review_notes", "reminder_sent_at",
                    "access_expires_at", "created_at", "updated_at",
                ])
            raw = issue_token(access_request, PortalAccessToken.Purpose.VERIFY_EMAIL)
            audit(access_request, "request_submitted" if created else "verification_resent")
            transaction.on_commit(lambda: send_verification(access_request, raw))
    return access_request


def throttle_allowed(action, key, *, limit=None, window=None):
    limit = limit or getattr(settings, "PORTAL_RATE_LIMIT", 5)
    window = window or timedelta(minutes=getattr(settings, "PORTAL_RATE_WINDOW_MINUTES", 15))
    now = timezone.now()
    key_hash = digest(key)
    with transaction.atomic():
        row, _ = PortalAccessThrottle.objects.select_for_update().get_or_create(
            action=action, key_digest=key_hash,
            defaults={"window_started_at": now, "count": 0},
        )
        if row.window_started_at <= now - window:
            row.window_started_at, row.count = now, 0
        row.count += 1
        row.save(update_fields=["window_started_at", "count"])
        return row.count <= limit


def verify_email(raw):
    with transaction.atomic():
        token = consume_token(raw, PortalAccessToken.Purpose.VERIFY_EMAIL)
        if token is None:
            return None
        access_request = PortalAccessRequest.objects.select_for_update().get(pk=token.request_id)
        if access_request.status != PortalAccessRequest.Status.UNVERIFIED:
            return None
        access_request.status = PortalAccessRequest.Status.PENDING_REVIEW
        access_request.verified_at = timezone.now()
        access_request.save(update_fields=["status", "verified_at", "updated_at"])
        audit(access_request, "email_verified")
        transaction.on_commit(lambda: notify_reviewers(access_request))
        return access_request


def resubmit_more_info(raw, cleaned_data):
    with transaction.atomic():
        token = consume_token(raw, PortalAccessToken.Purpose.MORE_INFO)
        if token is None:
            return None
        access_request = PortalAccessRequest.objects.select_for_update().get(pk=token.request_id)
        if access_request.status != PortalAccessRequest.Status.MORE_INFO:
            return None
        for field in (
            "full_name", "position", "contact_number", "department",
            "project_jobsite", "sponsor", "business_reason",
        ):
            setattr(access_request, field, (cleaned_data.get(field) or "").strip())
        access_request.status = PortalAccessRequest.Status.PENDING_REVIEW
        access_request.save(update_fields=[
            "full_name", "position", "contact_number", "department", "project_jobsite",
            "sponsor", "business_reason", "status", "updated_at",
        ])
        audit(access_request, "more_info_received")
        transaction.on_commit(lambda: notify_reviewers(access_request))
        return access_request


def resend_setup(access_request, reviewer):
    with transaction.atomic():
        access_request = PortalAccessRequest.objects.select_for_update().get(pk=access_request.pk)
        if access_request.status != PortalAccessRequest.Status.APPROVED_SETUP or not access_request.user_id:
            raise ValueError("A setup invitation can only be resent for an approved inactive account.")
        raw = issue_token(access_request, PortalAccessToken.Purpose.SET_PASSWORD)
        access_request.reminder_sent_at = None
        access_request.save(update_fields=["reminder_sent_at", "updated_at"])
        audit(access_request, "setup_resent", actor=reviewer)
        transaction.on_commit(lambda: send_setup(access_request, raw))
        return raw


def revoke_setup(access_request, reviewer):
    with transaction.atomic():
        access_request = PortalAccessRequest.objects.select_for_update().get(pk=access_request.pk)
        if access_request.status != PortalAccessRequest.Status.APPROVED_SETUP:
            raise ValueError("Only a pending setup invitation can be revoked.")
        PortalAccessToken.objects.filter(
            request=access_request,
            purpose=PortalAccessToken.Purpose.SET_PASSWORD,
            used_at__isnull=True,
            revoked_at__isnull=True,
        ).update(revoked_at=timezone.now())
        audit(access_request, "setup_revoked", actor=reviewer)


def approve_request(access_request, reviewer, *, notes="", access_expires_on=None):
    with transaction.atomic():
        access_request = PortalAccessRequest.objects.select_for_update().get(pk=access_request.pk)
        if access_request.status != PortalAccessRequest.Status.PENDING_REVIEW:
            raise ValueError("Only pending requests can be approved.")
        role = ManagedGroupRole.objects.select_related("group").get(role_key="material_requests")
        if User.objects.filter(username__iexact=access_request.email).exists() or User.objects.filter(email__iexact=access_request.email).exists():
            raise ValueError("An account already exists for this email.")
        names = access_request.full_name.split(None, 1)
        user = User(username=access_request.email, email=access_request.email, is_active=False, is_staff=False)
        user.first_name = names[0][:150] if names else ""
        user.last_name = names[1][:150] if len(names) > 1 else ""
        user.set_unusable_password()
        user.save()
        user.groups.add(role.group)
        access_request.user = user
        access_request.status = PortalAccessRequest.Status.APPROVED_SETUP
        access_request.reviewed_by = reviewer
        access_request.reviewed_at = timezone.now()
        access_request.review_notes = notes.strip()
        if access_expires_on:
            expiry = datetime.combine(access_expires_on, time.max)
            access_request.access_expires_at = timezone.make_aware(expiry)
        access_request.save(update_fields=[
            "user", "status", "reviewed_by", "reviewed_at", "review_notes",
            "access_expires_at", "updated_at",
        ])
        raw = issue_token(access_request, PortalAccessToken.Purpose.SET_PASSWORD)
        audit(access_request, "approved", actor=reviewer)
        transaction.on_commit(lambda: send_setup(access_request, raw))
        return raw


def review_request(access_request, reviewer, action, notes="", access_expires_on=None):
    if action == "approve":
        return approve_request(
            access_request, reviewer, notes=notes,
            access_expires_on=access_expires_on,
        )
    if action not in {"deny", "more_info"}:
        raise ValueError("Unknown review action.")
    with transaction.atomic():
        access_request = PortalAccessRequest.objects.select_for_update().get(pk=access_request.pk)
        if access_request.status != PortalAccessRequest.Status.PENDING_REVIEW:
            raise ValueError("Only pending requests can be reviewed.")
        access_request.status = (
            PortalAccessRequest.Status.DENIED
            if action == "deny"
            else PortalAccessRequest.Status.MORE_INFO
        )
        access_request.reviewed_by = reviewer
        access_request.reviewed_at = timezone.now()
        access_request.review_notes = notes.strip()
        access_request.save(update_fields=[
            "status", "reviewed_by", "reviewed_at", "review_notes", "updated_at",
        ])
        if action == "more_info":
            raw = issue_token(access_request, PortalAccessToken.Purpose.MORE_INFO)
            audit(access_request, "more_info_requested", actor=reviewer)
            transaction.on_commit(lambda: send_more_info(access_request, raw))
        else:
            PortalAccessToken.objects.filter(
                request=access_request, used_at__isnull=True, revoked_at__isnull=True,
            ).update(revoked_at=timezone.now())
            audit(access_request, "denied", actor=reviewer)
            transaction.on_commit(lambda: send_template(
                "Your RPL Material Request access request was not approved.",
                access_request.email, "portal_decision",
                {**email_context(access_request), "message": "Your access request was not approved.", "notes": notes},
                access_request=access_request,
            ))
    return None


def activate_account(raw, password):
    with transaction.atomic():
        token = consume_token(raw, PortalAccessToken.Purpose.SET_PASSWORD)
        if token is None:
            return None
        access_request = PortalAccessRequest.objects.select_for_update().select_related("user").get(pk=token.request_id)
        if access_request.status != PortalAccessRequest.Status.APPROVED_SETUP or not access_request.user:
            return None
        if access_request.access_expires_at and access_request.access_expires_at <= timezone.now():
            return None
        user = access_request.user
        user.set_password(password)
        user.is_active = True
        user.save(update_fields=["password", "is_active"])
        access_request.status = PortalAccessRequest.Status.ACTIVE
        access_request.save(update_fields=["status", "updated_at"])
        audit(access_request, "account_activated", actor=user)
        transaction.on_commit(lambda: send_template(
            "Your RPL Material Request account is activated", access_request.email,
            "portal_activated", email_context(access_request), access_request=access_request,
        ))
        return access_request
