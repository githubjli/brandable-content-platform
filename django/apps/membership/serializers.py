"""Serializers for membership (request-body validation only)."""

from __future__ import annotations

from rest_framework import serializers


class CreateMembershipOrderSerializer(serializers.Serializer):
    plan_id = serializers.UUIDField()
    payment_provider = serializers.ChoiceField(choices=["stripe", "wallet"])
    payment_asset = serializers.CharField(max_length=20)
