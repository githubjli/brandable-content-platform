"""Views for content.live (content-live.md §1, §5). Parse → service → respond.

Public browse/detail/status accept optional auth (owner sees broadcaster_config);
the broadcaster lifecycle requires auth + creator.
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
from .serializers import (
    BindProductSerializer,
    CreateStreamSerializer,
    PostChatMessageSerializer,
    SendLiveGiftSerializer,
    SetPaymentMethodsSerializer,
    UpdateProductBindingSerializer,
    UpdateStreamSerializer,
)


def _viewer_id(request: Request) -> str | None:
    return str(request.user.id) if request.user.is_authenticated else None


def _client_ip(request: Request) -> str | None:
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR")


# ---------------------------------------------------------------------------
# Viewer — browse
# ---------------------------------------------------------------------------


class StreamListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        qs = services.streams_queryset(
            status=request.query_params.get("status"),
            owner_id=request.query_params.get("owner_id"),
        )
        paginator = CursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(services.serialize_streams(list(page)))


class StreamDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request, stream_id: str) -> Response:
        return Response(services.get_stream(stream_id=stream_id, viewer_id=_viewer_id(request)))


class StreamStatusView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request, stream_id: str) -> Response:
        return Response(services.get_status(stream_id=stream_id, viewer_id=_viewer_id(request)))


class StreamWatchConfigView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request, stream_id: str) -> Response:
        return Response(
            services.get_watch_config(
                stream_id=stream_id,
                viewer_id=_viewer_id(request),
                ip_address=_client_ip(request),
            )
        )


class StreamProductsView(APIView):
    """Viewer-facing list of products bound to a stream (content-live.md §1)."""

    permission_classes = [AllowAny]

    def get(self, request: Request, stream_id: str) -> Response:
        return Response(
            services.list_stream_products(stream_id=stream_id, viewer_id=_viewer_id(request))
        )


# ---------------------------------------------------------------------------
# Broadcaster — lifecycle
# ---------------------------------------------------------------------------


class MyStreamListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        qs = services.my_streams_queryset(user_id=str(request.user.id))
        paginator = CursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(
            [services.serialize_stream(s, broadcaster=True) for s in page]
        )

    @idempotent
    def post(self, request: Request) -> Response:
        serializer = CreateStreamSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        category_id = data.pop("category_id", None)
        result = services.create_stream(
            user_id=str(request.user.id),
            category_id=str(category_id) if category_id else None,
            **data,
        )
        return Response(result, status=status.HTTP_201_CREATED)


class MyStreamDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, stream_id: str) -> Response:
        return Response(services.get_my_stream(user_id=str(request.user.id), stream_id=stream_id))

    def patch(self, request: Request, stream_id: str) -> Response:
        serializer = UpdateStreamSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        if "category_id" in data:
            cid = data.pop("category_id")
            data["category_id"] = str(cid) if cid else None
        result = services.update_stream(user_id=str(request.user.id), stream_id=stream_id, **data)
        return Response(result)


class MyStreamPrepareView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request, stream_id: str) -> Response:
        return Response(services.prepare_stream(user_id=str(request.user.id), stream_id=stream_id))


class MyStreamStartView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request, stream_id: str) -> Response:
        return Response(services.start_stream(user_id=str(request.user.id), stream_id=stream_id))


class MyStreamEndView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request, stream_id: str) -> Response:
        return Response(services.end_stream(user_id=str(request.user.id), stream_id=stream_id))


# ---------------------------------------------------------------------------
# Chat — content-live.md §2
# ---------------------------------------------------------------------------


class ChatMessagesView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request, stream_id: str) -> Response:
        return Response(
            services.list_messages(
                stream_id=stream_id,
                after_id=request.query_params.get("after_id"),
                limit=request.query_params.get("limit", 50),
                viewer_id=_viewer_id(request),
            )
        )

    def post(self, request: Request, stream_id: str) -> Response:
        if not request.user.is_authenticated:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        serializer = PostChatMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        product = data.get("product_id")
        result = services.post_message(
            user_id=str(request.user.id),
            stream_id=stream_id,
            content=data.get("content", ""),
            product_id=str(product) if product else None,
        )
        return Response(result, status=status.HTTP_201_CREATED)


class ChatMessageDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request: Request, stream_id: str, message_id: str) -> Response:
        services.delete_message(
            user_id=str(request.user.id), stream_id=stream_id, message_id=message_id
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChatMessagePinView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request: Request, stream_id: str, message_id: str) -> Response:
        return Response(
            services.pin_message(
                user_id=str(request.user.id), stream_id=stream_id, message_id=message_id
            )
        )


# ---------------------------------------------------------------------------
# Live gift — content-live.md §4
# ---------------------------------------------------------------------------


class LiveGiftSendView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request, stream_id: str) -> Response:
        serializer = SendLiveGiftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        idempotency_key = request.META.get("HTTP_IDEMPOTENCY_KEY") or request.headers.get(
            "Idempotency-Key", ""
        )
        result = services.send_live_gift(
            sender_id=str(request.user.id),
            stream_id=stream_id,
            amount=data["amount"],
            currency=data["currency"],
            payment_method=data["payment_method"],
            gift_code=data.get("gift_code", ""),
            idempotency_key=idempotency_key,
        )
        return Response(result, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Broadcaster — products & payment methods (content-live.md §6)
# ---------------------------------------------------------------------------


class MyStreamProductsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, stream_id: str) -> Response:
        return Response(
            services.list_my_stream_products(user_id=str(request.user.id), stream_id=stream_id)
        )

    def post(self, request: Request, stream_id: str) -> Response:
        serializer = BindProductSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = services.bind_product(
            user_id=str(request.user.id),
            stream_id=stream_id,
            product_id=str(data["product_id"]),
            sort_order=data.get("sort_order", 0),
            is_featured=data.get("is_featured", False),
        )
        return Response(result, status=status.HTTP_201_CREATED)


class MyStreamProductDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request: Request, stream_id: str, binding_id: str) -> Response:
        serializer = UpdateProductBindingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.update_product_binding(
            user_id=str(request.user.id),
            stream_id=stream_id,
            binding_id=binding_id,
            **dict(serializer.validated_data),
        )
        return Response(result)

    def delete(self, request: Request, stream_id: str, binding_id: str) -> Response:
        services.unbind_product(
            user_id=str(request.user.id), stream_id=stream_id, binding_id=binding_id
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MyStreamPaymentMethodsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, stream_id: str) -> Response:
        return Response(
            services.list_payment_methods(user_id=str(request.user.id), stream_id=stream_id)
        )

    def put(self, request: Request, stream_id: str) -> Response:
        serializer = SetPaymentMethodsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.set_payment_methods(
            user_id=str(request.user.id),
            stream_id=stream_id,
            methods=list(serializer.validated_data["methods"]),
        )
        return Response(result)
