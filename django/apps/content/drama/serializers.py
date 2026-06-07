"""Serializers for content.drama (request-body validation only)."""

from __future__ import annotations

from rest_framework import serializers


class UnlockEpisodeSerializer(serializers.Serializer):
    payment_method = serializers.ChoiceField(choices=["meow_points", "meow_credit"])


class SeriesProgressSerializer(serializers.Serializer):
    episode_id = serializers.UUIDField()
    progress_seconds = serializers.IntegerField(min_value=0)
    completed = serializers.BooleanField(default=False)


class EpisodeProgressSerializer(serializers.Serializer):
    progress_seconds = serializers.IntegerField(min_value=0)
    completed = serializers.BooleanField(default=False)


class AddCommentSerializer(serializers.Serializer):
    content = serializers.CharField()
    parent_id = serializers.UUIDField(required=False, allow_null=True)


class ShareSerializer(serializers.Serializer):
    channel = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
