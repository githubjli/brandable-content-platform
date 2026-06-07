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
    # Favorites — §5
    path(
        "content/drama/series/<uuid:series_id>/favorite",
        views.SeriesFavoriteView.as_view(),
        name="drama-series-favorite",
    ),
    # Watch progress — §4
    path(
        "content/drama/series/<uuid:series_id>/progress",
        views.SeriesProgressView.as_view(),
        name="drama-series-progress",
    ),
    path(
        "content/drama/episodes/<uuid:episode_id>/progress",
        views.EpisodeProgressView.as_view(),
        name="drama-episode-progress",
    ),
    # Comments — §6
    path(
        "content/drama/series/<uuid:series_id>/comments",
        views.SeriesCommentsView.as_view(),
        name="drama-series-comments",
    ),
    # View + share — §1
    path(
        "content/drama/series/<uuid:series_id>/view",
        views.SeriesViewTrackView.as_view(),
        name="drama-series-view",
    ),
    path(
        "content/drama/series/<uuid:series_id>/share",
        views.SeriesShareView.as_view(),
        name="drama-series-share",
    ),
]
