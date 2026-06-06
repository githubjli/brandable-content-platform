"""Tests for Membership V1: entitlement boundary, grant, and active-membership import."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.hashers import make_password

from apps.identity.models import User
from apps.membership import services
from apps.membership.management.commands.import_legacy_memberships import Command as ImportCommand
from apps.membership.models import MembershipPlan, UserMembership


def _uid() -> str:
    return str(uuid.uuid4())


def _plan(code: str = "PRO_MONTHLY", days: int = 30) -> MembershipPlan:
    return MembershipPlan.objects.create(code=code, name="Pro Monthly", duration_days=days)


@pytest.mark.django_db
class TestEntitlement:
    def test_active_membership_returned(self):
        uid = _uid()
        plan = _plan()
        UserMembership.objects.create(
            user_id=uid,
            plan=plan,
            status="active",
            ends_at=datetime.now(tz=UTC) + timedelta(days=10),
        )
        m = services.get_active_membership(user_id=uid)
        assert m is not None
        assert services.has_active_membership(user_id=uid) is True
        data = services.serialize_membership(m)
        assert data["status"] == "active"
        assert data["is_expired"] is False
        assert data["plan"]["code"] == "PRO_MONTHLY"

    def test_expired_row_not_active(self):
        uid = _uid()
        UserMembership.objects.create(
            user_id=uid,
            plan=_plan(),
            status="active",
            ends_at=datetime.now(tz=UTC) - timedelta(days=1),  # past
        )
        assert services.get_active_membership(user_id=uid) is None
        assert services.serialize_membership(None) == {"active_membership": None}

    def test_cancelled_not_active(self):
        uid = _uid()
        UserMembership.objects.create(user_id=uid, plan=_plan(), status="cancelled")
        assert services.get_active_membership(user_id=uid) is None


@pytest.mark.django_db
class TestGrant:
    def test_grant_creates_active_and_expires_previous(self):
        uid = _uid()
        _plan()
        with patch("apps.membership.services._emit"):
            services.grant_membership(user_id=uid, plan_code="PRO_MONTHLY", idempotency_key="g1")
            services.grant_membership(user_id=uid, plan_code="PRO_MONTHLY", idempotency_key="g2")
        # Only one active; the first was expired.
        assert UserMembership.objects.filter(user_id=uid, status="active").count() == 1
        assert UserMembership.objects.filter(user_id=uid, status="expired").count() == 1


@pytest.mark.django_db
class TestImport:
    def _user(self, email: str) -> User:
        return User.objects.create(email=email, password_hash=make_password("x"), display_name="U")

    def _row(self, legacy_id: int = 1, **over) -> dict:
        row = {
            "legacy_id": legacy_id,
            "email": "member@ipb.com",
            "plan_code": "PRO_MONTHLY",
            "started_at": datetime(2026, 1, 1, tzinfo=UTC),
            "expires_at": datetime(2026, 12, 1, tzinfo=UTC),
        }
        row.update(over)
        return row

    def test_imports_and_links_user_and_plan(self):
        self._user("member@ipb.com")
        status = ImportCommand().load_one(self._row())
        assert status == "inserted"
        m = UserMembership.objects.get()
        assert m.status == "active"
        assert m.source == "migration"
        assert m.source_ref == "1"
        assert MembershipPlan.objects.filter(code="PRO_MONTHLY").exists()

    def test_orphan_membership_raises(self):
        with pytest.raises(ValueError, match="run import_legacy_users first"):
            ImportCommand().load_one(self._row())

    def test_idempotent_on_legacy_id(self):
        self._user("member@ipb.com")
        cmd = ImportCommand()
        assert cmd.load_one(self._row()) == "inserted"
        assert cmd.load_one(self._row(plan_code="PRO_YEARLY")) == "updated"
        assert UserMembership.objects.count() == 1
