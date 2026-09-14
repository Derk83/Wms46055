from datetime import date, timedelta
import hashlib
import re
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group, Permission, User
from django.core import mail
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from inventory.forms import PortalAccessRequestForm
from inventory.models import (
    ManagedGroupRole,
    PortalAccessAuditEvent,
    PortalAccessRequest,
    PortalAccessThrottle,
    PortalAccessToken,
)
from inventory.onboarding import approve_request, issue_token, normalize_corporate_email

pytestmark = pytest.mark.django_db
REQUEST_HOST = "requests.rplwms.com"
WMS_HOST = "bbx.rplwms.com"


def application_data(**overrides):
    data = {
        "full_name": "Person Example",
        "position": "Construction Manager",
        "email": "person@blackbox.com",
        "contact_number": "555-0100",
        "department": "Operations",
        "project_jobsite": "Project Alpha",
        "sponsor": "Manager Example",
        "business_reason": "Request project materials",
        "acknowledge": "on",
    }
    data.update(overrides)
    return data


def manager(username="manager"):
    user = User.objects.create_user(username, password="test-password", email=f"{username}@blackbox.com")
    permission = Permission.objects.get(codename="review_portalaccessrequest")
    user.user_permissions.add(permission)
    return user


def role():
    managed = ManagedGroupRole.objects.filter(role_key="material_requests").select_related("group").first()
    if managed is None:
        managed = ManagedGroupRole.objects.create(
            role_key="material_requests", group=Group.objects.create(name="Renamable Requester Group")
        )
    managed.group.name = "Renamable Requester Group"
    managed.group.save(update_fields=["name"])
    return managed.group


def applicant(status=PortalAccessRequest.Status.PENDING_REVIEW, email="person@blackbox.com"):
    return PortalAccessRequest.objects.create(
        email=email,
        full_name="Person Example",
        position="Construction Manager",
        contact_number="555-0100",
        department="Operations",
        project_jobsite="Project Alpha",
        sponsor="Manager Example",
        business_reason="Request project materials",
        status=status,
        verified_at=timezone.now() if status != PortalAccessRequest.Status.UNVERIFIED else None,
    )


def token_from_outbox(purpose_segment):
    match = re.search(rf"/access/{purpose_segment}/([^/\s]+)/", mail.outbox[-1].body)
    assert match, mail.outbox[-1].body
    return match.group(1)


def test_exact_normalized_domain_and_required_identity_fields():
    assert normalize_corporate_email(" Person@BLACKBOX.COM ") == "person@blackbox.com"
    assert PortalAccessRequestForm(application_data()).is_valid()
    for bad in (
        "person@sub.blackbox.com",
        "person@blackbox.com.evil",
        "person+tag@gmail.com",
        f"{'x' * 140}@blackbox.com",
    ):
        assert not PortalAccessRequestForm(application_data(email=bad)).is_valid()
    assert not PortalAccessRequestForm(application_data(website="bot-filled")).is_valid()
    for missing in ("full_name", "position", "email", "contact_number", "acknowledge"):
        data = application_data(); data.pop(missing)
        assert not PortalAccessRequestForm(data).is_valid(), missing


def test_public_root_is_branded_registration_not_internal_portal_data():
    response = Client().get("/", HTTP_HOST=REQUEST_HOST)
    body = response.content.decode()
    assert response.status_code == 200
    assert "BLACK BOX" in body and "Request access" in body and "Already approved? Sign in" in body
    assert "Inventory On Hand" not in body and "Material Request Board" not in body
    assert "no-store" in response["Cache-Control"]


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_public_submission_is_generic_duplicate_safe_hashed_and_proxy_rate_limited(django_capture_on_commit_callbacks):
    client = Client(); url = reverse("portal_access_request", urlconf="inventory.request_urls")
    with django_capture_on_commit_callbacks(execute=True):
        first = client.post(url, application_data(email=" Person@BLACKBOX.COM "), HTTP_HOST=REQUEST_HOST, REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="198.51.100.20, 192.168.0.227")
    assert first.status_code == 200 and b"If the address is eligible" in first.content
    request = PortalAccessRequest.objects.get()
    assert request.email == "person@blackbox.com" and request.acknowledged_at
    raw = token_from_outbox("verify"); stored = PortalAccessToken.objects.get()
    assert stored.digest == hashlib.sha256(raw.encode()).hexdigest() and raw not in stored.digest
    with django_capture_on_commit_callbacks(execute=True):
        duplicate = client.post(url, application_data(), HTTP_HOST=REQUEST_HOST, REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="198.51.100.20, 192.168.0.227")
    assert duplicate.content == first.content and PortalAccessRequest.objects.count() == 1
    assert PortalAccessToken.objects.filter(used_at__isnull=True, revoked_at__isnull=True).count() == 1
    assert PortalAccessThrottle.objects.filter(key_digest=hashlib.sha256(b"198.51.100.20").hexdigest()).exists()


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_expired_unverified_request_can_reapply(django_capture_on_commit_callbacks):
    req = applicant(PortalAccessRequest.Status.EXPIRED)
    req.verified_at = None
    req.save(update_fields=["verified_at"])
    with django_capture_on_commit_callbacks(execute=True):
        response = Client().post(
            reverse("portal_access_request", urlconf="inventory.request_urls"),
            application_data(position="Project Manager"),
            HTTP_HOST=REQUEST_HOST,
        )
    req.refresh_from_db()
    assert response.status_code == 200
    assert req.status == PortalAccessRequest.Status.UNVERIFIED
    assert req.position == "Project Manager" and req.acknowledged_at
    assert PortalAccessToken.objects.filter(
        request=req, purpose="verify_email", revoked_at__isnull=True, used_at__isnull=True
    ).count() == 1


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    PORTAL_UNVERIFIED_TTL_HOURS=1,
)
def test_expired_unverified_reapplication_restarts_lifecycle_age(
    django_capture_on_commit_callbacks,
):
    req = applicant(PortalAccessRequest.Status.EXPIRED, "fresh@blackbox.com")
    req.verified_at = None
    req.save(update_fields=["verified_at"])
    stale_created_at = timezone.now() - timedelta(days=10)
    PortalAccessRequest.objects.filter(pk=req.pk).update(created_at=stale_created_at)

    with django_capture_on_commit_callbacks(execute=True):
        response = Client().post(
            reverse("portal_access_request", urlconf="inventory.request_urls"),
            application_data(email="fresh@blackbox.com"),
            HTTP_HOST=REQUEST_HOST,
        )

    req.refresh_from_db()
    assert response.status_code == 200
    assert req.created_at > stale_created_at
    call_command("process_portal_onboarding")
    req.refresh_from_db()
    assert req.status == PortalAccessRequest.Status.UNVERIFIED


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_existing_account_gets_generic_response_without_new_request_or_email(django_capture_on_commit_callbacks):
    User.objects.create_user("person@blackbox.com", email="person@blackbox.com")
    with django_capture_on_commit_callbacks(execute=True):
        response = Client().post(reverse("portal_access_request", urlconf="inventory.request_urls"), application_data(), HTTP_HOST=REQUEST_HOST)
    assert response.status_code == 200 and b"If the address is eligible" in response.content
    assert not PortalAccessRequest.objects.exists() and not mail.outbox


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_verification_get_does_not_consume_scanner_link_then_post_enters_queue(django_capture_on_commit_callbacks):
    reviewer = manager(); req = applicant(PortalAccessRequest.Status.UNVERIFIED)
    raw = issue_token(req, PortalAccessToken.Purpose.VERIFY_EMAIL)
    url = reverse("portal_access_verify", kwargs={"token": raw}, urlconf="inventory.request_urls")
    preview = Client().get(url, HTTP_HOST=REQUEST_HOST)
    req.refresh_from_db(); assert preview.status_code == 200 and req.status == PortalAccessRequest.Status.UNVERIFIED
    with django_capture_on_commit_callbacks(execute=True):
        response = Client().post(url, HTTP_HOST=REQUEST_HOST)
    req.refresh_from_db()
    assert response.status_code == 200 and req.status == PortalAccessRequest.Status.PENDING_REVIEW
    assert response["Referrer-Policy"] == "no-referrer"
    assert Client().get(url, HTTP_HOST=REQUEST_HOST).status_code == 400
    assert any("manager review" in message.subject.lower() for message in mail.outbox)


def test_host_permission_isolation_and_manager_groups_receive_reviewer_permission():
    req = applicant(); queue = reverse("portal_access_queue")
    public = reverse("portal_access_request", urlconf="inventory.request_urls")
    assert Client().get(public, HTTP_HOST=WMS_HOST).status_code == 404
    assert Client().get(queue, HTTP_HOST=REQUEST_HOST).status_code == 404
    assert Client().get(
        queue,
        HTTP_HOST=REQUEST_HOST,
        HTTP_X_FORWARDED_HOST=WMS_HOST,
    ).status_code == 404
    ordinary = User.objects.create_user("ordinary", password="test-password")
    client = Client(); client.force_login(ordinary); assert client.get(queue, HTTP_HOST=WMS_HOST).status_code == 403
    reviewer = manager(); client.force_login(reviewer)
    assert client.get(queue, HTTP_HOST=WMS_HOST).status_code == 200
    assert req.email.encode() in client.get(queue, HTTP_HOST=WMS_HOST).content
    permission = Permission.objects.get(codename="review_portalaccessrequest")
    for name in ("Logistics Manager", "Sr. Logistics Manager", "Procurement Manager"):
        assert Group.objects.get(name=name).permissions.filter(pk=permission.pk).exists()


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_manager_more_info_uses_one_time_response_link_and_returns_to_review(django_capture_on_commit_callbacks):
    reviewer = manager(); req = applicant()
    client = Client(); client.force_login(reviewer); detail = reverse("portal_access_detail", kwargs={"pk": req.pk})
    with django_capture_on_commit_callbacks(execute=True):
        assert client.post(detail, {"action": "more_info", "notes": "Provide project details"}, HTTP_HOST=WMS_HOST).status_code == 302
    req.refresh_from_db(); assert req.status == PortalAccessRequest.Status.MORE_INFO
    assert PortalAccessAuditEvent.objects.filter(request=req, event_type="more_info_requested").exists()
    raw = token_from_outbox("more-info")
    update_url = reverse("portal_access_more_info", kwargs={"token": raw}, urlconf="inventory.request_urls")
    updated = application_data(full_name="Updated Person", project_jobsite="Project Beta"); updated.pop("email"); updated.pop("acknowledge")
    with django_capture_on_commit_callbacks(execute=True):
        response = Client().post(update_url, updated, HTTP_HOST=REQUEST_HOST)
    req.refresh_from_db()
    assert response.status_code == 200 and req.status == PortalAccessRequest.Status.PENDING_REVIEW
    assert req.full_name == "Updated Person" and req.project_jobsite == "Project Beta"
    assert Client().get(update_url, HTTP_HOST=REQUEST_HOST).status_code == 400
    assert PortalAccessAuditEvent.objects.filter(request=req, event_type="more_info_received").exists()


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_deny_requires_notes_is_audited_and_revokes_tokens(django_capture_on_commit_callbacks):
    reviewer = manager(); req = applicant(); issue_token(req, PortalAccessToken.Purpose.MORE_INFO)
    client = Client(); client.force_login(reviewer); detail = reverse("portal_access_detail", kwargs={"pk": req.pk})
    assert client.post(detail, {"action": "deny", "notes": ""}, HTTP_HOST=WMS_HOST).status_code == 200
    req.refresh_from_db(); assert req.status == PortalAccessRequest.Status.PENDING_REVIEW
    with django_capture_on_commit_callbacks(execute=True):
        assert client.post(detail, {"action": "deny", "notes": "Unable to validate need"}, HTTP_HOST=WMS_HOST).status_code == 302
    req.refresh_from_db(); assert req.status == PortalAccessRequest.Status.DENIED
    assert PortalAccessAuditEvent.objects.filter(request=req, event_type="denied", actor=reviewer).exists()
    assert not PortalAccessToken.objects.filter(request=req, revoked_at__isnull=True, used_at__isnull=True).exists()


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_approval_creates_inactive_email_username_only_managed_role_and_expiry(django_capture_on_commit_callbacks):
    group = role(); reviewer = manager(); req = applicant()
    with django_capture_on_commit_callbacks(execute=True):
        raw = approve_request(req, reviewer, notes="Approved", access_expires_on=date.today() + timedelta(days=30))
    req.refresh_from_db(); user = req.user
    assert req.status == PortalAccessRequest.Status.APPROVED_SETUP and req.review_notes == "Approved" and req.access_expires_at
    assert user.username == user.email == "person@blackbox.com"
    assert not user.is_active and not user.is_staff and not user.is_superuser and not user.has_usable_password()
    assert list(user.groups.all()) == [group] and not user.user_permissions.exists()
    assert raw and "set up" in mail.outbox[-1].subject.lower()


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_setup_password_validation_activation_and_one_use(django_capture_on_commit_callbacks):
    role(); reviewer = manager(); req = applicant()
    with django_capture_on_commit_callbacks(execute=True): raw = approve_request(req, reviewer)
    url = reverse("portal_access_setup", kwargs={"token": raw}, urlconf="inventory.request_urls")
    weak = Client().post(url, {"new_password1": "short", "new_password2": "short"}, HTTP_HOST=REQUEST_HOST)
    assert weak.status_code == 200 and b"too short" in weak.content
    with django_capture_on_commit_callbacks(execute=True):
        good = Client().post(url, {"new_password1": "Extremely-safe-passphrase-483!", "new_password2": "Extremely-safe-passphrase-483!"}, HTTP_HOST=REQUEST_HOST)
    req.refresh_from_db(); req.user.refresh_from_db()
    assert good.status_code == 200 and req.status == PortalAccessRequest.Status.ACTIVE and req.user.is_active
    assert req.user.check_password("Extremely-safe-passphrase-483!")
    assert Client().get(url, HTTP_HOST=REQUEST_HOST).status_code == 400


def test_approval_fails_closed_without_managed_role_and_existing_user():
    reviewer = manager(); req = applicant(); ManagedGroupRole.objects.filter(role_key="material_requests").delete()
    with pytest.raises(ManagedGroupRole.DoesNotExist): approve_request(req, reviewer)
    role(); User.objects.create_user("person@blackbox.com", email="person@blackbox.com")
    with pytest.raises(ValueError): approve_request(req, reviewer)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_manager_can_resend_and_revoke_setup_link(django_capture_on_commit_callbacks):
    role(); reviewer = manager(); req = applicant()
    with django_capture_on_commit_callbacks(execute=True): approve_request(req, reviewer)
    client = Client(); client.force_login(reviewer); detail = reverse("portal_access_detail", kwargs={"pk": req.pk})
    with django_capture_on_commit_callbacks(execute=True):
        assert client.post(detail, {"action": "resend_setup"}, HTTP_HOST=WMS_HOST).status_code == 302
    assert PortalAccessToken.objects.filter(request=req, purpose="set_password", revoked_at__isnull=True, used_at__isnull=True).count() == 1
    assert PortalAccessAuditEvent.objects.filter(request=req, event_type="setup_resent").exists()
    assert client.post(detail, {"action": "revoke_setup"}, HTTP_HOST=WMS_HOST).status_code == 302
    assert not PortalAccessToken.objects.filter(request=req, purpose="set_password", revoked_at__isnull=True, used_at__isnull=True).exists()
    assert PortalAccessAuditEvent.objects.filter(request=req, event_type="setup_revoked").exists()


def test_material_request_email_is_bound_to_authenticated_identity():
    from inventory.forms import MaterialRequestForm
    user = User.objects.create_user("requester@blackbox.com", email="requester@blackbox.com")
    form = MaterialRequestForm(user=user, bind_requestor_email=True)
    assert form.fields["requestor_email"].disabled and form.initial["requestor_email"] == user.email
    bound = MaterialRequestForm(
        {"requestor_name": "R", "requestor_email": "attacker@blackbox.com"},
        user=user,
        bind_requestor_email=True,
    )
    assert bound.fields["requestor_email"].disabled
    assert not MaterialRequestForm(user=user).fields["requestor_email"].disabled


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", PORTAL_UNVERIFIED_TTL_HOURS=1, PORTAL_RETENTION_DAYS=30)
def test_lifecycle_expires_tokens_and_privacy_purges_all_identity_fields():
    old = applicant(PortalAccessRequest.Status.UNVERIFIED, "old@blackbox.com")
    PortalAccessRequest.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=40))
    issue_token(old, PortalAccessToken.Purpose.VERIFY_EMAIL, ttl=timedelta(minutes=-1))
    call_command("process_portal_onboarding"); old.refresh_from_db()
    assert old.status == PortalAccessRequest.Status.EXPIRED and old.email.startswith("purged-")
    assert not any([old.full_name, old.position, old.contact_number, old.department, old.project_jobsite, old.sponsor, old.business_reason, old.review_notes])
    assert PortalAccessToken.objects.filter(request=old, revoked_at__isnull=False).exists()
    assert PortalAccessAuditEvent.objects.filter(request=old, event_type="privacy_purged").exists()


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    PORTAL_SETUP_REMINDER_HOURS=24,
)
def test_lifecycle_reminder_rotates_token_and_includes_working_setup_url():
    role(); reviewer = manager(); req = applicant()
    raw = approve_request(req, reviewer)
    token = PortalAccessToken.objects.get(digest=hashlib.sha256(raw.encode()).hexdigest())
    PortalAccessToken.objects.filter(pk=token.pk).update(created_at=timezone.now() - timedelta(hours=30))
    mail.outbox.clear(); call_command("process_portal_onboarding"); req.refresh_from_db()
    assert req.reminder_sent_at and len(mail.outbox) == 1
    replacement = token_from_outbox("setup")
    assert replacement != raw
    assert PortalAccessToken.objects.filter(request=req, revoked_at__isnull=True, used_at__isnull=True).count() == 1
    assert Client().get(
        reverse("portal_access_setup", kwargs={"token": replacement}, urlconf="inventory.request_urls"),
        HTTP_HOST=REQUEST_HOST,
    ).status_code == 200


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", PORTAL_RETENTION_DAYS=30)
def test_privacy_purge_anonymizes_linked_inactive_account():
    user = User.objects.create_user(
        "former@blackbox.com", email="former@blackbox.com",
        first_name="Former", last_name="Person", is_active=False,
    )
    user.groups.add(Group.objects.create(name="Temporary group"))
    req = applicant(PortalAccessRequest.Status.EXPIRED, "former@blackbox.com")
    req.user = user; req.save(update_fields=["user"])
    PortalAccessRequest.objects.filter(pk=req.pk).update(created_at=timezone.now() - timedelta(days=40))
    call_command("process_portal_onboarding"); user.refresh_from_db(); req.refresh_from_db()
    assert user.username.startswith("purged-portal-user-")
    assert user.email == user.first_name == user.last_name == ""
    assert not user.has_usable_password() and not user.groups.exists()
    assert req.email.startswith("purged-") and req.full_name == ""


def test_lifecycle_rechecks_locked_state_and_newest_token_before_expiring():
    from inventory.management.commands.process_portal_onboarding import Command

    req = applicant(PortalAccessRequest.Status.APPROVED_SETUP)
    req.status = PortalAccessRequest.Status.ACTIVE; req.save(update_fields=["status"])
    assert Command()._expire_if_still_eligible(
        req.pk, PortalAccessRequest.Status.APPROVED_SETUP, timezone.now()
    ) is False
    req.status = PortalAccessRequest.Status.APPROVED_SETUP; req.save(update_fields=["status"])
    issue_token(req, PortalAccessToken.Purpose.SET_PASSWORD, ttl=timedelta(hours=2))
    assert Command()._process_setup_request(
        req.pk, timezone.now(), timezone.now() - timedelta(hours=24)
    ) is None
    req.refresh_from_db(); assert req.status == PortalAccessRequest.Status.APPROVED_SETUP


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_temporary_active_account_is_disabled_at_expiry():
    user = User.objects.create_user("expiring@blackbox.com", email="expiring@blackbox.com", is_active=True)
    req = applicant(PortalAccessRequest.Status.ACTIVE, "expiring@blackbox.com")
    req.user = user; req.access_expires_at = timezone.now() - timedelta(minutes=1); req.save()
    call_command("process_portal_onboarding"); req.refresh_from_db(); user.refresh_from_db()
    assert req.status == PortalAccessRequest.Status.EXPIRED and not user.is_active
    assert PortalAccessAuditEvent.objects.filter(request=req, event_type="account_expired").exists()


@pytest.mark.parametrize("host", [REQUEST_HOST, WMS_HOST])
def test_expired_temporary_access_is_synchronously_disabled_on_protected_requests(host):
    suffix = host.split(".", 1)[0]
    user = User.objects.create_user(
        f"expired-{suffix}@blackbox.com",
        email=f"expired-{suffix}@blackbox.com",
        is_active=True,
    )
    user.groups.add(role())
    req = applicant(PortalAccessRequest.Status.ACTIVE, user.email)
    req.user = user
    req.access_expires_at = timezone.now() - timedelta(seconds=1)
    req.save(update_fields=["user", "access_expires_at"])
    client = Client()
    client.force_login(user)

    response = client.get("/", HTTP_HOST=host)

    req.refresh_from_db()
    user.refresh_from_db()
    assert response.status_code == 403
    assert req.status == PortalAccessRequest.Status.EXPIRED
    assert not user.is_active
    assert PortalAccessAuditEvent.objects.filter(
        request=req, event_type="account_expired"
    ).exists()


@pytest.mark.django_db
def test_expiry_middleware_fails_closed_after_another_request_already_expired_account():
    from inventory.middleware import PortalAccessExpiryMiddleware

    user = User.objects.create_user(
        "expired-race@blackbox.com",
        email="expired-race@blackbox.com",
        is_active=True,
    )
    req = applicant(PortalAccessRequest.Status.ACTIVE, user.email)
    req.user = user
    req.access_expires_at = timezone.now() - timedelta(seconds=1)
    req.save(update_fields=["user", "access_expires_at"])

    assert PortalAccessExpiryMiddleware._disable_if_expired(user.pk) is True
    # Simulates a concurrent request that authenticated before the first request
    # committed and then resumed after the row was marked expired.
    assert PortalAccessExpiryMiddleware._disable_if_expired(user.pk) is True


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_email_failure_is_audited_without_rolling_back_submission(django_capture_on_commit_callbacks):
    with patch("inventory.onboarding.send_mail", side_effect=OSError("relay unavailable")):
        with django_capture_on_commit_callbacks(execute=True):
            response = Client().post(reverse("portal_access_request", urlconf="inventory.request_urls"), application_data(), HTTP_HOST=REQUEST_HOST)
    req = PortalAccessRequest.objects.get()
    assert response.status_code == 200 and req.status == PortalAccessRequest.Status.UNVERIFIED
    assert PortalAccessAuditEvent.objects.filter(request=req, event_type="email_delivery_failed").exists()


def test_csrf_is_required_for_public_and_manager_state_changes():
    public = Client(enforce_csrf_checks=True)
    assert public.post(reverse("portal_access_request", urlconf="inventory.request_urls"), application_data(), HTTP_HOST=REQUEST_HOST).status_code == 403
    reviewer = manager(); req = applicant(); manager_client = Client(enforce_csrf_checks=True); manager_client.force_login(reviewer)
    assert manager_client.post(reverse("portal_access_detail", kwargs={"pk": req.pk}), {"action": "deny", "notes": "No"}, HTTP_HOST=WMS_HOST).status_code == 403


def test_dashboard_pending_badge_for_reviewer():
    reviewer = manager(); applicant(); client = Client(); client.force_login(reviewer)
    response = client.get(reverse("dashboard"), HTTP_HOST=WMS_HOST)
    assert response.status_code == 200 and b"Access requests" in response.content and b">1<" in response.content
