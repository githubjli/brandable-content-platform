"""Serializers for payments (request-body validation only)."""

from __future__ import annotations

from rest_framework import serializers


class VerifyRequestSerializer(serializers.Serializer):
    txid = serializers.CharField(max_length=256)
