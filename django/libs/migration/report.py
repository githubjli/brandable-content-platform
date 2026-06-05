"""Counters, run reports, and resume checkpoints for migration commands."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Cap stored error samples so a pathological run can't produce a giant report.
MAX_ERROR_SAMPLES = 50


@dataclass
class Counters:
    """Mutable tally accumulated across batches."""

    total: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    error_samples: list[dict[str, str]] = field(default_factory=list)

    def record_error(self, key: str, exc: Exception) -> None:
        self.errors += 1
        if len(self.error_samples) < MAX_ERROR_SAMPLES:
            self.error_samples.append({"key": str(key), "error": f"{type(exc).__name__}: {exc}"})

    def merge(self, other: Counters) -> None:
        self.total += other.total
        self.inserted += other.inserted
        self.updated += other.updated
        self.skipped += other.skipped
        self.errors += other.errors
        for sample in other.error_samples:
            if len(self.error_samples) < MAX_ERROR_SAMPLES:
                self.error_samples.append(sample)


@dataclass
class ImportReport:
    """A single command run, serialised to JSON when the run finishes."""

    command: str
    dry_run: bool
    started_at: datetime
    counters: Counters = field(default_factory=Counters)
    batches: int = 0
    finished_at: datetime | None = None

    @property
    def duration_seconds(self) -> float:
        end = self.finished_at or datetime.now(tz=UTC)
        return round((end - self.started_at).total_seconds(), 3)

    def to_dict(self) -> dict[str, Any]:
        c = self.counters
        return {
            "command": self.command,
            "dry_run": self.dry_run,
            "started_at": self.started_at.isoformat().replace("+00:00", "Z"),
            "finished_at": self.finished_at.isoformat().replace("+00:00", "Z")
            if self.finished_at
            else None,
            "duration_seconds": self.duration_seconds,
            "batches": self.batches,
            "counts": {
                "total": c.total,
                "inserted": c.inserted,
                "updated": c.updated,
                "skipped": c.skipped,
                "errors": c.errors,
            },
            "error_samples": c.error_samples,
        }


def write_report(report: ImportReport, reports_dir: Path) -> Path:
    """Write ``report`` as JSON under ``reports_dir`` and return the path."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.started_at.strftime("%Y%m%dT%H%M%SZ")
    path = reports_dir / f"{stamp}-{report.command}.json"
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return path


# ---------------------------------------------------------------------------
# Resume checkpoints
# ---------------------------------------------------------------------------
# A checkpoint records the last successfully-processed keyset value so a failed
# or interrupted run can resume after it with --resume.


def _checkpoint_path(reports_dir: Path, command: str) -> Path:
    return reports_dir / f".{command}.checkpoint"


def read_checkpoint(reports_dir: Path, command: str) -> Any | None:
    path = _checkpoint_path(reports_dir, command)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())["after"]
    except (json.JSONDecodeError, KeyError):
        return None


def write_checkpoint(reports_dir: Path, command: str, after: Any) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = _checkpoint_path(reports_dir, command)
    path.write_text(json.dumps({"after": after}))


def clear_checkpoint(reports_dir: Path, command: str) -> None:
    _checkpoint_path(reports_dir, command).unlink(missing_ok=True)
