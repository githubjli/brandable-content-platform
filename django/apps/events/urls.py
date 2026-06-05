"""Admin URL patterns for events (events.md §12).

Mounted under api/v1/ by config/urls.py.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("admin/events/outbox", views.OutboxListView.as_view(), name="events-outbox-list"),
    path(
        "admin/events/outbox/<uuid:event_id>",
        views.OutboxDetailView.as_view(),
        name="events-outbox-detail",
    ),
    path(
        "admin/events/dlq/<uuid:dlq_id>/replay",
        views.DLQReplayView.as_view(),
        name="events-dlq-replay",
    ),
    path(
        "admin/events/dlq/<uuid:dlq_id>/resolve",
        views.DLQResolveView.as_view(),
        name="events-dlq-resolve",
    ),
]
