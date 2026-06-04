# Payments Contract

Covers: PaymentOrder state machine, Stripe adapter (V1 new), Blockchain adapter (LBC + LTT backends in V1; ETH/Tron/Sol as plug-in backends later), Wallet adapter (internal MP/MC), webhook handlers, order verification.

**App**: `apps/payments/`
**Legacy reference**: `MOBILE_API_CONTRACT_FULL.md` §28 (Product Orders), §34-35 (Membership)
**Related ADR**: ADR-0011 (gRPC client policy)

---

## Scope

`apps/payments/` is the **payment gateway abstraction layer**. It owns:
- `Order` state machine (generic, multiple order types)
- Payment provider adapters (Stripe, Blockchain with multiple network backends, future: Alipay, WeChat Pay)
- Webhook ingestion + verification
- Order linking to business entities (membership, product, point package, credit package)

It does **NOT** own:
- Business order semantics (those belong to membership/, commerce/, economy/)
- Wallet credits (delegate to `EconomyService`)

---

## 1. Order model

`Order` is the unified payment representation. Each business-side action (membership purchase, product purchase, point purchase, credit recharge) creates ONE `Order` with a `business_kind` discriminator.

### Order fields

```
id: UUID
order_no: string (server-generated, human-readable)
business_kind: enum
  - MEMBERSHIP
  - PRODUCT
  - CREDIT_RECHARGE
  -- POINT_PACKAGE removed: MP is earned-only, never purchased via Order flow
business_ref_id: UUID  (FK to UserMembership / ProductOrder / CreditRecharge)
user_id: UUID
amount: Decimal(18,4)
currency: string  -- ticker only; meaning depends on (provider, network) tuple
                  -- Naming convention for on-chain tokens: <TICKER>-<CHAIN> (e.g., THB-LTT, USDT-ETH, USDC-SOL)
                  -- MP / MC      → platform virtual currency (provider=wallet)
                  -- USD          → fiat (provider=stripe)
                  -- LBC          → native token of LBRY chain (provider=blockchain, network=lbc)
                  -- THB-LTT      → THB stablecoin on LTT chain (provider=blockchain, network=ltt)
                  -- USDT-ETH     → USDT stablecoin on ETH chain (provider=blockchain, network=eth) — future
                  -- USDC-SOL     → USDC stablecoin on Solana chain (provider=blockchain, network=sol) — future
status: enum
  - PENDING_PAYMENT
  - AUTHORIZED   (Stripe: PaymentIntent succeeded but funds not captured)
  - PAID         (funds captured / blockchain verified)
  - FAILED
  - EXPIRED
  - REFUNDING
  - REFUNDED
  - CANCELLED
payment_provider: enum
  - stripe            (fiat via Stripe — USD primary in V1)
  - blockchain        (any blockchain; specific chain via blockchain_network)
  - wallet            (platform virtual MP / MC — internal ledger debit/credit)
  - manual            (admin mark-paid, e.g., bank transfer reconciled offline)
blockchain_network: enum   (REQUIRED only when payment_provider=blockchain)
  - lbc               (LBRY chain; native token: LBC)
  - ltt               (LTT chain; primary stablecoin: THB-LTT — pegged ~ Thai Baht, analogous to USDT on ETH)
  - (future networks plug in via BlockchainBackend registry — eth, sol, tron, bsc, ...)
provider_intent_id: string (Stripe pi_..., blockchain chain txid, internal ledger entry id)
expected_amount: Decimal
expected_currency: string
expires_at: datetime
paid_at: datetime?
created_at: datetime
updated_at: datetime
idempotency_key: string (UNIQUE)
```

---

## 2. Order state machine

```
PENDING_PAYMENT ─(Stripe pi.authorized)──→ AUTHORIZED ─(capture)──→ PAID
       │                                                              │
       ├──(blockchain verify-now success)───────────────────────────→ PAID
       ├──(manual admin mark-paid)──────────────────────────────────→ PAID
       │
       ├──(expires_at past)─→ EXPIRED (terminal)
       ├──(provider failure)─→ FAILED (terminal)
       └──(user cancels)────→ CANCELLED (terminal)

PAID ─(refund initiated)─→ REFUNDING ─(refund posted)─→ REFUNDED (terminal)
```

### Transition rules

| From → To | Trigger | Actor |
|---|---|---|
| → PENDING_PAYMENT | `POST /api/v1/payments/orders` (internal) | Business app (membership, commerce, economy) |
| PENDING → AUTHORIZED | Stripe webhook `payment_intent.amount_capturable_updated` | System |
| AUTHORIZED → PAID | Stripe capture call | System |
| PENDING → PAID | Blockchain verify success / manual mark | System / Admin |
| PENDING → EXPIRED | TTL elapsed | Background job |
| * → FAILED | Provider error | System |
| * → CANCELLED | User action via business endpoint | User |
| PAID → REFUNDING | Refund initiated | Admin |
| REFUNDING → REFUNDED | Refund posted | System |

---

## 3. Public REST endpoints

All payment ordering is initiated from business-side endpoints (membership.md, commerce.md, economy.md). `apps/payments/` exposes:

### GET /api/v1/payments/orders/{order_no} 🟢 V1
**Auth**: required (owner only)

#### Response 200
```json
{
  "order_no": "ORD-2026-...",
  "business_kind": "CREDIT_RECHARGE",
  "business_ref_id": "<uuid>",
  "amount": "1.00",
  "currency": "USD",
  "status": "pending_payment",
  "payment_provider": "stripe",
  "payment": {
    "provider": "stripe",
    "intent_id": "pi_...",
    "client_secret": "pi_..._secret_..."
  },
  "expected_amount": "1.00",
  "expected_currency": "USD",
  "expires_at": "...",
  "paid_at": null,
  "created_at": "..."
}
```

For Blockchain payments — LBC native token on LBRY chain:
```json
{
  "payment": {
    "provider": "blockchain",
    "blockchain_network": "lbc",
    "expected_amount": "10.0000",
    "expected_currency": "LBC",
    "pay_to_address": "bC...",
    "required_confirmations": 0,
    "txid": null
  }
}
```

For Blockchain payments — THB-LTT stablecoin on LTT chain (analogous to USDT on Ethereum):
```json
{
  "payment": {
    "provider": "blockchain",
    "blockchain_network": "ltt",
    "expected_amount": "100.0000",
    "expected_currency": "THB-LTT",
    "pay_to_address": "0x...",
    "required_confirmations": 1,
    "txid": null
  }
}
```

For Blockchain payments — USDT-ETH stablecoin on Ethereum (future):
```json
{
  "payment": {
    "provider": "blockchain",
    "blockchain_network": "eth",
    "expected_amount": "3.00",
    "expected_currency": "USDT-ETH",
    "pay_to_address": "0x...",
    "required_confirmations": 12,
    "txid": null
  }
}
```

Naming convention: on-chain tokens use `<TICKER>-<CHAIN>` to make the chain explicit (e.g., `THB-LTT`, `USDT-ETH`, `USDC-SOL`). The redundancy with `blockchain_network` is intentional: enables direct currency-based filtering without joining tables.

For Wallet payments — platform virtual MP / MC (no chain, internal ledger):
```json
{
  "payment": {
    "provider": "wallet",
    "expected_amount": "1000.0000",
    "expected_currency": "MP",
    "ledger_entry_id": "<uuid>"
  }
}
```

---

### GET /api/v1/payments/orders 🟢 V1
**Auth**: required
**Cursor-paginated**

#### Request (query)
```
?cursor=<>&limit=20
&status=PENDING_PAYMENT,PAID
&business_kind=PRODUCT,MEMBERSHIP
&date_from=2026-01-01
```

#### Response 200
Cursor-paginated Order objects.

---

### POST /api/v1/payments/orders/{order_no}/verify 🟢 V1
**Auth**: required (owner only)
**Idempotency**: yes

For Blockchain payments — submit txid to trigger verification. The adapter dispatches to the correct backend based on `blockchain_network`.

#### Request
```json
{ "txid": "<chain-txid>" }
```

#### Response 200
```json
{
  "order_no": "...",
  "status": "verifying" | "paid" | "failed",
  "verification": {
    "txid": "...",
    "confirmations": 3,
    "required_confirmations": 0,
    "verified_at": "..."
  }
}
```

#### Errors
- 502 `PAYMENT_PROVIDER_UNAVAILABLE` (blockchain node / daemon down)
- 422 `PAYMENT_TXID_MISMATCH` (txid doesn't match expected amount/address)
- 422 `BLOCKCHAIN_NETWORK_UNSUPPORTED` (network value not registered)

---

### POST /api/v1/payments/orders/{order_no}/cancel 🟢 V1
**Auth**: required (owner only)
**Idempotency**: yes

#### Response 200
Updated order with `status: cancelled`.

#### Errors
- 409 `ORDER_NOT_CANCELLABLE` (already paid/refunded/etc.)

---

## 4. Webhook ingestion

### POST /api/v1/payments/webhooks/stripe 🟢 V1
**Auth**: Stripe signature verification (no JWT)
**Idempotency**: yes (Stripe event id)

#### Request
Raw Stripe webhook payload + `Stripe-Signature` header.

#### Response
- 200: event processed (or duplicate, idempotent)
- 400: signature invalid

#### Side effects
- Verifies signature using `STRIPE_WEBHOOK_SECRET`
- Records event in `WebhookEvent` table (dedup by event id)
- Routes by event type:
  - `payment_intent.succeeded` → mark order PAID
  - `payment_intent.payment_failed` → mark order FAILED
  - `charge.refunded` → transition to REFUNDED
- Emits `OutboxEvent`: `payments.OrderPaid` / `payments.OrderFailed` / `payments.OrderRefunded`

---

### POST /api/v1/payments/webhooks/blockchain/{network} 🟡 V2
**Auth**: shared secret or signature
**Idempotency**: yes

Optional — for blockchain nodes / daemons to push confirmation events. V1 uses polling via `verify-now`. Path includes the network name (e.g., `/blockchain/lbc`, `/blockchain/ltt`, `/blockchain/eth`).

---

## 5. Internal Service API

```python
# Module: apps.payments.services

class PaymentOrderService:
    def create_order(
        self,
        user_id: UUID,
        business_kind: BusinessKind,
        business_ref_id: UUID,
        amount: Decimal,
        currency: str,
        payment_provider: str,
        idempotency_key: str,
    ) -> Order: ...
    
    def cancel_order(self, order_no: str, actor_id: UUID) -> Order: ...
    
    def initiate_refund(
        self,
        order_no: str,
        amount: Decimal,
        reason: str,
        actor_id: UUID,
    ) -> Order: ...

class StripeAdapter:
    def create_payment_intent(self, order: Order) -> dict: ...
    def capture(self, intent_id: str) -> dict: ...
    def refund(self, charge_id: str, amount: Decimal) -> dict: ...
    def verify_webhook(self, payload: bytes, signature: str) -> dict: ...

class BlockchainAdapter:
    """Generic blockchain payment adapter. Dispatches to per-network backend."""
    def __init__(self, network: str): ...   # 'lbc', 'ltt', 'eth', ...
    def get_pay_to_address(self, order: Order, currency: str) -> str: ...
    def verify_txid(
        self, txid: str, expected_amount: Decimal, expected_currency: str, address: str
    ) -> VerifyResult: ...
    def get_required_confirmations(self) -> int: ...

class BlockchainBackend:
    """Abstract: one backend per chain. Each declares its supported currencies."""
    network: str                    # e.g., "ltt"
    supported_currencies: list[str] # e.g., ["THB-LTT"] or ["USDT-ETH", "USDC-ETH", "ETH"]

class LbcBackend(BlockchainBackend):
    network = "lbc"
    supported_currencies = ["LBC"]

class LttBackend(BlockchainBackend):
    network = "ltt"
    supported_currencies = ["THB-LTT"]   # add more LTT-chain tokens here as launched

class EthBackend(BlockchainBackend):    # future
    network = "eth"
    supported_currencies = ["USDT-ETH", "USDC-ETH", "ETH"]
```

Backends register themselves in `BLOCKCHAIN_BACKEND_REGISTRY` keyed by `network`. Adding a new chain = one new class + one config block + zero contract changes.

---

## 6. Webhook security

| Provider | Verification |
|---|---|
| Stripe | HMAC-SHA256 with `STRIPE_WEBHOOK_SECRET`, tolerance 5 min |
| Blockchain (per network) | Shared secret in header (TBD per network; configured per backend) |

**Diff from legacy**: legacy had no webhook security. New platform requires verification on every webhook.

---

## 7. Refund flow

```
Admin / system initiates refund
        ↓
Order: PAID → REFUNDING
        ↓
Provider call (Stripe refund / Blockchain manual return per network)
        ↓
On confirm:
  Order: REFUNDING → REFUNDED
  EconomyService.credit(wallet, REFUND, amount, ...)  (if originally paid in MP/MC)
  OutboxEvent: payments.OrderRefunded
```

Refund APIs are admin-only (see commerce.md §38-40 for product refund flow).

---

## 8. Outbox events emitted

| Event | When |
|---|---|
| `payments.OrderCreated` | After create_order |
| `payments.OrderAuthorized` | After Stripe authorization |
| `payments.OrderPaid` | After PAID transition |
| `payments.OrderFailed` | After FAILED transition |
| `payments.OrderExpired` | After EXPIRED transition |
| `payments.OrderCancelled` | After CANCELLED transition |
| `payments.OrderRefundInitiated` | On refund start |
| `payments.OrderRefunded` | After REFUNDED transition |
| `payments.WebhookReceived` | Every webhook (for audit) |

Payload always carries: `order_no`, `business_kind`, `business_ref_id`, `user_id`, `amount`, `currency`, `idempotency_key`.

---

## 9. Provider configuration

Settings managed via `PlatformConfig` (not env vars):

```
stripe_publishable_key: string (public)
stripe_secret_key: secret-ref
stripe_webhook_secret: secret-ref
blockchain_backends:
  lbc:
    node_url: string
    receive_address: string                # one address for LBC native
    required_confirmations: int (default 0)
  ltt:
    node_url: string
    receive_addresses:                     # multiple tokens may share or have per-token addresses
      THB-LTT: string
    required_confirmations: int (default 1)
  eth:                                     # future
    node_url: string
    receive_addresses:
      USDT-ETH: string
      USDC-ETH: string
    required_confirmations: int (default 12)
```

Each chain has its own config block. Adding a new chain = new config block + new `BlockchainBackend` subclass + registry registration. **No contract change, no client change.**

Mobile / client receives:
- `stripe_publishable_key` via `GET /api/v1/platform/config` (see platform-config.md)
- Stripe `client_secret` per-order via `POST /api/v1/economy/credit-recharges` (or membership/commerce order endpoints) response

---

## 10. V1 vs V2 scope

| Feature | V1 | V2 | V3 |
|---|---|---|---|
| Stripe USD | 🟢 | | |
| Blockchain LBC backend (network=lbc, currency=LBC) | 🟢 | | |
| Blockchain LTT backend (network=ltt, currency=THB-LTT) | 🟢 | | |
| Blockchain ETH backend (network=eth, currency=USDT-ETH/USDC-ETH) | | 🟡 | |
| Additional chains (Tron, BSC, Solana, ...) | | | 🔵 |
| Blockchain webhook push (per network) | | 🟡 | |
| Stripe Connect (seller payouts) | | 🟡 | |
| Alipay / WeChat | | | 🔵 |
| Subscription auto-renew | | 🟡 (V2 with Stripe) | |
| Refunds (admin) | 🟢 | | |
| Refunds (user request) | 🟢 (via commerce) | | |
