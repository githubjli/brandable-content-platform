"""Views for membership (membership.md V2). Parse → service → respond."""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from libs.idempotency import idempotent

from . import services
from .serializers import CreateMembershipOrderSerializer


class PlanListView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        return Response(services.list_plans())


class MembershipMeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(services.get_my_membership(user_id=str(request.user.id)))


class MembershipOrderCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request) -> Response:
        serializer = CreateMembershipOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        idempotency_key = request.META.get("HTTP_IDEMPOTENCY_KEY") or request.headers.get(
            "Idempotency-Key", ""
        )
        result = services.create_order(
            user_id=str(request.user.id),
            plan_id=str(data["plan_id"]),
            payment_provider=data["payment_provider"],
            payment_asset=data["payment_asset"],
            idempotency_key=idempotency_key,
        )
        return Response(result, status=status.HTTP_201_CREATED)
