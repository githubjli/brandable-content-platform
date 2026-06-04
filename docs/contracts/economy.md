# Economy Contract

Covers: PointWallet, CreditWallet, WalletLedger, Aggregate Balance, Daily Reward, Point/Credit purchase + recharge.

**App**: `apps/economy/`
**Legacy reference**: `MOBILE_API_CONTRACT_FULL.md` §6-9
**Critical ADR**: [ADR-0004 Wallet Ledger Invariants](../adr/0004-wallet-ledger-invariants.md)

---

## Core invariants (non-negotiable)

1. **Append-only ledger**: `WalletLedger` rows are never updated or deleted.
2. **Idempotency key UNIQUE**: every ledger row has a UNIQUE `idempotency_key` column.
3. **`balance_after` denormalized**: every ledger row stores the balance after applying the row.
4. **`SELECT FOR UPDATE`**: every credit/debit acquires row lock on wallet.
5. **Single write path**: only `EconomyService.credit/debit` writes ledger. Enforced by `import-linter`.
6. **No negative balances**: DB `CHECK (balance >= 0)` + service-level pre-check.

These are V0 day-1 requirements. Legacy did not enforce any of these.

---

## 1. Wallets

### Two wallet types per user

| Wallet | Currency | Source | Notes |
|---|---|---|---|
| `PointWallet` | `MP` (MeowPoints) | Daily reward, gifts received, creator activity, admin grant, refund | **Earned-only. No purchase channels (no Stripe, no blockchain).** Loyalty currency. |
| `CreditWallet` | `MC` (MeowCredit) | **Blockchain stablecoin recharge** (THB-LTT on LTT; USDT-ETH on ETH future; ...), **Stripe fiat purchase** (gateway path may produce stablecoin first, then credit MC), refund | Paid currency. Two acquisition rails: direct chain stablecoin OR third-party fiat (Stripe). |

Both auto-created at User registration (per identity.md). New platform creates explicitly; legacy created lazily.

**Important — these are platform virtual currencies, not blockchain tokens.**

- MP and MC live entirely in the platform's database (`apps/economy/` ledger).
- They are **not** on any chain (LBC, LTT, Ethereum, Solana, Tron, etc.).
- They cannot be transferred to an external wallet address.
- They can be **purchased with** fiat (Stripe → MP/MC) or **blockchain tokens** (LBC → MP/MC, future: THB → MP/MC), but the resulting balance is platform-internal.
- They can be **redeemed for** on-chain tokens via the redeem flow (CreditWallet only; admin-mediated), with the actual chain payout handled by the relevant Blockchain backend.

The taxonomy is:
- `payment_provider = wallet` → spending **from** MP/MC
- `payment_provider = stripe` → buying MP/MC with fiat
- `payment_provider = blockchain` → buying MP/MC with on-chain token, **or** spending an on-chain token directly for another asset (membership, drama unlock, etc.)

---

### GET /api/v1/economy/wallets/me/point 🟢 V1
**Auth**: required

#### Response 200
```json
{
  "wallet_id": "<uuid>",
  "currency": "MP",
  "balance": "1234.0000",
  "totals": {
    "earned": "5000.0000",
    "spent": "3766.0000",
    "purchased": "0.0000",
    "bonus": "100.0000"
  },
  "created_at": "...",
  "updated_at": "..."
}
```

#### Diff from legacy
- Currency now explicit (`"MP"`)
- Totals nested under `totals` (legacy was flat `total_earned`, `total_spent`, etc.)
- Balance as string Decimal (4 dp)
- Removed: implicit lazy creation; wallet exists at registration

---

### GET /api/v1/economy/wallets/me/credit 🟢 V1
Same shape as above; `currency: "MC"`, totals include `recharged`, `redeemed`, `adjusted` instead of `purchased`, `bonus`.

---

### GET /api/v1/economy/wallets/me 🟢 V1 (aggregate)
**Auth**: required

#### Response 200
```json
{
  "balances": [
    {"currency": "MP", "amount": "1234.0000"},
    {"currency": "MC", "amount": "56.7800"}
  ]
}
```

#### Diff from legacy
- Replaces `/api/user-balance/balance/`
- **Removed legacy aliases**: `coins`, top-level `currency`
- Balance as string Decimal (4 dp), not integer

⚠️ **Breaking for mobile**: must update aggregate balance parsing.

---

## 2. Ledger

### GET /api/v1/economy/wallets/me/point/ledger 🟢 V1
**Auth**: required

#### Request (query)
```
?cursor=<opaque>
&limit=20
&entry_type=PURCHASE,BONUS    (optional, comma-separated filter)
&date_from=2026-01-01
&date_to=2026-12-31
```

#### Response 200
```json
{
  "results": [
    {
      "id": "<uuid>",
      "idempotency_key": "...",
      "entry_type": "PURCHASE",
      "amount": "100.0000",
      "balance_before": "1134.0000",
      "balance_after": "1234.0000",
      "currency": "MP",
      "target_type": "PaymentOrder",
      "target_id": "<uuid>",
      "reference": {
        "type": "PaymentOrder",
        "id": "<uuid>",
        "order_no": "MP-PUR-2026-..."
      },
      "note": "Purchase via package CODE-100",
      "created_at": "..."
    }
  ],
  "cursor": {"next": "...", "prev": null}
}
```

### Ledger entry_type enum

| Type | Direction | Triggers |
|---|---|---|
| `PURCHASE` | credit | Successful point purchase |
| `BONUS` | credit | Bonus from purchase (separate row) |
| `REWARD` | credit | Daily login reward (granted) |
| `SPEND` | debit | Content gift, drama unlock, etc. |
| `REFUND` | credit | Returned from order cancellation |
| `ADMIN_ADJUST` | credit or debit | Admin manual adjustment (signed) |
| `GIFT_RECEIVED` | credit | Other user sent you a gift |
| `MIGRATION_INITIAL_BALANCE` | credit | One-time row per user during legacy migration |

#### Diff from legacy
- `idempotency_key` exposed as field (legacy didn't have)
- `target_type` uses PascalCase model name (legacy used snake_case strings like `live_gift`)
- `reference` object replaces flat `target_id` + `payment_order_id`
- Cursor pagination (legacy was `?page=N`)

---

### GET /api/v1/economy/wallets/me/credit/ledger 🟢 V1
Same shape; currency `MC`.

Entry types for credit wallet: `RECHARGE`, `SPEND`, `REFUND`, `ADMIN_ADJUST`, `GIFT_RECEIVED`, `REDEEM_HOLD`, `REDEEM_COMPLETE`, `MIGRATION_INITIAL_BALANCE`.

---

## 3. Packages

🚫 **No `point-packages` endpoint** — MP is earned-only (see §4). Only credit packages exist.

### GET /api/v1/economy/credit-packages 🟢 V1
**Auth**: required

#### Response 200
```json
{
  "results": [
    {
      "code": "CREDIT_100",
      "name": "100 Credits",
      "credit_amount": "100.0000",
      "bonus_credit": "10.0000",
      "total_credit": "110.0000",
      "price": {"amount": "100.0000", "currency": "THB-LTT"},
      "alternative_prices": [
        {"amount": "3.00", "currency": "USD"},
        {"amount": "10.0000", "currency": "LBC"}
      ],
      "sort_order": 1,
      "description": "..."
    }
  ]
}
```

`price` is the canonical price; `alternative_prices` lists equivalent amounts in other supported currencies (Stripe-payable fiat + supported chain stablecoins/tokens) so the client can show a price selector.

#### Diff from legacy
- Price nested under `price` object with explicit currency
- `alternative_prices` array makes multi-currency pricing explicit (legacy had flat `price_meow_points` / `price_meow_credit` etc.)
- No `status` field (only active packages returned)
- Decimal amounts as strings everywhere

---

## 4. MP — Earned-only (no purchase API)

🚫 **MP has zero direct purchase endpoints.** MP is acquired through earn-only mechanisms; there is no `POST /economy/point-orders` and no `point-packages` listing in V1.

### Acquisition channels (all asynchronous, all via `EconomyService.credit`)

| Channel | Trigger | entry_type |
|---|---|---|
| Daily login reward | `POST /api/v1/economy/daily-rewards/claim` or async on login | `REWARD` |
| Gifts received | Another user sends gift to this user (see gift.md) | `GIFT_RECEIVED` |
| Creator activity reward | TBD: scheduled or manual (V2+ activity programs) | `REWARD` |
| Admin grant | Admin action via Django admin or admin API | `ADMIN_ADJUST` (positive) |
| Refund (rare) | Refund of an MP-paid order | `REFUND` |

### Why no purchase

- **MP is loyalty currency**: keeping it earn-only protects its "earned recognition" value.
- **Avoids regulatory complexity**: no money-to-MP exchange means MP is not treated as a stored-value instrument.
- **Mobile UI implication**: any "Buy MP packages" screen in legacy mobile must be removed in the cutover release.

### Removed legacy endpoints (do not implement)

| Legacy endpoint | New platform |
|---|---|
| `GET /api/meow-points/packages/` | 🚫 not implemented |
| `POST /api/meow-points/orders/` | 🚫 not implemented |
| `GET /api/meow-points/orders/` | 🚫 not implemented |
| `GET /api/meow-points/orders/{order_no}/` | 🚫 not implemented |
| `POST /api/meow-points/orders/{order_no}/tx-hint/` | 🚫 not implemented |

See `deprecated.md` for the full do-not-implement index.

### Diff from legacy

- Legacy allowed `MeowPointPurchase` flows (Blockchain-LBC payment for MP). **All removed.**
- All MP balance growth in the new platform happens via `EconomyService.credit(REWARD | GIFT_RECEIVED | ADMIN_ADJUST | REFUND)` only.
- No `business_kind=POINT_PACKAGE` value exists in `payments.Order.business_kind`.

⚠️ **Breaking for mobile**: the "Buy points" UI must be hidden / removed in the cutover release.

---

## 5. Credit Recharge

Mirrors point purchase structure.

### GET /api/v1/economy/credit-recharge-info 🟢 V1
**Auth**: required

#### Request (query)
```
?package_code=CREDIT_100
```

#### Response 200
```json
{
  "package_code": "CREDIT_100",
  "package_name": "...",
  "credit_amount": "100.0000",
  "bonus_credit": "0.0000",
  "total_credit": "100.0000",
  "price": {"amount": "100.00", "currency": "LBC"},
  "payment_provider": "blockchain",
  "blockchain_network": "lbc",
  "expected_amount": "100.0000",
  "pay_to_address": "bC...",
  "required_confirmations": 0,
  "notice": "..."
}
```

#### Errors
- 503 `PAYMENT_ADDRESS_NOT_CONFIGURED`

---

### POST /api/v1/economy/credit-recharges 🟢 V1
**Auth**: required
**Idempotency**: yes

#### Request
```json
{ "package_code": "CREDIT_100" }
```

#### Response 201
Similar to point-orders but for credit recharge.

---

### POST /api/v1/economy/credit-recharges/submit-txid 🟢 V1
Combined endpoint: create recharge if not exists, submit txid, attempt verification.

#### Request
```json
{
  "package_code": "CREDIT_100",
  "txid": "<chain-txid>"
}
```

---

### POST /api/v1/economy/credit-recharges/{order_no}/verify 🟢 V1
**Auth**: required
**Idempotency**: yes

#### Request
```json
{ "txid": "<chain-txid>" }
```

#### Response 200
Verification result with current status.

---

## 6. Daily Login Reward

⚠️ **Architecture change**: legacy baked reward into login response. New platform makes it explicit.

### POST /api/v1/economy/daily-rewards/claim 🟢 V1
**Auth**: required
**Idempotency**: yes (server-enforced once-per-UTC-day)

#### Response 200 (granted)
```json
{
  "granted": true,
  "amount": "10.0000",
  "currency": "MP",
  "ledger_entry_id": "<uuid>",
  "next_eligible_at": "2026-06-05T00:00:00Z"
}
```

#### Response 200 (already claimed)
```json
{
  "granted": false,
  "reason": "ALREADY_CLAIMED_TODAY",
  "next_eligible_at": "..."
}
```

#### Side effects
- Calls `EconomyService.credit(wallet, REWARD, ...)`
- Idempotent: subsequent calls in same UTC day return `granted: false`

#### Diff from legacy
- **Login no longer returns reward.** Mobile must call this explicitly.
- New platform: also emits `OutboxEvent`: `economy.DailyLoginRewardClaimRequested` on login → async grant. Mobile can either poll or call this endpoint directly.

⚠️ **Breaking for mobile**: must add explicit reward claim call OR rely on async background grant + balance refresh.

---

### GET /api/v1/economy/daily-rewards/status 🟢 V1 (new)
**Auth**: required

#### Response 200
```json
{
  "eligible_now": false,
  "next_eligible_at": "2026-06-05T00:00:00Z",
  "today_amount": "10.0000",
  "currency": "MP",
  "streak_days": 7
}
```

---

## 7. Credit Redeem (admin workflow)

🛠 Mobile-unused. Backend supports user-initiated redeem request, admin reviews.

### POST /api/v1/economy/credit-redeems 🟡 V2
**Auth**: required
**Idempotency**: yes

#### Request
```json
{
  "amount": "100.0000",
  "redeem_method": "blockchain_transfer",
  "blockchain_network": "lbc",
  "account_snapshot": {"address": "bC..."}
}
```

#### Response 201
Redeem request object.

### GET /api/v1/economy/credit-redeems 🟡 V2
Cursor-paginated user's redeem requests.

---

## 8. Internal Service API (not REST)

Used by other apps via `EconomyService` (Python service-layer interface). Not exposed via HTTP except as documented above.

```python
# Module: apps.economy.services

class EconomyService:
    def credit(
        self,
        wallet_id: UUID,
        entry_type: LedgerEntryType,
        amount: Decimal,
        idempotency_key: str,
        target_type: str,
        target_id: UUID,
        note: str = "",
        actor_id: Optional[UUID] = None,
    ) -> WalletLedger: ...

    def debit(
        self,
        wallet_id: UUID,
        entry_type: LedgerEntryType,
        amount: Decimal,
        idempotency_key: str,
        target_type: str,
        target_id: UUID,
        note: str = "",
        actor_id: Optional[UUID] = None,
    ) -> WalletLedger: ...

    def get_balance(self, wallet_id: UUID) -> Decimal: ...

    def reconcile(self, wallet_id: UUID) -> ReconcileResult: ...
```

### Caller domains and their entry_types

| Caller | Operation | entry_type |
|---|---|---|
| `apps.payments` | Order paid (point purchase via Stripe only) | `PURCHASE` + `BONUS` (separate rows) |
| `apps.payments` | Order paid (credit recharge) | `RECHARGE` |
| `apps.payments` | Order refunded | `REFUND` |
| `apps.content.drama` | Episode unlock | `SPEND` |
| `apps.content.video` | Send gift | `SPEND` (sender) + `GIFT_RECEIVED` (receiver) |
| `apps.content.drama` | Send gift | same |
| `apps.content.live` | Send gift | same |
| `apps.identity` | Daily reward | `REWARD` |
| `apps.audit` (admin) | Manual adjustment | `ADMIN_ADJUST` (signed amount) |
| `ops.migration` | Legacy import | `MIGRATION_INITIAL_BALANCE` |

---

## 9. Outbox events emitted by Economy

| Event | When |
|---|---|
| `economy.WalletCredited` | After every credit |
| `economy.WalletDebited` | After every debit |
| `economy.DailyLoginRewardClaimRequested` | On login (handler grants) |
| `economy.DailyLoginRewardGranted` | After successful grant |
| ~~`economy.PointPurchaseCreated`~~ | 🚫 Not emitted (MP is earned-only) |
| ~~`economy.PointPurchaseFulfilled`~~ | 🚫 Not emitted (MP is earned-only) |
| `economy.CreditRechargeCreated` | On recharge create |
| `economy.CreditRechargeFulfilled` | After credit posted |
| `economy.CreditRedeemRequested` | On redeem request |
| `economy.WalletReconciliationMismatch` | Reconciliation failure (alert) |

All payloads carry: `wallet_id`, `user_id`, `amount`, `currency`, `entry_type`, `idempotency_key`, `ledger_id`, `occurred_at`.
