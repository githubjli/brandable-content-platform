"""Models for payments (payments.md §1).

`Order` is the unified payment representation across business kinds. Unlike the
wallet ledger / audit log it is mutable (status advances through a state machine);
the immutable financial record stays in economy's WalletLedger.
"""

from __future__ import annotations

from django.db.models import (
    BooleanField,
    CharField,
    DateTimeField,
    DecimalField,
    Index,
    TextField,
    UUIDField,
)

from libs.errors.base_model import AbstractBaseModel

# Business kinds (payments.md §1). POINT_PACKAGE intentionally absent (MP is earned-only).
BUSINESS_KINDS = ("MEMBERSHIP", "PRODUCT", "CREDIT_RECHARGE")
PROVIDERS = ("stripe", "blockchain", "wallet", "manual")


class Order(AbstractBaseModel):
    PENDING_PAYMENT = "pending_payment"
    AUTHORIZED = "authorized"
    PAID = "paid"
    FAILED = "failed"
    EXPIRED = "expired"
    REFUNDING = "refunding"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"
    STATUS = [
        (PENDING_PAYMENT, PENDING_PAYMENT),
        (AUTHORIZED, AUTHORIZED),
        (PAID, PAID),
        (FAILED, FAILED),
        (EXPIRED, EXPIRED),
        (REFUNDING, REFUNDING),
        (REFUNDED, REFUNDED),
        (CANCELLED, CANCELLED),
    ]
    TERMINAL = {PAID, FAILED, EXPIRED, REFUNDED, CANCELLED}

    order_no = CharField(max_length=64, unique=True)
    business_kind = CharField(max_length=32)
    business_ref_id = UUIDField()
    user_id = UUIDField(db_index=True)
    amount = DecimalField(max_digits=18, decimal_places=4)
    currency = CharField(max_length=20)  # ticker; <TICKER>-<CHAIN> for on-chain tokens
    status = CharField(max_length=20, choices=STATUS, default=PENDING_PAYMENT)
    payment_provider = CharField(max_length=20)
    blockchain_network = CharField(max_length=20, blank=True)  # required when provider=blockchain
    provider_intent_id = CharField(max_length=255, blank=True)  # pi_..., chain txid, ledger id
    pay_to_address = CharField(max_length=255, blank=True)
    expected_amount = DecimalField(max_digits=18, decimal_places=4)
    expected_currency = CharField(max_length=20)
    expires_at = DateTimeField(null=True, blank=True)
    paid_at = DateTimeField(null=True, blank=True)
    last_error = TextField(blank=True)
    refund_reason = TextField(blank=True)
    refunded_at = DateTimeField(null=True, blank=True)
    idempotency_key = CharField(max_length=128, unique=True)

    class Meta:
        db_table = "payments_order"
        ordering = ["-created_at"]
        indexes = [
            Index(fields=["status", "created_at"], name="idx_order_status_created"),
            Index(fields=["business_kind", "business_ref_id"], name="idx_order_business_ref"),
            Index(fields=["provider_intent_id"], name="idx_order_intent"),
        ]

    def __str__(self) -> str:
        return f"Order({self.order_no}, {self.status})"


class WebhookEvent(AbstractBaseModel):
    """Ingested provider webhook, deduplicated by (provider, event_id)."""

    provider = CharField(max_length=20)
    event_id = CharField(max_length=255)  # Stripe event id (evt_...)
    event_type = CharField(max_length=100, blank=True)
    signature_valid = BooleanField(default=False)
    payload_hash = CharField(max_length=64, blank=True)  # sha256 of raw body
    processed = BooleanField(default=False)

    class Meta:
        db_table = "payments_webhook_event"
        ordering = ["-created_at"]
        constraints = []
        unique_together = [("provider", "event_id")]

    def __str__(self) -> str:
        return f"WebhookEvent({self.provider}, {self.event_id})"
