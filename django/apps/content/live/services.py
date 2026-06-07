"""Service layer for content.live (content-live.md §1, §5, §7).

Owns stream metadata + the lifecycle state machine. Realtime credentials come
from the runtime adapter (fake-mode until the gRPC client is wired). Owner cards
are batched via identity.public_profiles.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from django.db import transaction

from libs.errors.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError

from . import runtime
from .models import LiveCategory, LiveStream

logger = logging.getLogger(__name__)

# Streams a viewer may start from (state machine §7).
_STARTABLE = {LiveStream.IDLE, LiveStream.READY}


# ---------------------------------------------------------------------------
# Helpers
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


def serialize_category(category: LiveCategory) -> dict[str, Any]:
    return {"id": str(category.id), "name": category.name, "slug": category.slug}


def serialize_stream(
    stream: LiveStream, *, owner: dict | None = None, broadcaster: bool = False
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": str(stream.id),
        "title": stream.title,
        "description": stream.description or None,
        "owner": owner
        or {"id": str(stream.owner_user_id), "display_name": None, "avatar_url": None},
        "category": serialize_category(stream.category) if stream.category else None,
        "visibility": stream.visibility,
        "thumbnail_url": stream.thumbnail_url or None,
        "preview_image_url": stream.preview_image_url or None,
        "snapshot_url": stream.snapshot_url or None,
        "status": stream.status,
        "effective_status": stream.status,
        "viewer_count": stream.viewer_count,
        "created_at": _iso(stream.created_at),
        "started_at": _iso(stream.started_at),
    }
    if broadcaster:
        data["broadcaster_config"] = {
            "stream_key": stream.stream_key or None,
            "rtmp_url": stream.rtmp_url or None,
            "webrtc_publish_config": stream.webrtc_publish_config or {},
        }
    return data


# ---------------------------------------------------------------------------
# Viewer — browse
# ---------------------------------------------------------------------------


def streams_queryset(*, status: str | None = None, owner_id: str | None = None):
    qs = LiveStream.objects.select_related("category").filter(visibility=LiveStream.PUBLIC)
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if statuses:
            qs = qs.filter(status__in=statuses)
    if owner_id:
        qs = qs.filter(owner_user_id=owner_id)
    return qs


def serialize_streams(streams: list[LiveStream]) -> list[dict[str, Any]]:
    from apps.identity.services import public_profiles

    owners = public_profiles([str(s.owner_user_id) for s in streams])
    return [serialize_stream(s, owner=owners.get(str(s.owner_user_id))) for s in streams]


def _get_stream(stream_id: str) -> LiveStream:
    stream = LiveStream.objects.select_related("category").filter(id=stream_id).first()
    if stream is None:
        raise NotFoundError(code="LIVE_STREAM_NOT_FOUND", message="Stream not found.")
    return stream


def get_stream(*, stream_id: str, viewer_id: str | None = None) -> dict[str, Any]:
    from apps.identity.services import public_profiles

    stream = _get_stream(stream_id)
    is_owner = viewer_id is not None and str(viewer_id) == str(stream.owner_user_id)
    if stream.visibility == LiveStream.PRIVATE and not is_owner:
        raise NotFoundError(code="LIVE_STREAM_NOT_FOUND", message="Stream not found.")
    owner = public_profiles([str(stream.owner_user_id)]).get(str(stream.owner_user_id))
    return serialize_stream(stream, owner=owner, broadcaster=is_owner)


def get_status(*, stream_id: str, viewer_id: str | None = None) -> dict[str, Any]:
    stream = _get_stream(stream_id)
    is_owner = viewer_id is not None and str(viewer_id) == str(stream.owner_user_id)
    if stream.visibility == LiveStream.PRIVATE and not is_owner:
        raise NotFoundError(code="LIVE_STREAM_NOT_FOUND", message="Stream not found.")
    return {
        "id": str(stream.id),
        "status": stream.status,
        "effective_status": stream.status,
        "can_start": stream.status in _STARTABLE,
        "can_end": stream.status == LiveStream.LIVE,
        "viewer_count": stream.viewer_count,
        "publish": {"connected": stream.status == LiveStream.LIVE, "status": stream.status},
        "play": {"connected": stream.status == LiveStream.LIVE, "status": stream.status},
    }


# ---------------------------------------------------------------------------
# Broadcaster — lifecycle
# ---------------------------------------------------------------------------


def my_streams_queryset(*, user_id: str):
    return LiveStream.objects.select_related("category").filter(owner_user_id=user_id)


def _owned_stream(user_id: str, stream_id: str, *, lock: bool = False) -> LiveStream:
    qs = LiveStream.objects.filter(id=stream_id, owner_user_id=user_id)
    if lock:
        qs = qs.select_for_update(of=("self",))
    stream = qs.select_related("category").first()
    if stream is None:
        raise NotFoundError(code="LIVE_STREAM_NOT_FOUND", message="Stream not found.")
    return stream


def _resolve_category(category_id: Any) -> LiveCategory | None:
    if not category_id:
        return None
    try:
        return LiveCategory.objects.get(id=category_id)
    except LiveCategory.DoesNotExist:
        raise ValidationError(code="CATEGORY_NOT_FOUND", message="Category not found.")


def create_stream(
    *,
    user_id: str,
    title: str,
    description: str = "",
    visibility: str = LiveStream.PUBLIC,
    thumbnail_url: str = "",
    category_id: str | None = None,
) -> dict[str, Any]:
    from apps.identity.services import is_creator

    if not is_creator(user_id):
        raise ForbiddenError(code="NOT_CREATOR", message="Only creators can broadcast.")
    if visibility not in {LiveStream.PUBLIC, LiveStream.PRIVATE}:
        raise ValidationError(code="LIVE_INVALID_VISIBILITY", message="Invalid visibility.")

    with transaction.atomic():
        category = _resolve_category(category_id)
        stream = LiveStream.objects.create(
            owner_user_id=user_id,
            category=category,
            title=title,
            description=description,
            visibility=visibility,
            thumbnail_url=thumbnail_url,
            status=LiveStream.IDLE,
        )
        creds = runtime.create_stream(stream_id=str(stream.id), owner_id=str(user_id))
        stream.ant_media_stream_id = creds.get("ant_media_stream_id", "")
        stream.stream_key = creds.get("stream_key", "")
        stream.rtmp_url = creds.get("rtmp_url", "")
        stream.hls_url = creds.get("hls_url", "")
        stream.websocket_url = creds.get("websocket_url", "")
        stream.webrtc_publish_config = creds.get("webrtc_publish_config", {})
        stream.save(
            update_fields=[
                "ant_media_stream_id",
                "stream_key",
                "rtmp_url",
                "hls_url",
                "websocket_url",
                "webrtc_publish_config",
                "updated_at",
            ]
        )
        _emit(
            event_type="content.live.StreamCreated",
            payload={
                "stream_id": str(stream.id),
                "owner_user_id": str(user_id),
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"live_stream_created:{stream.id}",
            actor_id=str(user_id),
        )
    return _serialize_owned(stream, broadcaster=True)


def _serialize_owned(stream: LiveStream, *, broadcaster: bool = True) -> dict[str, Any]:
    from apps.identity.services import public_profiles

    owner = public_profiles([str(stream.owner_user_id)]).get(str(stream.owner_user_id))
    return serialize_stream(stream, owner=owner, broadcaster=broadcaster)


def prepare_stream(*, user_id: str, stream_id: str) -> dict[str, Any]:
    with transaction.atomic():
        stream = _owned_stream(user_id, stream_id, lock=True)
        if stream.status not in {LiveStream.IDLE, LiveStream.READY}:
            raise ConflictError(
                code="LIVE_INVALID_STATE",
                message=f"Cannot prepare a stream in status {stream.status}.",
            )
        if stream.status == LiveStream.IDLE:
            stream.status = LiveStream.READY
            stream.save(update_fields=["status", "updated_at"])
    return _serialize_owned(stream, broadcaster=True)


def start_stream(*, user_id: str, stream_id: str) -> dict[str, Any]:
    with transaction.atomic():
        stream = _owned_stream(user_id, stream_id, lock=True)
        already = stream.status == LiveStream.LIVE
        if not already and stream.status not in _STARTABLE:
            raise ConflictError(
                code="LIVE_INVALID_STATE",
                message=f"Cannot start a stream in status {stream.status}.",
            )
        if not already:
            runtime.start_broadcast(stream_id=str(stream.id))
            stream.status = LiveStream.LIVE
            stream.started_at = _now()
            stream.save(update_fields=["status", "started_at", "updated_at"])
            _emit(
                event_type="content.live.StreamStarted",
                payload={
                    "stream_id": str(stream.id),
                    "owner_user_id": str(user_id),
                    "occurred_at": _iso(_now()),
                },
                idempotency_key=f"live_stream_started:{stream.id}",
                actor_id=str(user_id),
            )
    return {
        "ok": True,
        "status": stream.status,
        "already_started": already,
        "stream": _serialize_owned(stream, broadcaster=True),
    }


def end_stream(*, user_id: str, stream_id: str) -> dict[str, Any]:
    with transaction.atomic():
        stream = _owned_stream(user_id, stream_id, lock=True)
        if stream.status == LiveStream.ENDED:
            return _serialize_owned(stream, broadcaster=True)
        if stream.status not in {LiveStream.LIVE, LiveStream.READY, LiveStream.IDLE}:
            raise ConflictError(
                code="LIVE_INVALID_STATE",
                message=f"Cannot end a stream in status {stream.status}.",
            )
        runtime.stop_broadcast(stream_id=str(stream.id))
        stream.status = LiveStream.ENDED
        stream.ended_at = _now()
        stream.save(update_fields=["status", "ended_at", "updated_at"])
        _emit(
            event_type="content.live.StreamEnded",
            payload={
                "stream_id": str(stream.id),
                "owner_user_id": str(user_id),
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"live_stream_ended:{stream.id}",
            actor_id=str(user_id),
        )
    return _serialize_owned(stream, broadcaster=True)


def get_my_stream(*, user_id: str, stream_id: str) -> dict[str, Any]:
    return _serialize_owned(_owned_stream(user_id, stream_id), broadcaster=True)


def update_stream(*, user_id: str, stream_id: str, **fields: Any) -> dict[str, Any]:
    allowed = {"title", "description", "visibility", "thumbnail_url"}
    with transaction.atomic():
        stream = _owned_stream(user_id, stream_id, lock=True)
        if "category_id" in fields:
            stream.category = _resolve_category(fields.pop("category_id"))
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "visibility" and value not in {LiveStream.PUBLIC, LiveStream.PRIVATE}:
                raise ValidationError(code="LIVE_INVALID_VISIBILITY", message="Invalid visibility.")
            setattr(stream, key, value)
        stream.save()
    return _serialize_owned(stream, broadcaster=True)
