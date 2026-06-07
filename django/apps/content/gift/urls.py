"""URL patterns for content.gift (gift.md §1-2). Mounted under api/v1/."""

from django.urls import path

from . import views

urlpatterns = [
    path("gifts/catalog", views.GiftCatalogView.as_view(), name="gift-catalog"),
    path("gifts/sent", views.GiftSentView.as_view(), name="gift-sent"),
    path("gifts/received", views.GiftReceivedView.as_view(), name="gift-received"),
    path(
        "content/video/public/<uuid:video_id>/gifts/send",
        views.VideoGiftSendView.as_view(),
        name="gift-video-send",
    ),
    path(
        "content/drama/series/<uuid:series_id>/gifts/send",
        views.DramaGiftSendView.as_view(),
        name="gift-drama-send",
    ),
]
