"""URL patterns for economy.

Mounted under api/v1/ by config/urls.py, so paths here are relative to that
prefix (e.g. "economy/wallets/me" -> /api/v1/economy/wallets/me).
"""

from django.urls import path

from . import views

urlpatterns = [
    # Wallets
    path("economy/wallets/me", views.AggregateWalletView.as_view(), name="economy-wallet-me"),
    path(
        "economy/wallets/me/point",
        views.PointWalletView.as_view(),
        name="economy-wallet-point",
    ),
    path(
        "economy/wallets/me/credit",
        views.CreditWalletView.as_view(),
        name="economy-wallet-credit",
    ),
    # Ledgers
    path(
        "economy/wallets/me/point/ledger",
        views.PointLedgerView.as_view(),
        name="economy-ledger-point",
    ),
    path(
        "economy/wallets/me/credit/ledger",
        views.CreditLedgerView.as_view(),
        name="economy-ledger-credit",
    ),
    # Credit packages
    path(
        "economy/credit-packages",
        views.CreditPackagesView.as_view(),
        name="economy-credit-packages",
    ),
    # Daily login reward
    path(
        "economy/daily-rewards/claim",
        views.DailyRewardClaimView.as_view(),
        name="economy-daily-reward-claim",
    ),
    path(
        "economy/daily-rewards/status",
        views.DailyRewardStatusView.as_view(),
        name="economy-daily-reward-status",
    ),
    # Credit recharge (skeleton)
    path(
        "economy/credit-recharge-info",
        views.CreditRechargeInfoView.as_view(),
        name="economy-recharge-info",
    ),
    path(
        "economy/credit-recharges",
        views.CreditRechargeCreateView.as_view(),
        name="economy-recharge-create",
    ),
    path(
        "economy/credit-recharges/submit-txid",
        views.CreditRechargeSubmitTxidView.as_view(),
        name="economy-recharge-submit-txid",
    ),
    path(
        "economy/credit-recharges/<str:order_no>/verify",
        views.CreditRechargeVerifyView.as_view(),
        name="economy-recharge-verify",
    ),
    # Credit redeem (admin workflow) — §7
    path(
        "economy/credit-redeems",
        views.CreditRedeemView.as_view(),
        name="economy-credit-redeems",
    ),
    path(
        "economy/credit-redeems/<uuid:redeem_id>/approve",
        views.CreditRedeemApproveView.as_view(),
        name="economy-credit-redeem-approve",
    ),
    path(
        "economy/credit-redeems/<uuid:redeem_id>/reject",
        views.CreditRedeemRejectView.as_view(),
        name="economy-credit-redeem-reject",
    ),
    path(
        "economy/credit-redeems/<uuid:redeem_id>/complete",
        views.CreditRedeemCompleteView.as_view(),
        name="economy-credit-redeem-complete",
    ),
]
