"""Outbox dispatcher process (events.md §6).

    python manage.py run_dispatcher [--once] [--batch-size 100]

Single-leader via a PG advisory lock: only one instance dispatches at a time; a
standby exits (and would be restarted/retry by its supervisor). `--once` runs a
single batch and exits (handy for cron-style operation, ops, and smoke tests).
"""

from __future__ import annotations

import logging
import time
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand
from django.db import connection

from apps.events.dispatcher import dispatch_pending_batch

logger = logging.getLogger("events.dispatcher")

# Arbitrary constant shared by all dispatcher instances for leader election.
DISPATCHER_ADVISORY_LOCK_ID = 0x0E7E_0001
IDLE_SLEEP_SECONDS = 2.0
BUSY_SLEEP_SECONDS = 0.25


def _acquire_lock() -> bool:
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", [DISPATCHER_ADVISORY_LOCK_ID])
        return bool(cur.fetchone()[0])


class Command(BaseCommand):
    help = "Run the Outbox dispatcher (synchronous handler execution, V1)."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--once", action="store_true", help="Process one batch and exit.")
        parser.add_argument("--batch-size", type=int, default=100)

    def handle(self, *args: Any, **options: Any) -> None:
        batch_size: int = options["batch_size"]

        if options["once"]:
            counts = dispatch_pending_batch(limit=batch_size)
            self.stdout.write(self.style.SUCCESS(f"dispatched once: {counts}"))
            return

        if not _acquire_lock():
            self.stderr.write(
                self.style.WARNING("Another dispatcher holds the advisory lock; exiting.")
            )
            return

        self.stdout.write(
            self.style.SUCCESS("Dispatcher leader acquired; polling. Ctrl-C to stop.")
        )
        try:
            while True:
                counts = dispatch_pending_batch(limit=batch_size)
                worked = counts["processed"] + counts["failed"] + counts["dlq"]
                time.sleep(BUSY_SLEEP_SECONDS if worked else IDLE_SLEEP_SECONDS)
        except KeyboardInterrupt:
            self.stdout.write("Dispatcher stopped.")
