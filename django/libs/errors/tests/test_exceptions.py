"""Tests for the exception hierarchy."""

import pytest

from libs.errors.exceptions import (
    AppError,
    AuthError,
    ConflictError,
    ForbiddenError,
    InternalError,
    NotFoundError,
    RateLimitError,
    UpstreamError,
    ValidationError,
    WalletInsufficientBalanceError,
)


class TestAppError:
    def test_defaults(self):
        err = AppError()
        assert err.code == "INTERNAL_ERROR"
        assert err.message == "An unexpected error occurred."
        assert err.http_status == 500
        assert err.detail is None

    def test_custom_message_and_code(self):
        err = AppError(message="custom msg", code="CUSTOM_CODE")
        assert err.message == "custom msg"
        assert err.code == "CUSTOM_CODE"

    def test_to_dict_without_detail(self):
        err = AppError(message="oops", code="MY_CODE")
        d = err.to_dict()
        assert d == {"error": {"code": "MY_CODE", "message": "oops"}}
        assert "detail" not in d["error"]

    def test_to_dict_with_detail(self):
        err = AppError(message="oops", code="MY_CODE", detail={"field": "value"})
        d = err.to_dict()
        assert d["error"]["detail"] == {"field": "value"}

    def test_http_status_override(self):
        err = AppError(http_status=418)
        assert err.http_status == 418


class TestDomainErrors:
    @pytest.mark.parametrize(
        ("cls", "expected_status", "expected_code_prefix"),
        [
            (ValidationError, 400, "VALIDATION"),
            (AuthError, 401, "AUTH"),
            (ForbiddenError, 403, "AUTH"),
            (NotFoundError, 404, "NOT_FOUND"),
            (ConflictError, 409, "CONFLICT"),
            (RateLimitError, 429, "RATE_LIMIT"),
            (InternalError, 500, "INTERNAL"),
            (UpstreamError, 502, "UPSTREAM"),
        ],
    )
    def test_http_status(self, cls, expected_status, expected_code_prefix):
        err = cls()
        assert err.http_status == expected_status
        assert err.code.startswith(expected_code_prefix)

    def test_wallet_insufficient_balance(self):
        err = WalletInsufficientBalanceError(
            detail={"required": "100.00", "available": "37.50", "currency": "MP"}
        )
        assert err.http_status == 422
        assert err.code == "WALLET_INSUFFICIENT_BALANCE"
        d = err.to_dict()
        assert d["error"]["detail"]["currency"] == "MP"
