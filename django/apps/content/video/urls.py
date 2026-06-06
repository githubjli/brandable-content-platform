"""URL patterns for content.video (content-video.md §1-2). Mounted under api/v1/."""

from django.urls import path

from . import views

urlpatterns = [
    path("content/video/public", views.VideoListView.as_view(), name="video-public-list"),
    path(
        "content/video/public/<uuid:video_id>",
        views.VideoDetailView.as_view(),
        name="video-public-detail",
    ),
    path(
        "content/video/public/<uuid:video_id>/interactions",
        views.VideoInteractionsView.as_view(),
        name="video-interactions",
    ),
    path(
        "content/video/public/<uuid:video_id>/like",
        views.VideoLikeView.as_view(),
        name="video-like",
    ),
    path(
        "content/video/public/<uuid:video_id>/comments",
        views.VideoCommentsView.as_view(),
        name="video-comments",
    ),
    path(
        "content/video/public/<uuid:video_id>/share",
        views.VideoShareView.as_view(),
        name="video-share",
    ),
    path(
        "content/video/public/<uuid:video_id>/view",
        views.VideoViewTrackView.as_view(),
        name="video-view",
    ),
    # Creator management — §3
    path("content/video/me", views.MyVideoListCreateView.as_view(), name="video-me-list"),
    path(
        "content/video/me/<uuid:video_id>",
        views.MyVideoDetailView.as_view(),
        name="video-me-detail",
    ),
    path(
        "content/video/me/<uuid:video_id>/regenerate-thumbnail",
        views.MyVideoRegenerateThumbnailView.as_view(),
        name="video-me-regenerate-thumbnail",
    ),
]
