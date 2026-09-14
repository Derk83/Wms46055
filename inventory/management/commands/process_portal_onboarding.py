from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from inventory.models import PortalAccessRequest, PortalAccessThrottle, PortalAccessToken
from inventory.onboarding import audit, email_context, issue_token, send_setup, send_template


class Command(BaseCommand):
    help = "Expire, remind, revoke and privacy-purge portal onboarding records."

    def handle(self, *args, **options):
        now = timezone.now()
        unverified_cutoff = now - timedelta(
            hours=getattr(settings, "PORTAL_UNVERIFIED_TTL_HOURS", 48)
        )
        retention_cutoff = now - timedelta(
            days=getattr(settings, "PORTAL_RETENTION_DAYS", 180)
        )
        reminder_cutoff = now - timedelta(
            hours=getattr(settings, "PORTAL_SETUP_REMINDER_HOURS", 24)
        )
        expired_count = reminded_count = purged_count = 0

        for pk in PortalAccessRequest.objects.filter(
            status=PortalAccessRequest.Status.UNVERIFIED,
            created_at__lt=unverified_cutoff,
        ).values_list("pk", flat=True):
            if self._expire_if_still_eligible(
                pk, PortalAccessRequest.Status.UNVERIFIED, now,
                unverified_cutoff=unverified_cutoff,
            ):
                expired_count += 1

        for pk in PortalAccessRequest.objects.filter(
            status=PortalAccessRequest.Status.MORE_INFO
        ).values_list("pk", flat=True):
            if self._expire_if_still_eligible(
                pk, PortalAccessRequest.Status.MORE_INFO, now
            ):
                expired_count += 1

        for pk in PortalAccessRequest.objects.filter(
            status=PortalAccessRequest.Status.APPROVED_SETUP
        ).values_list("pk", flat=True):
            result = self._process_setup_request(pk, now, reminder_cutoff)
            expired_count += int(result == "expired")
            reminded_count += int(result == "reminded")

        for pk in PortalAccessRequest.objects.filter(
            status=PortalAccessRequest.Status.ACTIVE,
            access_expires_at__isnull=False,
            access_expires_at__lte=now,
        ).values_list("pk", flat=True):
            if self._expire_active_if_still_eligible(pk, now):
                expired_count += 1

        for pk in PortalAccessRequest.objects.filter(
            created_at__lt=retention_cutoff,
            status__in=[
                PortalAccessRequest.Status.DENIED,
                PortalAccessRequest.Status.EXPIRED,
            ],
        ).exclude(full_name="").values_list("pk", flat=True):
            if self._purge_if_still_eligible(pk, retention_cutoff):
                purged_count += 1

        PortalAccessThrottle.objects.filter(
            window_started_at__lt=now - timedelta(days=1)
        ).delete()
        self.stdout.write(self.style.SUCCESS(
            f"expired={expired_count} reminded={reminded_count} purged={purged_count}"
        ))

    def _expire_if_still_eligible(
        self, pk, expected_status, now, *, unverified_cutoff=None
    ):
        expired = None
        with transaction.atomic():
            access_request = PortalAccessRequest.objects.select_for_update().select_related("user").get(pk=pk)
            if access_request.status != expected_status:
                return False
            if (
                expected_status == PortalAccessRequest.Status.UNVERIFIED
                and (unverified_cutoff is None or access_request.created_at >= unverified_cutoff)
            ):
                return False
            if expected_status == PortalAccessRequest.Status.MORE_INFO:
                newest = access_request.tokens.filter(
                    purpose=PortalAccessToken.Purpose.MORE_INFO,
                    used_at__isnull=True,
                    revoked_at__isnull=True,
                ).order_by("-created_at").first()
                if newest is not None and newest.expires_at > now:
                    return False
            self._mark_expired_locked(access_request, now)
            expired = access_request
        self._send_expired(expired)
        return True

    def _process_setup_request(self, pk, now, reminder_cutoff):
        expired = None
        reminder = None
        with transaction.atomic():
            access_request = PortalAccessRequest.objects.select_for_update().select_related("user").get(pk=pk)
            if access_request.status != PortalAccessRequest.Status.APPROVED_SETUP:
                return None
            newest = access_request.tokens.filter(
                purpose=PortalAccessToken.Purpose.SET_PASSWORD,
                used_at__isnull=True,
                revoked_at__isnull=True,
            ).order_by("-created_at").first()
            should_expire = (
                access_request.access_expires_at is not None
                and access_request.access_expires_at <= now
            ) or newest is None or newest.expires_at <= now
            if should_expire:
                self._mark_expired_locked(access_request, now)
                expired = access_request
            elif (
                newest is not None
                and newest.created_at <= reminder_cutoff
                and access_request.reminder_sent_at is None
            ):
                raw = issue_token(access_request, PortalAccessToken.Purpose.SET_PASSWORD)
                reminder = (access_request, raw)
        if expired is not None:
            self._send_expired(expired)
            return "expired"
        if reminder is not None:
            access_request, raw = reminder
            if send_setup(access_request, raw, reminder=True):
                PortalAccessRequest.objects.filter(
                    pk=access_request.pk,
                    status=PortalAccessRequest.Status.APPROVED_SETUP,
                    reminder_sent_at__isnull=True,
                ).update(reminder_sent_at=now)
                audit(access_request, "setup_reminder_sent")
                return "reminded"
        return None

    def _expire_active_if_still_eligible(self, pk, now):
        expired = None
        with transaction.atomic():
            access_request = PortalAccessRequest.objects.select_for_update().select_related("user").get(pk=pk)
            if (
                access_request.status != PortalAccessRequest.Status.ACTIVE
                or access_request.access_expires_at is None
                or access_request.access_expires_at > now
            ):
                return False
            if access_request.user_id and access_request.user.is_active:
                access_request.user.is_active = False
                access_request.user.save(update_fields=["is_active"])
            access_request.status = PortalAccessRequest.Status.EXPIRED
            access_request.save(update_fields=["status", "updated_at"])
            PortalAccessToken.objects.filter(
                request=access_request,
                used_at__isnull=True,
                revoked_at__isnull=True,
            ).update(revoked_at=now)
            audit(access_request, "account_expired")
            expired = access_request
        send_template(
            "Your RPL Material Request access has expired",
            expired.email,
            "portal_expired",
            email_context(expired),
            access_request=expired,
        )
        return True

    @staticmethod
    def _mark_expired_locked(access_request, now):
        PortalAccessToken.objects.filter(
            request=access_request,
            used_at__isnull=True,
            revoked_at__isnull=True,
        ).update(revoked_at=now)
        if access_request.user_id and not access_request.user.is_active:
            access_request.user.delete()
            access_request.user = None
        access_request.status = PortalAccessRequest.Status.EXPIRED
        access_request.save(update_fields=["status", "user", "updated_at"])
        audit(access_request, "expired")

    @staticmethod
    def _send_expired(access_request):
        send_template(
            "Your RPL Material Request access request expired",
            access_request.email,
            "portal_expired",
            email_context(access_request),
            access_request=access_request,
        )

    @staticmethod
    def _purge_if_still_eligible(pk, retention_cutoff):
        with transaction.atomic():
            access_request = PortalAccessRequest.objects.select_for_update().select_related("user").get(pk=pk)
            if (
                access_request.status not in {
                    PortalAccessRequest.Status.DENIED,
                    PortalAccessRequest.Status.EXPIRED,
                }
                or access_request.created_at >= retention_cutoff
                or not access_request.full_name
            ):
                return False
            if access_request.user_id and not access_request.user.is_active:
                user = access_request.user
                user.username = f"purged-portal-user-{user.pk}"
                user.email = ""
                user.first_name = ""
                user.last_name = ""
                user.set_unusable_password()
                user.save(update_fields=["username", "email", "first_name", "last_name", "password"])
                user.groups.clear()
                user.user_permissions.clear()
            access_request.email = f"purged-{access_request.pk}@invalid.local"
            access_request.full_name = ""
            access_request.position = ""
            access_request.contact_number = ""
            access_request.department = ""
            access_request.project_jobsite = ""
            access_request.sponsor = ""
            access_request.business_reason = ""
            access_request.review_notes = ""
            access_request.save(update_fields=[
                "email", "full_name", "position", "contact_number", "department",
                "project_jobsite", "sponsor", "business_reason", "review_notes", "updated_at",
            ])
            audit(access_request, "privacy_purged")
            return True
