"""Model-level tests for identity."""

import pytest
from django.db import IntegrityError

from apps.identity.models import Follow, User


@pytest.mark.django_db
class TestUserModel:
    def test_create_user(self):
        user = User.objects.create(
            email="test@example.com",
            password_hash="hashed",
            display_name="Test User",
        )
        assert user.id is not None
        assert user.email == "test@example.com"
        assert user.is_active is True
        assert user.is_creator is False
        assert user.is_admin is False
        assert user.follower_count == 0
        assert user.following_count == 0

    def test_email_unique(self):
        User.objects.create(
            email="unique@example.com",
            password_hash="h",
            display_name="A",
        )
        with pytest.raises(IntegrityError):
            User.objects.create(
                email="unique@example.com",
                password_hash="h",
                display_name="B",
            )

    def test_email_normalization_in_service(self):
        """Email normalization happens in services, not model layer.
        Verify that a lowercase email is stored as-is."""
        user = User.objects.create(
            email="lower@example.com",
            password_hash="h",
            display_name="D",
        )
        assert user.email == "lower@example.com"

    def test_uuid_primary_key(self):
        import uuid

        user = User.objects.create(
            email="uuid@example.com",
            password_hash="h",
            display_name="U",
        )
        assert isinstance(user.id, uuid.UUID)

    def test_created_at_auto(self):
        user = User.objects.create(
            email="ts@example.com",
            password_hash="h",
            display_name="T",
        )
        assert user.created_at is not None
        assert user.updated_at is not None


@pytest.mark.django_db
class TestFollowModel:
    def _make_user(self, email: str) -> User:
        return User.objects.create(email=email, password_hash="h", display_name="D")

    def test_follow_unique_together(self):
        a = self._make_user("a@example.com")
        b = self._make_user("b@example.com")
        Follow.objects.create(follower=a, target=b)
        with pytest.raises(IntegrityError):
            Follow.objects.create(follower=a, target=b)

    def test_follow_reverse_allowed(self):
        a = self._make_user("ar@example.com")
        b = self._make_user("br@example.com")
        Follow.objects.create(follower=a, target=b)
        # b can follow a — that's allowed
        Follow.objects.create(follower=b, target=a)
        assert Follow.objects.count() == 2
