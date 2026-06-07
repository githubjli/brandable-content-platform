"""Service layer for content.video (content-video.md §1-2).

Public video catalog + engagement. Owner cards and follow flags come from
apps/identity (batched); counts are denormalized on Video and bumped with F().
Cross-app emit/audit follow the same swallow/raise rules as the other domains.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from django.db import transaction
from django.db.models import F, Q, Value
from django.db.models.functions import Greatest

from libs.errors.exceptions import (
    ForbiddenError,
    NotFoundError,
    UnprocessableError,
    ValidationError,
)

from .models import Video, VideoCategory, VideoComment, VideoLike, VideoShare, VideoView

logger = logging.getLogger(__name__)

_COMMENT_MAX = 2000
_VIDEO_ORDERINGS = {
    "-created_at": ("-created_at",),
    "-view_count": ("-view_count", "-created_at"),
    "-like_count": ("-like_count", "-created_at"),
}
_DEFAULT_VIDEO_ORDERING = "-created_at"


# ---------------------------------------------------------------------------
# Cross-app stubs / helpers
# ---------------------------------------------------------------------------


def _emit(
    event_type: str, payload: dict, idempotency_key: str, actor_id: str | None = None
) -> None:
    try:
        from apps.events.services import emit

        emit(
            event_type=event_type,
            idempotency_key=idempotency_key,
            payload=payload,
            actor_id=actor_id,
        )
    except Exception:
        logger.debug("_emit: emit failed; skipping %s", event_type)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def video_ordering(ordering: str | None) -> tuple[str, ...]:
    return _VIDEO_ORDERINGS.get(ordering or "", _VIDEO_ORDERINGS[_DEFAULT_VIDEO_ORDERING])


def _get_active_video(video_id: str, *, lock: bool = False) -> Video:
    qs = Video.objects.filter(id=video_id, visibility=Video.PUBLIC, is_active=True)
    if lock:
        qs = qs.select_for_update(of=("self",))
    video = qs.select_related("category").first()
    if video is None:
        raise NotFoundError(code="VIDEO_NOT_FOUND", message="Video not found.")
    return video


def gift_target(video_id: str) -> str:
    """Validate an active video and return its owner_user_id (gift receiver).
    Cross-app boundary for apps.content.gift."""
    from libs.errors.exceptions import NotFoundError as _NotFound

    owner = (
        Video.objects.filter(id=video_id, visibility=Video.PUBLIC, is_active=True)
        .values_list("owner_user_id", flat=True)
        .first()
    )
    if owner is None:
        raise _NotFound(code="TARGET_NOT_FOUND", message="Gift target not found.")
    return str(owner)


def _can_watch(video: Video) -> bool:
    # Free videos are watchable by anyone; members-only access stays gated until
    # the membership integration lands (erring toward not granting access).
    return video.access_type == Video.FREE


def _liked_video_ids(viewer_id: str | None, video_ids: list) -> set[str]:
    if not viewer_id or not video_ids:
        return set()
    rows = VideoLike.objects.filter(user_id=viewer_id, video_id__in=video_ids).values_list(
        "video_id", flat=True
    )
    return {str(v) for v in rows}


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def serialize_category(category: VideoCategory) -> dict[str, Any]:
    return {"id": str(category.id), "name": category.name, "slug": category.slug}


def serialize_video(
    video: Video,
    *,
    owner: dict | None = None,
    is_liked: bool = False,
    is_following: bool = False,
    detail: bool = False,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": str(video.id),
        "title": video.title,
        "description": video.description or None,
        "owner": owner
        or {
            "id": str(video.owner_user_id),
            "display_name": None,
            "avatar_url": None,
            "is_creator": False,
        },
        "category": serialize_category(video.category) if video.category else None,
        "visibility": video.visibility,
        "file_url": video.file_url or None,
        "thumbnail_url": video.thumbnail_url or None,
        "duration_seconds": video.duration_seconds,
        "counts": {
            "view": video.view_count,
            "like": video.like_count,
            "comment": video.comment_count,
            "share": video.share_count,
            "gift_amount": "0.0000",
            "gift_currency": "MP",
        },
        "viewer_context": {
            "is_liked": is_liked,
            "can_watch": _can_watch(video),
            "is_following_owner": is_following,
        },
        "created_at": _iso(video.created_at),
    }
    if detail:
        data["description_html"] = video.description or ""
    return data


def serialize_videos(videos: list[Video], viewer_id: str | None = None) -> list[dict[str, Any]]:
    """Serialize a video page with batched owner cards, liked set, and follow set."""
    from apps.identity.services import following_ids, public_profiles

    owner_ids = [str(v.owner_user_id) for v in videos]
    owners = public_profiles(owner_ids)
    liked = _liked_video_ids(viewer_id, [v.id for v in videos])
    following = following_ids(viewer_id, owner_ids)
    return [
        serialize_video(
            v,
            owner=owners.get(str(v.owner_user_id)),
            is_liked=str(v.id) in liked,
            is_following=str(v.owner_user_id) in following,
        )
        for v in videos
    ]


# ---------------------------------------------------------------------------
# Public catalog
# ---------------------------------------------------------------------------


def videos_queryset(
    *, category: str | None = None, access_type: str | None = None, search: str | None = None
):
    qs = Video.objects.select_related("category").filter(visibility=Video.PUBLIC, is_active=True)
    if category and category != "all":
        qs = qs.filter(category__slug=category)
    if access_type:
        qs = qs.filter(access_type=access_type)
    if search:
        qs = qs.filter(Q(title__icontains=search) | Q(description__icontains=search))
    return qs


def get_video(*, video_id: str, viewer_id: str | None = None) -> dict[str, Any]:
    from apps.identity.services import following_ids, public_profiles

    video = _get_active_video(video_id)
    owner = public_profiles([str(video.owner_user_id)]).get(str(video.owner_user_id))
    liked = _liked_video_ids(viewer_id, [video.id])
    following = following_ids(viewer_id, [str(video.owner_user_id)])
    return serialize_video(
        video,
        owner=owner,
        is_liked=str(video.id) in liked,
        is_following=str(video.owner_user_id) in following,
        detail=True,
    )


def get_interactions(*, video_id: str, viewer_id: str | None = None) -> dict[str, Any]:
    from apps.identity.services import follower_count, following_ids

    video = _get_active_video(video_id)
    liked = _liked_video_ids(viewer_id, [video.id])
    following = following_ids(viewer_id, [str(video.owner_user_id)])
    return {
        "video_id": str(video.id),
        "counts": {
            "view": video.view_count,
            "like": video.like_count,
            "comment": video.comment_count,
            "share": video.share_count,
            "gift_amount": "0.0000",
            "gift_currency": "MP",
        },
        "viewer_context": {
            "is_liked": str(video.id) in liked,
            "is_following_owner": str(video.owner_user_id) in following,
        },
        "owner_follower_count": follower_count(str(video.owner_user_id)),
    }


# ---------------------------------------------------------------------------
# Interactions — like / comment / share / view
# ---------------------------------------------------------------------------


def like_video(*, user_id: str, video_id: str) -> dict[str, Any]:
    video = _get_active_video(video_id)
    _, created = VideoLike.objects.get_or_create(video=video, user_id=user_id)
    if created:
        Video.objects.filter(id=video.id).update(like_count=F("like_count") + 1)
        video.refresh_from_db(fields=["like_count"])
        _emit(
            event_type="content.VideoLiked",
            payload={
                "video_id": str(video.id),
                "user_id": str(user_id),
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"content_video_liked:{video.id}:{user_id}",
            actor_id=str(user_id),
        )
    return {"video_id": str(video.id), "is_liked": True, "like_count": video.like_count}


def unlike_video(*, user_id: str, video_id: str) -> dict[str, Any]:
    video = _get_active_video(video_id)
    deleted, _ = VideoLike.objects.filter(video=video, user_id=user_id).delete()
    if deleted:
        Video.objects.filter(id=video.id).update(like_count=Greatest(F("like_count") - 1, Value(0)))
        video.refresh_from_db(fields=["like_count"])
        _emit(
            event_type="content.VideoUnliked",
            payload={
                "video_id": str(video.id),
                "user_id": str(user_id),
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"content_video_unliked:{video.id}:{user_id}:{_now().timestamp()}",
            actor_id=str(user_id),
        )
    return {"video_id": str(video.id), "is_liked": False, "like_count": video.like_count}


def comments_queryset(*, video_id: str):
    """Top-level comments for a video (replies are fetched via replies_queryset)."""
    video = _get_active_video(video_id)
    return VideoComment.objects.filter(video=video, parent__isnull=True)


def replies_queryset(*, video_id: str, parent_id: str):
    video = _get_active_video(video_id)
    return VideoComment.objects.filter(video=video, parent_id=parent_id)  # type: ignore[misc]


def serialize_comments(comments: list[VideoComment]) -> list[dict[str, Any]]:
    from apps.identity.services import public_profiles

    users = public_profiles([str(c.user_id) for c in comments])
    return [
        {
            "id": str(c.id),
            "content": c.content,
            "user": users.get(str(c.user_id))
            or {"id": str(c.user_id), "display_name": None, "avatar_url": None},
            "parent_id": str(c.parent_id) if c.parent_id else None,
            "reply_count": c.reply_count,
            "created_at": _iso(c.created_at),
        }
        for c in comments
    ]


def add_comment(
    *, user_id: str, video_id: str, content: str, parent_id: str | None = None
) -> dict[str, Any]:
    text = (content or "").strip()
    if not text:
        raise ValidationError(code="COMMENT_EMPTY", message="Comment content is required.")
    if len(text) > _COMMENT_MAX:
        raise UnprocessableError(
            code="COMMENT_TOO_LONG", message=f"Comment exceeds {_COMMENT_MAX} characters."
        )
    with transaction.atomic():
        video = _get_active_video(video_id, lock=True)
        parent = None
        if parent_id:
            parent = VideoComment.objects.filter(id=parent_id, video=video).first()
            if parent is None:
                raise UnprocessableError(
                    code="COMMENT_PARENT_INVALID",
                    message="parent_id does not belong to this video.",
                )
        comment = VideoComment.objects.create(
            video=video, user_id=user_id, content=text, parent=parent
        )
        Video.objects.filter(id=video.id).update(comment_count=F("comment_count") + 1)
        if parent is not None:
            VideoComment.objects.filter(id=parent.id).update(reply_count=F("reply_count") + 1)
        _emit(
            event_type="content.VideoCommented",
            payload={
                "video_id": str(video.id),
                "comment_id": str(comment.id),
                "user_id": str(user_id),
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"content_video_commented:{comment.id}",
            actor_id=str(user_id),
        )
    return serialize_comments([comment])[0]


def track_share(
    *,
    video_id: str,
    user_id: str | None = None,
    channel: str = "",
    ip_address: str | None = None,
    user_agent: str = "",
) -> dict[str, Any]:
    video = _get_active_video(video_id)
    VideoShare.objects.create(
        video=video,
        user_id=user_id,
        channel=(channel or "")[:64],
        ip_address=ip_address,
        user_agent=(user_agent or "")[:400],
    )
    Video.objects.filter(id=video.id).update(share_count=F("share_count") + 1)
    video.refresh_from_db(fields=["share_count"])
    _emit(
        event_type="content.VideoShared",
        payload={
            "video_id": str(video.id),
            "channel": channel or None,
            "occurred_at": _iso(_now()),
        },
        idempotency_key=f"content_video_shared:{video.id}:{_now().timestamp()}",
        actor_id=str(user_id) if user_id else None,
    )
    return {"video_id": str(video.id), "share_count": video.share_count}


def track_view(
    *, video_id: str, user_id: str | None = None, ip_address: str | None = None
) -> dict[str, Any]:
    video = _get_active_video(video_id)
    who = user_id or ip_address or "anon"
    dedup_key = f"{video.id}:{who}:{_now():%Y%m%d%H%M}"
    _, created = VideoView.objects.get_or_create(
        dedup_key=dedup_key,
        defaults={"video": video, "user_id": user_id, "ip_address": ip_address},
    )
    if created:
        Video.objects.filter(id=video.id).update(view_count=F("view_count") + 1)
        video.refresh_from_db(fields=["view_count"])
        _emit(
            event_type="content.VideoViewed",
            payload={"video_id": str(video.id), "occurred_at": _iso(_now())},
            idempotency_key=f"content_video_viewed:{dedup_key}",
            actor_id=str(user_id) if user_id else None,
        )
    return {"video_id": str(video.id), "view_count": video.view_count}


# ---------------------------------------------------------------------------
# Creator management — content-video.md §3
# ---------------------------------------------------------------------------

_VISIBILITIES = {Video.PUBLIC, Video.PRIVATE, Video.UNLISTED}
_ACCESS_TYPES = {Video.FREE, Video.MEMBERS_ONLY}


def _resolve_category(category_id: Any) -> VideoCategory | None:
    if not category_id:
        return None
    try:
        return VideoCategory.objects.get(id=category_id)
    except VideoCategory.DoesNotExist:
        raise ValidationError(code="CATEGORY_NOT_FOUND", message="Category not found.")


def serialize_owned_video(video: Video) -> dict[str, Any]:
    """Creator-facing video (own) — exposes visibility/access/is_active + dates."""
    return {
        "id": str(video.id),
        "title": video.title,
        "description": video.description or None,
        "category": serialize_category(video.category) if video.category else None,
        "visibility": video.visibility,
        "access_type": video.access_type,
        "file_url": video.file_url or None,
        "thumbnail_url": video.thumbnail_url or None,
        "duration_seconds": video.duration_seconds,
        "preview_seconds": video.preview_seconds,
        "counts": {
            "view": video.view_count,
            "like": video.like_count,
            "comment": video.comment_count,
            "share": video.share_count,
        },
        "is_active": video.is_active,
        "created_at": _iso(video.created_at),
        "updated_at": _iso(video.updated_at),
    }


def my_videos_queryset(*, user_id: str):
    """All of a creator's own videos (every visibility + inactive)."""
    return Video.objects.select_related("category").filter(owner_user_id=user_id)


def create_video(
    *,
    user_id: str,
    title: str,
    description: str = "",
    file_url: str = "",
    thumbnail_url: str = "",
    duration_seconds: int = 0,
    preview_seconds: int = 0,
    category_id: str | None = None,
    visibility: str = Video.PUBLIC,
    access_type: str = Video.FREE,
) -> dict[str, Any]:
    from apps.identity.services import is_creator

    if not is_creator(user_id):
        raise ForbiddenError(code="NOT_CREATOR", message="Only creators can publish videos.")
    if visibility not in _VISIBILITIES:
        raise ValidationError(code="VIDEO_INVALID_VISIBILITY", message="Invalid visibility.")
    if access_type not in _ACCESS_TYPES:
        raise ValidationError(code="VIDEO_INVALID_ACCESS_TYPE", message="Invalid access_type.")
    with transaction.atomic():
        category = _resolve_category(category_id)
        video = Video.objects.create(
            owner_user_id=user_id,
            category=category,
            title=title,
            description=description,
            file_url=file_url,
            thumbnail_url=thumbnail_url,
            duration_seconds=duration_seconds,
            preview_seconds=preview_seconds,
            visibility=visibility,
            access_type=access_type,
        )
        _emit(
            event_type="content.VideoCreated",
            payload={
                "video_id": str(video.id),
                "owner_user_id": str(user_id),
                "visibility": visibility,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"content_video_created:{video.id}",
            actor_id=str(user_id),
        )
    return serialize_owned_video(video)


def _owned_video(user_id: str, video_id: str, *, lock: bool = False) -> Video:
    qs = Video.objects.filter(id=video_id, owner_user_id=user_id)
    if lock:
        qs = qs.select_for_update(of=("self",))
    video = qs.select_related("category").first()
    if video is None:
        raise NotFoundError(code="VIDEO_NOT_FOUND", message="Video not found.")
    return video


def get_my_video(*, user_id: str, video_id: str) -> dict[str, Any]:
    return serialize_owned_video(_owned_video(user_id, video_id))


def update_my_video(*, user_id: str, video_id: str, **fields: Any) -> dict[str, Any]:
    allowed = {
        "title",
        "description",
        "file_url",
        "thumbnail_url",
        "duration_seconds",
        "preview_seconds",
        "visibility",
        "access_type",
    }
    with transaction.atomic():
        video = _owned_video(user_id, video_id, lock=True)
        if "category_id" in fields:
            video.category = _resolve_category(fields.pop("category_id"))
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "visibility" and value not in _VISIBILITIES:
                raise ValidationError(
                    code="VIDEO_INVALID_VISIBILITY", message="Invalid visibility."
                )
            if key == "access_type" and value not in _ACCESS_TYPES:
                raise ValidationError(
                    code="VIDEO_INVALID_ACCESS_TYPE", message="Invalid access_type."
                )
            setattr(video, key, value)
        video.save()
        _emit(
            event_type="content.VideoUpdated",
            payload={"video_id": str(video.id), "occurred_at": _iso(_now())},
            idempotency_key=f"content_video_updated:{video.id}:{_now().timestamp()}",
            actor_id=str(user_id),
        )
    return serialize_owned_video(video)


def delete_my_video(*, user_id: str, video_id: str) -> None:
    """Soft delete: is_active=False (removes it from the public catalog)."""
    with transaction.atomic():
        video = _owned_video(user_id, video_id, lock=True)
        if not video.is_active:
            return
        video.is_active = False
        video.save(update_fields=["is_active", "updated_at"])
        _emit(
            event_type="content.VideoDeleted",
            payload={"video_id": str(video.id), "occurred_at": _iso(_now())},
            idempotency_key=f"content_video_deleted:{video.id}",
            actor_id=str(user_id),
        )


def regenerate_thumbnail(
    *, user_id: str, video_id: str, time_offset_seconds: float = 0.0
) -> dict[str, Any]:
    """Acknowledge a thumbnail-regeneration request. Real frame extraction needs
    transcoding (V3); for now this records the request and returns the video."""
    video = _owned_video(user_id, video_id)
    _emit(
        event_type="content.VideoUpdated",
        payload={
            "video_id": str(video.id),
            "thumbnail_offset_seconds": time_offset_seconds,
            "occurred_at": _iso(_now()),
        },
        idempotency_key=f"content_video_thumbnail:{video.id}:{_now().timestamp()}",
        actor_id=str(user_id),
    )
    return serialize_owned_video(video)
