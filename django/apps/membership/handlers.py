"""Membership event handlers. Registered at AppConfig.ready()."""

from __future__ import annotations

import logging
from typing import Any

from apps.events import types
from apps.events.registry import on_event

from . import services

logger = logging.getLogger(__name__)


@on_event(types.PAYMENTS_ORDER_PAID)
def settle_membership_order(event: Any) -> None:
    """When a MEMBERSHIP payment settles (wallet debit or Stripe webhook), mark the
    order paid and grant the membership. Ignores other business kinds."""
    payload = event.payload
    if payload.get("business_kind") != "MEMBERSHIP":
        return
    services.mark_order_paid(membership_order_id=payload["business_ref_id"])
