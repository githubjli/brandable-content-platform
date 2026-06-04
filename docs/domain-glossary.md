# Domain Glossary

One word, one definition. If you find yourself wanting a synonym, change the code instead of inventing one.

---

## Identity

| Term | Definition |
|---|---|
| **User** | An account holder. Identified by UUID. Authenticated by email + password. |
| **Email** | Lowercased + whitespace-stripped at write time. Unique per platform. |
| **CreatorProfile** | A 1:1 extension of User granting creator capabilities. A user without a CreatorProfile cannot publish content. |
| **UserSession** | An active refresh token + its metadata (device, last seen, IP). Used to force-logout. |
| **Service Account** | A non-human identity used by gRPC services to call each other. Has `type=service` JWTs. |
| **KYC** | Know-Your-Customer profile + uploaded documents + 4-state machine. |
| **Follow** | Directional relationship: user A follows user B. Single canonical path `/api/v1/public/users/{user_id}/follow`. |

## Economy

| Term | Definition |
|---|---|
| **PointWallet** | Per-user wallet holding MeowPoints (MP). Earned-only currency. |
| **CreditWallet** | Per-user wallet holding MeowCredit (MC). Purchasable via Stripe or blockchain stablecoins. |
| **MP** (MeowPoints) | Platform virtual currency. Loyalty currency. NOT on any chain. NOT directly purchasable. |
| **MC** (MeowCredit) | Platform virtual currency. Paid currency. NOT on any chain. Purchasable via Stripe fiat OR blockchain stablecoin. |
| **WalletLedger** | Append-only record of every credit/debit. Each row has `idempotency_key`, `entry_type`, `amount`, `balance_after`. |
| **balance_after** | The wallet balance after a ledger row is applied. Denormalized for reconciliation. |
| **idempotency_key** | UNIQUE key per ledger row. Replays with same key are no-ops. |
| **EconomyService** | The only API that mutates wallets. No code outside `apps/economy/` writes to `WalletLedger`. |
| **entry_type** | Enum on each ledger row: PURCHASE / RECHARGE / SPEND / REFUND / REWARD / GIFT_RECEIVED / ADMIN_ADJUST / MIGRATION_INITIAL_BALANCE / ... |
| **Daily Login Reward** | Earned-only MP grant; once per UTC day; via explicit claim endpoint OR async on login. |
| **Migration Initial Balance** | The `entry_type` for the one-time row created per wallet during legacy import. |

## Payments

| Term | Definition |
|---|---|
| **Order** | A generic payment record. Owned by `apps/payments/`. Carries `business_kind` to identify what it's for. |
| **business_kind** | Enum on Order: MEMBERSHIP / PRODUCT / CREDIT_RECHARGE. (No POINT_PACKAGE — MP is earned-only.) |
| **business_ref_id** | Reference from Order to the owning business entity (e.g., a `ProductOrder` or `CreditRecharge`). |
| **payment_provider** | How payment is made: stripe / blockchain / wallet / manual. |
| **blockchain_network** | Which chain (when provider=blockchain): lbc / ltt / eth (future) / ... |
| **currency** | Ticker code only. Meaning depends on (provider, network) tuple. E.g., `THB-LTT` is the THB stablecoin on LTT chain. |
| **Stripe Intent** | A PaymentIntent in Stripe's terminology; tracks fiat payment authorization + capture. |
| **Blockchain Backend** | Per-chain implementation (LbcBackend, LttBackend, ...). Adapter pattern. |
| **Webhook Event** | Inbound notification from a provider (Stripe, blockchain node). Dedup-tracked. |

## Currency tickers (canonical)

| Ticker | Class | Where |
|---|---|---|
| `MP` | Platform virtual | Platform DB |
| `MC` | Platform virtual | Platform DB |
| `USD` | Fiat | Stripe |
| `LBC` | On-chain native | LBC (LBRY) chain |
| `THB-LTT` | On-chain stablecoin (peg ≈ Thai Baht; analogous to USDT) | LTT chain |
| `USDT-ETH` | On-chain stablecoin (peg USD) | Ethereum chain — future |
| `USDC-SOL` | On-chain stablecoin (peg USD) | Solana chain — future |

Naming convention: on-chain tokens are `<TICKER>-<CHAIN>` to make the chain explicit.

## Content

| Term | Definition |
|---|---|
| **Video** | Standalone VOD (Video On Demand) content. |
| **Drama** | Episodic VOD series. |
| **DramaSeries** | The container for episodes. |
| **DramaEpisode** | A single episode in a series. Has `episode_no` (1-indexed) unique within the series. |
| **Episode Unlock** | Permission grant to watch a paid episode. Four methods: free / meow_points / meow_credit / membership. |
| **LiveStream** | Django-side metadata of a live broadcast. Runtime state lives in `live_runtime`. |
| **Watch Config** | The HLS/WebRTC URLs returned to the viewer. Source of truth: Live Runtime. |
| **Stream Key** | Secret known only to the broadcaster, used to publish RTMP/WebRTC. |
| **Effective Status** | Live stream status normalized from Ant Media (may differ from raw `status` due to delays). |

## Gift

| Term | Definition |
|---|---|
| **Gift** | A user-to-user transfer of MP or MC, attached to a piece of content. |
| **GiftTransaction** | One row per gift, append-only. Records sender / receiver / target_type / amount / currency / payment_method. |
| **target_type** | What the gift is attached to: VIDEO / DRAMA_SERIES / LIVE_STREAM. |
| **Live Gift Broadcast** | The only gift type that triggers a real-time broadcast (to LiveRuntime → viewers). Video and drama gifts are silent. |
| **Gift Code** | A display hint for visual presentation (icon, animation). Does NOT affect charge logic. |

## Commerce

| Term | Definition |
|---|---|
| **Store** | A seller's storefront (`apps/commerce/SellerStore`). One per seller. |
| **Product** | An item sold by a Store. Has price + alternative prices in other currencies. |
| **Cart** | DB-backed per-user list of saved products (`apps/commerce/Cart` = SavedProduct). Persistent. |
| **ProductOrder** | A buyer's purchase intent for a product. Linked to a generic payments.Order via business_ref_id. |
| **SellerApplication** | Application to become a seller. Goes through pending → approved/rejected. |
| **SellerPayout** | TBD V2/V3 — Stripe Connect-mediated payout of seller earnings. |
| **Refund Request** | Buyer-initiated refund flow. Admin reviews and approves/rejects. |
| **Shipping Address** | A delivery address. Unified field names across endpoints (recipient_name, street_address, etc.). |

## Membership

| Term | Definition |
|---|---|
| **MembershipPlan** | A purchasable subscription tier. Has duration + price in multiple currencies. |
| **UserMembership** | An active or past membership a user holds. |
| **BillingSubscription** | A recurring auto-renewing subscription (Stripe-backed). V2. |
| **Manual Tx Hint** | User-submitted blockchain transaction id, awaiting verification (6-step flow). |

## Platform

| Term | Definition |
|---|---|
| **PlatformConfig** | Singleton table holding the deployment's branding (name, logo, colors, support email, feature flags). |
| **Brand** | NOT modeled in V1. When introduced, `PlatformConfig` becomes per-brand. Per ADR-0001. |
| **Feature Flag** | An on/off (and optional percentage rollout) per-key gate. Lifecycle-managed with expected_removal_date. |

## Events / Audit

| Term | Definition |
|---|---|
| **OutboxEvent** | A row written inside a business transaction. Carries the event type + payload + headers. |
| **Outbox Dispatcher** | A separate process that reads pending OutboxEvent rows and dispatches Celery tasks. |
| **DLQ** | Dead-letter queue. Events that failed all retries. Has an alert. |
| **AuditLog** | Append-only compliance record. Written in the same transaction as the business action. |
| **record_audit()** | The ONLY entry point for writing AuditLog. Direct `AuditLog.objects.create` is forbidden. |
| **Correlation ID** | UUID grouping multiple AuditLog rows produced by one logical operation. |
| **severity** | AuditLog field: info / notable / sensitive / critical. Drives retention + alerting. |

## Operational

| Term | Definition |
|---|---|
| **Cutover** | The moment we switch mobile clients from legacy backend to new platform. |
| **Rollback** | DNS / upstream switch back to legacy within 24-hour window. |
| **Dry-run** | Migration scripts executed in `--dry-run` mode: read + validate, no writes. |
| **Reconciliation** | Comparison between wallet balance and latest ledger balance_after; flags mismatches. |
| **Hot archive** | Read-available legacy DB, full availability (first 90 days post-cutover). |
| **Cold archive** | Object-storage dump of legacy DB; restorable but offline (1-7 years). |
| **Trace ID** | Per-request UUID that propagates through HTTP → gRPC → Celery → OutboxEvent → handler. Source of truth for cross-service debugging. |

## Discipline

Adding a new term to the platform requires updating this file in the same PR.
Renaming a term requires updating this file AND every reference in code/contracts.
"Synonyms" are not synonyms; they are bugs. Fix them when you find them.
