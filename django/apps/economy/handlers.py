"""Economy event handlers (events.md §7). Registered at AppConfig.ready()."""

from __future__ import annotations

import logging
from typing import Any

from apps.events import types
from apps.events.registry import on_event

from . import services

logger = logging.getLogger(__name__)


@on_event(types.ECONOMY_DAILY_LOGIN_REWARD_CLAIM_REQUESTED)
def grant_daily_reward(event: Any) -> None:
    """Async grant of the daily login reward (the chain identity.login kicks off).

    claim_daily_reward is idempotent per UTC day, so re-delivery is safe.
    """
    services.claim_daily_reward(user_id=event.payload["user_id"])


@on_event(types.ECONOMY_WALLET_RECONCILIATION_MISMATCH)
def alert_ops(event: Any) -> None:
    """A wallet failed reconciliation — page ops (log at critical for now)."""
    logger.critical("economy.reconciliation_mismatch", extra={"payload": event.payload})


@on_event(types.PAYMENTS_ORDER_PAID)
def fulfill_credit_recharge(event: Any) -> None:
    """When a CREDIT_RECHARGE payment settles, credit the MC wallet (the W6-7
    recharge skeleton becomes real). Ignores other business kinds."""
    payload = event.payload
    if payload.get("business_kind") != "CREDIT_RECHARGE":
        return
    services.fulfill_recharge(recharge_id=payload["business_ref_id"], order_no=payload["order_no"])
