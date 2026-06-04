# Membership Contract

Covers: membership plans, one-shot membership orders, 6-step manual Blockchain verification (V1 ships LBC + LTT backends; ETH and others plug in later), recurring subscription (Stripe).

**App**: `apps/membership/`
**Legacy reference**: `MOBILE_API_CONTRACT_FULL.md` §34-36
**Priority**: 🟡 V2 (mobile uses; depends on payments V1)

---

## 1. Plans

### GET /api/v1/membership/plans 🟡 V2
**Auth**: none

#### Response 200
```json
{
  "results": [
    {
      "id": "<uuid>",
      "code": "PRO_MONTHLY",
      "name": "Pro Monthly",
      "description": "...",
      "duration_days": 30,
      "billing_interval": "month",
      "prices": [
        {"amount": "9.99", "currency": "USD"},
        {"amount": "10.0000", "currency": "LBC"},
        {"amount": "1000.0000", "currency": "MP"},
        {"amount": "10.0000", "currency": "MC"}
      ],
      "benefits": ["Access to all drama", "Ad-free", "..."],
      "is_active": true,
      "sort_order": 1
    }
  ]
}
```

Not paginated.

#### Diff from legacy
- Prices as array of `{amount, currency}` (legacy was flat `price_lbc` / `price_meow_points` / `price_meow_credit`)
- USD added (Stripe V2)

---

## 2. One-shot orders

### POST /api/v1/membership/orders 🟡 V2
**Auth**: required
**Idempotency**: yes

#### Request
```json
{
  "plan_id": "<uuid>",
  "payment_provider": "stripe",
  "payment_asset": "USD"
}
```

`payment_provider` ∈ {`stripe`, `blockchain`, `wallet`}.
- If `payment_provider="blockchain"`: include `blockchain_network` ∈ {`lbc`, `ltt`, `eth`(future), ...} and `currency` like `LBC`, `THB-LTT`, `USDT-ETH`.
- If `payment_provider="wallet"`: `payment_asset` selects MP or MC (the internal wallet).
- If `payment_provider="stripe"`: `payment_asset` typically `"USD"` (or other Stripe-supported fiat).

`payment_asset` is the **currency code** to charge in.

#### Response 201 (new order)
```json
{
  "order_no": "MEM-...",
  "plan_snapshot": {
    "id": "<uuid>",
    "code": "PRO_MONTHLY",
    "name": "Pro Monthly",
    "duration_days": 30
  },
  "price": {"amount": "9.99", "currency": "USD"},
  "status": "pending_payment",
  "payment": {
    "provider": "stripe",
    "intent_id": "pi_...",
    "client_secret": "pi_..._secret_..."
  },
  "expires_at": "...",
  "paid_at": null,
  "created_at": "...",
  "reused": false
}
```

#### Response 200 (reused unpaid order)
Same shape with `reused: true`. Server returns existing unpaid order for same user/plan instead of creating duplicate.

#### Errors
- 404 `PLAN_NOT_FOUND`
- 422 `PLAN_INACTIVE`

#### Side effects
- Creates `UserMembershipOrder` + `payments.Order` (`business_kind=MEMBERSHIP`)
- Emits `OutboxEvent`: `membership.OrderCreated`

#### Diff from legacy
- Plan info nested under `plan_snapshot`
- Payment provider info nested
- Removed: flat `expected_amount_lbc`, `pay_to_address`, `qr_payload`, `txid` (now under `payment` regardless of provider)
- New: `payment_provider` + `blockchain_network` separation (replaces legacy implicit "LBC = the blockchain")

---

### GET /api/v1/membership/orders 🟡 V2
**Auth**: required
**Cursor-paginated**

### GET /api/v1/membership/orders/{order_no} 🟡 V2
Single order detail.

### POST /api/v1/membership/orders/{order_no}/verify 🟡 V2
For Blockchain payments: submit txid. The Blockchain adapter dispatches to the registered network backend (LBC / LTT / ETH / ...) and verifies the token (LBC native / THB-LTT / USDT-ETH / ...).

#### Request
```json
{ "txid": "<chain-txid>" }
```

#### Response 200
```json
{
  "order_no": "...",
  "status": "paid",
  "verification": { ... }
}
```

#### Side effects
- Verifies blockchain tx on the appropriate network
- On success: PAID → grants UserMembership
- Emits `OutboxEvent`: `membership.OrderPaid`, `membership.MembershipGranted`

---

## 3. Current membership

### GET /api/v1/membership/me 🟡 V2
**Auth**: required

#### Response 200 (has active)
```json
{
  "user_id": "<uuid>",
  "plan": {"id": "<uuid>", "code": "PRO_MONTHLY", "name": "Pro Monthly"},
  "status": "active",
  "starts_at": "2026-05-01T00:00:00Z",
  "ends_at": "2026-06-01T00:00:00Z",
  "is_expired": false,
  "days_remaining": 27,
  "auto_renew": false,
  "subscription_id": null
}
```

#### Response 200 (none active)
```json
{ "active_membership": null }
```

If user has BillingSubscription, `auto_renew=true` and `subscription_id` references it.

#### Diff from legacy
- Empty case returns `{ active_membership: null }` (legacy returned `{}`)
- New: `auto_renew` + `subscription_id`

---

## 4. Manual Blockchain Verification (6-step)

Generic flow for users who pay via blockchain transfer outside the app. **V1 ships LBC and LTT backends**; same endpoints support ETH and other networks once their backends register.

The endpoint paths are network-agnostic; the request body carries `blockchain_network` (the chain) and `currency` (the token, e.g., `THB-LTT`). 6 steps:

### Step 1: GET /api/v1/membership/manual/payment-info 🟡 V2
**Auth**: required

#### Request (query)
```
?plan_code=PRO_MONTHLY
&payment_provider=blockchain
&blockchain_network=lbc
&currency=LBC
```

#### Response 200
```json
{
  "plan_code": "PRO_MONTHLY",
  "plan_name": "Pro Monthly",
  "payment_provider": "blockchain",
  "blockchain_network": "lbc",
  "expected_amount": "10.0000",
  "currency": "LBC",
  "pay_to_address": "bC...",
  "required_confirmations": 0,
  "notice": "Send LBC to the address. After tx confirms, submit txid."
}
```

Same shape for other (network, currency) tuples:
- `(ltt, THB-LTT)` — THB stablecoin on LTT chain
- `(eth, USDT-ETH)` — USDT on Ethereum (future)
- `(tron, USDT-TRON)` — USDT on Tron (future)

**Does NOT create order.**

### Step 2: POST /api/v1/membership/manual/tx-hints 🟡 V2
**Auth**: required
**Idempotency**: yes

#### Request
```json
{
  "plan_code": "PRO_MONTHLY",
  "txid": "<chain-txid>",
  "payment_provider": "blockchain",
  "blockchain_network": "lbc",
  "currency": "LBC"
}
```

#### Response 201
```json
{
  "id": "<uuid>",
  "user_id": "<uuid>",
  "plan": {"code": "PRO_MONTHLY", "name": "Pro Monthly"},
  "txid": "<chain-txid>",
  "status": "submitted",
  "created_at": "...",
  "updated_at": "..."
}
```

#### Errors
- 409 `MEMBERSHIP_TXID_DUPLICATE`

### Step 3-4: Dry-run + verification (background or admin-triggered)

### Step 5: POST /api/v1/membership/manual/tx-hints/{hint_id}/verify 🟡 V2
**Auth**: required
**Idempotency**: yes

Manual verification trigger.

#### Response 200
Hint with `status: verified` if confirmed.

### Step 6: UserMembership created
Automatic upon verification → status `active`, starts_at = now, ends_at = now + duration_days. Emits `OutboxEvent`: `membership.MembershipGranted`.

### GET /api/v1/membership/manual/tx-hints 🟡 V2
List user's manual submissions (max 50, cursor-paginated).

---

## 5. Recurring Subscription (V2 with Stripe)

### POST /api/v1/membership/subscriptions 🟡 V2
**Auth**: required
**Idempotency**: yes

Initiates Stripe subscription with auto-renewal.

#### Request
```json
{
  "plan_id": "<uuid>",
  "payment_provider": "stripe"
}
```

#### Response 201
```json
{
  "id": "<uuid>",
  "user_id": "<uuid>",
  "plan": {"id": "<uuid>", "name": "..."},
  "status": "active",
  "auto_renew": true,
  "current_period_start": "...",
  "current_period_end": "...",
  "stripe_subscription_id": "sub_...",
  "created_at": "..."
}
```

#### Side effects
- Creates Stripe subscription
- Creates `BillingSubscription` record
- Creates first `UserMembership` (active)
- Emits `OutboxEvent`: `membership.SubscriptionCreated`

### GET /api/v1/membership/subscriptions/me 🟡 V2
Current subscription or null.

### POST /api/v1/membership/subscriptions/{subscription_id}/cancel 🟡 V2
**Auth**: required
**Idempotency**: yes

Cancels at period end (default) or immediately.

#### Request
```json
{ "cancel_immediately": false }
```

#### Side effects
- Calls Stripe to cancel
- `auto_renew=false`
- If immediate: `status=cancelled`, revokes membership
- Else: continues until current_period_end
- Emits `OutboxEvent`: `membership.SubscriptionCancelled`

---

## 6. State machine — UserMembership

```
─(grant)─→ ACTIVE ─(ends_at past, no auto-renew)─→ EXPIRED (terminal)
              │
              ├─(user/admin cancel)─→ CANCELLED (terminal, no refund)
              └─(subscription renews)→ ACTIVE (extended)
```

---

## 7. State machine — Subscription (Stripe)

```
ACTIVE ─(renew period)─→ ACTIVE
   │
   ├─(payment_failed)─→ PAST_DUE (grace period 7 days)
   │    │
   │    ├─(payment recovers)─→ ACTIVE
   │    └─(grace expires)────→ CANCELLED
   │
   └─(user cancel)─→ CANCEL_AT_PERIOD_END ─(period ends)─→ CANCELLED (terminal)
```

---

## 8. Outbox events emitted

| Event | When |
|---|---|
| `membership.OrderCreated` | After POST /orders |
| `membership.OrderPaid` | After payment confirmed |
| `membership.OrderCancelled` | After order cancel |
| `membership.MembershipGranted` | After UserMembership created |
| `membership.MembershipExpired` | When ends_at reached |
| `membership.MembershipCancelled` | After cancel |
| `membership.ManualTxHintSubmitted` | After step 2 |
| `membership.ManualTxHintVerified` | After step 5 success |
| `membership.SubscriptionCreated` | After Stripe sub create |
| `membership.SubscriptionRenewed` | After period renew |
| `membership.SubscriptionPastDue` | Payment fail |
| `membership.SubscriptionCancelled` | After cancel |

---

## 9. Internal service API

```python
# Module: apps.membership.services

class MembershipService:
    def grant_membership(
        self,
        user_id: UUID,
        plan_id: UUID,
        source_order_no: str,
        duration_days: int,
        idempotency_key: str,
    ) -> UserMembership: ...

    def has_active_membership(self, user_id: UUID) -> bool: ...

    def get_active_membership(self, user_id: UUID) -> Optional[UserMembership]: ...

    def revoke_membership(self, user_id: UUID, reason: str) -> UserMembership: ...

    def check_eligibility_for_resource(
        self, user_id: UUID, resource_type: str, resource_id: UUID
    ) -> bool: ...  # e.g., for drama unlock_type=membership
```

---

## 10. V1 vs V2 scope

| Feature | V1 | V2 | V3 |
|---|---|---|---|
| Plans listing | | 🟡 | |
| One-shot orders (Blockchain + Wallet) | | 🟡 | |
| One-shot orders (Stripe USD) | | 🟡 | |
| Manual Blockchain verification — LBC backend (6-step) | | 🟡 | |
| Manual Blockchain verification — LTT backend (THB-LTT) | | 🟡 | |
| Additional Blockchain backends (ETH, TRON, SOL, ...) | | | 🔵 |
| Subscription (Stripe auto-renew) | | 🟡 | |
| GET /me + status | | 🟡 | |
| Subscription cancel (grace period) | | 🟡 | |
| Past-due dunning | | | 🔵 |
| Mid-period plan changes (upgrade/downgrade) | | | 🔵 |
| Family / shared memberships | | | 🔵 |
