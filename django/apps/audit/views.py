"""Admin views for audit (audit.md §7). All require the admin scope."""

from __future__ import annotations

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from libs.jwt_auth.permissions import IsAdmin
from libs.pagination.cursor import CursorPagination

from . import services


class AuditListView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request: Request) -> Response:
        qs = services.audit_queryset(
            action=request.query_params.get("action"),
            actor_id=request.query_params.get("actor_id"),
            target_type=request.query_params.get("target_type"),
            target_id=request.query_params.get("target_id"),
            severity=request.query_params.get("severity"),
            correlation_id=request.query_params.get("correlation_id"),
            date_from=request.query_params.get("date_from"),
            date_to=request.query_params.get("date_to"),
        )
        paginator = CursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response([services.serialize_audit(r) for r in page])


class AuditDetailView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request: Request, audit_id: str) -> Response:
        return Response(services.get_audit(audit_id))
