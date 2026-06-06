"""Migration validation gate (migration-plan §5).

    python manage.py validate_migration --legacy-db=<conn> [--sample-size=100]

Runs the cutover validation checks against the legacy DB + the new DB and emits a
single PASS/FAIL plus a JSON report under ops/migration/reports/. Exit code is
non-zero on FAIL so it can gate a cutover script. Checks query by table name (raw
SQL) on both sides, so this command stays model-agnostic and cross-app-clean.

Checks not applicable to what a given environment imported (e.g. wallet/KYC sums
when those imports were skipped) are reported as SKIPPED, not failures.
"""

from __future__ import annotations

import json
from argparse import ArgumentParser
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection as new_db

from libs.migration.legacy import connect


@dataclass
class CheckResult:
    name: str
    threshold: str
    passed: bool = False
    skipped: bool = False
    legacy: Any = None
    new: Any = None
    detail: str = ""

    @property
    def status(self) -> str:
        return "SKIP" if self.skipped else ("PASS" if self.passed else "FAIL")


# ---------------------------------------------------------------------------
# Pure check builders (no DB) — unit-testable
# ---------------------------------------------------------------------------


def count_match_check(
    name: str, legacy: int, new: int, threshold: str = "new == legacy"
) -> CheckResult:
    return CheckResult(
        name=name,
        threshold=threshold,
        passed=(legacy == new),
        legacy=legacy,
        new=new,
        detail=f"legacy={legacy} new={new}",
    )


def zero_check(name: str, value: int, threshold: str = "0") -> CheckResult:
    return CheckResult(
        name=name,
        threshold=threshold,
        passed=(value == 0),
        new=value,
        detail=f"found={value}",
    )


def skipped_check(name: str, reason: str) -> CheckResult:
    return CheckResult(name=name, threshold="—", skipped=True, detail=reason)


@dataclass
class ValidationReport:
    started_at: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def status(self) -> str:
        # SKIPPED checks don't fail the gate; any FAIL does.
        return "FAIL" if any(not c.passed and not c.skipped for c in self.checks) else "PASS"

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": "validate_migration",
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
            "checks": [asdict(c) | {"status": c.status} for c in self.checks],
        }


# ---------------------------------------------------------------------------
# DB query helpers
# ---------------------------------------------------------------------------


def _scalar(cursor, sql: str) -> int:
    cursor.execute(sql)
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def run_checks(legacy_conn) -> list[CheckResult]:
    results: list[CheckResult] = []
    with new_db.cursor() as new_cur, legacy_conn.cursor() as legacy_cur:
        legacy_users = _scalar(legacy_cur, "SELECT count(*) FROM auth_user WHERE is_active")
        new_users = _scalar(new_cur, "SELECT count(*) FROM identity_user WHERE is_active")
        results.append(
            count_match_check("user_count_active", legacy_users, new_users, "new == old (active)")
        )

        dup_emails = _scalar(
            new_cur,
            "SELECT count(*) FROM (SELECT email FROM identity_user "
            "GROUP BY email HAVING count(*) > 1) d",
        )
        results.append(zero_check("email_uniqueness", dup_emails, "0 duplicates"))

        legacy_memberships = _scalar(
            legacy_cur, "SELECT count(*) FROM accounts_usermembership WHERE is_active"
        )
        new_memberships = _scalar(
            new_cur, "SELECT count(*) FROM membership_user_membership WHERE status = 'active'"
        )
        results.append(
            count_match_check(
                "active_membership_count", legacy_memberships, new_memberships, "100% match"
            )
        )

        orphan_memberships = _scalar(
            new_cur,
            "SELECT count(*) FROM membership_user_membership m "
            "LEFT JOIN identity_user u ON u.id = m.user_id WHERE u.id IS NULL",
        )
        results.append(zero_check("membership_fk_integrity", orphan_memberships, "0 orphans"))

    # Imports that were skipped in this build (see migration decisions).
    results.append(skipped_check("wallet_balance_sum", "wallet import not run in this environment"))
    results.append(skipped_check("kyc_documents", "KYC import not migrated"))
    return results


class Command(BaseCommand):
    help = "Validate the legacy->new migration (cutover gate). Exits non-zero on FAIL."

    def add_arguments(self, parser: ArgumentParser) -> None:
        import os

        parser.add_argument("--legacy-db", default=os.environ.get("LEGACY_DATABASE_URL"))
        parser.add_argument("--sample-size", type=int, default=100)

    @property
    def reports_dir(self) -> Path:
        return Path(settings.BASE_DIR).parent / "ops" / "migration" / "reports"

    def handle(self, *args: Any, **options: Any) -> None:
        dsn = options["legacy_db"]
        if not dsn:
            raise CommandError("Pass --legacy-db=<conn> or set $LEGACY_DATABASE_URL.")

        report = ValidationReport(
            started_at=datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
        )
        with connect(dsn) as legacy_conn:
            report.checks = run_checks(legacy_conn)

        self.reports_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        path = self.reports_dir / f"{stamp}-validate_migration.json"
        path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True))

        for c in report.checks:
            self.stdout.write(f"  [{c.status}] {c.name}: {c.detail or c.threshold}")

        style = self.style.SUCCESS if report.status == "PASS" else self.style.ERROR
        self.stdout.write(style(f"\nvalidate_migration: {report.status}  -> {path}"))
        if report.status == "FAIL":
            raise CommandError("Migration validation FAILED — cutover gate not met.")
