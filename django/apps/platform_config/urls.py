from django.urls import path

from . import views
from .views import health

urlpatterns = [
    path("health", health, name="health"),
    path("platform/config", views.PublicConfigView.as_view(), name="platform-config-public"),
    path(
        "admin/platform/config",
        views.AdminConfigView.as_view(),
        name="platform-config-admin",
    ),
]
