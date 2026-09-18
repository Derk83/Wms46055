from django.contrib.auth import views as auth_views
from django.urls import path

from inventory.auth_forms import WarehouseAuthenticationForm

from . import request_views, views

urlpatterns = [
    path("manifest.webmanifest", views.pwa_manifest, name="equipment_pwa_manifest"),
    path("service-worker.js", views.service_worker, name="equipment_service_worker"),
    path("offline/", views.offline, name="equipment_offline"),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="equipment/login.html",
            authentication_form=WarehouseAuthenticationForm,
            next_page="equipment_dashboard",
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(next_page="login"), name="logout"),
    path("", views.dashboard, name="equipment_dashboard"),
    path("requests/", request_views.manager_queue, name="equipment_request_queue"),
    path("requests/<uuid:pk>/", request_views.manager_detail, name="equipment_request_detail"),
    path("requests/<uuid:pk>/assign/", request_views.manager_assign, name="equipment_request_assign"),
    path("requests/<uuid:pk>/allocate/", request_views.manager_allocate, name="equipment_request_allocate"),
    path("requests/<uuid:pk>/transition/", request_views.manager_transition, name="equipment_request_transition"),
    path("assets/", views.asset_list, name="equipment_asset_list"),
    path("assets/add/", views.asset_create, name="equipment_asset_create"),
    path("assets/<uuid:pk>/", views.asset_detail, name="equipment_asset_detail"),
    path("assets/<uuid:pk>/edit/", views.asset_edit, name="equipment_asset_edit"),
    path("assets/<uuid:pk>/return/", views.asset_return, name="equipment_asset_return"),
    path("assets/<uuid:pk>/meter-readings/new/", views.asset_meter_reading, name="equipment_asset_meter_reading"),
    path("assets/<uuid:pk>/label/", views.asset_label, name="equipment_asset_label"),
    path("checkouts/", views.checkout_list, name="equipment_checkout_list"),
    path("checkouts/new/", views.checkout_create, name="equipment_checkout_create"),
    path("checkouts/<uuid:pk>/", views.checkout_detail, name="equipment_checkout_detail"),
    path("reservations/", views.reservation_list, name="equipment_reservations"),
    path("reservations/<uuid:pk>/status/", views.reservation_status, name="equipment_reservation_status"),
    path("maintenance/", views.maintenance_list, name="equipment_maintenance"),
    path("maintenance/generate/", views.maintenance_generate, name="equipment_maintenance_generate"),
    path("maintenance/plans/new/", views.maintenance_plan_edit, name="equipment_maintenance_plan_create"),
    path("maintenance/plans/<int:pk>/edit/", views.maintenance_plan_edit, name="equipment_maintenance_plan_edit"),
    path("maintenance/plans/<int:pk>/toggle/", views.maintenance_plan_toggle, name="equipment_maintenance_plan_toggle"),
    path("maintenance/<int:pk>/status/", views.maintenance_status, name="equipment_maintenance_status"),
    path("maintenance/<int:pk>/complete/", views.maintenance_complete, name="equipment_maintenance_complete"),
    path("rentals/", views.rental_list, name="equipment_rentals"),
    path("history/", views.history, name="equipment_history"),
    path("reports/", views.reports, name="equipment_reports"),
    path("reports/export.csv", views.export_assets, name="equipment_export"),
    path("imports/", views.import_center, name="equipment_import"),
    path("imports/<uuid:pk>/", views.import_batch, name="equipment_import_batch"),
    path("scan/", views.scan_lookup, name="equipment_scan"),
    path("api/assets/search/", views.asset_search_api, name="equipment_asset_search_api"),
    path("api/scan/", views.scan_api, name="equipment_scan_api"),
]
