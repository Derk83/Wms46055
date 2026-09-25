from django.urls import path

from inventory import hub_views


urlpatterns = [
    path("", hub_views.demo_home, name="demo_home"),
]
