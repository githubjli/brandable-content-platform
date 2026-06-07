"""Service layer for membership (membership.md §0, §3).

The entitlement read path (get_active_membership) is the boundary other apps use;
grant_membership is the single write path that flips entitlement state. V1 is the
internal foundation — the user-facing HTTP endpoints are V2.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone

from libs.errors.exceptions import NotFoundError, ValidationError

from .models import MembershipOrder, MembershipPlan, UserMembership

logger = logging.getLogger(__name__)

_CENT = Decimal("0.0001")
_ORDER_PROVIDERS = {"stripe", "wallet"}
_WALLET_ASSETS = {"MP", "MC"}


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(_CENT)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _order_no() -> str:
    return f"MO-{_now():%Y%m%d}-{uuid.uuid4().hex[:12].upper()}"


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


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def get_active_membership(*, user_id: str) -> UserMembership | None:
    """Return the user's current active (non-expired) membership, or None."""
    now = timezone.now()
    membership = (
        UserMembership.objects.select_related("plan")
        .filter(user_id=user_id, status=UserMembership.ACTIVE)
        .order_by("-ends_at")
        .first()
    )
    if membership is None:
        return None
    if membership.is_expired(now=now):
        return None
    return membership


def has_active_membership(*, user_id: str) -> bool:
    return get_active_membership(user_id=user_id) is not None


def serialize_membership(membership: UserMembership | None) -> dict[str, Any]:
    """Public /membership/me shape (the V2 endpoint will reuse this)."""
    if membership is None:
        return {"active_membership": None}
    now = timezone.now()
    days_remaining = None
    if membership.ends_at is not None:
        days_remaining = max(0, (membership.ends_at - now).days)
    return {
        "user_id": str(membership.user_id),
        "plan": {
            "id": str(membership.plan_id),
            "code": membership.plan.code,
            "name": membership.plan.name,
        },
        "status": membership.status,
        "starts_at": _iso(membership.starts_at),
        "ends_at": _iso(membership.ends_at),
        "is_expired": membership.is_expired(now=now),
        "days_remaining": days_remaining,
        "auto_renew": membership.auto_renew,
        "subscription_id": str(membership.subscription_id) if membership.subscription_id else None,
    }


def grant_membership(
    *,
    user_id: str,
    plan_code: str,
    duration_days: int | None = None,
    source: str = "purchase",
    idempotency_key: str,
) -> UserMembership:
    """Grant (or extend) a membership. Expires any existing active row so a user
    holds at most one active membership. Emits membership.MembershipGranted."""
    try:
        plan = MembershipPlan.objects.get(code=plan_code, is_active=True)
    except MembershipPlan.DoesNotExist:
        raise NotFoundError(code="PLAN_NOT_FOUND", message=f"No active plan '{plan_code}'.")

    now = datetime.now(tz=UTC)
    days = duration_days if duration_days is not None else plan.duration_days
    ends_at = now + timedelta(days=days) if days else None

    with transaction.atomic():
        UserMembership.objects.filter(user_id=user_id, status=UserMembership.ACTIVE).update(
            status=UserMembership.EXPIRED
        )
        membership = UserMembership.objects.create(
            user_id=user_id,
            plan=plan,
            status=UserMembership.ACTIVE,
            starts_at=now,
            ends_at=ends_at,
            source=source,
        )
        _emit(
            event_type="membership.MembershipGranted",
            payload={
                "user_id": str(user_id),
                "membership_id": str(membership.id),
                "plan_code": plan.code,
                "ends_at": _iso(ends_at),
                "occurred_at": _iso(now),
            },
            idempotency_key=idempotency_key,
            actor_id=str(user_id),
        )
    return membership


# ---------------------------------------------------------------------------
# V2 — plans, current membership, one-shot purchase
# ---------------------------------------------------------------------------


def serialize_plan(plan: MembershipPlan) -> dict[str, Any]:
    return {
        "id": str(plan.id),
        "code": plan.code,
        "name": plan.name,
        "duration_days": plan.duration_days,
        "price": {"amount": str(plan.price_amount), "currency": plan.price_currency},
    }


def list_plans() -> dict[str, Any]:
    plans = MembershipPlan.objects.filter(is_active=True)
    return {"results": [serialize_plan(p) for p in plans]}


def get_my_membership(*, user_id: str) -> dict[str, Any]:
    return serialize_membership(get_active_membership(user_id=user_id))


def serialize_order(order: MembershipOrder, payment: dict | None = None) -> dict[str, Any]:
    return {
        "order_no": order.order_no,
        "plan": {"id": str(order.plan_id), "code": order.plan.code, "name": order.plan.name},
        "amount": {"amount": str(order.amount), "currency": order.currency},
        "status": order.status,
        "payment": payment
        or {"provider": order.payment_provider, "payment_order_no": order.payment_order_no or None},
        "paid_at": _iso(order.paid_at),
        "created_at": _iso(order.created_at),
    }


def create_order(
    *,
    user_id: str,
    plan_id: str,
    payment_provider: str,
    payment_asset: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Create a one-shot membership purchase. Wallet pays immediately; Stripe
    settles via webhook. Both grant the membership through the PAYMENTS_ORDER_PAID
    handler."""
    if payment_provider not in _ORDER_PROVIDERS:
        raise ValidationError(
            code="ORDER_PROVIDER_UNSUPPORTED", message="payment_provider must be stripe or wallet."
        )
    if not idempotency_key:
        raise ValidationError(
            code="ORDER_IDEMPOTENCY_KEY_REQUIRED", message="idempotency_key required."
        )

    existing = MembershipOrder.objects.filter(idempotency_key=idempotency_key).first()
    if existing is not None:
        return serialize_order(existing)

    from apps.payments.services import create_order as payments_create_order
    from apps.payments.services import settle_wallet_order

    with transaction.atomic():
        try:
            plan = MembershipPlan.objects.get(id=plan_id, is_active=True)
        except MembershipPlan.DoesNotExist:
            raise NotFoundError(code="PLAN_NOT_FOUND", message="Plan not found.")

        if payment_asset != plan.price_currency:
            raise ValidationError(
                code="ORDER_ASSET_MISMATCH",
                message=f"payment_asset must be {plan.price_currency} for this plan.",
            )
        if payment_provider == "wallet" and payment_asset not in _WALLET_ASSETS:
            raise ValidationError(
                code="ORDER_ASSET_UNSUPPORTED", message="Wallet payment requires MP or MC."
            )

        amount = _money(plan.price_amount)
        order = MembershipOrder.objects.create(
            order_no=_order_no(),
            user_id=user_id,
            plan=plan,
            amount=amount,
            currency=plan.price_currency,
            payment_provider=payment_provider,
            payment_asset=payment_asset,
            idempotency_key=idempotency_key,
        )
        payment = payments_create_order(
            user_id=str(user_id),
            business_kind="MEMBERSHIP",
            business_ref_id=str(order.id),
            amount=amount,
            currency=plan.price_currency,
            payment_provider=payment_provider,
            idempotency_key=f"membership_order:{order.id}",
        )
        order.payment_order_no = payment["order_no"]
        order.save(update_fields=["payment_order_no", "updated_at"])

        if payment_provider == "wallet":
            from apps.economy.services import debit as economy_debit

            ledger = economy_debit(
                user_id=str(user_id),
                currency=payment_asset,
                entry_type="SPEND",
                amount=amount,
                idempotency_key=f"membership_spend:{order.id}",
                target_type="MembershipOrder",
                target_id=str(order.id),
                note=f"Membership order {order.order_no}",
            )
            settle_wallet_order(order_no=order.payment_order_no, ledger_entry_id=ledger["id"])

        _emit(
            event_type="membership.MembershipOrderCreated",
            payload={
                "order_no": order.order_no,
                "user_id": str(user_id),
                "plan_code": plan.code,
                "amount": str(amount),
                "currency": plan.price_currency,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"membership_order_created:{order.order_no}",
            actor_id=str(user_id),
        )
    return serialize_order(order, payment=payment["payment"])


def mark_order_paid(*, membership_order_id: str) -> None:
    """Settle a membership order and grant the membership. Idempotent."""
    try:
        order = MembershipOrder.objects.select_related("plan").get(id=membership_order_id)
    except MembershipOrder.DoesNotExist:
        return
    if order.status == MembershipOrder.PAID:
        return

    with transaction.atomic():
        order.status = MembershipOrder.PAID
        order.paid_at = _now()
        order.save(update_fields=["status", "paid_at", "updated_at"])
        grant_membership(
            user_id=str(order.user_id),
            plan_code=order.plan.code,
            source="purchase",
            idempotency_key=f"membership_grant:{order.id}",
        )
