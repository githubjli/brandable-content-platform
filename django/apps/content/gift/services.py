"""Service layer for content.gift (gift.md §1-4).

Amount-based cross-content gifting. send_gift debits the sender (SPEND) and
credits the content owner (GIFT_RECEIVED) through apps/economy, then records an
immutable GiftTransaction. The catalog is display-only and never affects charge
logic. Live-stream gifting is V3.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from django.db import transaction

from libs.errors.exceptions import UnprocessableError, ValidationError

from .models import GiftCatalogItem, GiftTransaction

logger = logging.getLogger(__name__)

_CENT = Decimal("0.0001")
_PAYMENT_CURRENCY = {"meow_points": "MP", "meow_credit": "MC"}
_TARGET_EVENT = {
    GiftTransaction.VIDEO: "content.VideoGifted",
    GiftTransaction.DRAMA_SERIES: "content.DramaGifted",
}


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


def _resolve_receiver(target_type: str, target_id: str) -> str:
    """Validate the target exists and return its owner (the gift receiver)."""
    if target_type == GiftTransaction.VIDEO:
        from apps.content.video.services import gift_target as video_gift_target

        return video_gift_target(target_id)
    if target_type == GiftTransaction.DRAMA_SERIES:
        from apps.content.drama.services import gift_target as drama_gift_target

        return drama_gift_target(target_id)
    raise ValidationError(code="GIFT_TARGET_TYPE_INVALID", message="Unsupported gift target.")


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def serialize_catalog_item(item: GiftCatalogItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "code": item.code,
        "name": item.name,
        "emoji": item.emoji or None,
        "icon_url": item.icon_url or None,
        "animation_url": item.animation_url or None,
        "preset_amount": str(item.preset_amount),
        "preset_currency": item.preset_currency,
        "is_active": item.is_active,
        "sort_order": item.sort_order,
    }


def list_catalog() -> dict[str, Any]:
    items = GiftCatalogItem.objects.filter(is_active=True)
    return {"results": [serialize_catalog_item(i) for i in items]}


# ---------------------------------------------------------------------------
# Send + history
# ---------------------------------------------------------------------------


def serialize_transaction(txn: GiftTransaction) -> dict[str, Any]:
    return {
        "id": str(txn.id),
        "sender_id": str(txn.sender_id),
        "receiver_id": str(txn.receiver_id),
        "target": {"type": txn.target_type, "id": str(txn.target_id)},
        "amount": str(txn.amount),
        "currency": txn.currency,
        "payment_method": txn.payment_method,
        "gift_code": txn.gift_code or None,
        "created_at": _iso(txn.created_at),
    }


def _send_result(
    txn: GiftTransaction, *, sender_balance: Any, receiver_balance: Any
) -> dict[str, Any]:
    return {
        "transaction": serialize_transaction(txn),
        "sender_balance": {"currency": txn.currency, "amount": str(sender_balance)},
        "receiver_balance": {"currency": txn.currency, "amount": str(receiver_balance)},
    }


def send_gift(
    *,
    sender_id: str,
    target_type: str,
    target_id: str,
    amount: Any,
    currency: str,
    payment_method: str,
    idempotency_key: str,
    gift_code: str = "",
) -> dict[str, Any]:
    if payment_method not in _PAYMENT_CURRENCY:
        raise ValidationError(
            code="GIFT_INVALID_PAYMENT_METHOD",
            message="payment_method must be meow_points or meow_credit.",
        )
    if currency != _PAYMENT_CURRENCY[payment_method]:
        raise ValidationError(
            code="GIFT_CURRENCY_MISMATCH",
            message=f"currency must be {_PAYMENT_CURRENCY[payment_method]} for {payment_method}.",
        )
    amt = _money(amount)
    if amt <= 0:
        raise UnprocessableError(code="GIFT_AMOUNT_INVALID", message="amount must be positive.")
    if not idempotency_key:
        raise ValidationError(
            code="GIFT_IDEMPOTENCY_KEY_REQUIRED", message="idempotency_key required."
        )

    from apps.economy.services import credit as economy_credit
    from apps.economy.services import debit as economy_debit
    from apps.economy.services import get_balance

    existing = GiftTransaction.objects.filter(idempotency_key=idempotency_key).first()
    if existing is not None:
        return _send_result(
            existing,
            sender_balance=get_balance(user_id=str(existing.sender_id), currency=existing.currency),
            receiver_balance=get_balance(
                user_id=str(existing.receiver_id), currency=existing.currency
            ),
        )

    receiver_id = _resolve_receiver(target_type, target_id)
    if str(receiver_id) == str(sender_id):
        raise UnprocessableError(
            code="GIFT_SELF_SEND_FORBIDDEN", message="You cannot gift your own content."
        )

    with transaction.atomic():
        sender_ledger = economy_debit(
            user_id=str(sender_id),
            currency=currency,
            entry_type="SPEND",
            amount=amt,
            idempotency_key=f"gift_debit:{idempotency_key}",
            target_type=f"Gift:{target_type}",
            target_id=str(target_id),
            note="Gift sent",
        )
        receiver_ledger = economy_credit(
            user_id=str(receiver_id),
            currency=currency,
            entry_type="GIFT_RECEIVED",
            amount=amt,
            idempotency_key=f"gift_credit:{idempotency_key}",
            target_type=f"Gift:{target_type}",
            target_id=str(target_id),
            note="Gift received",
        )
        txn = GiftTransaction.objects.create(
            idempotency_key=idempotency_key,
            sender_id=sender_id,
            receiver_id=receiver_id,
            target_type=target_type,
            target_id=target_id,
            amount=amt,
            currency=currency,
            payment_method=payment_method,
            gift_code=gift_code or "",
            sender_wallet_ledger_id=sender_ledger["id"],
            receiver_wallet_ledger_id=receiver_ledger["id"],
        )
        _emit(
            event_type=_TARGET_EVENT.get(target_type, "content.Gifted"),
            payload={
                "gift_id": str(txn.id),
                "sender_id": str(sender_id),
                "receiver_id": str(receiver_id),
                "target_type": target_type,
                "target_id": str(target_id),
                "amount": str(amt),
                "currency": currency,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"content_gifted:{txn.id}",
            actor_id=str(sender_id),
        )
    return _send_result(
        txn,
        sender_balance=sender_ledger["balance_after"],
        receiver_balance=receiver_ledger["balance_after"],
    )


def sent_queryset(*, user_id: str):
    return GiftTransaction.objects.filter(sender_id=user_id)


def received_queryset(*, user_id: str):
    return GiftTransaction.objects.filter(receiver_id=user_id)
