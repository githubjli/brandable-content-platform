"""Models for economy.

Implements the non-negotiable wallet/ledger invariants from ADR-0004:
  - append-only ledger (save() forbids updates, delete() forbids deletion)
  - UNIQUE idempotency_key per ledger
  - denormalized balance_after on every row
  - DB CHECK (balance >= 0) on wallets
  - wallets created explicitly at registration (see services.create_wallets_for_user)

PointWallet and CreditWallet are separate tables (per economy.md + migration-plan),
each with its own ledger. They share behaviour via the abstract bases below; the
single write path (EconomyService.credit/debit) is generic over the pair.

All money fields are Decimal(18, 4) (conventions.md §7). Never reference models
from other apps directly — cross-app writes come in through services.
"""

from __future__ import annotations

from decimal import Decimal

from django.db.models import (
    PROTECT,
    BooleanField,
    CharField,
    CheckConstraint,
    DateField,
    DateTimeField,
    DecimalField,
    ForeignKey,
    Index,
    IntegerField,
    JSONField,
    PositiveIntegerField,
    Q,
    TextField,
    UniqueConstraint,
    UUIDField,
)

from libs.errors.base_model import AbstractBaseModel
from libs.errors.exceptions import AppError

# Money precision (conventions.md §7): all money fields are Decimal(18, 4). Written
# as explicit max_digits/decimal_places kwargs (not a **dict) so the django-stubs
# mypy plugin can read each field's signature — it cannot follow **unpacking.
ZERO = Decimal("0.0000")

# Ledger entry types per wallet (economy.md §2).
POINT_ENTRY_TYPES = (
    "PURCHASE",
    "BONUS",
    "REWARD",
    "SPEND",
    "REFUND",
    "ADMIN_ADJUST",
    "GIFT_RECEIVED",
    "MIGRATION_INITIAL_BALANCE",
)
CREDIT_ENTRY_TYPES = (
    "RECHARGE",
    "SPEND",
    "REFUND",
    "ADMIN_ADJUST",
    "GIFT_RECEIVED",
    "REDEEM_HOLD",
    "REDEEM_COMPLETE",
    "MIGRATION_INITIAL_BALANCE",
)
# Entry types that DEBIT (reduce) a wallet. ADMIN_ADJUST is signed and may do either,
# so it is excluded here and validated by the chosen service method instead.
DEBIT_ENTRY_TYPES = {"SPEND", "REDEEM_HOLD"}


class LedgerImmutableError(AppError):
    """Raised on any attempt to update or delete a ledger row (ADR-0004 invariant 1)."""

    default_code = "WALLET_LEDGER_IMMUTABLE"
    default_message = "WalletLedger rows are append-only and cannot be modified or deleted."


# ---------------------------------------------------------------------------
# Wallets
# ---------------------------------------------------------------------------


class AbstractWallet(AbstractBaseModel):
    """Shared wallet shape. One row per user per currency."""

    user_id = UUIDField(unique=True, db_index=True)
    balance = DecimalField(default=ZERO, max_digits=18, decimal_places=4)
    is_active = BooleanField(default=True)

    # Subclasses set this.
    currency: str = ""

    class Meta:
        abstract = True
        constraints = [
            CheckConstraint(
                condition=Q(balance__gte=0),
                name="%(class)s_balance_non_negative",
            ),
        ]


class PointWallet(AbstractWallet):
    """MeowPoints (MP) — earned-only loyalty currency."""

    currency = "MP"

    class Meta(AbstractWallet.Meta):
        db_table = "economy_pointwallet"

    def __str__(self) -> str:
        return f"PointWallet(user={self.user_id}, balance={self.balance})"


class CreditWallet(AbstractWallet):
    """MeowCredit (MC) — paid currency (recharge / Stripe / refund)."""

    currency = "MC"

    class Meta(AbstractWallet.Meta):
        db_table = "economy_creditwallet"

    def __str__(self) -> str:
        return f"CreditWallet(user={self.user_id}, balance={self.balance})"


# ---------------------------------------------------------------------------
# Ledgers (append-only)
# ---------------------------------------------------------------------------


class AbstractWalletLedger(AbstractBaseModel):
    """Append-only ledger row. Written only by EconomyService.credit/debit."""

    entry_type = CharField(max_length=32)
    amount = DecimalField(
        max_digits=18, decimal_places=4
    )  # always positive; direction is by entry_type
    balance_before = DecimalField(max_digits=18, decimal_places=4)
    balance_after = DecimalField(max_digits=18, decimal_places=4)
    idempotency_key = CharField(max_length=255, unique=True)
    target_type = CharField(max_length=64, blank=True)  # PascalCase model name
    target_id = UUIDField(null=True, blank=True)
    note = TextField(blank=True)
    actor_id = UUIDField(null=True, blank=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        # Allow only the initial insert; any later save is a forbidden mutation.
        if not self._state.adding:
            raise LedgerImmutableError()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise LedgerImmutableError()


class PointLedger(AbstractWalletLedger):
    wallet = ForeignKey(PointWallet, on_delete=PROTECT, related_name="ledger_entries")

    class Meta(AbstractWalletLedger.Meta):
        db_table = "economy_point_ledger"

    def __str__(self) -> str:
        return f"PointLedger({self.entry_type} {self.amount} -> {self.balance_after})"


class CreditLedger(AbstractWalletLedger):
    wallet = ForeignKey(CreditWallet, on_delete=PROTECT, related_name="ledger_entries")

    class Meta(AbstractWalletLedger.Meta):
        db_table = "economy_credit_ledger"

    def __str__(self) -> str:
        return f"CreditLedger({self.entry_type} {self.amount} -> {self.balance_after})"


# ---------------------------------------------------------------------------
# Credit packages + recharge (recharge verification wired in Week 9 / payments)
# ---------------------------------------------------------------------------


class CreditPackage(AbstractBaseModel):
    """Purchasable credit bundle catalogue (economy.md §3)."""

    code = CharField(max_length=64, unique=True)
    name = CharField(max_length=200)
    credit_amount = DecimalField(max_digits=18, decimal_places=4)
    bonus_credit = DecimalField(default=ZERO, max_digits=18, decimal_places=4)
    price_amount = DecimalField(max_digits=18, decimal_places=4)
    price_currency = CharField(max_length=20)  # canonical price currency, e.g. "THB-LTT"
    alternative_prices = JSONField(default=list)  # [{"amount","currency"}, ...]
    payment_provider = CharField(max_length=20, default="blockchain")
    blockchain_network = CharField(max_length=20, blank=True)
    sort_order = IntegerField(default=0)
    description = TextField(blank=True)
    is_active = BooleanField(default=True)

    class Meta:
        db_table = "economy_credit_package"
        ordering = ["sort_order", "code"]

    @property
    def total_credit(self) -> Decimal:
        return self.credit_amount + self.bonus_credit

    def __str__(self) -> str:
        return f"CreditPackage({self.code})"


class CreditRecharge(AbstractBaseModel):
    """A credit-recharge intent. The on-chain/Stripe verification that flips this to
    `completed` and posts the ledger entry is owned by payments (Week 9); until then
    verification returns a not-yet-available result.
    """

    STATUS = [
        ("created", "created"),
        ("pending", "pending"),  # txid submitted, awaiting verification
        ("completed", "completed"),
        ("failed", "failed"),
    ]

    user_id = UUIDField(db_index=True)
    order_no = CharField(max_length=64, unique=True)
    package_code = CharField(max_length=64)
    credit_amount = DecimalField(max_digits=18, decimal_places=4)  # total credit to post on success
    expected_amount = DecimalField(
        max_digits=18, decimal_places=4
    )  # payable amount in price currency
    price_currency = CharField(max_length=20)
    payment_provider = CharField(max_length=20, default="blockchain")
    blockchain_network = CharField(max_length=20, blank=True)
    txid = CharField(max_length=256, blank=True)
    status = CharField(max_length=20, choices=STATUS, default="created")
    idempotency_key = CharField(max_length=255, unique=True)
    # Nullable so the column can be added to an existing table without a rewrite
    # / NOT NULL backfill (migration-linter add_not_null_column).
    payment_order_no = CharField(max_length=64, null=True, blank=True)  # linked payments.Order

    class Meta:
        db_table = "economy_credit_recharge"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"CreditRecharge({self.order_no}, {self.status})"


# ---------------------------------------------------------------------------
# Daily login reward
# ---------------------------------------------------------------------------


class DailyRewardClaim(AbstractBaseModel):
    """One row per user per UTC day — the UNIQUE constraint enforces once-per-day."""

    user_id = UUIDField(db_index=True)
    claim_date = DateField()  # UTC calendar date
    amount = DecimalField(max_digits=18, decimal_places=4)
    currency = CharField(max_length=8, default="MP")
    ledger_id = UUIDField(null=True, blank=True)  # PointLedger row created for the grant
    streak_days = PositiveIntegerField(default=1)

    class Meta:
        db_table = "economy_daily_reward_claim"
        constraints = [
            UniqueConstraint(fields=["user_id", "claim_date"], name="daily_reward_once_per_day"),
        ]

    def __str__(self) -> str:
        return f"DailyRewardClaim(user={self.user_id}, date={self.claim_date})"


# ---------------------------------------------------------------------------
# Credit redeem (admin workflow) — economy.md §7
# ---------------------------------------------------------------------------


class CreditRedeemRequest(AbstractBaseModel):
    """A user's request to redeem MeowCredit for an on-chain payout (admin-mediated).

    On request the amount is debited via REDEEM_HOLD (funds reserved out of the
    wallet). Admin completion records the external payout; rejection refunds the
    held amount. CreditWallet only.
    """

    REQUESTED = "requested"
    APPROVED = "approved"
    COMPLETED = "completed"
    REJECTED = "rejected"
    STATUS = [
        (REQUESTED, REQUESTED),
        (APPROVED, APPROVED),
        (COMPLETED, COMPLETED),
        (REJECTED, REJECTED),
    ]

    user_id = UUIDField(db_index=True)
    amount = DecimalField(max_digits=18, decimal_places=4)
    currency = CharField(max_length=8, default="MC")
    redeem_method = CharField(max_length=40)  # e.g. blockchain_transfer
    blockchain_network = CharField(max_length=20, blank=True)  # lbc | ltt | ...
    account_snapshot = JSONField(default=dict, blank=True)
    status = CharField(max_length=20, choices=STATUS, default=REQUESTED)
    hold_ledger_id = UUIDField(null=True, blank=True)  # REDEEM_HOLD debit
    refund_ledger_id = UUIDField(null=True, blank=True)  # REFUND credit (on reject)
    admin_note = TextField(blank=True)
    resolved_at = DateTimeField(null=True, blank=True)
    resolved_by = UUIDField(null=True, blank=True)
    idempotency_key = CharField(max_length=128, unique=True)

    class Meta:
        db_table = "economy_credit_redeem_request"
        ordering = ["-created_at"]
        indexes = [
            Index(fields=["user_id", "status"], name="idx_redeem_user_status"),
        ]

    def __str__(self) -> str:
        return f"CreditRedeemRequest({self.user_id}, {self.amount}{self.currency}, {self.status})"
