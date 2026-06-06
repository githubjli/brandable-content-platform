"""Platform config views — health + public/admin config (platform-config.md §2-3)."""

from __future__ import annotations

from django.http import HttpRequest
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from libs.idempotency import idempotent
from libs.jwt_auth.permissions import IsAdmin

from . import services
from .serializers import ConfigUpdateSerializer


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request: HttpRequest) -> Response:
    """GET /api/v1/health — returns {"status": "ok", "trace_id": "..."}"""
    from libs.telemetry import get_trace_id

    return Response({"status": "ok", "trace_id": get_trace_id()})


class PublicConfigView(APIView):
    """GET /api/v1/platform/config — public branding/feature config (no auth)."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        config = services.get_platform_config()
        response = Response(services.serialize_public(config))
        response["Cache-Control"] = "public, max-age=300"  # 5 min CDN cache
        return response


class AdminConfigView(APIView):
    """GET/PATCH /api/v1/admin/platform/config — admin read + partial update."""

    permission_classes = [IsAdmin]

    def get(self, request: Request) -> Response:
        return Response(services.serialize_public(services.get_platform_config()))

    @idempotent
    def patch(self, request: Request) -> Response:
        serializer = ConfigUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return Response(
            services.update_config(changes=serializer.validated_data, actor_id=str(request.user.id))
        )
