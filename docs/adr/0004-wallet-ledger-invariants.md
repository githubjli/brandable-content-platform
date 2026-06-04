# ADR-0004: Wallet ledger invariants

## Status
Accepted

## Context
Money bugs are the most expensive class of bug: irreversible, customer-trust-eroding, and often invisible until aggregated. The legacy system (`django-auth-core`) had three structural weaknesses:
1. No `SELECT FOR UPDATE` on wallet rows during credit/debit — race conditions latent
2. No explicit `idempotency_key` — relied on `created_at` to detect duplicates
3. Auto-create wallets on first access with silent warning logs

These haven't caused incidents (yet) because traffic is low, but they are time bombs.

## Decision
Three invariants enforced as close to the database as possible:

1. **Append-only**: `WalletLedger` rows are never updated or deleted. Model overrides `save()` and `delete()` to raise. DB trigger backstops.
2. **Idempotent**: every ledger row carries a `UNIQUE idempotency_key`. Replays with the same key are no-ops (return the existing row).
3. **`balance_after` denormalized**: every row records the wallet balance after the row is applied. Reconciliation = "does the latest row's `balance_after` match the wallet table's `balance`?"

Operational rules:
- All writes go through `EconomyService.credit(...)` / `debit(...)`. `WalletLedger.objects.create(...)` is forbidden outside `apps/economy/`. Enforced by import-linter.
- Reads/writes that update balance use `SELECT ... FOR UPDATE` within `transaction.atomic`.
- Wallet table has DB-level `CHECK (balance >= 0)`.
- Wallets are created **explicitly** at user registration, not lazily.
- Wallet-touching migrations require two reviewers (CODEOWNERS).

Full DB schema and service signature in `contracts/economy.md`.

## Anti-decision
We do NOT:
- **Double-entry accounting in V1**: not needed at this scale; would multiply rows for marginal benefit.
- **Use Decimal precision > 4 places**: 4 places (Decimal(18,4)) is sufficient for fractional MP/MC; matches blockchain payload precision needs.
- **Allow soft-delete on wallets**: deactivation is a flag, not a delete.
- **Trust application-layer idempotency alone**: the UNIQUE constraint at DB level is the actual guarantee.

## Consequences

**Good**
- Reconciliation is mechanical (compare `balance` to latest `balance_after`)
- Replays and retries are safe (idempotency_key)
- Money bugs become detectable in O(1) per wallet
- Append-only history is also the audit trail for routine wallet activity (per audit.md)

**Bad**
- Every ledger row carries a redundant `balance_after` field — accepted for safety
- Cannot edit a wallet row to "fix" an error; must add a compensating entry (an `ADMIN_ADJUST` with reason). This is a feature, not a bug.
- `SELECT FOR UPDATE` serializes concurrent writes against the same wallet — accepted; per-wallet throughput is bounded but per-platform throughput scales horizontally.

**Neutral**
- `EconomyService` becomes a chokepoint everyone learns; documented as such in onboarding
