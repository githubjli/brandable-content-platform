"""Views for content.video (content-video.md §1-2). Parse → service → respond.

Public catalog/interactions accept optional auth (richer viewer_context when a
token is present); like/comment require auth.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from libs.pagination.cursor import CursorPagination

from . import services
from .serializers import AddCommentSerializer, ShareSerializer


def _viewer_id(request: Request) -> str | None:
    return str(request.user.id) if request.user.is_authenticated else None


def _client_ip(request: Request) -> str | None:
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR")


class VideoListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        qs = services.videos_queryset(
            category=request.query_params.get("category"),
            access_type=request.query_params.get("access_type"),
            search=request.query_params.get("search"),
        )
        paginator = CursorPagination()
        paginator.ordering = services.video_ordering(  # type: ignore[assignment]
            request.query_params.get("ordering")
        )
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(
            services.serialize_videos(list(page), _viewer_id(request))
        )


class VideoDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request, video_id: str) -> Response:
        return Response(services.get_video(video_id=video_id, viewer_id=_viewer_id(request)))


class VideoInteractionsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request, video_id: str) -> Response:
        return Response(services.get_interactions(video_id=video_id, viewer_id=_viewer_id(request)))


class VideoLikeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, video_id: str) -> Response:
        return Response(services.like_video(user_id=str(request.user.id), video_id=video_id))

    def delete(self, request: Request, video_id: str) -> Response:
        return Response(services.unlike_video(user_id=str(request.user.id), video_id=video_id))


class VideoCommentsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request, video_id: str) -> Response:
        parent_id = request.query_params.get("parent_id")
        if parent_id:
            qs = services.replies_queryset(video_id=video_id, parent_id=parent_id)
        else:
            qs = services.comments_queryset(video_id=video_id)
        paginator = CursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(services.serialize_comments(list(page)))

    def post(self, request: Request, video_id: str) -> Response:
        if not request.user.is_authenticated:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        serializer = AddCommentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        parent = data.get("parent_id")
        result = services.add_comment(
            user_id=str(request.user.id),
            video_id=video_id,
            content=data["content"],
            parent_id=str(parent) if parent else None,
        )
        return Response(result, status=status.HTTP_201_CREATED)


class VideoShareView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request, video_id: str) -> Response:
        serializer = ShareSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.track_share(
            video_id=video_id,
            user_id=_viewer_id(request),
            channel=serializer.validated_data.get("channel", ""),
            ip_address=_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        return Response(result)


class VideoViewTrackView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request, video_id: str) -> Response:
        result = services.track_view(
            video_id=video_id, user_id=_viewer_id(request), ip_address=_client_ip(request)
        )
        return Response(result)
