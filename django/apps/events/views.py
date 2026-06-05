"""Admin views for events (events.md §12). All require the admin scope."""

from __future__ import annotations

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from libs.jwt_auth.permissions import IsAdmin
from libs.pagination.cursor import CursorPagination

from . import dispatcher, services


class OutboxListView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request: Request) -> Response:
        qs = services.outbox_queryset(
            event_type=request.query_params.get("event_type"),
            status=request.query_params.get("status"),
            actor_id=request.query_params.get("actor_id"),
            date_from=request.query_params.get("date_from"),
            date_to=request.query_params.get("date_to"),
        )
        paginator = CursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response([services.serialize_event(e) for e in page])


class OutboxDetailView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request: Request, event_id: str) -> Response:
        return Response(services.get_outbox_detail(event_id))


class DLQReplayView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request: Request, dlq_id: str) -> Response:
        new_event_id = dispatcher.replay_from_dlq(dlq_id)
        return Response({"replayed_event_id": new_event_id})


class DLQResolveView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request: Request, dlq_id: str) -> Response:
        note = request.data.get("note", "")
        dispatcher.resolve_dlq(dlq_id, resolved_by=str(request.user.id), note=note)
        return Response({"resolved": True})
