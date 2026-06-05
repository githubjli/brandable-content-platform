"""Import legacy users into Identity.

    python manage.py import_legacy_users \
        --legacy-db=<conn> [--dry-run] [--batch-size=1000] [--resume] [--since=<iso>]

Imports each legacy user into ``identity_user`` (idempotent on normalized email),
ensures a default ``identity_user_preferences`` row, and — for creators — upserts
``identity_creator_profile``. KYC is handled separately by ``import_legacy_kyc``.

Properties (see docs/migration/migration-plan.md §4): idempotent (UPSERT on email),
resumable (--resume), dry-run, batched, logged, audited.
"""

from __future__ import annotations

from typing import Any

from apps.identity.models import CreatorProfile, User, UserPreferences

from ._identity_migration import IdentityMigrationCommand, normalize_email

# ---------------------------------------------------------------------------
# ASSUMED LEGACY SCHEMA  ⚠️  PENDING CONFIRMATION
# ---------------------------------------------------------------------------
# The legacy DB is standard Django (django-auth-core); the password screenshot
# confirms pbkdf2_sha256 hashes in `auth_user.password`. The table/column names
# below follow docs/migration/migration-plan.md §3 (auth_user + accounts_profile).
# When the real schema is confirmed, adjust ONLY this SELECT — the load logic
# downstream operates on the aliased column names, not the legacy ones.
_USERS_SQL = """
    SELECT
        u.id            AS legacy_id,
        u.email         AS email,
        u.password      AS password_hash,
        u.is_active     AS is_active,
        u.is_staff      AS is_admin,
        u.date_joined   AS created_at,
        p.display_name  AS display_name,
        p.first_name    AS first_name,
        p.last_name     AS last_name,
        p.avatar_url    AS avatar_url,
        p.bio           AS bio,
        p.is_creator    AS is_creator,
        p.is_seller     AS is_seller,
        p.bio_extended  AS bio_extended,
        p.categories    AS categories,
        p.social_links  AS social_links,
        p.is_verified   AS is_verified,
        p.verified_at   AS verified_at
    FROM auth_user u
    LEFT JOIN accounts_profile p ON p.user_id = u.id
    WHERE (%(since)s::timestamptz IS NULL OR u.date_joined >= %(since)s::timestamptz)
      AND (%(after)s::bigint IS NULL OR u.id > %(after)s::bigint)
    ORDER BY u.id ASC
    LIMIT %(limit)s
"""


class Command(IdentityMigrationCommand):
    help = "Import legacy users (and creator profiles) into Identity."
    command_name = "import_legacy_users"
    default_batch_size = 1000

    def build_sql(
        self, *, since: str | None, after: Any | None, limit: int
    ) -> tuple[str, dict[str, Any]]:
        return _USERS_SQL, {"since": since, "after": after, "limit": limit}

    def load_one(self, row: dict[str, Any]) -> str:
        email = normalize_email(row.get("email"))
        if not email:
            raise ValueError("legacy user has no email")

        password_hash = row.get("password_hash") or ""
        if not password_hash:
            # An unusable login is worse than a visible error during dry-run.
            raise ValueError(f"legacy user {email} has empty password hash")

        user, created = User.objects.update_or_create(
            email=email,
            defaults={
                "password_hash": password_hash,
                "display_name": row.get("display_name") or "",
                "first_name": row.get("first_name") or "",
                "last_name": row.get("last_name") or "",
                "avatar_url": row.get("avatar_url"),
                "bio": row.get("bio") or "",
                "is_active": bool(row.get("is_active", True)),
                "is_creator": bool(row.get("is_creator", False)),
                "is_seller": bool(row.get("is_seller", False)),
                "is_admin": bool(row.get("is_admin", False)),
            },
        )

        # Preserve the original join date. created_at is auto_now_add, so it
        # ignores values passed to create(); set it explicitly via UPDATE.
        legacy_created = row.get("created_at")
        if legacy_created is not None:
            User.objects.filter(pk=user.pk).update(created_at=legacy_created)

        UserPreferences.objects.get_or_create(user=user)

        if user.is_creator:
            CreatorProfile.objects.update_or_create(
                user=user,
                defaults={
                    "bio_extended": row.get("bio_extended") or "",
                    "categories": row.get("categories") or [],
                    "social_links": row.get("social_links") or {},
                    "is_verified": bool(row.get("is_verified", False)),
                    "verified_at": row.get("verified_at"),
                },
            )

        return "inserted" if created else "updated"
