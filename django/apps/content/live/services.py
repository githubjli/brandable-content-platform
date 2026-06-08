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
from django.db.models import F

from apps.events import types
from libs.errors.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableError,
    ValidationError,
)

from . import runtime
from .models import (
    LiveCategory,
    LiveChatMessage,
    LiveStream,
    LiveStreamPaymentMethod,
    LiveStreamProduct,
    LiveStreamView,
)

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


def get_watch_config(
    *, stream_id: str, viewer_id: str | None = None, ip_address: str | None = None
) -> dict[str, Any]:
    """Mobile playback config. Increments viewer_count once per user/IP per minute
    and asks the runtime for the playback endpoints (WebRTC primary, HLS fallback)."""
    stream = _get_stream(stream_id)
    is_owner = viewer_id is not None and str(viewer_id) == str(stream.owner_user_id)
    if stream.visibility == LiveStream.PRIVATE and not is_owner:
        raise NotFoundError(code="LIVE_STREAM_NOT_FOUND", message="Stream not found.")

    who = viewer_id or ip_address or "anon"
    dedup_key = f"{stream.id}:{who}:{_now():%Y%m%d%H%M}"
    _, created = LiveStreamView.objects.get_or_create(
        dedup_key=dedup_key,
        defaults={"stream": stream, "user_id": viewer_id, "ip_address": ip_address},
    )
    if created:
        LiveStream.objects.filter(id=stream.id).update(viewer_count=F("viewer_count") + 1)
        stream.refresh_from_db(fields=["viewer_count"])

    cfg = runtime.get_watch_config(
        stream_id=str(stream.id),
        ant_media_stream_id=stream.ant_media_stream_id,
        is_live=stream.status == LiveStream.LIVE,
    )
    return {
        "live_id": str(stream.id),
        "status": stream.status,
        "effective_status": stream.status,
        "viewer_count": stream.viewer_count,
        "playback": cfg["playback"],
        "fallback": cfg["fallback"],
        "thumbnail_url": stream.thumbnail_url or None,
        "preview_image_url": stream.preview_image_url or None,
        "snapshot_url": stream.snapshot_url or None,
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
            event_type=types.CONTENT_LIVE_STREAM_CREATED,
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
                event_type=types.CONTENT_LIVE_STREAM_STARTED,
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
            event_type=types.CONTENT_LIVE_STREAM_ENDED,
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


# ---------------------------------------------------------------------------
# Chat — content-live.md §2
# ---------------------------------------------------------------------------

_CHAT_MAX = 2000


def serialize_message(msg: LiveChatMessage, *, user: dict | None = None) -> dict[str, Any]:
    return {
        "id": str(msg.id),
        "live_id": str(msg.stream_id),
        "type": msg.type,
        "content": msg.content or None,
        "user": user or {"id": str(msg.user_id), "display_name": None, "avatar_url": None},
        "product": {"id": str(msg.product_id)} if msg.product_id else None,
        "payload": msg.payload or None,
        "is_pinned": msg.is_pinned,
        "created_at": _iso(msg.created_at),
    }


def serialize_messages(messages: list[LiveChatMessage]) -> list[dict[str, Any]]:
    from apps.identity.services import public_profiles

    users = public_profiles([str(m.user_id) for m in messages])
    return [serialize_message(m, user=users.get(str(m.user_id))) for m in messages]


def list_messages(
    *, stream_id: str, after_id: str | None = None, limit: int = 50, viewer_id: str | None = None
) -> dict[str, Any]:
    stream = _get_stream(stream_id)
    is_owner = viewer_id is not None and str(viewer_id) == str(stream.owner_user_id)
    if stream.visibility == LiveStream.PRIVATE and not is_owner:
        raise NotFoundError(code="LIVE_STREAM_NOT_FOUND", message="Stream not found.")

    limit = max(1, min(int(limit or 50), 100))
    qs = LiveChatMessage.objects.filter(stream=stream, is_active=True).order_by("created_at")
    if after_id:
        after = LiveChatMessage.objects.filter(id=after_id, stream=stream).first()
        if after is not None:
            qs = qs.filter(created_at__gt=after.created_at)
    messages = list(qs[:limit])
    return {
        "results": serialize_messages(messages),
        "next_after_id": str(messages[-1].id) if messages else None,
    }


def post_message(
    *, user_id: str, stream_id: str, content: str = "", product_id: str | None = None
) -> dict[str, Any]:
    text = (content or "").strip()
    msg_type = LiveChatMessage.PRODUCT if product_id else LiveChatMessage.TEXT
    if msg_type == LiveChatMessage.TEXT and not text:
        raise ValidationError(code="CHAT_EMPTY", message="Message content is required.")
    if len(text) > _CHAT_MAX:
        raise UnprocessableError(
            code="CHAT_TOO_LONG", message=f"Message exceeds {_CHAT_MAX} characters."
        )

    with transaction.atomic():
        stream = LiveStream.objects.select_for_update(of=("self",)).filter(id=stream_id).first()
        if stream is None:
            raise NotFoundError(code="LIVE_STREAM_NOT_FOUND", message="Stream not found.")
        if stream.status != LiveStream.LIVE:
            raise UnprocessableError(code="LIVE_STREAM_NOT_LIVE", message="The stream is not live.")
        msg = LiveChatMessage.objects.create(
            stream=stream,
            user_id=user_id,
            type=msg_type,
            content=text,
            product_id=product_id,
        )
        _emit(
            event_type=types.CONTENT_LIVE_CHAT_MESSAGE_POSTED,
            payload={
                "stream_id": str(stream.id),
                "message_id": str(msg.id),
                "user_id": str(user_id),
                "type": msg_type,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"live_chat_posted:{msg.id}",
            actor_id=str(user_id),
        )
    return serialize_messages([msg])[0]


def _get_message(stream: LiveStream, message_id: str) -> LiveChatMessage:
    msg = LiveChatMessage.objects.filter(id=message_id, stream=stream, is_active=True).first()
    if msg is None:
        raise NotFoundError(code="CHAT_MESSAGE_NOT_FOUND", message="Message not found.")
    return msg


def delete_message(*, user_id: str, stream_id: str, message_id: str) -> None:
    """Soft delete. Allowed for the broadcaster or the message author."""
    with transaction.atomic():
        stream = _get_stream(stream_id)
        msg = _get_message(stream, message_id)
        is_broadcaster = str(user_id) == str(stream.owner_user_id)
        is_author = str(user_id) == str(msg.user_id)
        if not (is_broadcaster or is_author):
            raise ForbiddenError(
                code="CHAT_DELETE_FORBIDDEN", message="You cannot delete this message."
            )
        msg.is_active = False
        msg.save(update_fields=["is_active", "updated_at"])
        _emit(
            event_type=types.CONTENT_LIVE_CHAT_MESSAGE_DELETED,
            payload={
                "stream_id": str(stream.id),
                "message_id": str(msg.id),
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"live_chat_deleted:{msg.id}",
            actor_id=str(user_id),
        )


def pin_message(
    *, user_id: str, stream_id: str, message_id: str, pinned: bool = True
) -> dict[str, Any]:
    """Pin/unpin a message. Broadcaster only."""
    with transaction.atomic():
        stream = _get_stream(stream_id)
        if str(user_id) != str(stream.owner_user_id):
            raise ForbiddenError(
                code="CHAT_PIN_FORBIDDEN", message="Only the broadcaster can pin messages."
            )
        msg = _get_message(stream, message_id)
        msg.is_pinned = pinned
        msg.save(update_fields=["is_pinned", "updated_at"])
        _emit(
            event_type=types.CONTENT_LIVE_CHAT_MESSAGE_PINNED,
            payload={
                "stream_id": str(stream.id),
                "message_id": str(msg.id),
                "is_pinned": pinned,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"live_chat_pinned:{msg.id}:{_now().timestamp()}",
            actor_id=str(user_id),
        )
    return serialize_messages([msg])[0]


# ---------------------------------------------------------------------------
# Live gift (content-live.md §4; reuses apps.content.gift)
# ---------------------------------------------------------------------------


def gift_target(stream_id: str) -> str:
    """Validate a live stream and return its owner (the gift receiver). Cross-app
    boundary for apps.content.gift; live gifts require the stream to be live."""
    stream = LiveStream.objects.filter(id=stream_id).first()
    if stream is None:
        raise NotFoundError(code="TARGET_NOT_FOUND", message="Live stream not found.")
    if stream.status != LiveStream.LIVE:
        raise UnprocessableError(code="LIVE_STREAM_NOT_LIVE", message="The stream is not live.")
    return str(stream.owner_user_id)


def send_live_gift(
    *,
    sender_id: str,
    stream_id: str,
    amount: Any,
    currency: str,
    payment_method: str,
    idempotency_key: str,
    gift_code: str = "",
) -> dict[str, Any]:
    """Send a gift to a live stream. Unlike video/drama gifts, this one broadcasts:
    gift.send_gift emits content.live.GiftSent (the runtime relays it to viewers),
    and the response carries the broadcast event block."""
    from apps.content.gift.services import send_gift

    result = send_gift(
        sender_id=str(sender_id),
        target_type="live_stream",  # GiftTransaction.LIVE_STREAM
        target_id=str(stream_id),
        amount=amount,
        currency=currency,
        payment_method=payment_method,
        idempotency_key=idempotency_key,
        gift_code=gift_code,
    )
    result["event"] = {
        "id": result["transaction"]["id"],
        "type": "gift_event",
        "broadcast_status": "queued",
    }
    return result


# ---------------------------------------------------------------------------
# Broadcaster — products & payment methods (content-live.md §6)
# ---------------------------------------------------------------------------

_PAYMENT_METHODS = {m for m, _ in LiveStreamPaymentMethod.METHOD}


def serialize_product_binding(
    binding: LiveStreamProduct, *, product: dict | None = None
) -> dict[str, Any]:
    return {
        "id": str(binding.id),
        "product_id": str(binding.product_id),
        "sort_order": binding.sort_order,
        "is_featured": binding.is_featured,
        "is_active": binding.is_active,
        "product": product,  # commerce card, None if missing/inactive
        "created_at": _iso(binding.created_at),
    }


def _serialize_bindings(bindings: list[LiveStreamProduct]) -> list[dict[str, Any]]:
    from apps.commerce.services import products_by_ids

    cards = products_by_ids(product_ids=[str(b.product_id) for b in bindings])
    return [serialize_product_binding(b, product=cards.get(str(b.product_id))) for b in bindings]


def list_stream_products(*, stream_id: str, viewer_id: str | None = None) -> dict[str, Any]:
    """Viewer-facing: active product bindings for a stream (promotion list)."""
    stream = _get_stream(stream_id)
    is_owner = viewer_id is not None and str(viewer_id) == str(stream.owner_user_id)
    if stream.visibility == LiveStream.PRIVATE and not is_owner:
        raise NotFoundError(code="LIVE_STREAM_NOT_FOUND", message="Stream not found.")
    bindings = list(stream.products.filter(is_active=True))
    return {"results": _serialize_bindings(bindings)}


def list_my_stream_products(*, user_id: str, stream_id: str) -> dict[str, Any]:
    """Broadcaster-facing: all product bindings (incl. inactive)."""
    stream = _owned_stream(user_id, stream_id)
    return {"results": _serialize_bindings(list(stream.products.all()))}


def bind_product(
    *,
    user_id: str,
    stream_id: str,
    product_id: str,
    sort_order: int = 0,
    is_featured: bool = False,
) -> dict[str, Any]:
    from apps.commerce.services import get_product

    with transaction.atomic():
        stream = _owned_stream(user_id, stream_id, lock=True)
        product = get_product(product_id=str(product_id))  # raises PRODUCT_NOT_FOUND
        if stream.products.filter(product_id=product_id).exists():
            raise ConflictError(
                code="LIVE_PRODUCT_ALREADY_BOUND",
                message="Product is already bound to this stream.",
            )
        binding = LiveStreamProduct.objects.create(
            stream=stream,
            product_id=product_id,
            sort_order=sort_order,
            is_featured=is_featured,
        )
    return serialize_product_binding(binding, product=product)


def _get_binding(stream: LiveStream, binding_id: str) -> LiveStreamProduct:
    binding = stream.products.filter(id=binding_id).first()
    if binding is None:
        raise NotFoundError(code="LIVE_PRODUCT_NOT_FOUND", message="Product binding not found.")
    return binding


def update_product_binding(
    *, user_id: str, stream_id: str, binding_id: str, **fields: Any
) -> dict[str, Any]:
    allowed = {"sort_order", "is_featured", "is_active"}
    with transaction.atomic():
        stream = _owned_stream(user_id, stream_id, lock=True)
        binding = _get_binding(stream, binding_id)
        updates = {k: v for k, v in fields.items() if k in allowed}
        for key, value in updates.items():
            setattr(binding, key, value)
        if updates:
            binding.save(update_fields=[*updates.keys(), "updated_at"])
    from apps.commerce.services import products_by_ids

    card = products_by_ids(product_ids=[str(binding.product_id)]).get(str(binding.product_id))
    return serialize_product_binding(binding, product=card)


def unbind_product(*, user_id: str, stream_id: str, binding_id: str) -> None:
    with transaction.atomic():
        stream = _owned_stream(user_id, stream_id, lock=True)
        _get_binding(stream, binding_id).delete()


def serialize_payment_method(pm: LiveStreamPaymentMethod) -> dict[str, Any]:
    return {
        "id": str(pm.id),
        "method": pm.method,
        "is_enabled": pm.is_enabled,
        "sort_order": pm.sort_order,
    }


def list_payment_methods(*, user_id: str, stream_id: str) -> dict[str, Any]:
    stream = _owned_stream(user_id, stream_id)
    methods = [serialize_payment_method(pm) for pm in stream.payment_methods.all()]
    return {"results": methods}


def set_payment_methods(*, user_id: str, stream_id: str, methods: list[str]) -> dict[str, Any]:
    """Replace-all config: the methods in the list become enabled (in order); any
    previously-enabled method not listed is disabled. Unknown methods rejected."""
    ordered = list(dict.fromkeys(methods))  # de-dupe, preserve order
    unknown = [m for m in ordered if m not in _PAYMENT_METHODS]
    if unknown:
        raise ValidationError(
            code="LIVE_INVALID_PAYMENT_METHOD",
            message=f"Unknown payment method(s): {', '.join(unknown)}.",
        )
    with transaction.atomic():
        stream = _owned_stream(user_id, stream_id, lock=True)
        enabled = set(ordered)
        for method in _PAYMENT_METHODS:
            sort_order = ordered.index(method) if method in enabled else 0
            LiveStreamPaymentMethod.objects.update_or_create(
                stream=stream,
                method=method,
                defaults={"is_enabled": method in enabled, "sort_order": sort_order},
            )
    return list_payment_methods(user_id=user_id, stream_id=stream_id)
