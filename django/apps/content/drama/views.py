"""Views for content.drama (content-drama.md §1-3). Parse → service → respond.

Series/episodes catalog accepts optional auth (per-viewer unlock state); the
episode unlock requires auth + an Idempotency-Key header.
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
    AddCommentSerializer,
    EpisodeProgressSerializer,
    SeriesProgressSerializer,
    ShareSerializer,
    UnlockEpisodeSerializer,
)


def _viewer_id(request: Request) -> str | None:
    return str(request.user.id) if request.user.is_authenticated else None


def _client_ip(request: Request) -> str | None:
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR")


class SeriesListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        qs = services.series_queryset(category=request.query_params.get("category"))
        paginator = CursorPagination()
        paginator.ordering = services.series_ordering(  # type: ignore[assignment]
            request.query_params.get("ordering")
        )
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(
            services.serialize_series_list(list(page), _viewer_id(request))
        )


class SeriesDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request, series_id: str) -> Response:
        return Response(services.get_series(series_id=series_id, viewer_id=_viewer_id(request)))


class EpisodeListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request, series_id: str) -> Response:
        return Response(services.list_episodes(series_id=series_id, viewer_id=_viewer_id(request)))


class EpisodeDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request, series_id: str, episode_no: int) -> Response:
        return Response(
            services.get_episode(
                series_id=series_id, episode_no=episode_no, viewer_id=_viewer_id(request)
            )
        )


class EpisodeUnlockView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request, episode_id: str) -> Response:
        serializer = UnlockEpisodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            services.unlock_episode(
                user_id=str(request.user.id),
                episode_id=episode_id,
                payment_method=serializer.validated_data["payment_method"],
            )
        )


# ---------------------------------------------------------------------------
# Favorites — §5
# ---------------------------------------------------------------------------


class SeriesFavoriteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, series_id: str) -> Response:
        return Response(services.add_favorite(user_id=str(request.user.id), series_id=series_id))

    def delete(self, request: Request, series_id: str) -> Response:
        return Response(services.remove_favorite(user_id=str(request.user.id), series_id=series_id))


# ---------------------------------------------------------------------------
# Watch progress — §4
# ---------------------------------------------------------------------------


class SeriesProgressView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, series_id: str) -> Response:
        return Response(services.get_progress(user_id=str(request.user.id), series_id=series_id))

    def post(self, request: Request, series_id: str) -> Response:
        serializer = SeriesProgressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return Response(
            services.upsert_progress(
                user_id=str(request.user.id),
                series_id=series_id,
                episode_id=str(data["episode_id"]),
                progress_seconds=data["progress_seconds"],
                completed=data["completed"],
            )
        )


class EpisodeProgressView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, episode_id: str) -> Response:
        serializer = EpisodeProgressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return Response(
            services.upsert_episode_progress(
                user_id=str(request.user.id),
                episode_id=episode_id,
                progress_seconds=data["progress_seconds"],
                completed=data["completed"],
            )
        )


# ---------------------------------------------------------------------------
# Comments — §6
# ---------------------------------------------------------------------------


class SeriesCommentsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request, series_id: str) -> Response:
        parent_id = request.query_params.get("parent_id")
        if parent_id:
            qs = services.replies_queryset(series_id=series_id, parent_id=parent_id)
        else:
            qs = services.comments_queryset(series_id=series_id)
        paginator = CursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(services.serialize_comments(list(page)))

    def post(self, request: Request, series_id: str) -> Response:
        if not request.user.is_authenticated:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        serializer = AddCommentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        parent = data.get("parent_id")
        result = services.add_comment(
            user_id=str(request.user.id),
            series_id=series_id,
            content=data["content"],
            parent_id=str(parent) if parent else None,
        )
        return Response(result, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# View + share tracking — §1
# ---------------------------------------------------------------------------


class SeriesViewTrackView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request, series_id: str) -> Response:
        return Response(
            services.track_view(
                series_id=series_id, user_id=_viewer_id(request), ip_address=_client_ip(request)
            )
        )


class SeriesShareView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request, series_id: str) -> Response:
        serializer = ShareSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            services.track_share(
                series_id=series_id,
                user_id=_viewer_id(request),
                channel=serializer.validated_data.get("channel", ""),
            )
        )
