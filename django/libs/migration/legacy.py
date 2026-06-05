"""Legacy database access for migration commands.

The legacy system (``django-auth-core``) runs on a *separate* PostgreSQL
database. We connect to it directly with psycopg rather than wiring it into
Django's ``DATABASES``, so migration reads never accidentally route through the
ORM router and so the connection string can be passed per-invocation.
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row


def connect(dsn: str) -> psycopg.Connection[dict[str, Any]]:
    """Open a read-only-ish connection to the legacy DB.

    ``dsn`` accepts either a URL (``postgres://user:pass@host/db``) or a libpq
    keyword string (``host=... dbname=...``). Rows come back as dicts.
    """
    conn = psycopg.connect(dsn, row_factory=dict_row)
    # We only ever read from legacy; make that explicit at the session level.
    conn.read_only = True
    return conn


def check_connection(dsn: str) -> dict[str, Any]:
    """Probe the legacy DB. Never raises — returns a structured result.

    Returns ``{"ok": bool, "server_version": str|None, "error": str|None}``.
    """
    try:
        with connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT version()")
            row = cur.fetchone()
            version = row["version"] if row else None
        return {"ok": True, "server_version": version, "error": None}
    except Exception as exc:
        return {"ok": False, "server_version": None, "error": f"{type(exc).__name__}: {exc}"}


def fetch_dicts(
    conn: psycopg.Connection[dict[str, Any]], sql: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Run ``sql`` and return all rows as a list of dicts."""
    with conn.cursor() as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()
