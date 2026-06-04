"""Domain exception hierarchy.

All exceptions map to the standard error envelope (conventions.md §5):
    {"error": {"code": "...", "message": "...", "detail": {...}}}

Code namespaces:
    AUTH_*        authentication / authorization
    VALIDATION_*  input validation
    WALLET_*      economy domain
    ORDER_*       payments / commerce domain
    KYC_*         KYC domain
    LIVE_*        live streaming domain
    RATE_LIMIT_*  rate limiting
    INTERNAL_*    server-side bugs
    UPSTREAM_*    external dependency failures
"""

from __future__ import annotations

from rest_framework import status


class AppError(Exception):
    """Base class for all application errors.

    Subclass this per domain. The DRF exception handler in libs.errors.handlers
    converts any AppError subclass to the standard error envelope automatically.
    """

    http_status: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_code: str = "INTERNAL_ERROR"
    default_message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        code: str | None = None,
        detail: dict | None = None,
        http_status: int | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.code = code or self.default_code
        self.detail = detail
        if http_status is not None:
            self.http_status = http_status
        super().__init__(self.message)

    def to_dict(self) -> dict:
        error: dict = {"code": self.code, "message": self.message}
        if self.detail:
            error["detail"] = self.detail
        return {"error": error}


# ---------------------------------------------------------------------------
# HTTP 400 — Validation
# ---------------------------------------------------------------------------


class ValidationError(AppError):
    """Input validation failed (HTTP 400)."""

    http_status = status.HTTP_400_BAD_REQUEST
    default_code = "VALIDATION_ERROR"
    default_message = "Request validation failed."


# ---------------------------------------------------------------------------
# HTTP 401 — Auth
# ---------------------------------------------------------------------------


class AuthError(AppError):
    """Authentication failed (HTTP 401)."""

    http_status = status.HTTP_401_UNAUTHORIZED
    default_code = "AUTH_INVALID_TOKEN"
    default_message = "Authentication credentials were not provided or are invalid."


class TokenExpiredError(AuthError):
    default_code = "AUTH_TOKEN_EXPIRED"
    default_message = "The access token has expired."


class TokenInvalidError(AuthError):
    default_code = "AUTH_TOKEN_INVALID"
    default_message = "The access token is invalid."


# ---------------------------------------------------------------------------
# HTTP 403 — Forbidden
# ---------------------------------------------------------------------------


class ForbiddenError(AppError):
    """Authenticated but not authorized (HTTP 403)."""

    http_status = status.HTTP_403_FORBIDDEN
    default_code = "AUTH_FORBIDDEN"
    default_message = "You do not have permission to perform this action."


# ---------------------------------------------------------------------------
# HTTP 404 — Not found
# ---------------------------------------------------------------------------


class NotFoundError(AppError):
    """Resource not found (HTTP 404)."""

    http_status = status.HTTP_404_NOT_FOUND
    default_code = "NOT_FOUND"
    default_message = "The requested resource was not found."


# ---------------------------------------------------------------------------
# HTTP 409 — Conflict
# ---------------------------------------------------------------------------


class ConflictError(AppError):
    """Conflict (duplicate, state mismatch) (HTTP 409)."""

    http_status = status.HTTP_409_CONFLICT
    default_code = "CONFLICT"
    default_message = "The request conflicts with the current state of the resource."


# ---------------------------------------------------------------------------
# HTTP 422 — Business rule violation
# ---------------------------------------------------------------------------


class UnprocessableError(AppError):
    """Business rule violated (HTTP 422)."""

    http_status = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "UNPROCESSABLE"
    default_message = "The request is semantically invalid."


# ---------------------------------------------------------------------------
# HTTP 429 — Rate limit
# ---------------------------------------------------------------------------


class RateLimitError(AppError):
    """Rate limited (HTTP 429)."""

    http_status = status.HTTP_429_TOO_MANY_REQUESTS
    default_code = "RATE_LIMIT_EXCEEDED"
    default_message = "Too many requests. Please slow down."


# ---------------------------------------------------------------------------
# HTTP 500 — Internal
# ---------------------------------------------------------------------------


class InternalError(AppError):
    """Unexpected server error (HTTP 500)."""

    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_code = "INTERNAL_ERROR"
    default_message = "An internal server error occurred."


# ---------------------------------------------------------------------------
# HTTP 502 — Upstream
# ---------------------------------------------------------------------------


class UpstreamError(AppError):
    """External dependency failure: gRPC service, payment provider, etc. (HTTP 502)."""

    http_status = status.HTTP_502_BAD_GATEWAY
    default_code = "UPSTREAM_ERROR"
    default_message = "An upstream service is unavailable."


# ---------------------------------------------------------------------------
# Domain-specific subclasses (add more as domains are built)
# ---------------------------------------------------------------------------


class WalletInsufficientBalanceError(UnprocessableError):
    default_code = "WALLET_INSUFFICIENT_BALANCE"
    default_message = "Insufficient balance for this transaction."


class WalletNotFoundError(NotFoundError):
    default_code = "WALLET_NOT_FOUND"
    default_message = "Wallet not found."


class OrderNotFoundError(NotFoundError):
    default_code = "ORDER_NOT_FOUND"
    default_message = "Order not found."


class OrderStateError(ConflictError):
    default_code = "ORDER_INVALID_STATE"
    default_message = "The order is in an invalid state for this operation."


class KycRequiredError(ForbiddenError):
    default_code = "KYC_REQUIRED"
    default_message = "KYC verification is required to perform this action."
