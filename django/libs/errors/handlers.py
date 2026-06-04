"""DRF custom exception handler — returns the standard error envelope.

All API errors use the single shape from contracts/conventions.md §5:
    {"error": {"code": "...", "message": "...", "detail": {...}}}

Handles:
- libs.errors.AppError subclasses (domain errors)
- DRF ValidationError (field-level → detail map)
- DRF AuthenticationFailed / NotAuthenticated (401)
- DRF PermissionDenied (403)
- DRF NotFound (404)
- Unhandled exceptions → 500 (logged, detail stripped in production)
"""

from __future__ import annotations

import logging

from django.conf import settings
from rest_framework import exceptions as drf_exc
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def exception_handler(exc: Exception, context: dict) -> Response | None:
    """Convert any exception to the standard error envelope."""
    from libs.errors.exceptions import AppError

    # --- Domain errors (AppError subclasses) ---
    if isinstance(exc, AppError):
        logger.info(
            "Application error: %s %s",
            exc.code,
            exc.message,
            extra={"error_code": exc.code},
        )
        return Response(exc.to_dict(), status=exc.http_status)

    # --- DRF exceptions ---
    response = drf_exception_handler(exc, context)

    if response is not None:
        response.data = _normalise_drf_error(exc, response)
        return response

    # --- Unhandled exception → 500 ---
    logger.exception("Unhandled exception in view %s", context.get("view"))
    error: dict = {
        "code": "INTERNAL_ERROR",
        "message": "An internal server error occurred.",
    }
    if getattr(settings, "DEBUG", False):
        error["detail"] = {"exception": repr(exc)}
    return Response({"error": error}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_drf_error(exc: Exception, response: Response) -> dict:
    """Map DRF error shapes to the standard envelope."""
    data = response.data

    if isinstance(exc, drf_exc.ValidationError):
        return _handle_validation(data)

    if isinstance(exc, (drf_exc.NotAuthenticated, drf_exc.AuthenticationFailed)):
        return _simple("AUTH_INVALID_TOKEN", _detail_str(data))

    if isinstance(exc, drf_exc.PermissionDenied):
        return _simple("AUTH_FORBIDDEN", _detail_str(data))

    if isinstance(exc, drf_exc.NotFound):
        return _simple("NOT_FOUND", _detail_str(data))

    if isinstance(exc, drf_exc.MethodNotAllowed):
        return _simple("VALIDATION_METHOD_NOT_ALLOWED", _detail_str(data))

    if isinstance(exc, drf_exc.UnsupportedMediaType):
        return _simple("VALIDATION_UNSUPPORTED_MEDIA_TYPE", _detail_str(data))

    if isinstance(exc, drf_exc.Throttled):
        return _simple("RATE_LIMIT_EXCEEDED", "Too many requests. Please slow down.")

    # Fallback for any other DRF exception
    code = getattr(getattr(data, "get", lambda k, d=None: d)("detail", None), "code", "ERROR")
    return _simple(str(code).upper(), _detail_str(data))


def _handle_validation(data: dict | list | str) -> dict:
    """Convert DRF field-level validation errors to the error envelope.

    Field errors become:
        {"error": {"code": "VALIDATION_ERROR", "message": "...", "detail": {"field": ["msg"]}}}
    """
    if isinstance(data, dict):
        # Normalise each field's errors to a list of strings
        detail: dict = {}
        non_field_messages: list[str] = []
        for field, errors in data.items():
            msgs = _flatten_errors(errors)
            if field == "non_field_errors":
                non_field_messages.extend(msgs)
            else:
                detail[field] = msgs

        message = non_field_messages[0] if non_field_messages else "Request validation failed."
        error: dict = {"code": "VALIDATION_ERROR", "message": message}
        if detail:
            error["detail"] = detail
        if len(non_field_messages) > 1:
            error.setdefault("detail", {})["non_field_errors"] = non_field_messages  # type: ignore[index]
        return {"error": error}

    if isinstance(data, list):
        msgs = _flatten_errors(data)
        return {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": msgs[0] if msgs else "Validation failed.",
            }
        }

    return _simple("VALIDATION_ERROR", str(data))


def _flatten_errors(errors: object) -> list[str]:
    if isinstance(errors, list):
        out: list[str] = []
        for e in errors:
            if hasattr(e, "__str__"):
                out.append(str(e))
        return out
    return [str(errors)]


def _detail_str(data: object) -> str:
    if isinstance(data, dict) and "detail" in data:
        return str(data["detail"])
    return str(data)


def _simple(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}
