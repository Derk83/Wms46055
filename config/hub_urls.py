from django.urls import path

from inventory import hub_views


urlpatterns = [
    path("", hub_views.hub_home, name="hub_home"),
]
