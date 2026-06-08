"""Root URL configuration."""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from libs.telemetry.metrics import MetricsView

urlpatterns = [
    path("admin/", admin.site.urls),
    # Internal (not authenticated, restricted by network/nginx in production)
    path("internal/metrics", MetricsView.as_view(), name="metrics"),
    # OpenAPI schema + interactive docs (for API clients / Flutter codegen)
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/v1/schema/swagger-ui/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/v1/schema/redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
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
