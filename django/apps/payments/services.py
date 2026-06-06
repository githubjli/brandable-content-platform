"""Service layer for payments (payments.md §5).

PaymentOrderService owns the Order state machine and the provider adapters. It
never credits wallets directly — on PAID it emits ``payments.OrderPaid`` and the
business app (e.g. economy) reacts via an event handler.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import transaction

from libs.errors.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)

from .adapters import BlockchainAdapter, StripeAdapter
from .models import BUSINESS_KINDS, PROVIDERS, Order, WebhookEvent

logger = logging.getLogger(__name__)
_CENT = Decimal("0.0001")


# ---------------------------------------------------------------------------
# Cross-app stubs (events / audit)
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


def _record_audit(
    action: str,
    *,
    actor_id: str | None,
    target_id: str,
    actor_type: str = "system",
    target_type: str = "Order",
    after_state: dict | None = None,
    severity: str = "info",
) -> None:
    from apps.audit.services import record_audit

    record_audit(
        action=action,
        actor_type=actor_type,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
        after_state=after_state,
        severity=severity,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(_CENT)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def _order_no() -> str:
    return f"ORD-{_now():%Y%m%d}-{uuid.uuid4().hex[:12].upper()}"


def _event_payload(order: Order) -> dict[str, Any]:
    return {
        "order_no": order.order_no,
        "business_kind": order.business_kind,
        "business_ref_id": str(order.business_ref_id),
        "user_id": str(order.user_id),
        "amount": str(order.amount),
        "currency": order.currency,
        "idempotency_key": order.idempotency_key,
    }


def _payment_block(order: Order, client_secret: str | None = None) -> dict[str, Any]:
    provider = order.payment_provider
    if provider == "stripe":
        return {
            "provider": "stripe",
            "intent_id": order.provider_intent_id or None,
            "client_secret": client_secret,
        }
    if provider == "blockchain":
        confirmations = BlockchainAdapter(order.blockchain_network).get_required_confirmations()
        return {
            "provider": "blockchain",
            "blockchain_network": order.blockchain_network,
            "expected_amount": str(order.expected_amount),
            "expected_currency": order.expected_currency,
            "pay_to_address": order.pay_to_address or None,
            "required_confirmations": confirmations,
            "txid": order.provider_intent_id or None,
        }
    return {
        "provider": provider,
        "expected_amount": str(order.expected_amount),
        "expected_currency": order.expected_currency,
        "ledger_entry_id": order.provider_intent_id or None,
    }


def serialize_order(order: Order, client_secret: str | None = None) -> dict[str, Any]:
    return {
        "order_no": order.order_no,
        "business_kind": order.business_kind,
        "business_ref_id": str(order.business_ref_id),
        "amount": str(order.amount),
        "currency": order.currency,
        "status": order.status,
        "payment_provider": order.payment_provider,
        "payment": _payment_block(order, client_secret),
        "expected_amount": str(order.expected_amount),
        "expected_currency": order.expected_currency,
        "expires_at": _iso(order.expires_at),
        "paid_at": _iso(order.paid_at),
        "created_at": _iso(order.created_at),
    }


def _get_owned(order_no: str, user_id: str) -> Order:
    try:
        return Order.objects.get(order_no=order_no, user_id=user_id)
    except Order.DoesNotExist:
        raise NotFoundError(code="ORDER_NOT_FOUND", message="Order not found.")


# ---------------------------------------------------------------------------
# Order lifecycle
# ---------------------------------------------------------------------------


def create_order(
    *,
    user_id: str,
    business_kind: str,
    business_ref_id: str,
    amount: Any,
    currency: str,
    payment_provider: str,
    idempotency_key: str,
    blockchain_network: str = "",
) -> dict[str, Any]:
    """Create a PENDING_PAYMENT order and prime the provider. Idempotent."""
    if business_kind not in BUSINESS_KINDS:
        raise ValidationError(code="ORDER_INVALID_BUSINESS_KIND", message=f"{business_kind!r}.")
    if payment_provider not in PROVIDERS:
        raise ValidationError(code="ORDER_INVALID_PROVIDER", message=f"{payment_provider!r}.")
    if payment_provider == "blockchain" and not blockchain_network:
        raise ValidationError(
            code="ORDER_BLOCKCHAIN_NETWORK_REQUIRED",
            message="blockchain_network is required for blockchain orders.",
        )

    existing = Order.objects.filter(idempotency_key=idempotency_key).first()
    if existing is not None:
        return serialize_order(existing)

    money = _money(amount)
    with transaction.atomic():
        order = Order.objects.create(
            order_no=_order_no(),
            business_kind=business_kind,
            business_ref_id=business_ref_id,
            user_id=user_id,
            amount=money,
            currency=currency,
            payment_provider=payment_provider,
            blockchain_network=blockchain_network,
            expected_amount=money,
            expected_currency=currency,
            expires_at=_now() + timedelta(seconds=settings.PAYMENT_ORDER_TTL_SECONDS),
            idempotency_key=idempotency_key,
        )

        client_secret: str | None = None
        if payment_provider == "stripe":
            intent = StripeAdapter().create_payment_intent(order)
            order.provider_intent_id = intent["intent_id"]
            client_secret = intent["client_secret"]
            order.save(update_fields=["provider_intent_id", "updated_at"])
        elif payment_provider == "blockchain":
            order.pay_to_address = BlockchainAdapter(blockchain_network).get_pay_to_address(
                currency
            )
            order.save(update_fields=["pay_to_address", "updated_at"])

        _emit(
            event_type="payments.OrderCreated",
            payload=_event_payload(order),
            idempotency_key=f"order_created:{order.order_no}",
            actor_id=str(user_id),
        )
        _record_audit(
            action="payments.order.create",
            actor_type="user",
            actor_id=str(user_id),
            target_id=str(order.id),
            after_state={"order_no": order.order_no, "provider": payment_provider},
        )

    return serialize_order(order, client_secret=client_secret)


def get_order(*, order_no: str, user_id: str) -> dict[str, Any]:
    return serialize_order(_get_owned(order_no, user_id))


def settle_wallet_order(*, order_no: str, ledger_entry_id: str) -> dict[str, Any]:
    """Mark a wallet-provider Order PAID after the caller has debited the wallet.

    Used by business apps (e.g. commerce) that pay with internal MP/MC: they
    perform the EconomyService.debit, then call this so payments stays the owner
    of Order state and `payments.OrderPaid` fires uniformly with the Stripe path.
    """
    try:
        order = Order.objects.get(order_no=order_no)
    except Order.DoesNotExist:
        raise NotFoundError(code="ORDER_NOT_FOUND", message="Order not found.")
    if order.payment_provider != "wallet":
        raise ValidationError(
            code="ORDER_NOT_WALLET", message="Only wallet orders settle this way."
        )
    with transaction.atomic():
        _mark_paid(order, intent_id=ledger_entry_id)
    return serialize_order(order)


def orders_queryset(
    *,
    user_id: str,
    status: str | None = None,
    business_kind: str | None = None,
    date_from: str | None = None,
):
    qs = Order.objects.filter(user_id=user_id)
    if status:
        qs = qs.filter(status__in=[s.strip().lower() for s in status.split(",")])
    if business_kind:
        qs = qs.filter(business_kind__in=[b.strip().upper() for b in business_kind.split(",")])
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    return qs


def _mark_paid(order: Order, *, intent_id: str | None = None) -> None:
    if order.status == Order.PAID:
        return  # idempotent
    if order.status not in (Order.PENDING_PAYMENT, Order.AUTHORIZED):
        raise ConflictError(
            code="ORDER_NOT_PAYABLE", message=f"Order in status {order.status} cannot be paid."
        )
    order.status = Order.PAID
    order.paid_at = _now()
    if intent_id:
        order.provider_intent_id = intent_id
    order.save(update_fields=["status", "paid_at", "provider_intent_id", "updated_at"])
    _emit(
        event_type="payments.OrderPaid",
        payload={**_event_payload(order), "paid_at": _iso(order.paid_at)},
        idempotency_key=f"order_paid:{order.order_no}:PAID",
        actor_id=str(order.user_id),
    )


def _mark_failed(order: Order, reason: str) -> None:
    if order.status in Order.TERMINAL:
        return
    order.status = Order.FAILED
    order.last_error = reason
    order.save(update_fields=["status", "last_error", "updated_at"])
    _emit(
        event_type="payments.OrderFailed",
        payload={**_event_payload(order), "reason": reason},
        idempotency_key=f"order_failed:{order.order_no}:FAILED",
        actor_id=str(order.user_id),
    )


def verify_order(*, order_no: str, user_id: str, txid: str) -> dict[str, Any]:
    """Blockchain verify-now: submit a txid and dispatch to the network backend."""
    order = _get_owned(order_no, user_id)
    if order.payment_provider != "blockchain":
        raise ValidationError(
            code="ORDER_NOT_VERIFIABLE", message="Only blockchain orders are verified by txid."
        )

    adapter = BlockchainAdapter(order.blockchain_network)
    result = adapter.verify_txid(
        txid, order.expected_amount, order.expected_currency, order.pay_to_address
    )
    order.provider_intent_id = txid
    order.save(update_fields=["provider_intent_id", "updated_at"])

    if result.verified:
        with transaction.atomic():
            _mark_paid(order, intent_id=txid)
        status = Order.PAID
    elif result.pending:
        status = "verifying"
    else:
        with transaction.atomic():
            _mark_failed(order, result.error or "verification failed")
        status = Order.FAILED

    return {
        "order_no": order.order_no,
        "status": status,
        "verification": {
            "txid": txid,
            "confirmations": result.confirmations,
            "required_confirmations": result.required_confirmations,
            "verified_at": _iso(order.paid_at) if result.verified else None,
        },
    }


def cancel_order(*, order_no: str, actor_id: str) -> dict[str, Any]:
    order = _get_owned(order_no, actor_id)
    if order.status not in (Order.PENDING_PAYMENT, Order.AUTHORIZED):
        raise ConflictError(
            code="ORDER_NOT_CANCELLABLE",
            message=f"Order in status {order.status} cannot be cancelled.",
        )
    with transaction.atomic():
        order.status = Order.CANCELLED
        order.save(update_fields=["status", "updated_at"])
        _emit(
            event_type="payments.OrderCancelled",
            payload={**_event_payload(order), "cancel_reason": "user_cancelled"},
            idempotency_key=f"order_cancelled:{order.order_no}:CANCELLED",
            actor_id=str(actor_id),
        )
    return serialize_order(order)


def initiate_refund(*, order_no: str, amount: Any, reason: str, actor_id: str) -> dict[str, Any]:
    """Admin/commerce-initiated refund: PAID -> REFUNDING (payments.md §7)."""
    try:
        order = Order.objects.get(order_no=order_no)
    except Order.DoesNotExist:
        raise NotFoundError(code="ORDER_NOT_FOUND", message="Order not found.")
    if order.status != Order.PAID:
        raise ConflictError(
            code="ORDER_NOT_REFUNDABLE", message=f"Order in status {order.status} cannot refund."
        )
    with transaction.atomic():
        order.status = Order.REFUNDING
        order.refund_reason = reason
        order.save(update_fields=["status", "refund_reason", "updated_at"])
        if order.payment_provider == "stripe" and order.provider_intent_id:
            StripeAdapter().refund(order.provider_intent_id, _money(amount))
        _emit(
            event_type="payments.OrderRefundInitiated",
            payload={**_event_payload(order), "refund_amount": str(_money(amount))},
            idempotency_key=f"order_refund_initiated:{order.order_no}",
            actor_id=str(actor_id),
        )
        _record_audit(
            action="payments.order.refund_initiate",
            actor_type="admin",
            actor_id=str(actor_id),
            target_id=str(order.id),
            after_state={"amount": str(_money(amount)), "reason": reason},
            severity="sensitive",
        )
    return serialize_order(order)


def _complete_refund(order: Order) -> None:
    if order.status == Order.REFUNDED:
        return
    order.status = Order.REFUNDED
    order.refunded_at = _now()
    order.save(update_fields=["status", "refunded_at", "updated_at"])
    _emit(
        event_type="payments.OrderRefunded",
        payload={**_event_payload(order), "refunded_at": _iso(order.refunded_at)},
        idempotency_key=f"order_refunded:{order.order_no}:REFUNDED",
        actor_id=str(order.user_id),
    )


def expire_due_orders() -> int:
    """Mark pending orders past their TTL as EXPIRED. Returns count."""
    due = Order.objects.filter(status=Order.PENDING_PAYMENT, expires_at__lt=_now())
    count = 0
    for order in list(due):
        with transaction.atomic():
            order.status = Order.EXPIRED
            order.save(update_fields=["status", "updated_at"])
            _emit(
                event_type="payments.OrderExpired",
                payload=_event_payload(order),
                idempotency_key=f"order_expired:{order.order_no}:EXPIRED",
                actor_id=str(order.user_id),
            )
        count += 1
    return count


# ---------------------------------------------------------------------------
# Stripe webhook ingestion (payments.md §4)
# ---------------------------------------------------------------------------


def handle_stripe_webhook(*, payload: bytes, signature: str) -> dict[str, Any]:
    adapter = StripeAdapter()
    try:
        adapter.verify_webhook(payload, signature)  # raises on bad signature
    except ValidationError:
        _record_audit(
            action="payments.webhook.signature_invalid",
            actor_type="system",
            actor_id=None,
            target_type="WebhookEvent",
            target_id=str(uuid.UUID(int=0)),
            severity="critical",
        )
        raise

    # Signature is verified above; route off the plain parsed body (avoids
    # StripeObject attribute quirks).
    body = json.loads(payload)
    event_id = body["id"]
    event_type = body["type"]
    obj = body.get("data", {}).get("object", {})

    webhook, created = WebhookEvent.objects.get_or_create(
        provider="stripe",
        event_id=event_id,
        defaults={
            "event_type": event_type,
            "signature_valid": True,
            "payload_hash": hashlib.sha256(payload).hexdigest(),
        },
    )
    if not created and webhook.processed:
        return {"status": "duplicate", "event_id": event_id}

    if event_type == "payment_intent.succeeded":
        _mark_paid_by_intent(obj.get("id", ""))
    elif event_type == "payment_intent.payment_failed":
        reason = (obj.get("last_payment_error") or {}).get("message", "failed")
        _mark_failed_by_intent(obj.get("id", ""), reason)
    elif event_type == "charge.refunded":
        _complete_refund_by_intent(obj.get("payment_intent", ""))

    webhook.processed = True
    webhook.save(update_fields=["processed", "updated_at"])

    _emit(
        event_type="payments.WebhookReceived",
        payload={"provider": "stripe", "event_id": event_id, "event_type": event_type},
        idempotency_key=f"webhook_received:stripe:{event_id}",
    )
    return {"status": "processed", "event_id": event_id}


def _order_by_intent(intent_id: str) -> Order | None:
    if not intent_id:
        return None
    return Order.objects.filter(provider_intent_id=intent_id).first()


def _mark_paid_by_intent(intent_id: str) -> None:
    order = _order_by_intent(intent_id)
    if order is not None:
        with transaction.atomic():
            _mark_paid(order, intent_id=intent_id)


def _mark_failed_by_intent(intent_id: str, reason: str) -> None:
    order = _order_by_intent(intent_id)
    if order is not None:
        with transaction.atomic():
            _mark_failed(order, reason)


def _complete_refund_by_intent(intent_id: str) -> None:
    order = _order_by_intent(intent_id)
    if order is not None:
        with transaction.atomic():
            _complete_refund(order)
