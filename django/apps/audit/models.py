"""Models for audit (audit.md §2).

AuditLog is the immutable compliance record: append-only at the model layer
(save() forbids updates, delete() forbids deletion) and at the DB layer (a
trigger added in the migration). Writes go only through services.record_audit.
"""

from __future__ import annotations

from django.db.models import (
    CharField,
    DateTimeField,
    Index,
    JSONField,
    TextField,
    UUIDField,
)
from django.utils import timezone

from libs.errors.base_model import AbstractBaseModel
from libs.errors.exceptions import AppError

ACTOR_TYPES = ("user", "admin", "system", "service_account")
SEVERITIES = ("info", "notable", "sensitive", "critical")


class AuditImmutableError(AppError):
    """Raised on any attempt to update or delete an AuditLog row (audit.md §2)."""

    default_code = "AUDIT_LOG_IMMUTABLE"
    default_message = "audit_log is append-only; UPDATE/DELETE forbidden."


class AuditLog(AbstractBaseModel):
    """One immutable record of a sensitive action: who did what, when, to whom."""

    occurred_at = DateTimeField(default=timezone.now, db_index=True)
    actor_type = CharField(max_length=20)  # user | admin | system | service_account
    actor_id = UUIDField(null=True, blank=True)  # null for 'system'
    actor_display = CharField(max_length=255, blank=True)
    action = CharField(max_length=100)  # '<domain>.<verb>'
    target_type = CharField(max_length=100)  # model name
    target_id = UUIDField()
    target_display = CharField(max_length=255, blank=True)
    before_state = JSONField(null=True, blank=True)
    after_state = JSONField(null=True, blank=True)
    reason = TextField(blank=True)
    request_metadata = JSONField(null=True, blank=True)  # ip, user_agent, request_id, trace_id
    severity = CharField(max_length=20, default="info")
    correlation_id = UUIDField(null=True, blank=True)

    class Meta:
        db_table = "audit_log"
        ordering = ["-occurred_at"]
        indexes = [
            Index(fields=["actor_id", "occurred_at"], name="idx_audit_actor"),
            Index(fields=["target_type", "target_id", "occurred_at"], name="idx_audit_target"),
            Index(fields=["action", "occurred_at"], name="idx_audit_action"),
            Index(fields=["correlation_id"], name="idx_audit_correlation"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AuditImmutableError()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditImmutableError()

    def __str__(self) -> str:
        return f"AuditLog({self.action}, target={self.target_type}:{self.target_id})"
