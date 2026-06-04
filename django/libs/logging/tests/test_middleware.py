"""Tests for RequestLoggingMiddleware."""

import pytest
from django.test import RequestFactory

from libs.logging.middleware import REQUEST_ID_HEADER, RequestLoggingMiddleware


def _make_middleware(response_factory=None):
    from django.http import HttpResponse

    def get_response(request):
        return response_factory(request) if response_factory else HttpResponse("ok")

    return RequestLoggingMiddleware(get_response)


@pytest.mark.django_db
class TestRequestLoggingMiddleware:
    def test_adds_request_id_to_response(self):
        middleware = _make_middleware()
        factory = RequestFactory()
        request = factory.get("/api/v1/health")
        response = middleware(request)
        assert REQUEST_ID_HEADER in response

    def test_preserves_incoming_request_id(self):
        middleware = _make_middleware()
        factory = RequestFactory()
        request = factory.get("/", HTTP_X_REQUEST_ID="my-custom-id")
        response = middleware(request)
        assert response[REQUEST_ID_HEADER] == "my-custom-id"

    def test_attaches_request_id_to_request(self):
        middleware = _make_middleware()
        factory = RequestFactory()
        request = factory.get("/")
        middleware(request)
        assert hasattr(request, "request_id")
        assert request.request_id
