"""Smoke tests for the drf-spectacular OpenAPI schema endpoints.

Guards that the schema generates without blowing up across every app's URLConf
(a broken view annotation would 500 here) and that the docs UIs are wired.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient


@pytest.mark.django_db
class TestOpenAPISchema:
    def test_schema_generates(self):
        resp = APIClient().get("/api/v1/schema/")
        assert resp.status_code == 200
        # drf-spectacular serves YAML by default.
        body = resp.content.decode()
        assert "openapi:" in body
        assert "Brandable Content Platform API" in body

    def test_schema_json(self):
        resp = APIClient().get("/api/v1/schema/?format=json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["openapi"].startswith("3.")
        assert data["info"]["title"] == "Brandable Content Platform API"
        # A few representative paths should be present.
        paths = data["paths"]
        assert any(p.startswith("/api/v1/content/") for p in paths)

    def test_swagger_ui_loads(self):
        resp = APIClient().get("/api/v1/schema/swagger-ui/")
        assert resp.status_code == 200

    def test_redoc_loads(self):
        resp = APIClient().get("/api/v1/schema/redoc/")
        assert resp.status_code == 200
