"""Root URL configuration."""

from django.contrib import admin
from django.urls import include, path

from libs.telemetry.metrics import MetricsView

urlpatterns = [
    path("admin/", admin.site.urls),
    # Internal (not authenticated, restricted by network/nginx in production)
    path("internal/metrics", MetricsView.as_view(), name="metrics"),
    # Public API
    path("api/v1/", include("apps.platform_config.urls")),
    path("api/v1/", include("apps.identity.urls")),
    path("api/v1/", include("apps.economy.urls")),
    path("api/v1/", include("apps.events.urls")),
    path("api/v1/", include("apps.audit.urls")),
    path("api/v1/", include("apps.payments.urls")),
    path("api/v1/", include("apps.content.video.urls")),
    path("api/v1/", include("apps.content.drama.urls")),
    path("api/v1/", include("apps.content.live.urls")),
    path("api/v1/", include("apps.content.gift.urls")),
    path("api/v1/", include("apps.commerce.urls")),
    path("api/v1/", include("apps.membership.urls")),
    # Well-known (JWKS)
    path(".well-known/", include("libs.jwt_auth.urls")),
]
