from django.db import transaction
from django.http import HttpResponseForbidden
from django.utils import timezone


class ProxySSLHeaderMiddleware:
    """Ensure Django sees HTTPS when behind Nginx Proxy Manager."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if "HTTP_X_FORWARDED_PROTO" not in request.META:
            request.META["HTTP_X_FORWARDED_PROTO"] = "https"
        return self.get_response(request)


class HostURLConfMiddleware:
    """Strictly isolate the hub, request, and equipment hosts from warehouse URLs."""

    hub_hosts = frozenset({"rplwms.com", "www.rplwms.com"})
    request_host = "requests.rplwms.com"
    equipment_host = "equipment.rplwms.com"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":", 1)[0].lower().rstrip(".")
        request.is_hub_host = host in self.hub_hosts
        request.is_request_portal = host == self.request_host
        request.is_equipment_portal = host == self.equipment_host
        request.is_wms_host = host == "bbx.rplwms.com"
        if request.is_hub_host:
            request.urlconf = "config.hub_urls"
        elif request.is_request_portal:
            request.urlconf = "inventory.request_urls"
        elif request.is_equipment_portal:
            request.urlconf = "equipment.urls"
        return self.get_response(request)


class PortalAccessExpiryMiddleware:
    """Synchronously revoke expired temporary onboarding accounts."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and self._disable_if_expired(user.pk):
            user.is_active = False
            return HttpResponseForbidden("This temporary portal access has expired.")
        return self.get_response(request)

    @staticmethod
    def _disable_if_expired(user_id):
        from .models import PortalAccessAuditEvent, PortalAccessRequest, PortalAccessToken

        now = timezone.now()
        with transaction.atomic():
            access_request = (
                PortalAccessRequest.objects.select_for_update()
                .select_related("user")
                .filter(user_id=user_id)
                .first()
            )
            if access_request is None:
                # Legacy/local accounts are not governed by onboarding expiry.
                return False
            if access_request.status != PortalAccessRequest.Status.ACTIVE:
                # Fail closed when another concurrent request already expired or
                # otherwise revoked this linked onboarding account after Django
                # cached the authenticated user for the current request.
                return True
            if (
                access_request.access_expires_at is None
                or access_request.access_expires_at > now
            ):
                return False
            if access_request.user.is_active:
                access_request.user.is_active = False
                access_request.user.save(update_fields=["is_active"])
            access_request.status = PortalAccessRequest.Status.EXPIRED
            access_request.save(update_fields=["status", "updated_at"])
            PortalAccessToken.objects.filter(
                request=access_request,
                used_at__isnull=True,
                revoked_at__isnull=True,
            ).update(revoked_at=now)
            PortalAccessAuditEvent.objects.create(
                request=access_request,
                event_type="account_expired",
            )
            return True
