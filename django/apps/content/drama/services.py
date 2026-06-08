"""Service layer for content.drama (content-drama.md §1-3, §9).

Series + episodes catalog and the four-method episode unlock. Wallet debits go
through apps/economy; membership access through apps/membership; owner cards and
follow flags through apps/identity. All batched to avoid N+1.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest

from apps.events import types
from libs.errors.exceptions import NotFoundError, UnprocessableError, ValidationError

from .models import (
    DramaCategory,
    DramaComment,
    DramaEpisode,
    DramaFavorite,
    DramaSeries,
    DramaSeriesView,
    DramaUnlock,
    DramaWatchProgress,
)

logger = logging.getLogger(__name__)

_CENT = Decimal("0.0001")
_SERIES_ORDERINGS = {
    "-created_at": ("-created_at",),
    "-view_count": ("-view_count", "-created_at"),
    "-favorite_count": ("-favorite_count", "-created_at"),
}
_DEFAULT_SERIES_ORDERING = "-created_at"
_PAID_UNLOCK = {DramaEpisode.MEOW_POINTS, DramaEpisode.MEOW_CREDIT}
_ASSET = {DramaEpisode.MEOW_POINTS: "MP", DramaEpisode.MEOW_CREDIT: "MC"}


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


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(_CENT)


def series_ordering(ordering: str | None) -> tuple[str, ...]:
    return _SERIES_ORDERINGS.get(ordering or "", _SERIES_ORDERINGS[_DEFAULT_SERIES_ORDERING])


def _has_membership(user_id: str | None) -> bool:
    if not user_id:
        return False
    try:
        from apps.membership.services import has_active_membership

        return has_active_membership(user_id=user_id)
    except Exception:
        logger.debug("_has_membership: membership unavailable; treating as no membership")
        return False


def _unlocked_episode_ids(user_id: str | None, episode_ids: list) -> set[str]:
    if not user_id or not episode_ids:
        return set()
    rows = DramaUnlock.objects.filter(user_id=user_id, episode_id__in=episode_ids).values_list(
        "episode_id", flat=True
    )
    return {str(e) for e in rows}


# ---------------------------------------------------------------------------
# Series catalog
# ---------------------------------------------------------------------------


def serialize_category(category: DramaCategory) -> dict[str, Any]:
    return {"id": str(category.id), "name": category.name, "slug": category.slug}


def serialize_series(
    series: DramaSeries,
    *,
    owner: dict | None = None,
    is_following: bool = False,
    is_favorited: bool = False,
    continue_card: dict | None = None,
    gift_amount: str = "0.0000",
) -> dict[str, Any]:
    locked = max(series.total_episodes - series.free_episodes, 0)
    return {
        "id": str(series.id),
        "title": series.title,
        "description": series.description or None,
        "cover_url": series.cover_url or None,
        "tags": series.tags or [],
        "category": serialize_category(series.category) if series.category else None,
        "owner": owner
        or {
            "id": str(series.owner_user_id),
            "display_name": None,
            "avatar_url": None,
            "is_creator": False,
        },
        "counts": {
            "total_episodes": series.total_episodes,
            "free_episodes": series.free_episodes,
            "locked_episodes": locked,
            "view": series.view_count,
            "favorite": series.favorite_count,
            "comment": series.comment_count,
            "share": series.share_count,
            "gift_amount": gift_amount,
            "gift_currency": "MP",
        },
        "viewer_context": {
            "is_favorited": is_favorited,
            "is_following_owner": is_following,
            "continue": continue_card,
        },
        "created_at": _iso(series.created_at),
    }


def _favorited_series_ids(user_id: str | None, series_ids: list) -> set[str]:
    if not user_id or not series_ids:
        return set()
    rows = DramaFavorite.objects.filter(user_id=user_id, series_id__in=series_ids).values_list(
        "series_id", flat=True
    )
    return {str(s) for s in rows}


def _continue_cards(user_id: str | None, series_ids: list) -> dict[str, dict]:
    if not user_id or not series_ids:
        return {}
    rows = DramaWatchProgress.objects.filter(
        user_id=user_id, series_id__in=series_ids
    ).select_related("episode")
    return {
        str(p.series_id): {
            "episode_no": p.episode.episode_no,
            "progress_seconds": p.progress_seconds,
        }
        for p in rows
    }


def series_queryset(*, category: str | None = None):
    qs = DramaSeries.objects.select_related("category").filter(is_active=True)
    if category and category != "all":
        qs = qs.filter(category__slug=category)
    return qs


def serialize_series_list(series_list: list[DramaSeries], viewer_id: str | None = None):
    from apps.content.gift.services import TARGET_DRAMA_SERIES, gift_totals
    from apps.identity.services import following_ids, public_profiles

    owner_ids = [str(s.owner_user_id) for s in series_list]
    series_ids = [s.id for s in series_list]
    owners = public_profiles(owner_ids)
    following = following_ids(viewer_id, owner_ids)
    favorited = _favorited_series_ids(viewer_id, series_ids)
    continues = _continue_cards(viewer_id, series_ids)
    gifts = gift_totals(
        target_type=TARGET_DRAMA_SERIES, target_ids=[str(s.id) for s in series_list]
    )
    return [
        serialize_series(
            s,
            owner=owners.get(str(s.owner_user_id)),
            is_following=str(s.owner_user_id) in following,
            is_favorited=str(s.id) in favorited,
            continue_card=continues.get(str(s.id)),
            gift_amount=gifts.get(str(s.id), "0.0000"),
        )
        for s in series_list
    ]


def _get_active_series(series_id: str) -> DramaSeries:
    series = (
        DramaSeries.objects.select_related("category").filter(id=series_id, is_active=True).first()
    )
    if series is None:
        raise NotFoundError(code="SERIES_NOT_FOUND", message="Drama series not found.")
    return series


def gift_target(series_id: str) -> str:
    """Validate an active series and return its owner_user_id (gift receiver).
    Cross-app boundary for apps.content.gift."""
    owner = (
        DramaSeries.objects.filter(id=series_id, is_active=True)
        .values_list("owner_user_id", flat=True)
        .first()
    )
    if owner is None:
        raise NotFoundError(code="TARGET_NOT_FOUND", message="Gift target not found.")
    return str(owner)


def get_series(*, series_id: str, viewer_id: str | None = None) -> dict[str, Any]:
    from apps.content.gift.services import TARGET_DRAMA_SERIES, gift_totals
    from apps.identity.services import following_ids, public_profiles

    series = _get_active_series(series_id)
    owner = public_profiles([str(series.owner_user_id)]).get(str(series.owner_user_id))
    following = following_ids(viewer_id, [str(series.owner_user_id)])
    favorited = _favorited_series_ids(viewer_id, [series.id])
    continues = _continue_cards(viewer_id, [series.id])
    gifts = gift_totals(target_type=TARGET_DRAMA_SERIES, target_ids=[str(series.id)])
    return serialize_series(
        series,
        owner=owner,
        is_following=str(series.owner_user_id) in following,
        is_favorited=str(series.id) in favorited,
        continue_card=continues.get(str(series.id)),
        gift_amount=gifts.get(str(series.id), "0.0000"),
    )


# ---------------------------------------------------------------------------
# Episodes + access
# ---------------------------------------------------------------------------


def _episode_access(
    episode: DramaEpisode, *, unlocked_ids: set[str], has_membership: bool
) -> tuple[bool, bool, str | None]:
    """Returns (is_unlocked, can_watch, unlocked_via)."""
    if episode.is_free or episode.unlock_type == DramaEpisode.FREE:
        return True, True, "free"
    if str(episode.id) in unlocked_ids:
        return True, True, episode.unlock_type
    if episode.unlock_type == DramaEpisode.MEMBERSHIP and has_membership:
        return True, True, "membership"
    return False, False, None


def _serialize_episode(
    episode: DramaEpisode, *, unlocked_ids: set[str], has_membership: bool, detail: bool = False
) -> dict[str, Any]:
    is_unlocked, can_watch, via = _episode_access(
        episode, unlocked_ids=unlocked_ids, has_membership=has_membership
    )
    data: dict[str, Any] = {
        "id": str(episode.id),
        "episode_no": episode.episode_no,
        "title": episode.title,
        "duration_seconds": episode.duration_seconds,
        "thumbnail_url": episode.thumbnail_url or None,
        "is_free": episode.is_free or episode.unlock_type == DramaEpisode.FREE,
        "unlock_type": episode.unlock_type,
        "pricing": {
            "points_price": str(episode.points_price),
            "credits_price": str(episode.credits_price),
        },
        "viewer_context": {
            "is_unlocked": is_unlocked,
            "can_watch": can_watch,
            "unlocked_via": via,
        },
    }
    if detail:
        data["description"] = episode.description or None
        data["playback"] = (
            {"playback_url": episode.playback_url or None, "hls_url": episode.hls_url or None}
            if can_watch
            else None
        )
    return data


def list_episodes(*, series_id: str, viewer_id: str | None = None) -> dict[str, Any]:
    series = _get_active_series(series_id)
    episodes = list(series.episodes.filter(is_active=True).order_by("episode_no"))
    unlocked = _unlocked_episode_ids(viewer_id, [e.id for e in episodes])
    has_membership = _has_membership(viewer_id)
    return {
        "series_id": str(series.id),
        "episodes": [
            _serialize_episode(e, unlocked_ids=unlocked, has_membership=has_membership)
            for e in episodes
        ],
    }


def get_episode(*, series_id: str, episode_no: int, viewer_id: str | None = None) -> dict[str, Any]:
    series = _get_active_series(series_id)
    episode = series.episodes.filter(episode_no=episode_no, is_active=True).first()
    if episode is None:
        raise NotFoundError(code="EPISODE_NOT_FOUND", message="Episode not found.")
    unlocked = _unlocked_episode_ids(viewer_id, [episode.id])
    has_membership = _has_membership(viewer_id)
    data = _serialize_episode(
        episode, unlocked_ids=unlocked, has_membership=has_membership, detail=True
    )
    nos = list(
        series.episodes.filter(is_active=True)
        .order_by("episode_no")
        .values_list("episode_no", flat=True)
    )
    idx = nos.index(episode.episode_no)
    data["navigation"] = {
        "previous_episode_no": nos[idx - 1] if idx > 0 else None,
        "next_episode_no": nos[idx + 1] if idx < len(nos) - 1 else None,
    }
    return data


# ---------------------------------------------------------------------------
# Unlock
# ---------------------------------------------------------------------------


def _unlock_result(
    episode: DramaEpisode, payment_method: str, ledger_entry_id: Any, *, code: str | None
) -> dict[str, Any]:
    charged = code != "ALREADY_UNLOCKED"
    points = (
        str(_money(episode.points_price))
        if (charged and payment_method == DramaEpisode.MEOW_POINTS)
        else "0.0000"
    )
    credits = (
        str(_money(episode.credits_price))
        if (charged and payment_method == DramaEpisode.MEOW_CREDIT)
        else "0.0000"
    )
    return {
        "episode_id": str(episode.id),
        "series_id": str(episode.series_id),
        "is_unlocked": True,
        "payment_method": payment_method,
        "points_charged": points,
        "credits_charged": credits,
        "currency": _ASSET.get(payment_method),
        "ledger_entry_id": str(ledger_entry_id) if ledger_entry_id else None,
        "code": code,
    }


def unlock_episode(*, user_id: str, episode_id: str, payment_method: str) -> dict[str, Any]:
    if payment_method not in _PAID_UNLOCK:
        raise ValidationError(
            code="DRAMA_INVALID_PAYMENT_METHOD",
            message="payment_method must be meow_points or meow_credit.",
        )
    from apps.economy.services import debit as economy_debit

    with transaction.atomic():
        episode = (
            DramaEpisode.objects.select_related("series")
            .filter(id=episode_id, is_active=True)
            .first()
        )
        if episode is None:
            raise NotFoundError(code="EPISODE_NOT_FOUND", message="Episode not found.")
        if episode.is_free or episode.unlock_type == DramaEpisode.FREE:
            raise UnprocessableError(
                code="DRAMA_FREE_EPISODE", message="Free episodes do not need unlocking."
            )
        if episode.unlock_type == DramaEpisode.MEMBERSHIP:
            raise UnprocessableError(
                code="DRAMA_MEMBERSHIP_EPISODE",
                message="Membership episodes are accessed via an active membership.",
            )
        if payment_method != episode.unlock_type:
            raise ValidationError(
                code="DRAMA_PAYMENT_METHOD_MISMATCH",
                message=f"This episode unlocks with {episode.unlock_type}.",
            )

        existing = DramaUnlock.objects.filter(user_id=user_id, episode=episode).first()
        if existing is not None:
            return _unlock_result(
                episode, payment_method, existing.ledger_entry_id, code="ALREADY_UNLOCKED"
            )

        currency = _ASSET[payment_method]
        amount = _money(
            episode.points_price
            if payment_method == DramaEpisode.MEOW_POINTS
            else episode.credits_price
        )
        ledger = economy_debit(
            user_id=str(user_id),
            currency=currency,
            entry_type="SPEND",
            amount=amount,
            idempotency_key=f"drama_unlock:{episode.id}:{user_id}",
            target_type="DramaEpisode",
            target_id=str(episode.id),
            note=f"Unlock episode {episode.episode_no} of series {episode.series_id}",
        )
        DramaUnlock.objects.create(
            user_id=user_id,
            episode=episode,
            unlock_type=payment_method,
            ledger_entry_id=ledger["id"],
        )
        _emit(
            event_type=types.CONTENT_DRAMA_EPISODE_UNLOCKED,
            payload={
                "episode_id": str(episode.id),
                "series_id": str(episode.series_id),
                "user_id": str(user_id),
                "payment_method": payment_method,
                "amount": str(amount),
                "currency": currency,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"content_drama_unlocked:{episode.id}:{user_id}",
            actor_id=str(user_id),
        )
        return _unlock_result(episode, payment_method, ledger["id"], code=None)


# ---------------------------------------------------------------------------
# Favorites — content-drama.md §5
# ---------------------------------------------------------------------------


def add_favorite(*, user_id: str, series_id: str) -> dict[str, Any]:
    series = _get_active_series(series_id)
    _, created = DramaFavorite.objects.get_or_create(user_id=user_id, series=series)
    if created:
        DramaSeries.objects.filter(id=series.id).update(favorite_count=F("favorite_count") + 1)
        series.refresh_from_db(fields=["favorite_count"])
        _emit(
            event_type=types.CONTENT_DRAMA_SERIES_FAVORITED,
            payload={
                "series_id": str(series.id),
                "user_id": str(user_id),
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"content_drama_favorited:{series.id}:{user_id}",
            actor_id=str(user_id),
        )
    return {
        "series_id": str(series.id),
        "is_favorited": True,
        "favorite_count": series.favorite_count,
    }


def remove_favorite(*, user_id: str, series_id: str) -> dict[str, Any]:
    series = _get_active_series(series_id)
    deleted, _ = DramaFavorite.objects.filter(user_id=user_id, series=series).delete()
    if deleted:
        DramaSeries.objects.filter(id=series.id).update(
            favorite_count=Greatest(F("favorite_count") - 1, Value(0))
        )
        series.refresh_from_db(fields=["favorite_count"])
        _emit(
            event_type=types.CONTENT_DRAMA_SERIES_UNFAVORITED,
            payload={
                "series_id": str(series.id),
                "user_id": str(user_id),
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"content_drama_unfavorited:{series.id}:{user_id}:{_now().timestamp()}",
            actor_id=str(user_id),
        )
    return {
        "series_id": str(series.id),
        "is_favorited": False,
        "favorite_count": series.favorite_count,
    }


# ---------------------------------------------------------------------------
# Watch progress — content-drama.md §4
# ---------------------------------------------------------------------------


def _serialize_progress(progress: DramaWatchProgress) -> dict[str, Any]:
    return {
        "series_id": str(progress.series_id),
        "episode_id": str(progress.episode_id),
        "episode_no": progress.episode.episode_no,
        "progress_seconds": progress.progress_seconds,
        "completed": progress.completed,
        "updated_at": _iso(progress.updated_at),
    }


def get_progress(*, user_id: str, series_id: str) -> dict[str, Any]:
    progress = (
        DramaWatchProgress.objects.select_related("episode")
        .filter(user_id=user_id, series_id=series_id)  # type: ignore[misc]
        .first()
    )
    if progress is None:
        raise NotFoundError(code="PROGRESS_NOT_FOUND", message="No watch progress recorded.")
    return _serialize_progress(progress)


def upsert_progress(
    *, user_id: str, series_id: str, episode_id: str, progress_seconds: int, completed: bool = False
) -> dict[str, Any]:
    with transaction.atomic():
        series = _get_active_series(series_id)
        episode = series.episodes.filter(id=episode_id, is_active=True).first()
        if episode is None:
            raise NotFoundError(code="EPISODE_NOT_FOUND", message="Episode not found in series.")
        progress, _ = DramaWatchProgress.objects.update_or_create(
            user_id=user_id,
            series=series,
            defaults={
                "episode": episode,
                "progress_seconds": max(int(progress_seconds), 0),
                "completed": completed,
            },
        )
        progress.episode = episode  # ensure select_related-free serialize is correct
        _emit(
            event_type=types.CONTENT_DRAMA_PROGRESS_UPDATED,
            payload={
                "series_id": str(series.id),
                "episode_id": str(episode.id),
                "user_id": str(user_id),
                "progress_seconds": progress.progress_seconds,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"content_drama_progress:{series.id}:{user_id}:{_now().timestamp()}",
            actor_id=str(user_id),
        )
    return _serialize_progress(progress)


def upsert_episode_progress(
    *, user_id: str, episode_id: str, progress_seconds: int, completed: bool = False
) -> dict[str, Any]:
    """Episode-scoped progress: the series is derived from the episode."""
    episode = (
        DramaEpisode.objects.select_related("series")
        .filter(id=episode_id, is_active=True, series__is_active=True)
        .first()
    )
    if episode is None:
        raise NotFoundError(code="EPISODE_NOT_FOUND", message="Episode not found.")
    return upsert_progress(
        user_id=user_id,
        series_id=str(episode.series_id),
        episode_id=str(episode.id),
        progress_seconds=progress_seconds,
        completed=completed,
    )


# ---------------------------------------------------------------------------
# Comments — content-drama.md §6
# ---------------------------------------------------------------------------

_COMMENT_MAX = 2000


def comments_queryset(*, series_id: str):
    series = _get_active_series(series_id)
    return DramaComment.objects.filter(series=series, parent__isnull=True)


def replies_queryset(*, series_id: str, parent_id: str):
    series = _get_active_series(series_id)
    return DramaComment.objects.filter(series=series, parent_id=parent_id)  # type: ignore[misc]


def serialize_comments(comments: list[DramaComment]) -> list[dict[str, Any]]:
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
    *, user_id: str, series_id: str, content: str, parent_id: str | None = None
) -> dict[str, Any]:
    text = (content or "").strip()
    if not text:
        raise ValidationError(code="COMMENT_EMPTY", message="Comment content is required.")
    if len(text) > _COMMENT_MAX:
        raise UnprocessableError(
            code="COMMENT_TOO_LONG", message=f"Comment exceeds {_COMMENT_MAX} characters."
        )
    with transaction.atomic():
        series = _get_active_series(series_id)
        parent = None
        if parent_id:
            parent = DramaComment.objects.filter(id=parent_id, series=series).first()
            if parent is None:
                raise UnprocessableError(
                    code="COMMENT_PARENT_INVALID",
                    message="parent_id does not belong to this series.",
                )
        comment = DramaComment.objects.create(
            series=series, user_id=user_id, content=text, parent=parent
        )
        DramaSeries.objects.filter(id=series.id).update(comment_count=F("comment_count") + 1)
        if parent is not None:
            DramaComment.objects.filter(id=parent.id).update(reply_count=F("reply_count") + 1)
        _emit(
            event_type=types.CONTENT_DRAMA_SERIES_COMMENTED,
            payload={
                "series_id": str(series.id),
                "comment_id": str(comment.id),
                "user_id": str(user_id),
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"content_drama_commented:{comment.id}",
            actor_id=str(user_id),
        )
    return serialize_comments([comment])[0]


# ---------------------------------------------------------------------------
# View + share tracking — content-drama.md §1
# ---------------------------------------------------------------------------


def track_view(
    *, series_id: str, user_id: str | None = None, ip_address: str | None = None
) -> dict[str, Any]:
    series = _get_active_series(series_id)
    who = user_id or ip_address or "anon"
    dedup_key = f"{series.id}:{who}:{_now():%Y%m%d%H%M}"
    _, created = DramaSeriesView.objects.get_or_create(
        dedup_key=dedup_key,
        defaults={"series": series, "user_id": user_id, "ip_address": ip_address},
    )
    if created:
        DramaSeries.objects.filter(id=series.id).update(view_count=F("view_count") + 1)
        series.refresh_from_db(fields=["view_count"])
        _emit(
            event_type=types.CONTENT_DRAMA_SERIES_VIEWED,
            payload={"series_id": str(series.id), "occurred_at": _iso(_now())},
            idempotency_key=f"content_drama_viewed:{dedup_key}",
            actor_id=str(user_id) if user_id else None,
        )
    return {"series_id": str(series.id), "view_count": series.view_count}


def track_share(*, series_id: str, user_id: str | None = None, channel: str = "") -> dict[str, Any]:
    series = _get_active_series(series_id)
    DramaSeries.objects.filter(id=series.id).update(share_count=F("share_count") + 1)
    series.refresh_from_db(fields=["share_count"])
    _emit(
        event_type=types.CONTENT_DRAMA_SERIES_SHARED,
        payload={
            "series_id": str(series.id),
            "channel": channel or None,
            "occurred_at": _iso(_now()),
        },
        idempotency_key=f"content_drama_shared:{series.id}:{_now().timestamp()}",
        actor_id=str(user_id) if user_id else None,
    )
    return {"series_id": str(series.id), "share_count": series.share_count}
