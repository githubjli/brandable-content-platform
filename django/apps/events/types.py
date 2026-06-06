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

# Commerce (V2 — cart)
COMMERCE_CART_ITEM_ADDED = "commerce.CartItemAdded"
COMMERCE_CART_ITEM_REMOVED = "commerce.CartItemRemoved"

# Commerce (V2 — seller onboarding)
COMMERCE_SELLER_APPLICATION_SUBMITTED = "commerce.SellerApplicationSubmitted"
COMMERCE_SELLER_APPLICATION_APPROVED = "commerce.SellerApplicationApproved"
COMMERCE_SELLER_APPLICATION_REJECTED = "commerce.SellerApplicationRejected"
COMMERCE_STORE_CREATED = "commerce.StoreCreated"

# Commerce (V2 — seller products + fulfillment)
COMMERCE_PRODUCT_CREATED = "commerce.ProductCreated"
COMMERCE_PRODUCT_UPDATED = "commerce.ProductUpdated"
COMMERCE_PRODUCT_ARCHIVED = "commerce.ProductArchived"
COMMERCE_ORDER_SHIPPED = "commerce.OrderShipped"
COMMERCE_ORDER_COMPLETED = "commerce.OrderCompleted"

# Commerce (V2 — refunds)
COMMERCE_REFUND_REQUESTED = "commerce.RefundRequested"
COMMERCE_REFUND_APPROVED = "commerce.RefundApproved"
COMMERCE_REFUND_REJECTED = "commerce.RefundRejected"
COMMERCE_REFUND_COMPLETED = "commerce.RefundCompleted"

# Content — video (V2)
CONTENT_VIDEO_LIKED = "content.VideoLiked"
CONTENT_VIDEO_UNLIKED = "content.VideoUnliked"
CONTENT_VIDEO_COMMENTED = "content.VideoCommented"
CONTENT_VIDEO_SHARED = "content.VideoShared"
CONTENT_VIDEO_VIEWED = "content.VideoViewed"

# Platform
PLATFORM_CONFIG_UPDATED = "platform.ConfigUpdated"
PLATFORM_FEATURE_TOGGLED = "platform.FeatureToggled"

# Audit (meta)
AUDIT_AUDIT_FAILED = "audit.AuditFailed"
