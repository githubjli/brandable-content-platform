"""Platform config views — includes the health endpoint."""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request: HttpRequest) -> Response:
    """GET /api/v1/health — returns {"status": "ok", "trace_id": "..."}"""
    from libs.telemetry import get_trace_id  # noqa: PLC0415
    return Response({"status": "ok", "trace_id": get_trace_id()})
