"""DRF permissions backed by JWT scope claims."""

from __future__ import annotations

from typing import Any

from rest_framework.permissions import BasePermission


class IsAdmin(BasePermission):
    """Allow only tokens carrying the ``admin`` scope (signer adds it for admins)."""

    message = "Admin privileges are required for this endpoint."

    def has_permission(self, request: Any, view: Any) -> bool:
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return False
        payload = getattr(user, "payload", {}) or {}
        return "admin" in payload.get("scope", [])
