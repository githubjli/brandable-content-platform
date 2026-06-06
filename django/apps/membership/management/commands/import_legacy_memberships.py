"""Import legacy ACTIVE memberships into Membership.

    python manage.py import_legacy_memberships \
        --legacy-db=<conn> [--dry-run] [--batch-size=500] [--resume] [--since=<iso>]

Imports only active legacy memberships (migration-plan §3 — past/expired are NOT
migrated). Idempotent on the legacy membership id (UserMembership.source_ref).
Reuses the shared libs.migration runner (batching / dry-run / resume / report).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from apps.membership.models import MembershipPlan, UserMembership
from django.apps import apps as django_apps
from libs.migration.command import BaseMigrationCommand

# ---------------------------------------------------------------------------
# ASSUMED LEGACY SCHEMA  ⚠️  PENDING CONFIRMATION
# ---------------------------------------------------------------------------
# Follows docs/migration/migration-plan.md §3: accounts_usermembership (active
# only), joined to auth_user for the email (our natural key into the
# already-imported users). Adjust ONLY this SELECT when the real schema is known.
_MEMBERSHIPS_SQL = """
    SELECT
        m.id          AS legacy_id,
        u.email       AS email,
        m.plan_code   AS plan_code,
        m.started_at  AS started_at,
        m.expires_at  AS expires_at
    FROM accounts_usermembership m
    JOIN auth_user u ON u.id = m.user_id
    WHERE m.is_active = true
      AND (%(since)s::timestamptz IS NULL OR m.started_at >= %(since)s::timestamptz)
      AND (%(after)s::bigint IS NULL OR m.id > %(after)s::bigint)
    ORDER BY m.id ASC
    LIMIT %(limit)s
"""


def _normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


class Command(BaseMigrationCommand):
    help = "Import active legacy memberships into Membership."
    command_name = "import_legacy_memberships"
    default_batch_size = 500

    def build_sql(
        self, *, since: str | None, after: Any | None, limit: int
    ) -> tuple[str, dict[str, Any]]:
        return _MEMBERSHIPS_SQL, {"since": since, "after": after, "limit": limit}

    def load_one(self, row: dict[str, Any]) -> str:
        email = _normalize_email(row.get("email"))
        if not email:
            raise ValueError("legacy membership row has no user email")

        # Resolve the already-imported user by email. apps.get_model avoids a
        # static cross-app model import (kept clean for import-linter); this is a
        # one-off ops command, not runtime business logic.
        user_model = django_apps.get_model("identity", "User")
        user = user_model.objects.filter(email=email).first()
        if user is None:
            raise ValueError(f"no imported user for {email}; run import_legacy_users first")

        plan_code = (row.get("plan_code") or "LEGACY").strip() or "LEGACY"
        plan, _ = MembershipPlan.objects.get_or_create(
            code=plan_code, defaults={"name": plan_code.replace("_", " ").title()}
        )

        _membership, created = UserMembership.objects.update_or_create(
            source_ref=str(row["legacy_id"]),
            defaults={
                "user_id": user.id,
                "plan": plan,
                "status": UserMembership.ACTIVE,
                "starts_at": row.get("started_at") or datetime.now(tz=UTC),
                "ends_at": row.get("expires_at"),
                "source": "migration",
            },
        )
        return "inserted" if created else "updated"

    def audit_event(self, action: str, payload: dict[str, Any]) -> None:
        try:
            from apps.audit.services import record_audit

            record_audit(
                action=action,
                actor_type="system",
                actor_id=None,
                target_type="MigrationRun",
                target_id=uuid.UUID(int=0),
                after_state=payload,
                severity="critical",
            )
        except Exception:
            self.stderr.write(f"audit_event: skipped {action}")
