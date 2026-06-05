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
