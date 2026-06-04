# Runbook: Wallet Reconciliation Mismatch

## Symptom
- Support ticket: user reports balance doesn't match transactions
- Reconciliation job alerts (Prometheus `wallet_balance_mismatch_count > 0`)
- `economy.WalletReconciliationMismatch` OutboxEvent fires

## Severity
- Any confirmed mismatch: **critical, page platform team immediately**
- Money bugs are the highest-severity class

## Investigate

Pull the user's full ledger and current balance:

```sql
-- Ledger
SELECT id, entry_type, amount, balance_before, balance_after,
       idempotency_key, target_type, target_id, created_at
FROM economy_walletledger
WHERE wallet_id = '<wallet-uuid>'
ORDER BY created_at ASC;

-- Wallet
SELECT id, balance, created_at, updated_at
FROM economy_pointwallet
WHERE id = '<wallet-uuid>';
-- (or economy_creditwallet)
```

Check:
1. Does the **latest ledger row's `balance_after` == wallet's `balance`?**
2. Are there duplicates by `idempotency_key`? (Should be impossible — DB UNIQUE constraint)
3. Do amounts sum correctly: `SUM(credits) - SUM(debits) == latest balance`?
4. Are timestamps monotonic? (Out-of-order would indicate clock issues)

Common causes:
- A code path bypassed `EconomyService` (look at recent deploys for direct ORM writes — should be impossible per import-linter, but verify)
- Race condition (two concurrent writes without proper `SELECT FOR UPDATE`)
- Migration script bug (especially recent data import)
- Data corruption (DB restore, manual UPDATE, replication lag)

## Mitigate

Freeze writes to the affected wallet (do not allow further debits/credits):

```sql
UPDATE economy_pointwallet 
SET frozen_at = now(), frozen_reason = 'Reconciliation incident <id>'
WHERE id = '<wallet-uuid>';
-- (or economy_creditwallet)
```

Frozen wallets reject all `EconomyService.credit/debit` calls with `WALLET_FROZEN` error. This stops bleeding while you investigate.

## Resolve

1. **Identify the divergence point** (which ledger entry first deviated):
```sql
WITH ordered AS (
  SELECT *, ROW_NUMBER() OVER (ORDER BY created_at) AS rn
  FROM economy_walletledger
  WHERE wallet_id = '<wallet-uuid>'
)
SELECT a.id, a.entry_type, a.amount, a.balance_before, a.balance_after,
       b.balance_after AS prev_balance_after,
       a.balance_before - COALESCE(b.balance_after, 0) AS gap
FROM ordered a
LEFT JOIN ordered b ON b.rn = a.rn - 1
WHERE a.balance_before != COALESCE(b.balance_after, 0)
ORDER BY a.created_at
LIMIT 5;
```

2. **Decide approach**:
   - Replay missing ledger rows (rare; only if you know what was lost)
   - Correct the wallet balance to match ledger (via compensating entry)

3. **NEVER** edit `WalletLedger` rows directly. Always add new compensating entries:
```bash
sudo -u bcp /opt/bcp/venv/bin/python /opt/bcp/django/manage.py wallet_adjust \
  --wallet-id=<wallet-uuid> \
  --entry-type=ADMIN_ADJUST \
  --amount=<correcting-delta> \
  --reason="Reconciliation following incident <id>; correcting drift detected at ledger entry <id>" \
  --idempotency-key="reconcile:<incident-id>:<wallet-uuid>"
```

The adjust command requires `--actor-id` (your admin uuid). Creates a row with `entry_type=ADMIN_ADJUST` and writes an `AuditLog` at `severity=critical` (per audit.md §5).

4. Unfreeze the wallet:
```sql
UPDATE economy_pointwallet SET frozen_at = NULL, frozen_reason = NULL WHERE id = '<wallet-uuid>';
```

5. Notify the affected user with explanation + apology.

## Post-incident
Mandatory post-mortem. Topics:
- Root cause
- Why did our invariants (idempotency key, balance_after, SELECT FOR UPDATE) not prevent it?
- What detection lag did we have, and how can we shorten it?
- Compensation owed to user
- Whether other wallets are at risk (run reconciliation sweep)
- New test that would have caught it

## Sweep for related issues

After fixing one wallet, check all wallets for the same issue:
```sql
SELECT w.id, w.balance,
       (SELECT balance_after FROM economy_walletledger 
        WHERE wallet_id=w.id ORDER BY created_at DESC LIMIT 1) AS latest_after
FROM economy_pointwallet w
WHERE w.balance != (
  SELECT balance_after FROM economy_walletledger
  WHERE wallet_id=w.id ORDER BY created_at DESC LIMIT 1
)
LIMIT 100;
```
