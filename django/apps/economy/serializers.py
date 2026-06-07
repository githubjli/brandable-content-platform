"""Serializers for economy.

Request-body validation only. Response shaping lives in the service layer
(which returns plain dicts), matching the identity app's convention.
"""

from __future__ import annotations

from rest_framework import serializers


class CreditRechargeCreateSerializer(serializers.Serializer):
    package_code = serializers.CharField(max_length=64)


class CreditRechargeSubmitTxidSerializer(serializers.Serializer):
    package_code = serializers.CharField(max_length=64)
    txid = serializers.CharField(max_length=256)


class CreditRechargeVerifySerializer(serializers.Serializer):
    txid = serializers.CharField(max_length=256)


class CreditRedeemCreateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=0)
    redeem_method = serializers.CharField(max_length=40)
    blockchain_network = serializers.CharField(
        max_length=20, required=False, allow_blank=True, default=""
    )
    account_snapshot = serializers.DictField(required=False, default=dict)


class RedeemReviewSerializer(serializers.Serializer):
    """Admin note for approve/reject."""

    admin_note = serializers.CharField(required=False, allow_blank=True, default="")
