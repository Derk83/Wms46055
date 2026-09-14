"""Host-isolated views for secure material-request portal onboarding."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.forms import SetPasswordForm
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .forms import PortalAccessMoreInfoForm, PortalAccessRequestForm, PortalAccessReviewForm
from .models import ManagedGroupRole, PortalAccessRequest, PortalAccessToken
from .onboarding import (
    activate_account,
    resend_setup,
    resubmit_more_info,
    review_request,
    revoke_setup,
    submit_access_request,
    throttle_allowed,
    valid_token,
    verify_email,
)

GENERIC_RESPONSE = "If the address is eligible, we sent an email with the next step."


def _request_host_only(request):
    if not getattr(request, "is_request_portal", False):
        raise Http404


def _wms_reviewer_only(request):
    if not getattr(request, "is_wms_host", False):
        raise Http404
    if not request.user.is_authenticated or not request.user.has_perm(
        "inventory.review_portalaccessrequest"
    ):
        raise PermissionDenied


def _private_response(request, template, context, status=200):
    response = render(request, template, context, status=status)
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


def _client_ip(request):
    remote = request.META.get("REMOTE_ADDR", "unknown")
    trusted = set(getattr(settings, "PORTAL_TRUSTED_PROXY_IPS", ["127.0.0.1", "::1"]))
    forwarded = [value.strip() for value in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if value.strip()]
    if remote not in trusted:
        return remote
    for address in reversed([*forwarded, remote]):
        if address not in trusted:
            return address
    return remote


@never_cache
@require_http_methods(["GET", "POST"])
def portal_access_request(request):
    _request_host_only(request)
    if request.user.is_authenticated and request.path == "/":
        from .views import material_request_board

        return material_request_board(request)
    form = PortalAccessRequestForm(request.POST or None)
    if request.method == "POST":
        email_key = (request.POST.get("email") or "").strip().casefold()
        allowed = throttle_allowed("submit-ip", _client_ip(request)) and throttle_allowed(
            "submit-email", email_key
        )
        if allowed and form.is_valid():
            submit_access_request(**form.cleaned_data)
        return _private_response(
            request, "inventory/portal_access_generic.html", {"message": GENERIC_RESPONSE}
        )
    return _private_response(request, "inventory/portal_access_request.html", {"form": form})


@never_cache
@require_http_methods(["GET", "POST"])
def portal_access_verify(request, token):
    _request_host_only(request)
    token_row = valid_token(token, PortalAccessToken.Purpose.VERIFY_EMAIL)
    if token_row is None:
        return _private_response(
            request, "inventory/portal_access_token_invalid.html", {}, status=400
        )
    if request.method == "POST":
        if not throttle_allowed("verify-ip", _client_ip(request), limit=10):
            return _private_response(
                request, "inventory/portal_access_token_invalid.html", {}, status=400
            )
        access_request = verify_email(token)
        if access_request is None:
            return _private_response(
                request, "inventory/portal_access_token_invalid.html", {}, status=400
            )
        return _private_response(request, "inventory/portal_access_verified.html", {})
    return _private_response(
        request,
        "inventory/portal_access_verify_confirm.html",
        {"email": token_row.request.email},
    )


@never_cache
@require_http_methods(["GET", "POST"])
def portal_access_more_info(request, token):
    _request_host_only(request)
    token_row = valid_token(token, PortalAccessToken.Purpose.MORE_INFO)
    if token_row is None or token_row.request.status != PortalAccessRequest.Status.MORE_INFO:
        return _private_response(
            request, "inventory/portal_access_token_invalid.html", {}, status=400
        )
    access_request = token_row.request
    form = PortalAccessMoreInfoForm(request.POST or None, instance=access_request)
    if request.method == "POST":
        allowed = throttle_allowed("more-info-ip", _client_ip(request), limit=10)
        if allowed and form.is_valid():
            result = resubmit_more_info(token, form.cleaned_data)
            if result is not None:
                return _private_response(
                    request,
                    "inventory/portal_access_generic.html",
                    {"message": "Your updated information was returned to the manager review queue."},
                )
        if not allowed:
            return _private_response(
                request, "inventory/portal_access_token_invalid.html", {}, status=400
            )
    return _private_response(
        request,
        "inventory/portal_access_more_info.html",
        {"form": form, "manager_notes": access_request.review_notes},
    )


@never_cache
@require_http_methods(["GET", "POST"])
def portal_access_setup(request, token):
    _request_host_only(request)
    token_row = valid_token(token, PortalAccessToken.Purpose.SET_PASSWORD)
    if token_row is None or not token_row.request.user_id:
        return _private_response(
            request, "inventory/portal_access_token_invalid.html", {}, status=400
        )
    user = token_row.request.user
    form = SetPasswordForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        if not throttle_allowed("setup-ip", _client_ip(request), limit=10):
            return _private_response(
                request, "inventory/portal_access_token_invalid.html", {}, status=400
            )
        access_request = activate_account(token, form.cleaned_data["new_password1"])
        if access_request is None:
            return _private_response(
                request, "inventory/portal_access_token_invalid.html", {}, status=400
            )
        return _private_response(request, "inventory/portal_access_activated.html", {})
    return _private_response(request, "inventory/portal_access_setup.html", {"form": form})


@never_cache
def portal_access_queue(request):
    _wms_reviewer_only(request)
    rows = PortalAccessRequest.objects.select_related("reviewed_by", "user")
    status = request.GET.get("status", PortalAccessRequest.Status.PENDING_REVIEW)
    if status in PortalAccessRequest.Status.values:
        rows = rows.filter(status=status)
    query = request.GET.get("q", "").strip()
    if query:
        from django.db.models import Q

        rows = rows.filter(
            Q(email__icontains=query)
            | Q(full_name__icontains=query)
            | Q(position__icontains=query)
            | Q(department__icontains=query)
            | Q(project_jobsite__icontains=query)
        )
    return _private_response(
        request,
        "inventory/portal_access_queue.html",
        {
            "access_requests": rows,
            "statuses": PortalAccessRequest.Status.choices,
            "status_filter": status,
            "query": query,
        },
    )


@never_cache
@require_http_methods(["GET", "POST"])
def portal_access_detail(request, pk):
    _wms_reviewer_only(request)
    access_request = get_object_or_404(
        PortalAccessRequest.objects.select_related("user", "reviewed_by"), pk=pk
    )
    form = PortalAccessReviewForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        action = form.cleaned_data["action"]
        try:
            if action == "resend_setup":
                resend_setup(access_request, request.user)
            elif action == "revoke_setup":
                revoke_setup(access_request, request.user)
            else:
                review_request(
                    access_request,
                    request.user,
                    action,
                    form.cleaned_data["notes"],
                    form.cleaned_data["access_expires_on"],
                )
        except (ValueError, ManagedGroupRole.DoesNotExist, IntegrityError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Access request updated.")
        return redirect("portal_access_detail", pk=pk)
    return _private_response(
        request,
        "inventory/portal_access_detail.html",
        {"access_request": access_request, "review_form": form},
    )
