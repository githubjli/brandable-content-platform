"""Serializers for content.gift (request-body validation only)."""

from __future__ import annotations

from rest_framework import serializers


class SendGiftSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=0)
    currency = serializers.CharField(max_length=20)
    payment_method = serializers.ChoiceField(choices=["meow_points", "meow_credit"])
    gift_code = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
