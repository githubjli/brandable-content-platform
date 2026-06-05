"""Connectivity check against the legacy database.

python manage.py legacy_db_check --legacy-db=<conn-string>
"""

from __future__ import annotations

import os
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from libs.migration import check_connection


class Command(BaseCommand):
    help = "Check connectivity to the legacy (django-auth-core) database."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--legacy-db",
            default=os.environ.get("LEGACY_DATABASE_URL"),
            help="Legacy DB connection string (URL or libpq DSN). "
            "Falls back to $LEGACY_DATABASE_URL.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        dsn: str | None = options["legacy_db"]
        if not dsn:
            raise CommandError(
                "No legacy DB given. Pass --legacy-db=<conn> or set $LEGACY_DATABASE_URL."
            )

        result = check_connection(dsn)
        if result["ok"]:
            self.stdout.write(self.style.SUCCESS(f"OK — {result['server_version']}"))
        else:
            raise CommandError(f"Legacy DB unreachable: {result['error']}")
