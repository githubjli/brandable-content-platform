"""Reusable building blocks for legacy data-migration management commands.

App-agnostic on purpose: this package must never import from ``apps`` (enforced
by import-linter). A migration command lives in its app's
``management/commands/`` directory and subclasses :class:`BaseMigrationCommand`,
supplying the legacy SQL and the per-row load logic for its own models.

Shared concerns handled here:
  - legacy DB connection + connectivity check (psycopg, separate from Django's
    own ``DATABASES``)
  - keyset-paginated batched reads (resumable)
  - dry-run (validate + rollback, write nothing)
  - structured per-batch logging
  - JSON run reports under ops/migration/reports/
  - resume checkpoints
"""

from .legacy import check_connection, connect, fetch_dicts
from .report import Counters, ImportReport, write_report

__all__ = [
    "Counters",
    "ImportReport",
    "check_connection",
    "connect",
    "fetch_dicts",
    "write_report",
]
