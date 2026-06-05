"""Service-layer + invariant tests for economy (ADR-0004)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from apps.economy import services
from apps.economy.models import (
    CreditPackage,
    DailyRewardClaim,
    LedgerImmutableError,
    PointLedger,
    PointWallet,
)
from libs.errors.exceptions import NotFoundError, UnprocessableError, ValidationError

PATCH_EMIT = "apps.economy.services._emit_outbox"
PATCH_AUDIT = "apps.economy.services._record_audit"


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def user_id() -> str:
    uid = _uid()
    services.create_wallets_for_user(user_id=uid)
    return uid


# ---------------------------------------------------------------------------
# Wallet provisioning
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProvisioning:
    def test_create_wallets_is_idempotent(self):
        uid = _uid()
        services.create_wallets_for_user(user_id=uid)
        services.create_wallets_for_user(user_id=uid)
        assert PointWallet.objects.filter(user_id=uid).count() == 1

    def test_balance_starts_at_zero(self, user_id):
        assert services.get_balance(user_id=user_id, currency="MP") == Decimal("0.0000")

    def test_unknown_wallet_raises(self):
        with pytest.raises(NotFoundError):
            services.get_balance(user_id=_uid(), currency="MP")


# ---------------------------------------------------------------------------
# credit / debit invariants
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreditDebit:
    def test_credit_sets_balance_and_ledger_fields(self, user_id):
        entry = services.credit(
            user_id=user_id,
            currency="MP",
            entry_type="REWARD",
            amount="100",
            idempotency_key="k1",
        )
        assert entry["balance_before"] == "0.0000"
        assert entry["balance_after"] == "100.0000"
        assert entry["amount"] == "100.0000"
        assert entry["currency"] == "MP"
        assert services.get_balance(user_id=user_id, currency="MP") == Decimal("100.0000")

    def test_debit_reduces_balance(self, user_id):
        services.credit(
            user_id=user_id, currency="MP", entry_type="REWARD", amount="100", idempotency_key="k1"
        )
        services.debit(
            user_id=user_id, currency="MP", entry_type="SPEND", amount="30", idempotency_key="k2"
        )
        assert services.get_balance(user_id=user_id, currency="MP") == Decimal("70.0000")

    def test_debit_insufficient_balance_raises(self, user_id):
        with pytest.raises(UnprocessableError) as exc:
            services.debit(
                user_id=user_id, currency="MP", entry_type="SPEND", amount="5", idempotency_key="k1"
            )
        assert exc.value.code == "WALLET_INSUFFICIENT_BALANCE"
        assert services.get_balance(user_id=user_id, currency="MP") == Decimal("0.0000")

    def test_idempotent_replay_does_not_double_credit(self, user_id):
        a = services.credit(
            user_id=user_id,
            currency="MP",
            entry_type="REWARD",
            amount="100",
            idempotency_key="same",
        )
        b = services.credit(
            user_id=user_id,
            currency="MP",
            entry_type="REWARD",
            amount="100",
            idempotency_key="same",
        )
        assert a["id"] == b["id"]
        assert services.get_balance(user_id=user_id, currency="MP") == Decimal("100.0000")
        assert PointLedger.objects.count() == 1

    def test_invalid_entry_type_for_currency(self, user_id):
        # RECHARGE belongs to MC, not MP.
        with pytest.raises(ValidationError) as exc:
            services.credit(
                user_id=user_id,
                currency="MP",
                entry_type="RECHARGE",
                amount="10",
                idempotency_key="k1",
            )
        assert exc.value.code == "WALLET_INVALID_ENTRY_TYPE"

    def test_non_positive_amount_rejected(self, user_id):
        with pytest.raises(ValidationError) as exc:
            services.credit(
                user_id=user_id,
                currency="MP",
                entry_type="REWARD",
                amount="0",
                idempotency_key="k1",
            )
        assert exc.value.code == "WALLET_INVALID_AMOUNT"

    def test_reconcile_matches_after_ops(self, user_id):
        services.credit(
            user_id=user_id, currency="MP", entry_type="REWARD", amount="100", idempotency_key="k1"
        )
        services.debit(
            user_id=user_id, currency="MP", entry_type="SPEND", amount="40", idempotency_key="k2"
        )
        result = services.reconcile(user_id=user_id, currency="MP")
        assert result["reconciled"] is True
        assert result["wallet_balance"] == "60.0000"


# ---------------------------------------------------------------------------
# append-only ledger
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLedgerImmutable:
    def test_update_forbidden(self, user_id):
        services.credit(
            user_id=user_id, currency="MP", entry_type="REWARD", amount="100", idempotency_key="k1"
        )
        entry = PointLedger.objects.get()
        entry.note = "tampered"
        with pytest.raises(LedgerImmutableError):
            entry.save()

    def test_delete_forbidden(self, user_id):
        services.credit(
            user_id=user_id, currency="MP", entry_type="REWARD", amount="100", idempotency_key="k1"
        )
        with pytest.raises(LedgerImmutableError):
            PointLedger.objects.get().delete()


# ---------------------------------------------------------------------------
# totals + aggregate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTotalsAndAggregate:
    def test_point_totals(self, user_id):
        services.credit(
            user_id=user_id, currency="MP", entry_type="REWARD", amount="100", idempotency_key="k1"
        )
        services.debit(
            user_id=user_id, currency="MP", entry_type="SPEND", amount="30", idempotency_key="k2"
        )
        wallet = services.get_wallet(user_id=user_id, currency="MP")
        assert wallet["totals"]["earned"] == "100.0000"
        assert wallet["totals"]["spent"] == "30.0000"
        assert wallet["balance"] == "70.0000"

    def test_aggregate_lists_both_currencies(self, user_id):
        services.credit(
            user_id=user_id, currency="MC", entry_type="RECHARGE", amount="5", idempotency_key="k1"
        )
        agg = services.get_aggregate_balance(user_id=user_id)
        by = {b["currency"]: b["amount"] for b in agg["balances"]}
        assert by == {"MP": "0.0000", "MC": "5.0000"}


# ---------------------------------------------------------------------------
# daily login reward
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDailyReward:
    def test_claim_then_already_claimed(self, user_id):
        first = services.claim_daily_reward(user_id=user_id)
        assert first["granted"] is True
        assert first["amount"] == "10.0000"
        assert first["streak_days"] == 1
        assert services.get_balance(user_id=user_id, currency="MP") == Decimal("10.0000")

        second = services.claim_daily_reward(user_id=user_id)
        assert second["granted"] is False
        assert second["reason"] == "ALREADY_CLAIMED_TODAY"
        # No double grant.
        assert services.get_balance(user_id=user_id, currency="MP") == Decimal("10.0000")
        assert DailyRewardClaim.objects.filter(user_id=user_id).count() == 1

    def test_status_reflects_claim(self, user_id):
        assert services.daily_reward_status(user_id=user_id)["eligible_now"] is True
        services.claim_daily_reward(user_id=user_id)
        status = services.daily_reward_status(user_id=user_id)
        assert status["eligible_now"] is False
        assert status["streak_days"] == 1


# ---------------------------------------------------------------------------
# credit recharge skeleton
# ---------------------------------------------------------------------------


@pytest.fixture
def package() -> CreditPackage:
    # Stripe/USD package: create_credit_recharge spins up a Stripe payment Order,
    # which runs in fake mode without external config (LTT path is tested in payments).
    return CreditPackage.objects.create(
        code="CREDIT_100",
        name="100 Credits",
        credit_amount=Decimal("100.0000"),
        bonus_credit=Decimal("10.0000"),
        price_amount=Decimal("3.0000"),
        price_currency="USD",
        payment_provider="stripe",
        blockchain_network="",
    )


@pytest.mark.django_db
class TestRecharge:
    def test_info_503_when_address_unconfigured(self, package, settings):
        settings.ECONOMY_RECHARGE_PAY_TO_ADDRESS = ""
        from libs.errors.exceptions import AppError

        with pytest.raises(AppError) as exc:
            services.recharge_info(package_code="CREDIT_100")
        assert exc.value.code == "PAYMENT_ADDRESS_NOT_CONFIGURED"
        assert exc.value.http_status == 503

    def test_info_ok_when_configured(self, package, settings):
        settings.ECONOMY_RECHARGE_PAY_TO_ADDRESS = "bC0xADDRESS"
        info = services.recharge_info(package_code="CREDIT_100")
        assert info["pay_to_address"] == "bC0xADDRESS"
        assert info["total_credit"] == "110.0000"

    def test_create_is_idempotent(self, package):
        uid = _uid()
        a = services.create_credit_recharge(
            user_id=uid, package_code="CREDIT_100", idempotency_key="rk1"
        )
        b = services.create_credit_recharge(
            user_id=uid, package_code="CREDIT_100", idempotency_key="rk1"
        )
        assert a["order_no"] == b["order_no"]
        assert a["credit_amount"] == "110.0000"

    def test_submit_and_verify_stay_pending(self, package):
        uid = _uid()
        services.create_credit_recharge(
            user_id=uid, package_code="CREDIT_100", idempotency_key="rk1"
        )
        submitted = services.submit_recharge_txid(
            user_id=uid, package_code="CREDIT_100", txid="0xabc"
        )
        assert submitted["status"] == "pending"
        assert submitted["txid"] == "0xabc"

        verified = services.verify_recharge(
            user_id=uid, order_no=submitted["order_no"], txid="0xabc"
        )
        assert verified["verified"] is False
        assert verified["status"] == "pending"
