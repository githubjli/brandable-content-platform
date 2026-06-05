"""Tests for the legacy-import management commands.

These exercise the *load* logic (legacy row dict -> Identity models) directly,
without a real legacy DB: the SQL/read layer is the only schema-dependent part
and is injected as plain dicts here. Idempotency, normalization, creator-profile
handling and the JSON report writer are all covered.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from apps.identity.management.commands.import_legacy_users import Command as UsersCommand
from apps.identity.models import CreatorProfile, User, UserPreferences
from libs.migration.report import Counters, ImportReport, write_report

# ---------------------------------------------------------------------------
# Sample legacy rows (shape = aliased columns produced by the import SQL)
# ---------------------------------------------------------------------------

_LEGACY_HASH = "pbkdf2_sha256$1000000$jLHISOsalt$xTN21Yhashvalueplaceholder="


def _user_row(legacy_id: int = 1, **overrides) -> dict:
    row = {
        "legacy_id": legacy_id,
        "email": "  Beauty@IPB.com ",
        "password_hash": _LEGACY_HASH,
        "is_active": True,
        "is_admin": False,
        "created_at": datetime(2020, 1, 1, tzinfo=UTC),
        "display_name": "Beauty",
        "first_name": "Bea",
        "last_name": "Uty",
        "avatar_url": "https://cdn.example.com/a.png",
        "bio": "hi",
        "is_creator": False,
        "is_seller": False,
        "bio_extended": "",
        "categories": [],
        "social_links": {},
        "is_verified": False,
        "verified_at": None,
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# import_legacy_users
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestImportUsers:
    def test_inserts_and_normalizes_email(self):
        status = UsersCommand().load_one(_user_row())
        assert status == "inserted"

        user = User.objects.get()
        assert user.email == "beauty@ipb.com"  # stripped + lowercased
        assert user.password_hash == _LEGACY_HASH  # preserved verbatim
        assert UserPreferences.objects.filter(user=user).exists()

    def test_preserves_legacy_join_date(self):
        UsersCommand().load_one(_user_row())
        user = User.objects.get()
        assert user.created_at == datetime(2020, 1, 1, tzinfo=UTC)

    def test_is_idempotent(self):
        cmd = UsersCommand()
        assert cmd.load_one(_user_row()) == "inserted"
        # Re-run with a changed field: updates in place, no duplicate.
        assert cmd.load_one(_user_row(display_name="Renamed")) == "updated"
        assert User.objects.count() == 1
        assert User.objects.get().display_name == "Renamed"

    def test_creator_gets_creator_profile(self):
        UsersCommand().load_one(
            _user_row(
                is_creator=True,
                bio_extended="Long bio",
                categories=["music"],
                social_links={"x": "https://x.com/a"},
                is_verified=True,
            )
        )
        cp = CreatorProfile.objects.get()
        assert cp.bio_extended == "Long bio"
        assert cp.categories == ["music"]
        assert cp.social_links == {"x": "https://x.com/a"}
        assert cp.is_verified is True

    def test_non_creator_has_no_creator_profile(self):
        UsersCommand().load_one(_user_row(is_creator=False))
        assert not CreatorProfile.objects.exists()

    def test_missing_email_raises(self):
        with pytest.raises(ValueError, match="no email"):
            UsersCommand().load_one(_user_row(email="  "))

    def test_empty_password_hash_raises(self):
        with pytest.raises(ValueError, match="empty password hash"):
            UsersCommand().load_one(_user_row(password_hash=""))

    def test_load_batch_counts_and_isolates_errors(self):
        rows = [
            _user_row(1, email="a@example.com"),
            _user_row(2, email=""),  # bad → recorded as error, not fatal
            _user_row(3, email="c@example.com"),
        ]
        counters = UsersCommand().load_batch(rows, dry_run=False)
        assert counters.total == 3
        assert counters.inserted == 2
        assert counters.errors == 1
        assert User.objects.count() == 2
        assert len(counters.error_samples) == 1
        assert "error" in counters.error_samples[0]


# ---------------------------------------------------------------------------
# report writer
# ---------------------------------------------------------------------------


class TestReport:
    def test_writes_json_with_counts(self, tmp_path: Path):
        report = ImportReport(
            command="import_legacy_users",
            dry_run=True,
            started_at=datetime(2026, 6, 4, 12, 0, 0, tzinfo=UTC),
            counters=Counters(total=10, inserted=7, updated=2, errors=1),
        )
        report.finished_at = datetime(2026, 6, 4, 12, 0, 5, tzinfo=UTC)

        path = write_report(report, tmp_path)
        assert path.exists()
        assert path.name == "20260604T120000Z-import_legacy_users.json"

        import json

        data = json.loads(path.read_text())
        assert data["dry_run"] is True
        assert data["counts"] == {
            "total": 10,
            "inserted": 7,
            "updated": 2,
            "skipped": 0,
            "errors": 1,
        }
        assert data["duration_seconds"] == 5.0
