"""Service layer for audit (audit.md §3).

record_audit is the only entry point for writing the audit trail. It MUST be
called inside the caller's business transaction (audit.md §4) — if the audit
insert fails, the business write rolls back with it.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from libs.errors.exceptions import ValidationError

from .models import ACTOR_TYPES, SEVERITIES, AuditLog

# '<domain>.<verb>' allowing sub-segments, e.g. 'commerce.seller_application.approve'.
_ACTION_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def record_audit(
    *,
    action: str,
    actor_type: str,
    actor_id: UUID | str | None,
    target_type: str,
    target_id: UUID | str,
    actor_display: str = "",
    target_display: str = "",
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
    reason: str = "",
    severity: str = "info",
    correlation_id: UUID | str | None = None,
    request_metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Write one immutable AuditLog row. Raises (rolling back the caller's tx) on
    invalid input or insert failure."""
    if not _ACTION_RE.match(action):
        raise ValidationError(
            code="AUDIT_INVALID_ACTION", message=f"action '{action}' must be '<domain>.<verb>'."
        )
    if actor_type not in ACTOR_TYPES:
        raise ValidationError(
            code="AUDIT_INVALID_ACTOR_TYPE", message=f"actor_type must be one of {ACTOR_TYPES}."
        )
    if severity not in SEVERITIES:
        raise ValidationError(
            code="AUDIT_INVALID_SEVERITY", message=f"severity must be one of {SEVERITIES}."
        )

    return AuditLog.objects.create(
        action=action,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_display=actor_display,
        target_type=target_type,
        target_id=target_id,
        target_display=target_display,
        before_state=before_state,
        after_state=after_state,
        reason=reason,
        severity=severity,
        correlation_id=correlation_id,
        request_metadata=request_metadata,
    )


# ---------------------------------------------------------------------------
# Admin read helpers (audit.md §7)
# ---------------------------------------------------------------------------


def _iso(dt: Any) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def serialize_audit(row: AuditLog) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "occurred_at": _iso(row.occurred_at),
        "actor": {
            "type": row.actor_type,
            "id": str(row.actor_id) if row.actor_id else None,
            "display": row.actor_display or None,
        },
        "action": row.action,
        "target": {
            "type": row.target_type,
            "id": str(row.target_id),
            "display": row.target_display or None,
        },
        "before_state": row.before_state,
        "after_state": row.after_state,
        "reason": row.reason or None,
        "severity": row.severity,
        "request_metadata": row.request_metadata,
        "correlation_id": str(row.correlation_id) if row.correlation_id else None,
    }


def audit_queryset(
    *,
    action: str | None = None,
    actor_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    severity: str | None = None,
    correlation_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    qs = AuditLog.objects.all()
    if action:
        qs = qs.filter(action__in=[a.strip() for a in action.split(",")])
    if actor_id:
        qs = qs.filter(actor_id=actor_id)
    if target_type:
        qs = qs.filter(target_type=target_type)
    if target_id:
        qs = qs.filter(target_id=target_id)
    if severity:
        qs = qs.filter(severity__in=[s.strip() for s in severity.split(",")])
    if correlation_id:
        qs = qs.filter(correlation_id=correlation_id)
    if date_from:
        qs = qs.filter(occurred_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(occurred_at__date__lte=date_to)
    return qs


def get_audit(audit_id: str) -> dict[str, Any]:
    from libs.errors.exceptions import NotFoundError

    try:
        return serialize_audit(AuditLog.objects.get(id=audit_id))
    except AuditLog.DoesNotExist:
        raise NotFoundError(code="AUDIT_NOT_FOUND", message="Audit record not found.")
