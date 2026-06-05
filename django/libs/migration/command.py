"""BaseMigrationCommand — shared orchestration for legacy import commands.

Subclasses (in each app's ``management/commands/``) implement three hooks:

  * ``build_sql(since, after, limit) -> (sql, params)`` — one keyset-paginated
    page of legacy rows, ordered ascending by the keyset column, each row a dict
    that includes the keyset value under :attr:`keyset_field`.
  * ``load_batch(rows, dry_run) -> Counters`` — load one page into the new
    models. Must isolate each row in its own savepoint so one bad row does not
    abort the page.
  * (optional) ``audit_event(action, payload)`` — record an AuditLog row. The
    default is a no-op; app-layer subclasses override it (audit is in ``apps``
    and this package may not import ``apps``).

The base handles argument parsing, batching, dry-run rollback, resume
checkpoints, structured logging and the JSON report.
"""

from __future__ import annotations

import logging
import os
from argparse import ArgumentParser
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from .legacy import connect, fetch_dicts
from .report import (
    Counters,
    ImportReport,
    clear_checkpoint,
    read_checkpoint,
    write_checkpoint,
    write_report,
)

logger = logging.getLogger("migration")


class BaseMigrationCommand(BaseCommand):
    # --- subclass contract -------------------------------------------------
    command_name: str = ""  # used for report/checkpoint filenames
    default_batch_size: int = 1000
    keyset_field: str = "legacy_id"  # ascending order/resume column in each row

    def build_sql(
        self, *, since: str | None, after: Any | None, limit: int
    ) -> tuple[str, dict[str, Any]]:
        raise NotImplementedError

    def load_batch(self, rows: list[dict[str, Any]], *, dry_run: bool) -> Counters:
        raise NotImplementedError

    def audit_event(self, action: str, payload: dict[str, Any]) -> None:
        """Override in the app layer to write an AuditLog row. Default: no-op."""

    # --- argument parsing --------------------------------------------------
    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--legacy-db",
            default=os.environ.get("LEGACY_DATABASE_URL"),
            help="Legacy DB connection string (URL or libpq DSN). "
            "Falls back to $LEGACY_DATABASE_URL.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and report without committing any writes.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=self.default_batch_size,
            help=f"Rows per batch (default {self.default_batch_size}).",
        )
        parser.add_argument(
            "--resume",
            action="store_true",
            help="Resume after the last checkpoint instead of from the start.",
        )
        parser.add_argument(
            "--since",
            default=None,
            help="Only import rows changed at/after this ISO timestamp (delta import for cutover).",
        )

    # --- helpers -----------------------------------------------------------
    @property
    def reports_dir(self) -> Path:
        # ops/ lives at the repo root, one level above the Django project dir.
        return Path(settings.BASE_DIR).parent / "ops" / "migration" / "reports"

    # --- orchestration -----------------------------------------------------
    def handle(self, *args: Any, **options: Any) -> None:
        dsn: str | None = options["legacy_db"]
        if not dsn:
            raise CommandError(
                "No legacy DB given. Pass --legacy-db=<conn> or set $LEGACY_DATABASE_URL."
            )

        dry_run: bool = options["dry_run"]
        batch_size: int = options["batch_size"]
        since: str | None = options["since"]

        after: Any | None = None
        if options["resume"]:
            after = read_checkpoint(self.reports_dir, self.command_name)
            self.stdout.write(f"Resuming after keyset={after!r}")

        report = ImportReport(
            command=self.command_name,
            dry_run=dry_run,
            started_at=datetime.now(tz=UTC),
        )
        logger.info(
            "migration.start",
            extra={
                "command": self.command_name,
                "dry_run": dry_run,
                "batch_size": batch_size,
                "since": since,
                "resume_after": after,
            },
        )

        try:
            with connect(dsn) as conn:
                while True:
                    sql, params = self.build_sql(since=since, after=after, limit=batch_size)
                    rows = fetch_dicts(conn, sql, params)
                    if not rows:
                        break

                    with transaction.atomic():
                        counters = self.load_batch(rows, dry_run=dry_run)
                        if dry_run:
                            transaction.set_rollback(True)

                    report.counters.merge(counters)
                    report.batches += 1
                    after = rows[-1][self.keyset_field]

                    if not dry_run:
                        write_checkpoint(self.reports_dir, self.command_name, after)

                    logger.info(
                        "migration.batch",
                        extra={
                            "command": self.command_name,
                            "batch": report.batches,
                            "keyset_after": after,
                            "inserted": counters.inserted,
                            "updated": counters.updated,
                            "skipped": counters.skipped,
                            "errors": counters.errors,
                        },
                    )

                    if len(rows) < batch_size:
                        break
        finally:
            report.finished_at = datetime.now(tz=UTC)
            report_path = write_report(report, self.reports_dir)

        # A clean, complete real run clears its checkpoint so the next run starts fresh.
        if not dry_run and report.counters.errors == 0:
            clear_checkpoint(self.reports_dir, self.command_name)

        self.audit_event(
            action=f"migration.{self.command_name}",
            payload=report.to_dict(),
        )

        c = report.counters
        logger.info("migration.done", extra={"command": self.command_name, **report.to_dict()})
        self.stdout.write(
            self.style.SUCCESS(
                f"{self.command_name}: total={c.total} inserted={c.inserted} "
                f"updated={c.updated} skipped={c.skipped} errors={c.errors} "
                f"{'(DRY-RUN, nothing committed) ' if dry_run else ''}"
                f"-> {report_path}"
            )
        )
        if c.errors:
            self.stderr.write(
                self.style.WARNING(f"{c.errors} row error(s); see the report for samples.")
            )
