# Audit Contract

The platform's **immutable audit trail**. Every sensitive action — wallet adjustment, KYC decision, admin override, configuration change, role grant — writes an `AuditLog` row in the same transaction as the business write.

**App**: `apps/audit/`
**Related ADR**: ADR-0004 Wallet Ledger Invariants (audit is the meta-layer above the wallet ledger)
**Status**: 🟢 V1 (cross-cutting; every app uses it from day one)

---

## 1. Why this exists separately from events

| | OutboxEvent (events.md) | AuditLog (this doc) |
|---|---|---|
| Purpose | Async fan-out + side effects | Compliance / forensic record |
| Timing | Same tx, processed later | Same tx, **never deleted** |
| Failure | Retry / DLQ acceptable | Failure = block the business write |
| Mutability | Can change status | Append-only, immutable |
| Retention | 30-90 days hot | **Years** (per regulator) |
| Query | by event_type / time | by actor / target / action / time |

**Audit is not "an event you can subscribe to."** Audit is the legal record of what happened. Events drive workflows; audit answers "who did what when to whom" forever.

---

## 2. AuditLog table

```sql
CREATE TABLE audit_log (
    id                 UUID PRIMARY KEY,
    occurred_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_type         TEXT NOT NULL,        -- 'user' | 'admin' | 'system' | 'service_account'
    actor_id           UUID,                  -- nullable for 'system'
    actor_display      TEXT,                  -- snapshot of actor name/email at time of action
    action             TEXT NOT NULL,        -- e.g., 'wallet.credit', 'kyc.approve', 'order.refund'
    target_type        TEXT NOT NULL,        -- model name, e.g., 'PointWallet', 'KycProfile', 'Order'
    target_id          UUID NOT NULL,
    target_display     TEXT,                  -- snapshot identifier (e.g., order_no, email)
    before_state       JSONB,                 -- relevant fields before change (nullable for create actions)
    after_state        JSONB,                 -- relevant fields after change (nullable for delete actions)
    reason             TEXT,                  -- human-provided reason (admin-required for sensitive actions)
    request_metadata   JSONB,                 -- ip_address, user_agent, request_id, trace_id
    severity           TEXT NOT NULL DEFAULT 'info',
                                              -- info | notable | sensitive | critical
    correlation_id     UUID                    -- groups related audit rows in one logical operation
);

-- Indexes for common queries
CREATE INDEX idx_audit_actor ON audit_log (actor_id, occurred_at DESC);
CREATE INDEX idx_audit_target ON audit_log (target_type, target_id, occurred_at DESC);
CREATE INDEX idx_audit_action ON audit_log (action, occurred_at DESC);
CREATE INDEX idx_audit_severity ON audit_log (severity, occurred_at DESC)
  WHERE severity IN ('sensitive', 'critical');
CREATE INDEX idx_audit_correlation ON audit_log (correlation_id)
  WHERE correlation_id IS NOT NULL;

-- Append-only enforcement at DB level
CREATE OR REPLACE FUNCTION audit_log_prevent_changes()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only; UPDATE/DELETE forbidden';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_prevent_changes();
CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_prevent_changes();
```

### Immutability rules (enforced at multiple layers)

1. DB trigger (above) — last line of defense
2. Django model: `save()` raises if `pk` already set; `delete()` raises always
3. Admin: no edit/delete buttons in Django admin
4. `import-linter`: forbids `AuditLog.objects.update()` / `.delete()` anywhere outside `apps.audit.archival` (annual archival job)

---

## 3. record_audit() helper

The **only** entry point for writing audit:

```python
# apps/audit/services.py
from typing import Optional, Any

def record_audit(
    *,
    action: str,                           # 'domain.verb' e.g., 'wallet.credit'
    actor_type: str,                        # 'user' | 'admin' | 'system' | 'service_account'
    actor_id: Optional[UUID],
    actor_display: Optional[str],
    target_type: str,                       # model class name
    target_id: UUID,
    target_display: Optional[str] = None,
    before_state: Optional[dict] = None,
    after_state: Optional[dict] = None,
    reason: Optional[str] = None,
    severity: str = "info",                 # info | notable | sensitive | critical
    correlation_id: Optional[UUID] = None,
    request_metadata: Optional[dict] = None,
) -> AuditLog: ...
```

### Usage in a service method

```python
def approve_seller_application(self, application_id: UUID, admin_id: UUID, note: str) -> SellerApplication:
    with transaction.atomic():
        app = SellerApplication.objects.select_for_update().get(id=application_id)
        before = {"status": app.status, "reviewed_at": app.reviewed_at}

        app.status = "approved"
        app.reviewed_at = timezone.now()
        app.reviewed_by_id = admin_id
        app.admin_note = note
        app.save()

        store = SellerStore.objects.create(owner_id=app.user_id, ...)

        # Audit BOTH the approval and the store creation as a correlated pair
        corr_id = uuid4()
        record_audit(
            action="commerce.seller_application.approve",
            actor_type="admin",
            actor_id=admin_id,
            actor_display=get_admin_display(admin_id),
            target_type="SellerApplication",
            target_id=app.id,
            target_display=f"application-{app.id}",
            before_state=before,
            after_state={"status": "approved", "reviewed_at": app.reviewed_at.isoformat()},
            reason=note,
            severity="sensitive",
            correlation_id=corr_id,
        )
        record_audit(
            action="commerce.store.create",
            actor_type="system",
            actor_id=None,
            target_type="SellerStore",
            target_id=store.id,
            target_display=store.slug,
            after_state={"owner_id": str(store.owner_id), "slug": store.slug},
            severity="notable",
            correlation_id=corr_id,
        )

        EventBus.emit(...)  # Outbox event for downstream (separate concern)

        return app
```

### Helper behavior

- Validates `action` matches `<domain>.<verb>` pattern
- Validates `severity` is in enum
- Auto-fills `request_metadata` from current request context (trace_id, ip, user_agent, request_id)
- Inserts row; **raises if insert fails** — caller's transaction rolls back
- Returns the AuditLog instance (for further reference if needed)

---

## 4. Same-transaction requirement (non-negotiable)

`record_audit()` **must** be called inside the same `transaction.atomic()` block as the business write.

```python
# ✅ CORRECT
with transaction.atomic():
    user.is_active = False
    user.save()
    record_audit(action="identity.user.deactivate", ...)

# ❌ WRONG — audit can be lost if the request crashes between user.save() and record_audit
user.is_active = False
user.save()
record_audit(action="identity.user.deactivate", ...)

# ❌ WRONG — audit must NEVER be async (use Outbox for async; audit is sync)
user.is_active = False
user.save()
celery_task_record_audit.delay(...)
```

**If audit write fails, the business write rolls back.** Better to refuse the action than to lose the audit record.

For the (rare) case where audit failure must not block business (e.g., view-tracking), emit an `audit.AuditFailed` Outbox event and write to a fallback sink. This is **explicit** and requires a `severity="critical"` outage to consider.

---

## 5. Must-audit operations (per domain)

These are the **minimum** required audit actions. Each domain may add more.

### Identity (apps/identity/)

| Action | Severity | Required reason? |
|---|---|---|
| `identity.user.register` | info | no |
| `identity.user.deactivate` (by self) | notable | no |
| `identity.user.deactivate` (by admin) | sensitive | **yes** |
| `identity.user.activate` (by admin) | sensitive | **yes** |
| `identity.user.password_change` (by self) | notable | no |
| `identity.user.password_reset` (via email) | notable | no |
| `identity.user.email_change` | sensitive | no |
| `identity.kyc.submit` | info | no |
| `identity.kyc.approve` | sensitive | optional |
| `identity.kyc.reject` | sensitive | **yes** |
| `identity.session.revoke` (force-logout) | notable | no |
| `identity.role.grant_admin` | critical | **yes** |
| `identity.role.revoke_admin` | critical | **yes** |
| `identity.creator_profile.promote` | sensitive | no |

### Economy (apps/economy/)

⚠️ **Every wallet write is implicitly audited via WalletLedger** (per ADR-0004). AuditLog supplements with admin-mediated and unusual actions.

| Action | Severity | Required reason? |
|---|---|---|
| `economy.wallet.admin_adjust` (positive) | sensitive | **yes** |
| `economy.wallet.admin_adjust` (negative) | critical | **yes** |
| `economy.wallet.freeze` (admin freeze) | sensitive | **yes** |
| `economy.wallet.unfreeze` | sensitive | **yes** |
| `economy.credit_redeem.request` (by user) | notable | no |
| `economy.credit_redeem.approve` (admin) | sensitive | **yes** |
| `economy.credit_redeem.reject` (admin) | sensitive | **yes** |
| `economy.credit_redeem.complete` (system) | notable | no |
| `economy.reconciliation.mismatch_detected` | critical | system-recorded |
| `economy.reconciliation.adjustment` | critical | **yes** |

Routine wallet credits/debits (drama unlock, gift, daily reward) are **not** in AuditLog — they're in WalletLedger which is already the immutable financial record.

### Payments (apps/payments/)

| Action | Severity | Required reason? |
|---|---|---|
| `payments.order.create` (by user) | info | no |
| `payments.order.cancel` (by user) | info | no |
| `payments.order.mark_paid` (admin manual) | sensitive | **yes** |
| `payments.order.refund_initiate` | sensitive | **yes** |
| `payments.order.refund_complete` | sensitive | system-recorded |
| `payments.webhook.received` | info | no (high volume; consider sampling) |
| `payments.webhook.signature_invalid` | critical | system-recorded |

### Commerce (apps/commerce/)

| Action | Severity | Required reason? |
|---|---|---|
| `commerce.seller_application.submit` | info | no |
| `commerce.seller_application.approve` | sensitive | optional |
| `commerce.seller_application.reject` | sensitive | **yes** |
| `commerce.store.create` | notable | no |
| `commerce.store.update` | info | no |
| `commerce.store.deactivate` (by admin) | sensitive | **yes** |
| `commerce.product.create` | info | no |
| `commerce.product.update` (admin override) | sensitive | **yes** |
| `commerce.product.archive` (admin) | sensitive | **yes** |
| `commerce.order.cancel` (by buyer) | info | no |
| `commerce.order.cancel` (by admin) | sensitive | **yes** |
| `commerce.order.mark_settled` (admin) | sensitive | **yes** |
| `commerce.refund.approve` (admin) | sensitive | **yes** |
| `commerce.refund.reject` (admin) | sensitive | **yes** |
| `commerce.refund.mark_refunded` (admin) | sensitive | **yes** |

### Content (apps/content/{video,drama,live}/)

| Action | Severity | Required reason? |
|---|---|---|
| `content.video.delete` (by owner) | info | no |
| `content.video.delete` (by admin moderation) | sensitive | **yes** |
| `content.drama.episode.delete` (admin) | sensitive | **yes** |
| `content.live.stream.force_end` (admin moderation) | sensitive | **yes** |
| `content.live.chat.message_delete` (by broadcaster) | info | no |
| `content.live.chat.message_delete` (by admin) | sensitive | **yes** |
| `content.*.ban_user` (admin ban from content) | sensitive | **yes** |

### Membership (apps/membership/)

| Action | Severity | Required reason? |
|---|---|---|
| `membership.grant` (system from order) | info | no |
| `membership.grant` (admin manual) | sensitive | **yes** |
| `membership.revoke` (admin) | sensitive | **yes** |
| `membership.extend` (admin) | sensitive | **yes** |
| `membership.subscription.cancel` (by user) | info | no |
| `membership.subscription.cancel` (by admin) | sensitive | **yes** |

### Platform (apps/platform_config/)

⚠️ **Every config change is audited** — these touch all users.

| Action | Severity | Required reason? |
|---|---|---|
| `platform.config.update` | sensitive | optional |
| `platform.feature_flag.toggle` | sensitive | optional |
| `platform.feature_flag.create` | sensitive | optional |

### Events (apps/events/)

| Action | Severity | Required reason? |
|---|---|---|
| `events.dlq.replay` (admin) | notable | no |
| `events.dlq.resolve` (admin) | notable | **yes** (note) |

### Audit (meta)

| Action | Severity |
|---|---|
| `audit.export` (admin downloads audit data) | critical |
| `audit.archival.complete` (annual job) | notable |

---

## 6. Severity meanings

| Severity | Examples | Retention | Alert? |
|---|---|---|---|
| `info` | Self-service profile update, routine order creation | 1 year hot, then archive | no |
| `notable` | Self-deactivation, password change, refund request | 3 years hot, then archive | no |
| `sensitive` | KYC decision, admin override, refund completion, store deactivation | 7 years (regulator default) | dashboard widget |
| `critical` | Role grant/revoke admin, negative wallet adjustment, webhook signature fail, reconciliation mismatch | 7+ years, replicated | **pager** |

---

## 7. Admin query API

### GET /api/v1/admin/audit 🛠 Admin

**Auth**: required + admin

#### Request (query)
```
?cursor=<>&limit=20
&action=<action>            (e.g., commerce.refund.approve; supports comma-separated)
&actor_id=<uuid>
&target_type=<TypeName>
&target_id=<uuid>
&severity=sensitive,critical
&date_from=2026-01-01
&date_to=2026-12-31
&correlation_id=<uuid>      (returns all audits in the correlated group)
```

#### Response 200
```json
{
  "results": [
    {
      "id": "<uuid>",
      "occurred_at": "2026-06-04T10:00:00.123Z",
      "actor": {"type": "admin", "id": "<uuid>", "display": "admin@example.com"},
      "action": "commerce.refund.approve",
      "target": {"type": "ProductRefundRequest", "id": "<uuid>", "display": "refund-XXX"},
      "before_state": {"status": "requested"},
      "after_state": {"status": "approved", "admin_note": "..."},
      "reason": "Buyer received damaged product",
      "severity": "sensitive",
      "request_metadata": {"ip_address": "1.2.3.4", "user_agent": "...", "trace_id": "..."},
      "correlation_id": "<uuid>"
    }
  ],
  "cursor": {"next": "...", "prev": null}
}
```

### GET /api/v1/admin/audit/{audit_id} 🛠 Admin
Single record.

### POST /api/v1/admin/audit/export 🛠 Admin

Schedules a CSV / JSON export of filtered audit rows. Self-audited as `audit.export` with `severity=critical` (you're extracting potentially sensitive data).

#### Request
```json
{
  "filters": { ... same as list },
  "format": "csv" | "json",
  "delivery": "download" | "email"
}
```

#### Response 202
```json
{
  "export_id": "<uuid>",
  "status": "queued",
  "estimated_rows": 12345
}
```

### GET /api/v1/admin/audit/exports/{export_id} 🛠 Admin
Check export status + download link.

---

## 8. Self-audit (audit of audit)

The audit module audits itself:
- Every `audit.export` records its own AuditLog row with `severity=critical`
- Annual archival job records `audit.archival.complete`
- Failed audit writes (DB trigger fire) emit `audit.AuditFailed` Outbox event → pager
- Anyone with `admin:audit:read` role grants triggers `identity.role.grant_admin` audit

---

## 9. Retention & archival

| Severity | Hot retention (Postgres) | Cold retention (object storage) |
|---|---|---|
| info | 1 year | 3 additional years |
| notable | 3 years | 4 additional years |
| sensitive | 7 years | — (kept hot) |
| critical | 7 years | — (kept hot, replicated) |

Annual archival job (`audit_archival`) runs January 1st:
- Copy info/notable rows older than threshold to S3 (compressed parquet)
- Verify checksum
- Delete from Postgres
- Record `audit.archival.complete`
- Cold storage objects are write-once, lifecycle-protected, with separate IAM credentials

---

## 10. Anti-patterns

- ❌ Calling `AuditLog.objects.create(...)` directly (use `record_audit`)
- ❌ Audit outside `transaction.atomic()` block
- ❌ Async audit via Celery
- ❌ Catching the audit insert failure to "let the business write succeed"
- ❌ Missing `reason` on actions marked "required"
- ❌ Logging audit data via `print` / `logger.info` instead of `AuditLog`
- ❌ Querying production AuditLog directly via psql (use admin API; admin queries themselves leave audit trails)

---

## 11. Performance considerations

- AuditLog write is on the critical path of every sensitive operation. Cap to **< 5ms** per write (single INSERT, no joins).
- High-volume actions (`payments.webhook.received`, content view tracking) should be **sampled** — record 1 in N or only critical sub-cases.
- Partition `audit_log` by `occurred_at` month in V2 if row count exceeds 100M (improves archival and query performance).

---

## 12. V1 deliverables (cross-cutting, throughout V1)

- [ ] `apps/audit/models.AuditLog` + DB triggers
- [ ] `apps/audit/services.record_audit()` helper
- [ ] Admin query API (list + detail + export)
- [ ] import-linter rule: forbid direct `AuditLog.objects` mutations outside `apps.audit`
- [ ] Pre-commit hook: detect `record_audit` calls outside `transaction.atomic` block
- [ ] Each domain wires its required audit calls per §5
- [ ] Alert rules for `audit.AuditFailed` Outbox events
- [ ] Retention / archival job (skeleton in V1; archival itself can wait until V2)

---

## 13. Relationship to other contracts

- **events.md**: produces async fan-out; audit produces sync compliance record. Both written in same transaction.
- **economy.md (WalletLedger)**: ADR-0004 makes WalletLedger the immutable financial record for wallet activity. Audit covers **admin-mediated** wallet actions on top.
- **identity.md**: KYC / role / session changes all audited per §5.
- **commerce.md**: order state transitions audited where admin-mediated; user self-service transitions audited at `info` level.
- **conventions.md §10**: `trace_id` and `request_id` propagate into `request_metadata`.

---

## 14. Open items (V2+)

- Per-domain query convenience APIs (e.g., `GET /api/v1/admin/audit/wallet/{wallet_id}/history`)
- Audit dashboard with anomaly detection
- Tamper-evident chaining (Merkle root or signed log)
- Read replicas dedicated to audit queries
- SIEM integration (export sensitive/critical to external compliance tool)
