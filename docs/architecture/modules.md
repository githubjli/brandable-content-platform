# Module Map

The complete module inventory for `brandable-content-platform`. **14 modules in three groups**.

> See `contracts/` for the API surface of each module. This document is the structural map.

---

## Group 1 — Business domains (6 Django apps)

These map roughly to the legacy `django-auth-core` domains, but each is now its own app with proper boundaries.

| # | App | Role | Owns |
|---|---|---|---|
| 1 | `apps/identity/` | Auth + Profile + KYC + CreatorProfile + Follow | User, UserSession, KycProfile, KycDocument, CreatorProfile, Follow |
| 2 | `apps/economy/` | Wallets + ledger + daily reward | PointWallet, CreditWallet, WalletLedger, CreditPackage, CreditRecharge, CreditRedeem |
| 3 | `apps/payments/` | Generic payment + provider adapters | Order, PaymentIntent, WebhookEvent, BlockchainBackend registry |
| 4 | `apps/content/{video,drama,live}/` | Content CRUD + interactions | Video / DramaSeries / DramaEpisode / DramaUnlock / DramaFavorite / LiveStream + their interactions |
| 5 | `apps/commerce/` | Shop + Cart + Orders + Seller + Store + Shipping | SellerStore, SellerApplication, Product, Cart (SavedProduct), ProductOrder, ProductShipment, ProductRefundRequest, ShippingAddress |
| 6 | `apps/membership/` | Plans + Subscriptions + Manual blockchain verification | MembershipPlan, UserMembership, BillingSubscription, ManualMembershipPayment |

## Group 2 — Cross-cutting infrastructure (5 modules)

Built once, used by every business domain.

| # | App | Role | Owns |
|---|---|---|---|
| 7 | `apps/events/` | OutboxEvent + Dispatcher + DLQ | OutboxEvent, OutboxEventDLQ, OutboxEventHandlerAck |
| 8 | `apps/audit/` | AuditLog + record_audit() helper | AuditLog (append-only) |
| 9 | `apps/platform_config/` | Singleton config (V1 single brand) | PlatformConfig |
| 10 | `apps/economy/gift/` (submodule) | Cross-content gift system | GiftTransaction |
| 11 | (aggregator views in `apps/identity/views/library.py`) | Library / Activity unified feed | — (no models; aggregates other apps via service calls) |

`#11` is not a Django app — it's a thin aggregation layer with HTTP endpoints. Documented in contracts/library.md.

## Group 3 — gRPC services (3 services)

Each is a separate process under `services/`. Built in V1 sequentially per ADR-0006.

| # | Service | Build week | Implementation | Owns |
|---|---|---|---|---|
| 12 | `services/notification/` | W9 (canary) | Python (grpcio + asyncio) | Notification record, delivery state |
| 13 | `services/chat/` | W12–13 | Python (grpcio + asyncio) | Room, RoomMember, Message |
| 14 | `services/live_runtime/` | W15–16 | Python (grpcio + asyncio) | Active stream sessions, viewer presence (Redis), Ant Media integration |

Per ADR-0007, all start in Python; per-service Go rewrites are options when production load demands.

---

## Directory layout

```
brandable-content-platform/
├── django/
│   ├── apps/
│   │   ├── identity/                       (#1)
│   │   ├── economy/                        (#2 + #10 gift)
│   │   │   ├── gift/                        # submodule, not separate app
│   │   ├── payments/                       (#3)
│   │   │   └── adapters/
│   │   │       ├── stripe.py
│   │   │       ├── wallet.py
│   │   │       └── blockchain/
│   │   │           ├── base.py
│   │   │           ├── lbc.py
│   │   │           └── ltt.py
│   │   ├── content/                        (#4)
│   │   │   ├── video/
│   │   │   ├── drama/
│   │   │   └── live/
│   │   ├── commerce/                       (#5)
│   │   ├── membership/                     (#6)
│   │   ├── events/                         (#7)
│   │   ├── audit/                          (#8)
│   │   └── platform_config/                (#9)
│   ├── libs/
│   │   ├── errors/
│   │   ├── pagination/
│   │   ├── jwt_auth/
│   │   ├── idempotency/
│   │   ├── logging/
│   │   ├── telemetry/
│   │   └── grpc_client/
│   ├── config/                              # Django settings
│   └── manage.py
│
├── services/
│   ├── notification/                       (#12)
│   ├── chat/                               (#13)
│   └── live_runtime/                       (#14)
│
├── proto/
│   ├── common/v1/
│   ├── notification/v1/
│   ├── chat/v1/
│   └── live/v1/
│
├── ops/
│   ├── ansible/
│   ├── systemd/
│   ├── nginx/
│   └── migration/                           # legacy import scripts
│
└── docs/                                    # this directory
    ├── contracts/
    ├── adr/
    ├── architecture/
    ├── ops/
    ├── migration/
    └── legacy/
```

---

## Modules that do NOT exist (per discussions)

Documented in `contracts/deprecated.md`, but worth restating here:

| Name | Why not |
|---|---|
| `apps/creator/` | CreatorProfile is a 1:1 extension on User, lives in `apps/identity/` |
| `apps/analytics/` | Analytics is a downstream consumer of OutboxEvents, not a module |
| `apps/log/` | Logging is infrastructure (`libs/logging/`), not a domain |
| `apps/branding/` | Single brand → `apps/platform_config/` singleton |
| `apps/chat/` | Chat is a gRPC service, not a Django app |
| `apps/tenancy/` | Single brand → no tenancy machinery (ADR-0001) |

---

## Dependency rules (enforced by import-linter)

```
                ┌──────────────────────────────┐
                │  apps/* (business + infra)   │
                │                              │
                │   - identity, economy,       │
                │     payments, content,       │
                │     commerce, membership     │
                │   - events, audit,           │
                │     platform_config          │
                └──────────┬───────────────────┘
                           │ may import
                           ▼
                ┌──────────────────────────────┐
                │  libs/*                       │  (errors, pagination, jwt_auth, ...)
                └──────────────────────────────┘
```

Rules:
1. `libs/*` cannot import from `apps/*`.
2. `apps/A` cannot import `apps/B.models` (cross-app `models` import banned). 
3. `apps/A` CAN import `apps/B.services` (cross-app calls via service-layer API).
4. `services/*` (gRPC) NEVER import from `apps/*` or vice versa; communication is via gRPC only.
5. `apps/audit` and `apps/events` are exceptions in that everyone CAN import their services (they're infrastructure).

These are enforced in `.import-linter` config in the repo.

---

## When new modules appear

Per `docs/ANTIPATTERNS.md`: don't create speculative empty modules. A new app appears only when:
- Real domain emerges (not "we might need this")
- Existing app has > 30 models and clear split point
- A team has been arguing about ownership for a week

Adding a new app requires: ADR + module description added here + import-linter rule update.
