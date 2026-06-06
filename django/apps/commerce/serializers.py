"""Serializers for commerce (request-body validation only)."""

from __future__ import annotations

from rest_framework import serializers


class CreateOrderSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, default=1)
    payment_provider = serializers.ChoiceField(choices=["stripe", "wallet"])
    payment_asset = serializers.CharField(max_length=20)
    shipping_address_id = serializers.UUIDField(required=False, allow_null=True)


class CancelOrderSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")
