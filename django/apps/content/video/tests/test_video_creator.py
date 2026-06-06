"""Tests for content.video creator management (content-video.md §3)."""

from __future__ import annotations

import uuid

import pytest
from rest_framework.test import APIClient

from apps.content.video.models import Video, VideoCategory
from apps.identity.models import User


def _user(*, creator: bool = True) -> User:
    return User.objects.create(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        display_name="Creator",
        is_creator=creator,
    )


def _client_for(user: User) -> APIClient:
    from libs.jwt_auth.authentication import JWTUser

    client = APIClient()
    client.force_authenticate(
        user=JWTUser({"sub": str(user.id), "type": "access", "jti": str(uuid.uuid4())})
    )
    return client


def _create(client: APIClient, **over) -> dict:
    payload = {"title": "My Clip", "duration_seconds": 90, "visibility": "public"}
    payload.update(over)
    return client.post("/api/v1/content/video/me", payload, format="json").json()


@pytest.mark.django_db
class TestCreatorVideo:
    def test_requires_auth(self):
        assert APIClient().get("/api/v1/content/video/me").status_code == 401

    def test_non_creator_cannot_create(self):
        client = _client_for(_user(creator=False))
        resp = client.post("/api/v1/content/video/me", {"title": "X"}, format="json")
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "NOT_CREATOR"

    def test_create_list_get(self):
        creator = _user()
        client = _client_for(creator)
        cat = VideoCategory.objects.create(name="Music", slug="music")

        created = client.post(
            "/api/v1/content/video/me",
            {
                "title": "My Clip",
                "description": "hi",
                "duration_seconds": 120,
                "category_id": str(cat.id),
                "visibility": "unlisted",
                "access_type": "members_only",
            },
            format="json",
        )
        assert created.status_code == 201
        body = created.json()
        vid = body["id"]
        assert body["visibility"] == "unlisted"
        assert body["access_type"] == "members_only"
        assert body["category"]["slug"] == "music"
        assert body["is_active"] is True

        listed = client.get("/api/v1/content/video/me")
        assert len(listed.json()["results"]) == 1

        fetched = client.get(f"/api/v1/content/video/me/{vid}")
        assert fetched.status_code == 200
        assert fetched.json()["title"] == "My Clip"

    def test_me_list_includes_private_but_public_catalog_does_not(self):
        creator = _user()
        client = _client_for(creator)
        _create(client, title="Pub", visibility="public")
        _create(client, title="Priv", visibility="private")

        assert len(client.get("/api/v1/content/video/me").json()["results"]) == 2
        public = APIClient().get("/api/v1/content/video/public")
        assert {v["title"] for v in public.json()["results"]} == {"Pub"}

    def test_update_metadata(self):
        creator = _user()
        client = _client_for(creator)
        vid = _create(client)["id"]
        resp = client.patch(
            f"/api/v1/content/video/me/{vid}",
            {"title": "Renamed", "visibility": "private"},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Renamed"
        assert resp.json()["visibility"] == "private"

    def test_soft_delete_hides_from_public(self):
        creator = _user()
        client = _client_for(creator)
        vid = _create(client, visibility="public")["id"]
        # visible in public catalog
        assert len(APIClient().get("/api/v1/content/video/public").json()["results"]) == 1

        assert client.delete(f"/api/v1/content/video/me/{vid}").status_code == 204
        Video.objects.get(id=vid).refresh_from_db()
        assert Video.objects.get(id=vid).is_active is False
        # gone from public catalog + public detail 404
        assert APIClient().get("/api/v1/content/video/public").json()["results"] == []
        assert APIClient().get(f"/api/v1/content/video/public/{vid}").status_code == 404

    def test_ownership_scoping(self):
        owner = _user()
        intruder = _user()
        vid = _create(_client_for(owner))["id"]
        # Another creator cannot see or mutate it.
        assert _client_for(intruder).get(f"/api/v1/content/video/me/{vid}").status_code == 404
        assert (
            _client_for(intruder)
            .patch(f"/api/v1/content/video/me/{vid}", {"title": "hax"}, format="json")
            .status_code
            == 404
        )

    def test_regenerate_thumbnail_acknowledges(self):
        creator = _user()
        client = _client_for(creator)
        vid = _create(client)["id"]
        resp = client.post(
            f"/api/v1/content/video/me/{vid}/regenerate-thumbnail",
            {"time_offset_seconds": 5.0},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == vid
