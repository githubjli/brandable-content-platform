# Migration Rehearsal Runbook

Operational checklist for a migration rehearsal / cutover dry-run. The full plan
(timeline, rollback, comms) is in [migration-plan.md](migration-plan.md); this is
the hands-on command sequence and the **PASS/FAIL gate**.

All commands run from `django/` via the venv (`make` wraps env + cwd). Every
import command is idempotent, resumable (`--resume`), and supports `--dry-run`;
each writes a JSON report under `ops/migration/reports/`.

---

## 1. Pre-flight

```bash
# Confirm the legacy DB is reachable.
python manage.py legacy_db_check --legacy-db="$LEGACY_DATABASE_URL"
```

## 2. Dry-run every import (writes nothing)

```bash
python manage.py import_legacy_users        --legacy-db="$LEGACY_DATABASE_URL" --dry-run
python manage.py import_legacy_memberships  --legacy-db="$LEGACY_DATABASE_URL" --dry-run
```

Review the reports: `total` should match expectations and `errors` should be 0.

## 3. Real import (ordered — users first)

`import_legacy_memberships` resolves each membership's user by email, so users
must land first.

```bash
python manage.py import_legacy_users        --legacy-db="$LEGACY_DATABASE_URL"
python manage.py import_legacy_memberships  --legacy-db="$LEGACY_DATABASE_URL"
```

Re-running is safe (UPSERT on natural keys). On a partial failure, re-run with
`--resume` to continue after the last checkpoint.

## 4. Validate — the cutover gate

```bash
python manage.py validate_migration --legacy-db="$LEGACY_DATABASE_URL"
```

- Prints each check as `[PASS] / [FAIL] / [SKIP]` and a single overall verdict.
- Writes a JSON report and **exits non-zero on FAIL** (so it gates a cutover script).
- Checks: active user-count match, email uniqueness, active-membership count,
  membership→user FK integrity. Checks for data not imported in this environment
  (wallet balances, KYC documents) report `SKIP` and do not fail the gate.

**Gate rule (migration-plan §5): all green or no cutover.**

## 5. Delta import (cutover only)

During the freeze window, import rows created since the freeze, then re-validate:

```bash
python manage.py import_legacy_users --legacy-db="$LEGACY_DATABASE_URL" --since="<freeze-ts>" --resume
python manage.py validate_migration  --legacy-db="$LEGACY_DATABASE_URL"
```

---

## Notes

- The legacy source SQL in the import commands is an **assumed schema** (banner in
  each command) pending confirmation of the real `django-auth-core` tables.
- Password hashes are migrated as-is (`pbkdf2_sha256`) and verify on first login;
  no user reset needed (see [[week4-5-legacy-import-assumed-schema]] in the
  build notes / migration-plan §8).
