"""Tests for the DRF exception handler."""

import pytest
from django.test import RequestFactory
from rest_framework import exceptions as drf_exc

from libs.errors.exceptions import NotFoundError, WalletInsufficientBalanceError
from libs.errors.handlers import _handle_validation, exception_handler


@pytest.fixture
def context():
    factory = RequestFactory()
    return {"request": factory.get("/"), "view": None}


class TestAppErrorHandling:
    def test_app_error_returns_envelope(self, context):
        exc = NotFoundError()
        response = exception_handler(exc, context)
        assert response is not None
        assert response.status_code == 404
        assert response.data["error"]["code"] == "NOT_FOUND"

    def test_domain_error_with_detail(self, context):
        exc = WalletInsufficientBalanceError(
            detail={"required": "100", "available": "50", "currency": "MP"}
        )
        response = exception_handler(exc, context)
        assert response.status_code == 422
        assert response.data["error"]["detail"]["currency"] == "MP"


class TestDRFValidationErrors:
    def test_field_errors(self, context):
        exc = drf_exc.ValidationError({"email": ["Enter a valid email address."]})
        response = exception_handler(exc, context)
        assert response is not None
        assert response.status_code == 400
        error = response.data["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert "email" in error["detail"]
        assert error["detail"]["email"] == ["Enter a valid email address."]

    def test_non_field_errors(self, context):
        exc = drf_exc.ValidationError({"non_field_errors": ["Passwords do not match."]})
        response = exception_handler(exc, context)
        error = response.data["error"]
        assert error["message"] == "Passwords do not match."
        assert "non_field_errors" not in error.get("detail", {})

    def test_string_validation_error(self, context):
        exc = drf_exc.ValidationError("Something is wrong.")
        response = exception_handler(exc, context)
        assert response.data["error"]["code"] == "VALIDATION_ERROR"


class TestAuthErrors:
    def test_not_authenticated(self, context):
        exc = drf_exc.NotAuthenticated()
        response = exception_handler(exc, context)
        assert response.status_code == 401
        assert response.data["error"]["code"] == "AUTH_INVALID_TOKEN"

    def test_permission_denied(self, context):
        exc = drf_exc.PermissionDenied()
        response = exception_handler(exc, context)
        assert response.status_code == 403
        assert response.data["error"]["code"] == "AUTH_FORBIDDEN"


class TestHandleValidationHelper:
    def test_multi_field_errors(self):
        data = {
            "username": ["This field is required."],
            "email": ["Enter a valid email address.", "This field must be unique."],
        }
        result = _handle_validation(data)
        error = result["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert len(error["detail"]["email"]) == 2

    def test_list_input(self):
        result = _handle_validation(["Validation failed."])
        assert result["error"]["message"] == "Validation failed."
