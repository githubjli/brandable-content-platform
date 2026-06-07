"""Serializers for content.drama (request-body validation only)."""

from __future__ import annotations

from rest_framework import serializers


class UnlockEpisodeSerializer(serializers.Serializer):
    payment_method = serializers.ChoiceField(choices=["meow_points", "meow_credit"])
