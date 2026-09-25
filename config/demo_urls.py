from django.urls import path

from inventory import demo, hub_views, public_training_views


urlpatterns = [
    path("", hub_views.demo_home, name="demo_home"),
    # Alias used by the shared anonymous training shell.
    path("", hub_views.demo_home, name="dashboard"),
    path("inventory-demo/", demo.public_inventory_demo, name="inventory_demo"),
    path(
        "inventory-demo/action/",
        demo.public_inventory_demo_action,
        name="inventory_demo_action",
    ),
    path(
        "training/<slug:family>/<slug:slug>/",
        public_training_views.public_training_course,
        name="public_training_course",
    ),
    path(
        "training/<slug:family>/<slug:slug>/progress/",
        public_training_views.public_training_progress,
        name="public_training_progress",
    ),
    path(
        "guides/<slug:slug>/",
        public_training_views.public_request_guide,
        name="public_request_guide",
    ),
    path(
        "guides/<slug:slug>/progress/",
        public_training_views.public_request_guide_progress,
        name="public_request_guide_progress",
    ),
]
