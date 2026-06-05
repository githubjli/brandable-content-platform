"""Tests for Audit V1: record_audit, immutability (model + DB trigger), admin API."""

from __future__ import annotations

import uuid

import pytest
from django.db import Error as DBError
from django.db import transaction
from rest_framework.test import APIClient

from apps.audit import services
from apps.audit.models import AuditImmutableError, AuditLog
from libs.errors.exceptions import ValidationError


def _admin_client() -> APIClient:
    from libs.jwt_auth.authentication import JWTUser

    client = APIClient()
    client.force_authenticate(
        user=JWTUser({"sub": str(uuid.uuid4()), "scope": ["default", "admin"], "type": "access"})
    )
    return client


def _plain_client() -> APIClient:
    from libs.jwt_auth.authentication import JWTUser

    client = APIClient()
    client.force_authenticate(
        user=JWTUser({"sub": str(uuid.uuid4()), "scope": ["default"], "type": "access"})
    )
    return client


def _record(**overrides):
    defaults = {
        "action": "identity.user.register",
        "actor_type": "user",
        "actor_id": uuid.uuid4(),
        "target_type": "User",
        "target_id": uuid.uuid4(),
        "severity": "info",
    }
    defaults.update(overrides)
    return services.record_audit(**defaults)


@pytest.mark.django_db
class TestRecordAudit:
    def test_writes_row(self):
        row = _record(after_state={"email": "a@b.com"})
        assert AuditLog.objects.count() == 1
        assert row.action == "identity.user.register"
        assert row.after_state == {"email": "a@b.com"}

    def test_invalid_action_rejected(self):
        with pytest.raises(ValidationError) as exc:
            _record(action="bogus")
        assert exc.value.code == "AUDIT_INVALID_ACTION"

    def test_invalid_severity_rejected(self):
        with pytest.raises(ValidationError) as exc:
            _record(severity="loud")
        assert exc.value.code == "AUDIT_INVALID_SEVERITY"

    def test_invalid_actor_type_rejected(self):
        with pytest.raises(ValidationError) as exc:
            _record(actor_type="robot")
        assert exc.value.code == "AUDIT_INVALID_ACTOR_TYPE"


@pytest.mark.django_db
class TestImmutability:
    def test_model_save_forbidden(self):
        row = _record()
        row.reason = "tampered"
        with pytest.raises(AuditImmutableError):
            row.save()

    def test_model_delete_forbidden(self):
        row = _record()
        with pytest.raises(AuditImmutableError):
            row.delete()

    def test_db_trigger_blocks_queryset_update(self):
        row = _record()
        # .update() bypasses the model layer — the DB trigger is the backstop.
        with pytest.raises(DBError), transaction.atomic():
            AuditLog.objects.filter(id=row.id).update(reason="tampered")

    def test_db_trigger_blocks_queryset_delete(self):
        row = _record()
        with pytest.raises(DBError), transaction.atomic():
            AuditLog.objects.filter(id=row.id).delete()


@pytest.mark.django_db
class TestAdminApi:
    def test_requires_admin(self):
        assert _plain_client().get("/api/v1/admin/audit").status_code == 403

    def test_list_and_filter(self):
        _record(action="identity.user.register", severity="info")
        _record(action="economy.wallet.admin_adjust", severity="critical")
        client = _admin_client()

        resp = client.get("/api/v1/admin/audit")
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 2

        resp = client.get("/api/v1/admin/audit?severity=critical")
        actions = [r["action"] for r in resp.json()["results"]]
        assert actions == ["economy.wallet.admin_adjust"]

    def test_detail(self):
        row = _record()
        resp = _admin_client().get(f"/api/v1/admin/audit/{row.id}")
        assert resp.status_code == 200
        assert resp.json()["action"] == "identity.user.register"
        assert resp.json()["actor"]["type"] == "user"
