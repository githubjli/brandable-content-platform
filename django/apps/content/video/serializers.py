"""Serializers for content.video (request-body validation only)."""

from __future__ import annotations

from rest_framework import serializers


class AddCommentSerializer(serializers.Serializer):
    content = serializers.CharField()
    parent_id = serializers.UUIDField(required=False, allow_null=True)


class ShareSerializer(serializers.Serializer):
    channel = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")


class CreateVideoSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=300)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    file_url = serializers.URLField(required=False, allow_blank=True, default="")
    thumbnail_url = serializers.URLField(required=False, allow_blank=True, default="")
    duration_seconds = serializers.IntegerField(min_value=0, default=0)
    preview_seconds = serializers.IntegerField(min_value=0, default=0)
    category_id = serializers.UUIDField(required=False, allow_null=True)
    visibility = serializers.ChoiceField(
        choices=["public", "private", "unlisted"], default="public"
    )
    access_type = serializers.ChoiceField(choices=["free", "members_only"], default="free")


class UpdateVideoSerializer(serializers.Serializer):
    """All fields optional (PATCH semantics)."""

    title = serializers.CharField(max_length=300, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    file_url = serializers.URLField(required=False, allow_blank=True)
    thumbnail_url = serializers.URLField(required=False, allow_blank=True)
    duration_seconds = serializers.IntegerField(min_value=0, required=False)
    preview_seconds = serializers.IntegerField(min_value=0, required=False)
    category_id = serializers.UUIDField(required=False, allow_null=True)
    visibility = serializers.ChoiceField(choices=["public", "private", "unlisted"], required=False)
    access_type = serializers.ChoiceField(choices=["free", "members_only"], required=False)


class RegenerateThumbnailSerializer(serializers.Serializer):
    time_offset_seconds = serializers.FloatField(min_value=0, required=False, default=0.0)
