"""Views for payments (payments.md §3-4). Views parse → call service → return."""

from __future__ import annotations

from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from libs.idempotency import idempotent
from libs.pagination.cursor import CursorPagination

from . import services
from .serializers import VerifyRequestSerializer


def _uid(request: Request) -> str:
    return str(request.user.id)


class OrderDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, order_no: str) -> Response:
        return Response(services.get_order(order_no=order_no, user_id=_uid(request)))


class OrderListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        qs = services.orders_queryset(
            user_id=_uid(request),
            status=request.query_params.get("status"),
            business_kind=request.query_params.get("business_kind"),
            date_from=request.query_params.get("date_from"),
        )
        paginator = CursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response([services.serialize_order(o) for o in page])


class OrderVerifyView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request, order_no: str) -> Response:
        serializer = VerifyRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            services.verify_order(
                order_no=order_no, user_id=_uid(request), txid=serializer.validated_data["txid"]
            )
        )


class OrderCancelView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request, order_no: str) -> Response:
        return Response(services.cancel_order(order_no=order_no, actor_id=_uid(request)))


class StripeWebhookView(APIView):
    # Stripe signs the request; no JWT. Verification happens in the service.
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        result = services.handle_stripe_webhook(
            payload=request.body,
            signature=request.META.get("HTTP_STRIPE_SIGNATURE", ""),
        )
        return Response(result)
