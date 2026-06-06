"""Canonical event_type constants (events.md §3, §15 anti-pattern: no inline strings).

`<domain>.<PastTense>` or `<domain>.<sub>.<PastTense>`. Only the types actually
emitted today are listed; add more as domains start emitting.
"""

from __future__ import annotations

# Identity
IDENTITY_USER_REGISTERED = "identity.UserRegistered"
IDENTITY_USER_LOGGED_IN = "identity.UserLoggedIn"
IDENTITY_PASSWORD_RESET_REQUESTED = "identity.PasswordResetRequested"
IDENTITY_PASSWORD_CHANGED = "identity.PasswordChanged"
IDENTITY_PROFILE_UPDATED = "identity.ProfileUpdated"
IDENTITY_KYC_SUBMITTED = "identity.KycSubmitted"
IDENTITY_KYC_RESUBMITTED = "identity.KycResubmitted"
IDENTITY_USER_FOLLOWED = "identity.UserFollowed"
IDENTITY_USER_UNFOLLOWED = "identity.UserUnfollowed"

# Economy
ECONOMY_WALLET_CREDITED = "economy.WalletCredited"
ECONOMY_WALLET_DEBITED = "economy.WalletDebited"
ECONOMY_DAILY_LOGIN_REWARD_CLAIM_REQUESTED = "economy.DailyLoginRewardClaimRequested"
ECONOMY_DAILY_LOGIN_REWARD_GRANTED = "economy.DailyLoginRewardGranted"
ECONOMY_CREDIT_RECHARGE_CREATED = "economy.CreditRechargeCreated"
ECONOMY_WALLET_RECONCILIATION_MISMATCH = "economy.WalletReconciliationMismatch"

# Payments
PAYMENTS_ORDER_CREATED = "payments.OrderCreated"
PAYMENTS_ORDER_AUTHORIZED = "payments.OrderAuthorized"
PAYMENTS_ORDER_PAID = "payments.OrderPaid"
PAYMENTS_ORDER_FAILED = "payments.OrderFailed"
PAYMENTS_ORDER_EXPIRED = "payments.OrderExpired"
PAYMENTS_ORDER_CANCELLED = "payments.OrderCancelled"
PAYMENTS_ORDER_REFUND_INITIATED = "payments.OrderRefundInitiated"
PAYMENTS_ORDER_REFUNDED = "payments.OrderRefunded"
PAYMENTS_WEBHOOK_RECEIVED = "payments.WebhookReceived"

# Economy (recharge fulfilment, emitted once payment settles)
ECONOMY_CREDIT_RECHARGE_FULFILLED = "economy.CreditRechargeFulfilled"

# Commerce (V1-AVS)
COMMERCE_ORDER_CREATED = "commerce.OrderCreated"
COMMERCE_ORDER_PAID = "commerce.OrderPaid"
COMMERCE_ORDER_CANCELLED = "commerce.OrderCancelled"

# Platform
PLATFORM_CONFIG_UPDATED = "platform.ConfigUpdated"
PLATFORM_FEATURE_TOGGLED = "platform.FeatureToggled"

# Audit (meta)
AUDIT_AUDIT_FAILED = "audit.AuditFailed"
