"""Pins the canonical wire strings for the content event types that video / live /
gift services now emit via apps.events.types constants (events.md §15: no inline
strings). Changing a value here is a breaking event-contract change, so each is
asserted explicitly; the gift target→event mapping is checked against the model.
"""

from __future__ import annotations

from apps.content.gift import services as gift_services
from apps.content.gift.models import GiftTransaction
from apps.events import types


def test_video_event_constants():
    assert types.CONTENT_VIDEO_CREATED == "content.VideoCreated"
    assert types.CONTENT_VIDEO_UPDATED == "content.VideoUpdated"
    assert types.CONTENT_VIDEO_DELETED == "content.VideoDeleted"
    assert types.CONTENT_VIDEO_LIKED == "content.VideoLiked"
    assert types.CONTENT_VIDEO_UNLIKED == "content.VideoUnliked"
    assert types.CONTENT_VIDEO_COMMENTED == "content.VideoCommented"
    assert types.CONTENT_VIDEO_SHARED == "content.VideoShared"
    assert types.CONTENT_VIDEO_VIEWED == "content.VideoViewed"


def test_live_event_constants():
    assert types.CONTENT_LIVE_STREAM_CREATED == "content.live.StreamCreated"
    assert types.CONTENT_LIVE_STREAM_STARTED == "content.live.StreamStarted"
    assert types.CONTENT_LIVE_STREAM_ENDED == "content.live.StreamEnded"
    assert types.CONTENT_LIVE_CHAT_MESSAGE_POSTED == "content.live.ChatMessagePosted"
    assert types.CONTENT_LIVE_CHAT_MESSAGE_DELETED == "content.live.ChatMessageDeleted"
    assert types.CONTENT_LIVE_CHAT_MESSAGE_PINNED == "content.live.ChatMessagePinned"


def test_gift_event_constants():
    assert types.CONTENT_VIDEO_GIFTED == "content.VideoGifted"
    assert types.CONTENT_DRAMA_GIFTED == "content.DramaGifted"
    assert types.CONTENT_LIVE_GIFT_SENT == "content.live.GiftSent"


def test_gift_target_event_mapping_uses_constants():
    mapping = gift_services._TARGET_EVENT
    assert mapping[GiftTransaction.VIDEO] == types.CONTENT_VIDEO_GIFTED
    assert mapping[GiftTransaction.DRAMA_SERIES] == types.CONTENT_DRAMA_GIFTED
    assert mapping[GiftTransaction.LIVE_STREAM] == types.CONTENT_LIVE_GIFT_SENT
