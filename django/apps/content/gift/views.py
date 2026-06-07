"""Views for content.gift (gift.md §1-2). Parse → service → respond.

Catalog is public; send + history require auth. The send endpoints live under the
content target paths but resolve to this app's views.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from libs.idempotency import idempotent
from libs.pagination.cursor import CursorPagination

from . import services
from .models import GiftTransaction
from .serializers import SendGiftSerializer


class GiftCatalogView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        return Response(services.list_catalog())


class _SendGiftBase(APIView):
    permission_classes = [IsAuthenticated]
    target_type: str = ""

    def _send(self, request: Request, target_id: str) -> Response:
        serializer = SendGiftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        idempotency_key = request.META.get("HTTP_IDEMPOTENCY_KEY") or request.headers.get(
            "Idempotency-Key", ""
        )
        result = services.send_gift(
            sender_id=str(request.user.id),
            target_type=self.target_type,
            target_id=target_id,
            amount=data["amount"],
            currency=data["currency"],
            payment_method=data["payment_method"],
            gift_code=data.get("gift_code", ""),
            idempotency_key=idempotency_key,
        )
        return Response(result, status=status.HTTP_201_CREATED)


class VideoGiftSendView(_SendGiftBase):
    target_type = GiftTransaction.VIDEO

    @idempotent
    def post(self, request: Request, video_id: str) -> Response:
        return self._send(request, str(video_id))


class DramaGiftSendView(_SendGiftBase):
    target_type = GiftTransaction.DRAMA_SERIES

    @idempotent
    def post(self, request: Request, series_id: str) -> Response:
        return self._send(request, str(series_id))


class GiftSentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        qs = services.sent_queryset(user_id=str(request.user.id))
        paginator = CursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response([services.serialize_transaction(t) for t in page])


class GiftReceivedView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        qs = services.received_queryset(user_id=str(request.user.id))
        paginator = CursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response([services.serialize_transaction(t) for t in page])
