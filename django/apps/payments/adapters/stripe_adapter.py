"""Stripe adapter (payments.md §5).

Webhook signature verification is real (HMAC, done locally by the Stripe SDK — no
network). PaymentIntent creation calls Stripe only when a live secret key is set;
otherwise (dev/test, STRIPE_FAKE_MODE) it returns a synthetic intent so the flow
runs without network access. Capture/refund follow the same live-or-fake pattern.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import stripe
from django.conf import settings

from libs.errors.exceptions import AppError, ValidationError


def _to_minor_units(amount: Decimal) -> int:
    # USD and most Stripe currencies use 2-decimal minor units.
    return int((amount * 100).to_integral_value())


class StripeAdapter:
    def __init__(self) -> None:
        self.fake = settings.STRIPE_FAKE_MODE
        if not self.fake:
            stripe.api_key = settings.STRIPE_SECRET_KEY

    def create_payment_intent(self, order: Any) -> dict[str, str]:
        """Return {"intent_id", "client_secret"} for the order."""
        if self.fake:
            intent_id = f"pi_fake_{order.id.hex[:24]}"
            return {"intent_id": intent_id, "client_secret": f"{intent_id}_secret_test"}

        intent = stripe.PaymentIntent.create(
            amount=_to_minor_units(order.amount),
            currency=order.currency.lower(),
            metadata={"order_no": order.order_no},
            idempotency_key=f"pi:{order.idempotency_key}",
        )
        return {"intent_id": intent.id, "client_secret": intent.client_secret or ""}

    def refund(self, intent_id: str, amount: Decimal) -> dict[str, Any]:
        if self.fake:
            return {"id": f"re_fake_{intent_id[-12:]}", "status": "succeeded"}
        refund = stripe.Refund.create(payment_intent=intent_id, amount=_to_minor_units(amount))
        return {"id": refund.id, "status": refund.status}

    def verify_webhook(self, payload: bytes, signature: str) -> dict[str, Any]:
        """Verify the Stripe signature and return the parsed event (raises on bad sig)."""
        secret = settings.STRIPE_WEBHOOK_SECRET
        if not secret:
            raise AppError(
                code="PAYMENT_WEBHOOK_NOT_CONFIGURED",
                message="Stripe webhook secret is not configured.",
                http_status=503,
            )
        try:
            event = stripe.Webhook.construct_event(payload, signature, secret)
        except stripe.SignatureVerificationError as exc:
            raise ValidationError(
                code="PAYMENT_WEBHOOK_SIGNATURE_INVALID",
                message="Stripe webhook signature verification failed.",
            ) from exc
        return event
