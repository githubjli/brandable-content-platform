"""Cursor-based pagination (as mandated by contracts/conventions.md)."""

from rest_framework.pagination import CursorPagination as _DRFCursorPagination
from rest_framework.response import Response


class CursorPagination(_DRFCursorPagination):
    page_size = 20
    max_page_size = 100
    ordering = "-created_at"
    cursor_query_param = "cursor"
    page_size_query_param = "limit"

    def get_paginated_response(self, data: list) -> Response:
        return Response(
            {
                "results": data,
                "cursor": {
                    "next": self.get_next_link(),
                    "prev": self.get_previous_link(),
                },
            }
        )

    def get_paginated_response_schema(self, schema: dict) -> dict:
        return {
            "type": "object",
            "properties": {
                "results": schema,
                "cursor": {
                    "type": "object",
                    "properties": {
                        "next": {"type": "string", "nullable": True},
                        "prev": {"type": "string", "nullable": True},
                    },
                },
            },
        }
