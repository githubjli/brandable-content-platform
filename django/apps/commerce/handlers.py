"""Commerce event handlers (commerce.md §0 chain). Registered at AppConfig.ready()."""

from __future__ import annotations

import logging
from typing import Any

from apps.events import types
from apps.events.registry import on_event

from . import services

logger = logging.getLogger(__name__)


@on_event(types.PAYMENTS_ORDER_PAID)
def settle_product_order(event: Any) -> None:
    """When a PRODUCT payment settles (wallet debit or Stripe webhook), flip the
    ProductOrder to paid. Ignores other business kinds."""
    payload = event.payload
    if payload.get("business_kind") != "PRODUCT":
        return
    services.mark_order_paid(product_order_id=payload["business_ref_id"])
