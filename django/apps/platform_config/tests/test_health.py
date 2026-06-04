"""Tests for the health endpoint."""

import pytest


@pytest.mark.django_db
def test_health_returns_ok(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "trace_id" in data


@pytest.mark.django_db
def test_health_does_not_require_auth(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200


@pytest.mark.django_db
def test_health_response_has_request_id_header(client):
    response = client.get("/api/v1/health")
    assert "X-Request-Id" in response
