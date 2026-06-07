"""URL patterns for content.live (content-live.md §1, §5). Mounted under api/v1/."""

from django.urls import path

from . import views

urlpatterns = [
    # Viewer — browse
    path("content/live/streams", views.StreamListView.as_view(), name="live-stream-list"),
    path(
        "content/live/streams/<uuid:stream_id>",
        views.StreamDetailView.as_view(),
        name="live-stream-detail",
    ),
    path(
        "content/live/streams/<uuid:stream_id>/status",
        views.StreamStatusView.as_view(),
        name="live-stream-status",
    ),
    # Broadcaster — lifecycle
    path(
        "content/live/me/streams",
        views.MyStreamListCreateView.as_view(),
        name="live-my-streams",
    ),
    path(
        "content/live/me/streams/<uuid:stream_id>",
        views.MyStreamDetailView.as_view(),
        name="live-my-stream-detail",
    ),
    path(
        "content/live/me/streams/<uuid:stream_id>/prepare",
        views.MyStreamPrepareView.as_view(),
        name="live-my-stream-prepare",
    ),
    path(
        "content/live/me/streams/<uuid:stream_id>/start",
        views.MyStreamStartView.as_view(),
        name="live-my-stream-start",
    ),
    path(
        "content/live/me/streams/<uuid:stream_id>/end",
        views.MyStreamEndView.as_view(),
        name="live-my-stream-end",
    ),
    # Chat — §2
    path(
        "content/live/streams/<uuid:stream_id>/chat/messages",
        views.ChatMessagesView.as_view(),
        name="live-chat-messages",
    ),
    path(
        "content/live/streams/<uuid:stream_id>/chat/messages/<uuid:message_id>",
        views.ChatMessageDetailView.as_view(),
        name="live-chat-message-detail",
    ),
    path(
        "content/live/streams/<uuid:stream_id>/chat/messages/<uuid:message_id>/pin",
        views.ChatMessagePinView.as_view(),
        name="live-chat-message-pin",
    ),
    # Live gift — §4
    path(
        "content/live/streams/<uuid:stream_id>/gifts/send",
        views.LiveGiftSendView.as_view(),
        name="live-gift-send",
    ),
]
