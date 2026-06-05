"""Shared base for identity legacy-import commands.

Leading-underscore module name => Django's command loader ignores it, so it is
never runnable as ``manage.py _identity_migration``; the real commands subclass
:class:`IdentityMigrationCommand`.

It adds two things on top of :class:`libs.migration.BaseMigrationCommand`:
  * a per-row batch loop that isolates each row in its own savepoint, so one bad
    row is recorded and skipped rather than failing the whole page;
  * an ``audit_event`` override that records the run via the audit app (Audit V1,
    Week 8). Audit is infrastructure (any app may call it). The call stays inside
    try/except because a migration is an ops action and a missing audit backend
    should not abort an import run.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from django.db import transaction

from libs.migration import Counters
from libs.migration.command import BaseMigrationCommand

logger = logging.getLogger("migration")


def normalize_email(value: str | None) -> str:
    """Match Identity's canonical email form: stripped + lowercased."""
    return (value or "").strip().lower()


class IdentityMigrationCommand(BaseMigrationCommand):
    def load_one(self, row: dict[str, Any]) -> str:
        """Upsert a single legacy row. Return "inserted" | "updated" | "skipped"."""
        raise NotImplementedError

    def load_batch(self, rows: list[dict[str, Any]], *, dry_run: bool) -> Counters:
        c = Counters()
        for row in rows:
            c.total += 1
            key = row.get("email") or row.get(self.keyset_field)
            try:
                # Savepoint per row, nested inside the page transaction opened by
                # the base command (which rolls the whole page back on --dry-run).
                with transaction.atomic():
                    status = self.load_one(row)
                if status == "inserted":
                    c.inserted += 1
                elif status == "updated":
                    c.updated += 1
                else:
                    c.skipped += 1
            except Exception as exc:
                c.record_error(str(key), exc)
        return c

    def audit_event(self, action: str, payload: dict[str, Any]) -> None:
        try:
            from apps.audit.services import record_audit

            # Migration operations are recorded at critical severity (see
            # docs/migration/migration-plan.md §4). No single target row, so use a
            # nil UUID + a synthetic target_type.
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
            logger.debug("audit_event: audit write failed; skipping %s", action)
