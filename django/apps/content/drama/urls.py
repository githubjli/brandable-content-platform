"""URL patterns for content.drama (content-drama.md §1-3). Mounted under api/v1/."""

from django.urls import path

from . import views

urlpatterns = [
    path("content/drama/series", views.SeriesListView.as_view(), name="drama-series-list"),
    path(
        "content/drama/series/<uuid:series_id>",
        views.SeriesDetailView.as_view(),
        name="drama-series-detail",
    ),
    path(
        "content/drama/series/<uuid:series_id>/episodes",
        views.EpisodeListView.as_view(),
        name="drama-episode-list",
    ),
    path(
        "content/drama/series/<uuid:series_id>/episodes/<int:episode_no>",
        views.EpisodeDetailView.as_view(),
        name="drama-episode-detail",
    ),
    path(
        "content/drama/episodes/<uuid:episode_id>/unlock",
        views.EpisodeUnlockView.as_view(),
        name="drama-episode-unlock",
    ),
]
