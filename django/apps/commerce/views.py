"""Views for commerce (commerce.md §3, V1-AVS). Parse → call service → return."""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from libs.idempotency import idempotent

from . import services
from .serializers import CancelOrderSerializer, CreateOrderSerializer


def _uid(request: Request) -> str:
    return str(request.user.id)


class OrderCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request) -> Response:
        serializer = CreateOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        idempotency_key = request.META.get("HTTP_IDEMPOTENCY_KEY") or request.headers.get(
            "Idempotency-Key", ""
        )
        shipping = data.get("shipping_address_id")
        result = services.create_order(
            user_id=_uid(request),
            product_id=str(data["product_id"]),
            quantity=data["quantity"],
            payment_provider=data["payment_provider"],
            payment_asset=data["payment_asset"],
            shipping_address_id=str(shipping) if shipping else None,
            idempotency_key=idempotency_key,
        )
        return Response(result, status=status.HTTP_201_CREATED)


class OrderDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, order_no: str) -> Response:
        return Response(services.get_order(order_no=order_no, user_id=_uid(request)))


class OrderCancelView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request, order_no: str) -> Response:
        serializer = CancelOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            services.cancel_order(
                order_no=order_no,
                user_id=_uid(request),
                reason=serializer.validated_data.get("reason", ""),
            )
        )
