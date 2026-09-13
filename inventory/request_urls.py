from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

# This URLconf is selected only for requests.rplwms.com by HostURLConfMiddleware.
urlpatterns = [
    path("material-requests/email-response/", views.material_request_email_delivery_response, name="material_request_email_delivery_response"),
    path("manifest.webmanifest", views.pwa_manifest, name="pwa_manifest"),
    path("service-worker.js", views.pwa_service_worker, name="pwa_service_worker"),
    path("api/push/config/", views.push_config, name="push_config"),
    path("api/push/subscribe/", views.push_subscribe, name="push_subscribe"),
    path("api/push/unsubscribe/", views.push_unsubscribe, name="push_unsubscribe"),
    path(
        "material-requests/<int:pk>/confirm-ready/",
        views.material_request_confirm_ready,
        name="request_material_request_confirm_ready",
    ),
    path("offline/", views.pwa_offline, name="pwa_offline"),
    path("", views.material_request_board, name="dashboard"),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("inventory/", views.inventory_list, name="inventory_list"),
    path("inventory/<int:pk>/", views.item_detail, name="item_detail"),
    path("material-requests/", views.material_request_board, name="material_request_board"),
    path("material-requests/archive/", views.material_request_archive, name="material_request_archive"),
    path("material-requests/new/", views.material_request_create, name="material_request_create"),
    path("material-requests/<int:pk>/", views.material_request_detail, name="material_request_detail"),
    path(
        "material-requests/<int:pk>/delivery-response/",
        views.material_request_delivery_response,
        name="material_request_delivery_response",
    ),
    path("material-requests/<int:pk>/edit/", views.material_request_edit, name="material_request_edit"),
    path("material-requests/<int:pk>/delete/", views.material_request_delete, name="material_request_delete"),
]
