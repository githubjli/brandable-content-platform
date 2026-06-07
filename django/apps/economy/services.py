"""Service layer for economy.

This module is the **single write path** for wallet balances (ADR-0004 invariant 5).
`PointLedger`/`CreditLedger` rows are only ever created here; other apps reach
this through the documented functions, never by importing economy models
(enforced by import-linter's layered contract).

Public API uses `user_id` + `currency` ("MP"|"MC") to select the wallet+ledger
pair rather than the contract's raw `wallet_id`, because there are two wallet
tables and this is ergonomic across the app boundary (one wallet per user per
currency). Returned values are plain dicts, never model instances.

Cross-app stubs (events/audit) follow the same no-op-until-built pattern as
apps/identity/services.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import DecimalField, F, Sum
from django.db.models.functions import Coalesce

from libs.errors.exceptions import (
    AppError,
    ConflictError,
    NotFoundError,
    UnprocessableError,
    ValidationError,
)

from .models import (
    CREDIT_ENTRY_TYPES,
    POINT_ENTRY_TYPES,
    ZERO,
    CreditLedger,
    CreditPackage,
    CreditRecharge,
    CreditRedeemRequest,
    CreditWallet,
    DailyRewardClaim,
    PointLedger,
    PointWallet,
)

logger = logging.getLogger(__name__)

_CENT = Decimal("0.0001")

# currency -> (wallet model, ledger model, allowed entry types)
_REGISTRY = {
    "MP": (PointWallet, PointLedger, set(POINT_ENTRY_TYPES)),
    "MC": (CreditWallet, CreditLedger, set(CREDIT_ENTRY_TYPES)),
}


# ---------------------------------------------------------------------------
# Cross-app stubs
# ---------------------------------------------------------------------------


def _emit_outbox(
    event_type: str, payload: dict, idempotency_key: str, actor_id: str | None = None
) -> None:
    """Emit an OutboxEvent via events.services (EventBus). Emit failures (incl.
    idempotency-key collisions) are swallowed so they never break a wallet write."""
    try:
        from apps.events.services import emit

        emit(
            event_type=event_type,
            idempotency_key=idempotency_key,
            payload=payload,
            actor_id=actor_id,
        )
    except Exception:
        logger.debug("_emit_outbox: emit failed; skipping %s", event_type)


def _record_audit(
    action: str,
    *,
    actor_id: str | None,
    target_id: str,
    target_type: str,
    actor_type: str = "system",
    after_state: dict | None = None,
    severity: str = "info",
) -> None:
    """Write an AuditLog row in the caller's transaction (audit.md §4). Does not
    swallow — audit failure must roll the business write back."""
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


def _resolve(currency: str):
    try:
        return _REGISTRY[currency]
    except KeyError:
        raise ValidationError(
            code="WALLET_INVALID_CURRENCY", message=f"Unknown currency '{currency}'."
        )


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(_CENT)


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _next_utc_midnight(d: date) -> str:
    nxt = datetime(d.year, d.month, d.day, tzinfo=UTC) + timedelta(days=1)
    return _iso(nxt)


def _serialize_ledger(entry, currency: str) -> dict:
    return {
        "id": str(entry.id),
        "idempotency_key": entry.idempotency_key,
        "entry_type": entry.entry_type,
        "amount": str(entry.amount),
        "balance_before": str(entry.balance_before),
        "balance_after": str(entry.balance_after),
        "currency": currency,
        "target_type": entry.target_type or None,
        "target_id": str(entry.target_id) if entry.target_id else None,
        "note": entry.note,
        "created_at": _iso(entry.created_at),
    }


# ---------------------------------------------------------------------------
# Single write path
# ---------------------------------------------------------------------------


def _post(
    *,
    user_id: str,
    currency: str,
    entry_type: str,
    amount: Any,
    idempotency_key: str,
    direction: int,  # +1 credit, -1 debit
    target_type: str,
    target_id: str | None,
    note: str,
    actor_id: str | None,
) -> dict:
    wallet_model, ledger_model, allowed = _resolve(currency)

    if entry_type not in allowed:
        raise ValidationError(
            code="WALLET_INVALID_ENTRY_TYPE",
            message=f"'{entry_type}' is not valid for a {currency} wallet.",
        )
    amount = _money(amount)
    if amount <= 0:
        raise ValidationError(
            code="WALLET_INVALID_AMOUNT", message="Amount must be greater than zero."
        )
    if not idempotency_key:
        raise ValidationError(
            code="WALLET_IDEMPOTENCY_KEY_REQUIRED", message="idempotency_key is required."
        )

    with transaction.atomic():
        # Lock the wallet first so concurrent posts to the same wallet serialize;
        # the post-lock idempotency check then reliably sees a prior replay.
        try:
            wallet = wallet_model.objects.select_for_update().get(user_id=user_id)
        except wallet_model.DoesNotExist:
            raise NotFoundError(
                code="WALLET_NOT_FOUND", message=f"No {currency} wallet for this user."
            )

        existing = ledger_model.objects.filter(idempotency_key=idempotency_key).first()
        if existing is not None:
            return _serialize_ledger(existing, currency)

        before = wallet.balance
        after = before + amount if direction > 0 else before - amount
        if after < 0:
            raise UnprocessableError(
                code="WALLET_INSUFFICIENT_BALANCE",
                message="Insufficient balance for this transaction.",
            )

        entry = ledger_model(
            wallet=wallet,
            entry_type=entry_type,
            amount=amount,
            balance_before=before,
            balance_after=after,
            idempotency_key=idempotency_key,
            target_type=target_type,
            target_id=target_id,
            note=note,
            actor_id=actor_id,
        )
        entry.save()  # append-only insert

        wallet.balance = after
        wallet.save(update_fields=["balance", "updated_at"])

        event = "economy.WalletCredited" if direction > 0 else "economy.WalletDebited"
        slug = "wallet_credited" if direction > 0 else "wallet_debited"
        _emit_outbox(
            event_type=event,
            payload={
                "wallet_id": str(wallet.id),
                "user_id": str(user_id),
                "amount": str(amount),
                "currency": currency,
                "entry_type": entry_type,
                "idempotency_key": idempotency_key,
                "ledger_id": str(entry.id),
                "occurred_at": _iso(_now_utc()),
            },
            idempotency_key=f"{slug}:{wallet.id}:{entry.id}",
            actor_id=str(actor_id) if actor_id else str(user_id),
        )
        # Routine wallet writes are the immutable record via WalletLedger (ADR-0004);
        # only admin-mediated adjustments additionally go to AuditLog (audit.md §5).
        if entry_type == "ADMIN_ADJUST":
            _record_audit(
                action="economy.wallet.admin_adjust",
                actor_id=str(actor_id) if actor_id else None,
                target_id=str(wallet.id),
                target_type=wallet_model.__name__,
                actor_type="admin",
                after_state={"amount": str(amount), "currency": currency, "direction": direction},
                severity="sensitive" if direction > 0 else "critical",
            )

    return _serialize_ledger(entry, currency)


def credit(
    *,
    user_id: str,
    currency: str,
    entry_type: str,
    amount: Any,
    idempotency_key: str,
    target_type: str = "",
    target_id: str | None = None,
    note: str = "",
    actor_id: str | None = None,
) -> dict:
    """Add to a wallet. Idempotent on idempotency_key. Returns the ledger row dict."""
    return _post(
        user_id=user_id,
        currency=currency,
        entry_type=entry_type,
        amount=amount,
        idempotency_key=idempotency_key,
        direction=1,
        target_type=target_type,
        target_id=target_id,
        note=note,
        actor_id=actor_id,
    )


def debit(
    *,
    user_id: str,
    currency: str,
    entry_type: str,
    amount: Any,
    idempotency_key: str,
    target_type: str = "",
    target_id: str | None = None,
    note: str = "",
    actor_id: str | None = None,
) -> dict:
    """Subtract from a wallet. Rejects overdraw (WALLET_INSUFFICIENT_BALANCE)."""
    return _post(
        user_id=user_id,
        currency=currency,
        entry_type=entry_type,
        amount=amount,
        idempotency_key=idempotency_key,
        direction=-1,
        target_type=target_type,
        target_id=target_id,
        note=note,
        actor_id=actor_id,
    )


def get_balance(*, user_id: str, currency: str) -> Decimal:
    wallet_model, _, _ = _resolve(currency)
    try:
        return wallet_model.objects.get(user_id=user_id).balance
    except wallet_model.DoesNotExist:
        raise NotFoundError(code="WALLET_NOT_FOUND", message=f"No {currency} wallet for this user.")


def reconcile(*, user_id: str, currency: str) -> dict:
    """Compare wallet.balance to the latest ledger row's balance_after (ADR-0004)."""
    wallet_model, ledger_model, _ = _resolve(currency)
    wallet = wallet_model.objects.get(user_id=user_id)
    last = ledger_model.objects.filter(wallet=wallet).order_by("-created_at", "-id").first()
    ledger_balance = last.balance_after if last else ZERO
    reconciled = ledger_balance == wallet.balance
    if not reconciled:
        _emit_outbox(
            event_type="economy.WalletReconciliationMismatch",
            payload={
                "wallet_id": str(wallet.id),
                "user_id": str(user_id),
                "currency": currency,
                "wallet_balance": str(wallet.balance),
                "ledger_balance": str(ledger_balance),
                "occurred_at": _iso(_now_utc()),
            },
            idempotency_key=f"wallet_reconciliation_mismatch:{wallet.id}:{uuid.uuid4().hex}",
            actor_id=None,
        )
    return {
        "wallet_id": str(wallet.id),
        "currency": currency,
        "wallet_balance": str(wallet.balance),
        "ledger_balance": str(ledger_balance),
        "reconciled": reconciled,
    }


# ---------------------------------------------------------------------------
# Wallet provisioning (called by identity at registration)
# ---------------------------------------------------------------------------


def create_wallets_for_user(*, user_id: str) -> None:
    """Explicitly create both wallets for a user. Idempotent (ADR-0004)."""
    PointWallet.objects.get_or_create(user_id=user_id)
    CreditWallet.objects.get_or_create(user_id=user_id)


# ---------------------------------------------------------------------------
# Wallet + ledger read views
# ---------------------------------------------------------------------------

# entry_type -> totals bucket, computed from signed (balance_after - balance_before).
_POINT_TOTAL_BUCKET = {
    "PURCHASE": "purchased",
    "BONUS": "bonus",
    "REWARD": "earned",
    "GIFT_RECEIVED": "earned",
    "REFUND": "earned",
    "MIGRATION_INITIAL_BALANCE": "earned",
    "ADMIN_ADJUST": "earned",
    "SPEND": "spent",
}
_CREDIT_TOTAL_BUCKET = {
    "RECHARGE": "recharged",
    "REDEEM_HOLD": "redeemed",
    "REDEEM_COMPLETE": "redeemed",
    "ADMIN_ADJUST": "adjusted",
    "REFUND": "recharged",
    "GIFT_RECEIVED": "recharged",
    "MIGRATION_INITIAL_BALANCE": "recharged",
    "SPEND": "spent",
}
_TOTAL_KEYS = {
    "MP": ("earned", "spent", "purchased", "bonus"),
    "MC": ("recharged", "spent", "redeemed", "adjusted"),
}


def _wallet_totals(ledger_model, wallet, currency: str) -> dict[str, str]:
    buckets = _POINT_TOTAL_BUCKET if currency == "MP" else _CREDIT_TOTAL_BUCKET
    totals = dict.fromkeys(_TOTAL_KEYS[currency], ZERO)
    rows = (
        ledger_model.objects.filter(wallet=wallet)
        .values("entry_type")
        .annotate(
            delta=Coalesce(
                Sum(F("balance_after") - F("balance_before")),
                ZERO,
                output_field=DecimalField(max_digits=18, decimal_places=4),
            )
        )
    )
    for row in rows:
        bucket = buckets.get(row["entry_type"])
        if bucket is None:
            continue
        delta = row["delta"] or ZERO
        # "spent"/"redeemed" buckets are reported as positive magnitudes.
        totals[bucket] += -delta if bucket in ("spent", "redeemed") else delta
    return {k: str(v.quantize(_CENT)) for k, v in totals.items()}


def get_wallet(*, user_id: str, currency: str) -> dict:
    wallet_model, ledger_model, _ = _resolve(currency)
    try:
        wallet = wallet_model.objects.get(user_id=user_id)
    except wallet_model.DoesNotExist:
        raise NotFoundError(code="WALLET_NOT_FOUND", message=f"No {currency} wallet for this user.")

    return {
        "wallet_id": str(wallet.id),
        "currency": currency,
        "balance": str(wallet.balance),
        "totals": _wallet_totals(ledger_model, wallet, currency),
        "created_at": _iso(wallet.created_at),
        "updated_at": _iso(wallet.updated_at),
    }


def get_aggregate_balance(*, user_id: str) -> dict:
    balances = []
    for currency in ("MP", "MC"):
        wallet_model, _, _ = _resolve(currency)
        wallet = wallet_model.objects.filter(user_id=user_id).first()
        amount = wallet.balance if wallet else ZERO
        balances.append({"currency": currency, "amount": str(amount)})
    return {"balances": balances}


def ledger_queryset(
    *,
    user_id: str,
    currency: str,
    entry_types: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """Return a queryset for cursor pagination in the view layer."""
    wallet_model, ledger_model, _ = _resolve(currency)
    try:
        wallet = wallet_model.objects.get(user_id=user_id)
    except wallet_model.DoesNotExist:
        raise NotFoundError(code="WALLET_NOT_FOUND", message=f"No {currency} wallet for this user.")

    qs = ledger_model.objects.filter(wallet=wallet)
    if entry_types:
        qs = qs.filter(entry_type__in=entry_types)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    return qs


def serialize_ledger_entry(entry, currency: str) -> dict:
    return _serialize_ledger(entry, currency)


# ---------------------------------------------------------------------------
# Credit packages
# ---------------------------------------------------------------------------


def _serialize_package(pkg: CreditPackage) -> dict:
    return {
        "code": pkg.code,
        "name": pkg.name,
        "credit_amount": str(pkg.credit_amount),
        "bonus_credit": str(pkg.bonus_credit),
        "total_credit": str(pkg.total_credit),
        "price": {"amount": str(pkg.price_amount), "currency": pkg.price_currency},
        "alternative_prices": pkg.alternative_prices,
        "sort_order": pkg.sort_order,
        "description": pkg.description,
    }


def list_credit_packages() -> dict:
    packages = CreditPackage.objects.filter(is_active=True)
    return {"results": [_serialize_package(p) for p in packages]}


# ---------------------------------------------------------------------------
# Daily login reward
# ---------------------------------------------------------------------------


def _daily_reward_amount() -> Decimal:
    return _money(getattr(settings, "ECONOMY_DAILY_REWARD_MP", "10.0000"))


def claim_daily_reward(*, user_id: str) -> dict:
    """Grant the once-per-UTC-day MP reward. Idempotent across the day."""
    today = _now_utc().date()
    amount = _daily_reward_amount()

    with transaction.atomic():
        if DailyRewardClaim.objects.filter(user_id=user_id, claim_date=today).exists():
            return {
                "granted": False,
                "reason": "ALREADY_CLAIMED_TODAY",
                "next_eligible_at": _next_utc_midnight(today),
            }

        prev = DailyRewardClaim.objects.filter(
            user_id=user_id, claim_date=today - timedelta(days=1)
        ).first()
        streak = (prev.streak_days + 1) if prev else 1

        ledger = credit(
            user_id=user_id,
            currency="MP",
            entry_type="REWARD",
            amount=amount,
            idempotency_key=f"daily-reward:{user_id}:{today.isoformat()}",
            target_type="DailyRewardClaim",
            note="Daily login reward",
        )

        # The UNIQUE(user_id, claim_date) constraint is the real once-per-day guard;
        # a concurrent claim raises here and rolls back this savepoint (the credit
        # above is idempotent, so there is no double grant).
        try:
            with transaction.atomic():
                DailyRewardClaim.objects.create(
                    user_id=user_id,
                    claim_date=today,
                    amount=amount,
                    currency="MP",
                    ledger_id=ledger["id"],
                    streak_days=streak,
                )
        except Exception:
            return {
                "granted": False,
                "reason": "ALREADY_CLAIMED_TODAY",
                "next_eligible_at": _next_utc_midnight(today),
            }

        _emit_outbox(
            event_type="economy.DailyLoginRewardGranted",
            payload={
                "user_id": str(user_id),
                "amount": str(amount),
                "currency": "MP",
                "ledger_id": ledger["id"],
                "streak_days": streak,
                "occurred_at": _iso(_now_utc()),
            },
            idempotency_key=f"daily_reward_granted:{user_id}:{today.isoformat()}",
            actor_id=str(user_id),
        )

    return {
        "granted": True,
        "amount": str(amount),
        "currency": "MP",
        "ledger_entry_id": ledger["id"],
        "next_eligible_at": _next_utc_midnight(today),
        "streak_days": streak,
    }


def daily_reward_status(*, user_id: str) -> dict:
    today = _now_utc().date()
    todays = DailyRewardClaim.objects.filter(user_id=user_id, claim_date=today).first()
    latest = DailyRewardClaim.objects.filter(user_id=user_id).order_by("-claim_date").first()
    return {
        "eligible_now": todays is None,
        "next_eligible_at": _next_utc_midnight(today),
        "today_amount": str(_daily_reward_amount()),
        "currency": "MP",
        "streak_days": latest.streak_days if latest else 0,
    }


# ---------------------------------------------------------------------------
# Credit recharge (skeleton — on-chain/Stripe verification arrives with payments, W9)
# ---------------------------------------------------------------------------


def _serialize_recharge(rc: CreditRecharge) -> dict:
    return {
        "id": str(rc.id),
        "order_no": rc.order_no,
        "package_code": rc.package_code,
        "credit_amount": str(rc.credit_amount),
        "expected_amount": str(rc.expected_amount),
        "price_currency": rc.price_currency,
        "payment_provider": rc.payment_provider,
        "blockchain_network": rc.blockchain_network or None,
        "txid": rc.txid or None,
        "status": rc.status,
        "created_at": _iso(rc.created_at),
    }


def _get_active_package(package_code: str) -> CreditPackage:
    try:
        return CreditPackage.objects.get(code=package_code, is_active=True)
    except CreditPackage.DoesNotExist:
        raise NotFoundError(
            code="PACKAGE_NOT_FOUND", message=f"No active credit package '{package_code}'."
        )


def recharge_info(*, package_code: str) -> dict:
    pkg = _get_active_package(package_code)
    pay_to = getattr(settings, "ECONOMY_RECHARGE_PAY_TO_ADDRESS", "")
    if not pay_to:
        raise AppError(
            code="PAYMENT_ADDRESS_NOT_CONFIGURED",
            message="Recharge payment address is not configured.",
            http_status=503,
        )
    return {
        "package_code": pkg.code,
        "package_name": pkg.name,
        "credit_amount": str(pkg.credit_amount),
        "bonus_credit": str(pkg.bonus_credit),
        "total_credit": str(pkg.total_credit),
        "price": {"amount": str(pkg.price_amount), "currency": pkg.price_currency},
        "payment_provider": pkg.payment_provider,
        "blockchain_network": pkg.blockchain_network or None,
        "expected_amount": str(pkg.price_amount),
        "pay_to_address": pay_to,
        "required_confirmations": getattr(settings, "ECONOMY_RECHARGE_CONFIRMATIONS", 0),
        "notice": "On-chain verification is finalised by the payments service (W9).",
    }


def create_credit_recharge(*, user_id: str, package_code: str, idempotency_key: str) -> dict:
    pkg = _get_active_package(package_code)
    if not idempotency_key:
        raise ValidationError(
            code="WALLET_IDEMPOTENCY_KEY_REQUIRED", message="idempotency_key is required."
        )

    existing = CreditRecharge.objects.filter(idempotency_key=idempotency_key).first()
    if existing is not None:
        return _serialize_recharge(existing)

    order_no = f"MC-RCG-{_now_utc():%Y%m%d}-{uuid.uuid4().hex[:12]}"
    rc = CreditRecharge.objects.create(
        user_id=user_id,
        order_no=order_no,
        package_code=pkg.code,
        credit_amount=pkg.total_credit,
        expected_amount=pkg.price_amount,
        price_currency=pkg.price_currency,
        payment_provider=pkg.payment_provider,
        blockchain_network=pkg.blockchain_network,
        idempotency_key=idempotency_key,
    )

    # Create the gateway payment Order (payments owns provider integration, W9).
    from apps.payments.services import create_order as _create_payment_order

    order = _create_payment_order(
        user_id=str(user_id),
        business_kind="CREDIT_RECHARGE",
        business_ref_id=str(rc.id),
        amount=pkg.price_amount,
        currency=pkg.price_currency,
        payment_provider=pkg.payment_provider,
        blockchain_network=pkg.blockchain_network or "",
        idempotency_key=f"recharge_order:{rc.id}",
    )
    rc.payment_order_no = order["order_no"]
    rc.save(update_fields=["payment_order_no", "updated_at"])

    _emit_outbox(
        event_type="economy.CreditRechargeCreated",
        payload={
            "user_id": str(user_id),
            "order_no": order_no,
            "package_code": pkg.code,
            "expected_amount": str(pkg.price_amount),
            "occurred_at": _iso(_now_utc()),
        },
        idempotency_key=f"credit_recharge_created:{order_no}",
        actor_id=str(user_id),
    )
    return {
        **_serialize_recharge(rc),
        "payment": order["payment"],
        "payment_order_no": order["order_no"],
    }


def fulfill_recharge(*, recharge_id: str, order_no: str) -> None:
    """Credit MC once the linked payment Order is PAID (payments.OrderPaid handler).

    Idempotent: the credit uses a stable key and the status guard skips re-runs.
    """
    try:
        rc = CreditRecharge.objects.get(id=recharge_id)
    except CreditRecharge.DoesNotExist:
        return
    if rc.status == "completed":
        return

    ledger = credit(
        user_id=str(rc.user_id),
        currency="MC",
        entry_type="RECHARGE",
        amount=rc.credit_amount,
        idempotency_key=f"recharge_fulfill:{rc.id}",
        target_type="CreditRecharge",
        target_id=str(rc.id),
        note=f"Credit recharge {rc.order_no}",
    )
    rc.status = "completed"
    if order_no:
        rc.payment_order_no = order_no
    rc.save(update_fields=["status", "payment_order_no", "updated_at"])

    _emit_outbox(
        event_type="economy.CreditRechargeFulfilled",
        payload={
            "recharge_id": str(rc.id),
            "user_id": str(rc.user_id),
            "ledger_id": ledger["id"],
            "amount": str(rc.credit_amount),
            "currency": "MC",
            "occurred_at": _iso(_now_utc()),
        },
        idempotency_key=f"credit_recharge_fulfilled:{rc.id}",
        actor_id=str(rc.user_id),
    )


def submit_recharge_txid(*, user_id: str, package_code: str, txid: str) -> dict:
    """Create-if-absent + attach txid + move to pending. Verification is W9."""
    rc = (
        CreditRecharge.objects.filter(
            user_id=user_id, package_code=package_code, status__in=["created", "pending"]
        )
        .order_by("-created_at")
        .first()
    )
    if rc is None:
        created = create_credit_recharge(
            user_id=user_id,
            package_code=package_code,
            idempotency_key=f"recharge:{user_id}:{package_code}:{uuid.uuid4().hex[:8]}",
        )
        rc = CreditRecharge.objects.get(order_no=created["order_no"])

    rc.txid = txid
    rc.status = "pending"
    rc.save(update_fields=["txid", "status", "updated_at"])
    return _serialize_recharge(rc)


def verify_recharge(*, user_id: str, order_no: str, txid: str) -> dict:
    """Skeleton: records the txid and leaves the recharge pending. The actual
    on-chain/Stripe settlement that posts the RECHARGE ledger entry is owned by
    the payments service (Week 9)."""
    try:
        rc = CreditRecharge.objects.get(order_no=order_no, user_id=user_id)
    except CreditRecharge.DoesNotExist:
        raise NotFoundError(code="RECHARGE_NOT_FOUND", message="Recharge order not found.")

    if rc.status == "completed":
        return {"status": "completed", "recharge": _serialize_recharge(rc), "verified": True}

    if txid:
        rc.txid = txid
    rc.status = "pending"
    rc.save(update_fields=["txid", "status", "updated_at"])
    return {
        "status": "pending",
        "recharge": _serialize_recharge(rc),
        "verified": False,
        "notice": "Awaiting payment-service verification (W9).",
    }


# ---------------------------------------------------------------------------
# Credit redeem (admin workflow) — economy.md §7
# ---------------------------------------------------------------------------

_ACTIVE_REDEEM_STATES = (CreditRedeemRequest.REQUESTED, CreditRedeemRequest.APPROVED)


def serialize_redeem(req: CreditRedeemRequest) -> dict[str, Any]:
    return {
        "id": str(req.id),
        "amount": {"amount": str(req.amount), "currency": req.currency},
        "redeem_method": req.redeem_method,
        "blockchain_network": req.blockchain_network or None,
        "account_snapshot": req.account_snapshot,
        "status": req.status,
        "admin_note": req.admin_note or None,
        "resolved_at": _iso(req.resolved_at) if req.resolved_at else None,
        "created_at": _iso(req.created_at),
    }


def request_credit_redeem(
    *,
    user_id: str,
    amount: Any,
    redeem_method: str,
    blockchain_network: str = "",
    account_snapshot: dict | None = None,
    idempotency_key: str,
) -> dict[str, Any]:
    """Request an MC redeem. Debits the amount via REDEEM_HOLD (funds reserved
    out of the wallet) and records the request for admin review."""
    if not idempotency_key:
        raise ValidationError(
            code="REDEEM_IDEMPOTENCY_KEY_REQUIRED", message="idempotency_key required."
        )
    amt = _money(amount)
    if amt <= 0:
        raise UnprocessableError(code="REDEEM_AMOUNT_INVALID", message="amount must be positive.")

    existing = CreditRedeemRequest.objects.filter(idempotency_key=idempotency_key).first()
    if existing is not None:
        return serialize_redeem(existing)

    with transaction.atomic():
        req = CreditRedeemRequest.objects.create(
            user_id=user_id,
            amount=amt,
            currency="MC",
            redeem_method=redeem_method,
            blockchain_network=blockchain_network,
            account_snapshot=account_snapshot or {},
            idempotency_key=idempotency_key,
        )
        ledger = debit(
            user_id=str(user_id),
            currency="MC",
            entry_type="REDEEM_HOLD",
            amount=amt,
            idempotency_key=f"redeem_hold:{req.id}",
            target_type="CreditRedeemRequest",
            target_id=str(req.id),
            note="Credit redeem hold",
        )
        req.hold_ledger_id = ledger["id"]
        req.save(update_fields=["hold_ledger_id", "updated_at"])
        _emit_outbox(
            event_type="economy.CreditRedeemRequested",
            payload={
                "redeem_id": str(req.id),
                "user_id": str(user_id),
                "amount": str(amt),
                "currency": "MC",
                "redeem_method": redeem_method,
                "occurred_at": _iso(_now_utc()),
            },
            idempotency_key=f"credit_redeem_requested:{req.id}",
            actor_id=str(user_id),
        )
        _record_audit(
            action="economy.credit_redeem.request",
            actor_id=str(user_id),
            actor_type="user",
            target_id=str(req.id),
            target_type="CreditRedeemRequest",
            after_state={"amount": str(amt), "status": req.status},
        )
    return serialize_redeem(req)


def redeems_queryset(*, user_id: str):
    return CreditRedeemRequest.objects.filter(user_id=user_id)


def _locked_redeem(redeem_id: str) -> CreditRedeemRequest:
    try:
        return CreditRedeemRequest.objects.select_for_update().get(id=redeem_id)
    except CreditRedeemRequest.DoesNotExist:
        raise NotFoundError(code="REDEEM_NOT_FOUND", message="Redeem request not found.")


def approve_credit_redeem(*, redeem_id: str, admin_id: str, admin_note: str = "") -> dict[str, Any]:
    with transaction.atomic():
        req = _locked_redeem(redeem_id)
        if req.status != CreditRedeemRequest.REQUESTED:
            raise ConflictError(
                code="REDEEM_NOT_PENDING", message=f"Redeem is already {req.status}."
            )
        req.status = CreditRedeemRequest.APPROVED
        req.admin_note = admin_note
        req.save(update_fields=["status", "admin_note", "updated_at"])
        _emit_outbox(
            event_type="economy.CreditRedeemApproved",
            payload={"redeem_id": str(req.id), "occurred_at": _iso(_now_utc())},
            idempotency_key=f"credit_redeem_approved:{req.id}",
            actor_id=str(admin_id),
        )
        _record_audit(
            action="economy.credit_redeem.approve",
            actor_id=str(admin_id),
            target_id=str(req.id),
            target_type="CreditRedeemRequest",
            after_state={"status": "approved"},
        )
    return serialize_redeem(req)


def reject_credit_redeem(*, redeem_id: str, admin_id: str, admin_note: str = "") -> dict[str, Any]:
    """Reject a redeem and refund the held amount back to the wallet."""
    with transaction.atomic():
        req = _locked_redeem(redeem_id)
        if req.status not in _ACTIVE_REDEEM_STATES:
            raise ConflictError(
                code="REDEEM_NOT_PENDING", message=f"Redeem is already {req.status}."
            )
        ledger = credit(
            user_id=str(req.user_id),
            currency="MC",
            entry_type="REFUND",
            amount=req.amount,
            idempotency_key=f"redeem_refund:{req.id}",
            target_type="CreditRedeemRequest",
            target_id=str(req.id),
            note="Credit redeem rejected — refund",
        )
        req.status = CreditRedeemRequest.REJECTED
        req.refund_ledger_id = ledger["id"]
        req.admin_note = admin_note
        req.resolved_at = _now_utc()
        req.resolved_by = admin_id
        req.save(
            update_fields=[
                "status",
                "refund_ledger_id",
                "admin_note",
                "resolved_at",
                "resolved_by",
                "updated_at",
            ]
        )
        _emit_outbox(
            event_type="economy.CreditRedeemRejected",
            payload={"redeem_id": str(req.id), "occurred_at": _iso(_now_utc())},
            idempotency_key=f"credit_redeem_rejected:{req.id}",
            actor_id=str(admin_id),
        )
        _record_audit(
            action="economy.credit_redeem.reject",
            actor_id=str(admin_id),
            target_id=str(req.id),
            target_type="CreditRedeemRequest",
            after_state={"status": "rejected"},
        )
    return serialize_redeem(req)


def complete_credit_redeem(*, redeem_id: str, admin_id: str) -> dict[str, Any]:
    """Mark an approved redeem completed (the on-chain payout happened out-of-band;
    the funds were already removed at REDEEM_HOLD)."""
    with transaction.atomic():
        req = _locked_redeem(redeem_id)
        if req.status != CreditRedeemRequest.APPROVED:
            raise ConflictError(
                code="REDEEM_NOT_APPROVED",
                message=f"Redeem must be approved before completion (is {req.status}).",
            )
        req.status = CreditRedeemRequest.COMPLETED
        req.resolved_at = _now_utc()
        req.resolved_by = admin_id
        req.save(update_fields=["status", "resolved_at", "resolved_by", "updated_at"])
        _emit_outbox(
            event_type="economy.CreditRedeemCompleted",
            payload={
                "redeem_id": str(req.id),
                "user_id": str(req.user_id),
                "amount": str(req.amount),
                "currency": "MC",
                "occurred_at": _iso(_now_utc()),
            },
            idempotency_key=f"credit_redeem_completed:{req.id}",
            actor_id=str(admin_id),
        )
        _record_audit(
            action="economy.credit_redeem.complete",
            actor_id=str(admin_id),
            target_id=str(req.id),
            target_type="CreditRedeemRequest",
            after_state={"status": "completed"},
        )
    return serialize_redeem(req)
