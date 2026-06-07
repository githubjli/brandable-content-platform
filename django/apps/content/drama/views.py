"""Views for content.drama (content-drama.md §1-3). Parse → service → respond.

Series/episodes catalog accepts optional auth (per-viewer unlock state); the
episode unlock requires auth + an Idempotency-Key header.
"""

from __future__ import annotations

from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from libs.idempotency import idempotent
from libs.pagination.cursor import CursorPagination

from . import services
from .serializers import UnlockEpisodeSerializer


def _viewer_id(request: Request) -> str | None:
    return str(request.user.id) if request.user.is_authenticated else None


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
