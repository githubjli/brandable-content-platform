# Migration Plan: django-auth-core → brandable-content-platform

Operationalizes ADR-0009. This is the **single most important document** for V1 launch.

---

## 1. Strategy

**Parallel read-only + scheduled cutover** (per ADR-0009).

- Legacy `django-auth-core` remains authoritative throughout build phase
- New `brandable-content-platform` ingests snapshot, runs feature parity validation internally, **not** customer-visible until cutover day
- Cutover is a scheduled event with announced freeze window (target: 30-60 min)
- Mobile cuts over first; web continues using legacy until V3 (Live Runtime ships)

Why not big-bang: no baseline; first complaint = full rollback with no learning.
Why not gradual sharding: wallet consistency across two live writeable systems is unsafe.

---

## 2. Timeline

| Phase | Week | Activity |
|---|---|---|
| Prep | W4-W5 | Build Identity; write `import_legacy_users` script |
| Prep | W6-W7 | Build Economy; write `import_legacy_wallets` script |
| Dry-run 1 | W7 end | Full import of dev/staging slice; validation report |
| Dry-run 2 | W10 | Full import of production snapshot to staging; validation report |
| Dry-run 3 | W14 | Final rehearsal, timed (target < 60 min) |
| Cutover | W16 | Production cutover with announced freeze window |
| Rollback window | W16-W17 | 24 hours after cutover |
| Post-cutover validation | W17 | Daily smoke tests + reconciliation |

---

## 3. Data scope

### Migrated

| Dataset | Source | Target | Volume estimate | Notes |
|---|---|---|---|---|
| Users (email, password hash, status flags) | `auth_user` | `identity_user` | TBD | Email lowercased + stripped; hashes preserved |
| Profile (display name, avatar, bio, dates) | `accounts_profile` | `identity_user` (extended) | TBD | Split into User fields vs CreatorProfile fields |
| CreatorProfile | `is_creator=true` users | `identity_creatorprofile` | TBD | 1:1 to User |
| KYC profile + documents | `accounts_kycprofile` + `accounts_kycdocument` | `identity_kycprofile` + `identity_kycdocument` | TBD | Status enum preserved |
| MeowPointWallet balances | `wallets_meowpointwallet` | `economy_pointwallet` + one ledger row | TBD | `entry_type=MIGRATION_INITIAL_BALANCE` |
| MeowCreditWallet balances | `wallets_meowcreditwallet` | `economy_creditwallet` + one ledger row | TBD | Same |
| Active memberships | `accounts_usermembership` (active only) | `membership_usermembership` | TBD | Past/expired NOT migrated |

### NOT migrated

| Dataset | Why | Where it lives |
|---|---|---|
| Full legacy ledger | Volume + audit complexity; legacy DB kept as archive | Legacy DB read-only |
| Drama unlock history | V1 scope; mobile shows "you previously unlocked X" via legacy lookup | Legacy DB |
| Order history | Same | Legacy DB |
| Gift transaction history | Same | Legacy DB |
| Live streams (any state) | V3 scope; mobile keeps using legacy for live | Legacy DB |
| Drama series + episodes | V2 scope; mobile keeps using legacy | Legacy DB |
| Video catalog | V2 scope | Legacy DB |
| Shop products + orders | V2 scope | Legacy DB |
| Channel records | Deprecated entirely | Discarded |

---

## 4. Migration scripts

Each is a Django management command under `ops/migration/`. Required properties:

- **Idempotent**: re-running produces no duplicates and no errors (natural keys + UPSERT)
- **Resumable**: can resume from the last successful batch on failure
- **Dry-run mode**: `--dry-run` runs all validation logic and writes a report without committing
- **Batched**: default batch size 1000 rows, configurable
- **Logged**: every batch writes structured log lines with counts, durations, error samples
- **Audited**: writes AuditLog rows at `severity=critical` for the import operation

Commands:

```bash
# Connectivity check
python manage.py legacy_db_check --legacy-db=<conn-string>

# Import users
python manage.py import_legacy_users \
  --legacy-db=<conn-string> [--dry-run] [--batch-size=1000] [--resume]

# Import wallets
python manage.py import_legacy_wallets \
  --legacy-db=<conn-string> [--dry-run] [--batch-size=1000] [--resume]

# Import KYC
python manage.py import_legacy_kyc \
  --legacy-db=<conn-string> [--dry-run] [--batch-size=500]

# Import active memberships
python manage.py import_legacy_memberships \
  --legacy-db=<conn-string> [--dry-run] [--batch-size=500]

# Validate
python manage.py validate_migration \
  --legacy-db=<conn-string> [--sample-size=100]
```

Output of each command:
- JSON report under `ops/migration/reports/<timestamp>-<command>.json`
- Counts: total / inserted / updated / skipped / errors
- Sample of errors with explanations
- Duration

---

## 5. Validation criteria

Cutover gate: **all green or no cutover**.

| Check | Threshold |
|---|---|
| User count: new == old (active users only) | 100% match |
| Email uniqueness in new system | 0 duplicates |
| `PointWallet.balance` sum across all wallets | new == old, exact |
| `CreditWallet.balance` sum | new == old, exact |
| Per-wallet: latest `WalletLedger.balance_after` == `Wallet.balance` | 100% reconciled |
| Random sample of 100 users: login with old password | 100% success |
| Random sample of 100 wallets: balance match | 100% match |
| FK integrity: no orphans (KycProfile → User, etc.) | 0 orphans |
| KYC approved users: documents accessible | 100% |
| Active memberships still active | 100% match |

`validate_migration` outputs a single PASS/FAIL plus a JSON report. Required gate for cutover.

---

## 6. Cutover-day plan

### T-24h: Final freeze
- Code freeze on both repos
- Final staging dry-run with prod-snapshot
- Confirm rollback plan with on-call
- Notify mobile users via in-app banner (24h notice)

### T-2h: Announce window
- Status page: maintenance window starting
- In-app banner: "Maintenance in 2 hours"
- Confirm all on-call available

### T-30min: Final validation on staging
- Run `validate_migration` against staging
- Confirm PASS

### T+0: Freeze writes on legacy
- Switch legacy backend to read-only mode (env flag)
- Status page: maintenance in progress

### T+5min: Final delta import
- Import any rows created in the last few minutes
- `import_legacy_users --since=<freeze-time> --resume`
- `import_legacy_wallets --since=<freeze-time> --resume`

### T+15min: Run validate_migration on production
- Must PASS
- If FAIL: abort, debug, possibly rollback

### T+25min: Switch nginx upstream
- nginx config update: mobile API hostname now routes to new platform
- Web continues to route to legacy (until V3)

### T+30min: Smoke tests
- Login as test user (real mobile)
- Read wallet balance
- Send a daily reward claim
- Check error rates on Grafana

### T+35min: Open for traffic
- Remove banner
- Status page: maintenance complete

### T+24h: Cutover commit
- Decision point: rollback or commit
- If green throughout: legacy goes read-only-archive mode permanently

---

## 7. Rollback plan

### Window
24 hours after cutover.

### Triggers (any one)
- Login success rate < 95% for any 15-min window
- Wallet balance discrepancy reported by support, confirmed
- Critical endpoint error rate > 10%
- DLQ depth growing > 100 events/hour without resolution

### Procedure
1. nginx upstream switch back to legacy (1 command)
2. Legacy backend writes re-enabled
3. New platform frozen for forensics
4. Customer comms within 30 minutes (in-app + email)
5. Decision on next attempt

### Post-rollback constraint
Any wallet activity on the new platform before rollback must be **manually replayed** onto legacy. This is why:
- Cutover window is intentionally short
- Monitoring is intense in the first hours
- The decision to commit or rollback is firm at T+24h

---

## 8. User communication

| Time | Channel | Message |
|---|---|---|
| Cutover - 7 days | Email to all users | Maintenance window announcement |
| Cutover - 24 hours | In-app banner | Reminder |
| Cutover - 2 hours | In-app banner | Freeze starting soon |
| Cutover - 0 | Status page | Maintenance in progress |
| Cutover + done | Status page + email | Service restored |

**Password experience**: users do not need to reset passwords (hashes migrated; first successful login auto-rehashes if format upgraded).

**Security follow-up**: all users prompted to set up 2FA on first login post-cutover (advisory in V1).

---

## 9. Old system lifecycle

| Phase | Duration | State |
|---|---|---|
| Hot archive | First 90 days post-cutover | Read-only, full availability for support / disputes |
| Warm archive | 90 days to 1 year | Read-only, periodic restore drills |
| Cold archive | 1 to 7 years | DB dump in object storage; restorable but not online |
| Deletion | After 7 years (or per regulator) | Per data retention policy |

Legacy code repo: stays available, marked archived after 1 year. Legacy infra: shut down after 1 year (cost recovery).

---

## 10. Pre-cutover blockers (must resolve before W16)

- [ ] Exact production user / wallet volume (sets batch sizes and freeze window estimate)
- [ ] Cutover window: which day of week / time minimizes user impact?
- [ ] Status page tooling chosen and integrated
- [ ] Customer support runbook for post-cutover issues
- [ ] On-call rotation prepared for cutover-day + 24h window
- [ ] Mobile build with new backend support shipped to production (per `contracts/diff-from-legacy.md`)
- [ ] Web frontend migrated from legacy `/api/channels/{id}/subscribe/` to new `/api/public/users/{id}/follow/` (web continues on legacy backend; just needs the path change)
- [ ] All migration commands dry-run-tested 3 times against staging

---

## 11. Roles on cutover day

| Role | Responsibilities |
|---|---|
| Incident commander | Calls go/no-go decisions; runs the meeting |
| Database operator | Runs migration commands; monitors PostgreSQL |
| Platform engineer | Watches Grafana; ready to push hotfixes |
| Customer support lead | Watches support channels; relays user reports |
| Comms lead | Posts status page updates; sends emails |

Total: 5 people minimum on cutover day. Rehearsed roles in dry-run 3 (W14).
