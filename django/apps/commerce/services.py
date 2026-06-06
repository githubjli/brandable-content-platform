"""Service layer for commerce (commerce.md §3, V1-AVS).

Commerce orchestrates the purchase chain: it owns the ProductOrder, delegates the
payment to apps/payments, and (for wallet payment) the debit to apps/economy. It
never touches Stripe/blockchain adapters or wallet rows directly. Payment
settlement flows back uniformly through the `payments.OrderPaid` event, handled in
apps/commerce/handlers.py.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import transaction

from libs.errors.exceptions import NotFoundError, UnprocessableError, ValidationError

from .models import Product, ProductOrder

logger = logging.getLogger(__name__)
_CENT = Decimal("0.0001")
_AVS_PROVIDERS = {"stripe", "wallet"}  # blockchain product payment is V2
_WALLET_ASSETS = {"MP", "MC"}


# ---------------------------------------------------------------------------
# Cross-app stubs
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
    action: str, *, actor_id: str | None, target_id: str, after_state: dict | None = None
) -> None:
    from apps.audit.services import record_audit

    record_audit(
        action=action,
        actor_type="user",
        actor_id=actor_id,
        target_type="ProductOrder",
        target_id=target_id,
        after_state=after_state,
        severity="info",
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
    return f"CO-{_now():%Y%m%d}-{uuid.uuid4().hex[:12].upper()}"


def _fee_rate() -> Decimal:
    return Decimal(str(getattr(settings, "COMMERCE_PLATFORM_FEE_RATE", "0.05")))


def _compute_amounts(unit_price: Decimal, quantity: int) -> tuple[Decimal, Decimal, Decimal]:
    subtotal = _money(unit_price * quantity)
    platform_fee = _money(subtotal * _fee_rate())
    seller_receivable = _money(subtotal - platform_fee)
    return subtotal, platform_fee, seller_receivable


def _money_obj(amount: Decimal, currency: str) -> dict[str, str]:
    return {"amount": str(amount), "currency": currency}


def serialize_order(order: ProductOrder, payment: dict | None = None) -> dict[str, Any]:
    return {
        "order_no": order.order_no,
        "product_snapshot": order.product_snapshot,
        "quantity": order.quantity,
        "amounts": {
            "subtotal": _money_obj(order.subtotal, order.currency),
            "platform_fee": _money_obj(order.platform_fee, order.currency),
            "seller_receivable": _money_obj(order.seller_receivable, order.currency),
        },
        "shipping_address_snapshot": order.shipping_address_snapshot,
        "seller_store": {
            "id": str(order.store_id),
            "slug": order.store.slug,
            "name": order.store.name,
        },
        "status": order.status,
        "payment": payment
        or {
            "provider": order.payment_provider,
            "payment_order_no": order.payment_order_no or None,
        },
        "expires_at": _iso(order.expires_at),
        "paid_at": _iso(order.paid_at),
        "created_at": _iso(order.created_at),
    }


# ---------------------------------------------------------------------------
# Create / get / cancel
# ---------------------------------------------------------------------------


def create_order(
    *,
    user_id: str,
    product_id: str,
    quantity: int,
    payment_provider: str,
    payment_asset: str,
    idempotency_key: str,
    shipping_address_id: str | None = None,
) -> dict[str, Any]:
    """Create a ProductOrder + linked payments.Order. Idempotent on idempotency_key.

    Wallet payment debits MP/MC immediately and settles the order; Stripe payment
    returns a client_secret and settles later via webhook. Both flip the order to
    `paid` through the `payments.OrderPaid` handler.
    """
    if quantity < 1:
        raise ValidationError(code="ORDER_INVALID_QUANTITY", message="quantity must be >= 1.")
    if payment_provider not in _AVS_PROVIDERS:
        raise ValidationError(
            code="ORDER_PROVIDER_UNSUPPORTED",
            message="V1-AVS supports stripe or wallet payment only.",
        )
    if not idempotency_key:
        raise ValidationError(
            code="ORDER_IDEMPOTENCY_KEY_REQUIRED", message="idempotency_key required."
        )

    existing = ProductOrder.objects.filter(idempotency_key=idempotency_key).first()
    if existing is not None:
        return serialize_order(existing)

    from apps.payments.services import create_order as payments_create_order
    from apps.payments.services import settle_wallet_order

    with transaction.atomic():
        try:
            product = (
                Product.objects.select_for_update()
                .select_related("store")
                .get(id=product_id, is_active=True)
            )
        except Product.DoesNotExist:
            raise NotFoundError(code="PRODUCT_NOT_FOUND", message="Product not found.")

        if payment_asset != product.price_currency:
            raise ValidationError(
                code="ORDER_ASSET_MISMATCH",
                message=f"payment_asset must be {product.price_currency} for this product.",
            )
        if payment_provider == "wallet" and payment_asset not in _WALLET_ASSETS:
            raise ValidationError(
                code="ORDER_ASSET_UNSUPPORTED", message="Wallet payment requires MP or MC."
            )

        if product.stock < quantity:
            raise UnprocessableError(
                code="STOCK_INSUFFICIENT",
                message="Not enough stock.",
                detail={"requested": quantity, "available": product.stock},
            )
        product.stock -= quantity
        product.save(update_fields=["stock", "updated_at"])

        subtotal, platform_fee, seller_receivable = _compute_amounts(product.price_amount, quantity)
        order = ProductOrder.objects.create(
            order_no=_order_no(),
            buyer_user_id=user_id,
            product=product,
            store=product.store,
            product_snapshot={
                "id": str(product.id),
                "title": product.title,
                "cover_image_url": product.cover_image_url or None,
                "price_at_order": _money_obj(product.price_amount, product.price_currency),
            },
            quantity=quantity,
            currency=product.price_currency,
            subtotal=subtotal,
            platform_fee=platform_fee,
            seller_receivable=seller_receivable,
            payment_provider=payment_provider,
            payment_asset=payment_asset,
            expires_at=_now() + timedelta(seconds=settings.COMMERCE_ORDER_TTL_SECONDS),
            idempotency_key=idempotency_key,
        )

        payment = payments_create_order(
            user_id=str(user_id),
            business_kind="PRODUCT",
            business_ref_id=str(order.id),
            amount=subtotal,
            currency=product.price_currency,
            payment_provider=payment_provider,
            idempotency_key=f"product_order:{order.id}",
        )
        order.payment_order_no = payment["order_no"]
        order.save(update_fields=["payment_order_no", "updated_at"])

        if payment_provider == "wallet":
            from apps.economy.services import debit as economy_debit

            ledger = economy_debit(
                user_id=str(user_id),
                currency=payment_asset,
                entry_type="SPEND",
                amount=subtotal,
                idempotency_key=f"product_spend:{order.id}",
                target_type="ProductOrder",
                target_id=str(order.id),
                note=f"Product order {order.order_no}",
            )
            # Settle the payment Order → emits payments.OrderPaid (handled async).
            settle_wallet_order(order_no=order.payment_order_no, ledger_entry_id=ledger["id"])

        _emit(
            event_type="commerce.OrderCreated",
            payload={
                "order_no": order.order_no,
                "buyer_user_id": str(user_id),
                "product_id": str(product.id),
                "store_id": str(order.store_id),
                "subtotal": str(subtotal),
                "currency": product.price_currency,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"commerce_order_created:{order.order_no}",
            actor_id=str(user_id),
        )
        _record_audit(
            action="commerce.order.create",
            actor_id=str(user_id),
            target_id=str(order.id),
            after_state={"order_no": order.order_no, "provider": payment_provider},
        )

    return serialize_order(order, payment=payment["payment"])


def get_order(*, order_no: str, user_id: str) -> dict[str, Any]:
    try:
        order = ProductOrder.objects.select_related("store").get(
            order_no=order_no, buyer_user_id=user_id
        )
    except ProductOrder.DoesNotExist:
        raise NotFoundError(code="ORDER_NOT_FOUND", message="Order not found.")
    return serialize_order(order)


def cancel_order(*, order_no: str, user_id: str, reason: str = "") -> dict[str, Any]:
    try:
        order = (
            ProductOrder.objects.select_for_update()
            .select_related("store")
            .get(order_no=order_no, buyer_user_id=user_id)
        )
    except ProductOrder.DoesNotExist:
        raise NotFoundError(code="ORDER_NOT_FOUND", message="Order not found.")

    # V1-AVS: only unpaid orders can be cancelled (paid → refund is V2).
    if order.status != ProductOrder.PENDING_PAYMENT:
        from libs.errors.exceptions import ConflictError

        raise ConflictError(
            code="ORDER_NOT_CANCELLABLE",
            message=f"Order in status {order.status} cannot be cancelled.",
        )

    with transaction.atomic():
        # Release the reserved stock.
        Product.objects.filter(id=order.product_id).update(stock=_stock_plus(order))
        order.status = ProductOrder.CANCELLED
        order.cancelled_at = _now()
        order.cancel_reason = reason
        order.save(update_fields=["status", "cancelled_at", "cancel_reason", "updated_at"])

        # Best-effort cancel of the linked payment order.
        if order.payment_order_no:
            try:
                from apps.payments.services import cancel_order as payments_cancel

                payments_cancel(order_no=order.payment_order_no, actor_id=str(user_id))
            except Exception:
                logger.debug("cancel_order: payment order already terminal; skipping")

        _emit(
            event_type="commerce.OrderCancelled",
            payload={
                "order_no": order.order_no,
                "buyer_user_id": str(user_id),
                "reason": reason,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"commerce_order_cancelled:{order.order_no}",
            actor_id=str(user_id),
        )
        _record_audit(
            action="commerce.order.cancel",
            actor_id=str(user_id),
            target_id=str(order.id),
            after_state={"reason": reason},
        )
    return serialize_order(order)


def _stock_plus(order: ProductOrder):
    from django.db.models import F

    return F("stock") + order.quantity


# ---------------------------------------------------------------------------
# Settlement (called by the payments.OrderPaid handler)
# ---------------------------------------------------------------------------


def mark_order_paid(*, product_order_id: str) -> None:
    """Flip a ProductOrder to paid once its payment settled. Idempotent."""
    try:
        order = ProductOrder.objects.get(id=product_order_id)
    except ProductOrder.DoesNotExist:
        return
    if order.status == ProductOrder.PAID:
        return

    with transaction.atomic():
        order.status = ProductOrder.PAID
        order.paid_at = _now()
        order.save(update_fields=["status", "paid_at", "updated_at"])
        _emit(
            event_type="commerce.OrderPaid",
            payload={
                "order_no": order.order_no,
                "buyer_user_id": str(order.buyer_user_id),
                "store_id": str(order.store_id),
                "seller_receivable": str(order.seller_receivable),
                "currency": order.currency,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"commerce_order_paid:{order.order_no}",
            actor_id=str(order.buyer_user_id),
        )
        _record_audit(
            action="commerce.order.paid",
            actor_id=str(order.buyer_user_id),
            target_id=str(order.id),
            after_state={"status": "paid"},
        )
