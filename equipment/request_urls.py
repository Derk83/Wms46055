from django.contrib.auth import views as auth_views
from django.urls import path

from inventory.auth_forms import WarehouseAuthenticationForm

from . import request_views

urlpatterns = [
    path("manifest.webmanifest", request_views.pwa_manifest, name="eqreq_manifest"),
    path("service-worker.js", request_views.service_worker, name="eqreq_service_worker"),
    path("offline/", request_views.offline, name="eqreq_offline"),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="equipment/requests/login.html",
            authentication_form=WarehouseAuthenticationForm,
            next_page="eqreq_dashboard",
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(next_page="login"), name="logout"),
    path("", request_views.dashboard, name="eqreq_dashboard"),
    path("new/", request_views.request_create, name="eqreq_create"),
    path("help/", request_views.help_page, name="eqreq_help"),
    path("account/", request_views.account, name="eqreq_account"),
    path("requests/<uuid:pk>/", request_views.request_detail, name="eqreq_detail"),
    path("requests/<uuid:pk>/edit/", request_views.request_edit, name="eqreq_edit"),
    path("requests/<uuid:pk>/cancel/", request_views.request_cancel, name="eqreq_cancel"),
]
