# ADR-0009: Migration strategy — parallel read-only + scheduled cutover

## Status
Accepted

## Context
Migration from `django-auth-core` to `brandable-content-platform` is a project within the project. We have ~6 weeks of building before any data moves; the data move itself is the riskiest moment. Options considered:

1. **Big-bang cutover**: replace legacy at a chosen moment. Maximum simplicity, maximum risk.
2. **Gradual migration (shard users)**: 10% to new, then 25%, etc. Reduces blast radius per cohort but doubles operational surface for the migration window, and wallet consistency across two live systems is brittle.
3. **Parallel read-only + scheduled cutover**: new system fully built and validated against a snapshot of legacy data, while legacy continues serving production. On cutover day, brief freeze, final delta sync, validation, then DNS flip.

Wallet consistency between two live writeable systems is the dealbreaker for option 2. We go with option 3.

## Decision

### Strategy: parallel read-only + scheduled cutover
- Legacy system (`django-auth-core`) remains authoritative throughout the build phase.
- New system (`brandable-content-platform`) ingests legacy snapshot data; runs validation internally; is not customer-visible until cutover day.
- Cutover is a scheduled event with announced freeze window (target: 30–60 minutes).
- After cutover, legacy goes read-only and is preserved as the legal archive (hot 90d, warm to 1yr, cold to 7yr per regulator).

### Cutover scope: mobile only in V1
- Mobile clients switch to new backend at cutover.
- Web clients continue using legacy backend until V3 (Live Runtime ships); their cutover is a separate event.
- This decouples mobile cutover from completing Live / Drama / Video features in V1.

### Migration dataset
- **Migrated**: users (email + password hash), profiles, KYC profile + documents, wallet balances (one-time initial-balance ledger entry per wallet).
- **NOT migrated**: full legacy ledger history (legacy DB kept as cold archive for forensic queries).

### Validation gates
Cutover is blocked unless all of:
- User count: new == old (active users only), 100% match
- Email uniqueness in new system: 0 duplicates
- Wallet balance sums: `SUM(MP)_new == SUM(MP)_legacy` and `SUM(MC)_new == SUM(MC)_legacy` exact
- Per-wallet: latest ledger `balance_after == wallet.balance` for every wallet
- Random sample of 100 users: login with old password succeeds
- Random sample of 100 wallets: balance match
- FK integrity: 0 orphans

### Rollback window
24 hours post-cutover. Triggers:
- Login success rate < 95% any 15-min window
- Confirmed wallet balance discrepancy
- Critical endpoint error rate > 10%
- DLQ depth growing > 100 events/hour without resolution

Rollback procedure: DNS flip back; legacy writes re-enabled; new system frozen for forensics; customer comms within 30 minutes.

Full operational details: `migration/migration-plan.md`.

## Anti-decision
We do NOT:
- **Dual-write to both systems during migration window**: too easy to drift; rollback becomes data archeology
- **Migrate the full legacy ledger row by row**: scale and audit complexity not worth it; one-time `migration_initial_balance` row per wallet is sufficient continuity
- **Promise zero-downtime cutover**: brief freeze is the trade-off for clean state guarantees
- **Cut over web and mobile together**: web is a smaller and lagging consumer; bundling adds risk to mobile cutover with no upside

## Consequences

**Good**
- Cutover gates are mechanical (all-green or no-cut)
- Rollback is well-defined
- Customer notification flow is simple ("brief maintenance window")
- Legacy stays available as ground truth during entire build phase

**Bad**
- Maintenance window required (30–60 min freeze)
- Cutover day is an event with people on-call (high coordination cost)
- Web users continue on legacy for several more months — accepted

**Neutral**
- Legal/compliance: legacy data retention obligations preserved by hot+cold archival
