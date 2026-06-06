"""Serializers for content.video (request-body validation only)."""

from __future__ import annotations

from rest_framework import serializers


class AddCommentSerializer(serializers.Serializer):
    content = serializers.CharField()
    parent_id = serializers.UUIDField(required=False, allow_null=True)


class ShareSerializer(serializers.Serializer):
    channel = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
