"""Serializers for content.live (request-body validation only)."""

from __future__ import annotations

from rest_framework import serializers


class CreateStreamSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=300)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    visibility = serializers.ChoiceField(choices=["public", "private"], default="public")
    thumbnail_url = serializers.URLField(required=False, allow_blank=True, default="")
    category_id = serializers.UUIDField(required=False, allow_null=True)


class UpdateStreamSerializer(serializers.Serializer):
    """All fields optional (PATCH semantics)."""

    title = serializers.CharField(max_length=300, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    visibility = serializers.ChoiceField(choices=["public", "private"], required=False)
    thumbnail_url = serializers.URLField(required=False, allow_blank=True)
    category_id = serializers.UUIDField(required=False, allow_null=True)


class PostChatMessageSerializer(serializers.Serializer):
    content = serializers.CharField(required=False, allow_blank=True, default="")
    product_id = serializers.UUIDField(required=False, allow_null=True)


class SendLiveGiftSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=0)
    currency = serializers.CharField(max_length=20)
    payment_method = serializers.ChoiceField(choices=["meow_points", "meow_credit"])
    gift_code = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
