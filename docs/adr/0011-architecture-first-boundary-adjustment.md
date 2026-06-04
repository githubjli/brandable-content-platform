# ADR-0011: Architecture-first boundary adjustment

## Status
Accepted

Supersedes the build-order portion of ADR-0006. ADR-0006 boundary rules still apply unless explicitly changed here.

## Context

The legacy system currently has very low active usage (<10 effective users). This reduces migration-data pressure and makes it practical to validate the new platform architecture before waiting for every V2 user-facing feature.

Low data volume does **not** make every domain a good gRPC service. The deciding factor is domain boundary and transaction shape:

- Real-time, high-churn, long-lived connection workloads can move earlier into gRPC services.
- Money, payment, membership entitlement, and commerce order state remain in Django, where one database transaction can own the business invariant.
- The platform should validate the Commerce -> Payments -> Economy -> Events -> Audit chain early, because that chain proves the hardest cross-domain contracts.

## Decision

### 1. Membership remains a Django app

`apps/membership/` owns `MembershipPlan`, `UserMembership`, `BillingSubscription`, and `ManualMembershipPayment`.

There is no `services/membership/` gRPC service. Membership calls:

- `apps/payments/services.py` for payment order orchestration.
- `apps/economy/services.py` for MP/MC wallet payments when applicable.
- `apps/events/services.py` for async side effects.
- `apps/audit/services.py` for sensitive state changes.

Membership may emit Outbox events consumed by NotificationService. It must not rely on a gRPC service to grant or revoke entitlements.

### 2. Commerce remains a Django app, with a V1 architecture-validation slice

`apps/commerce/` stays in Django. It owns product-order business state, seller/store state, shipping state, refund requests, and product snapshots.

V1 adds a narrow **Commerce Architecture Validation Slice (V1-AVS)**:

1. Admin-seeded `Product`.
2. `ProductOrder` creation.
3. Linked `payments.Order` with `business_kind=PRODUCT`.
4. Wallet MP/MC payment and Stripe USD payment paths.
5. Order state transitions through `pending_payment -> paid`.
6. Required `Idempotency-Key`.
7. `OutboxEvent` emissions for created/paid/cancelled.
8. Same-transaction `AuditLog` for sensitive transitions.

Full buyer marketplace features remain V2: public catalog browsing, cart, seller onboarding, seller shipment, QR resolution, refunds, and store management.

Commerce is not a gRPC service.

### 3. ChatService can move earlier

Chat is a good gRPC boundary because it owns long-lived connections and message persistence independent of Django's transactional domains.

The early ChatService scope is:

- `Ping`
- auth + trace interceptors
- PostgreSQL schema
- `CreateDirectRoom`
- `SendMessage`
- `ListMessages`
- `MarkRead`
- minimal WebSocket gateway for mobile compatibility

Group chat, attachments, reactions, and moderation remain V3+.

### 4. LiveRuntimeService can move earlier as a skeleton

LiveRuntime owns runtime state only:

- Ant Media integration
- viewer WebSocket sessions
- viewer presence
- watch config
- runtime broadcasts

Django still owns:

- live stream metadata
- live chat message persistence
- gift transactions
- wallet debit/credit
- content/live order-like state

The early LiveRuntime scope is a skeleton and integration smoke test: `Ping`, auth/tracing, Ant Media health/config calls, `CreateStream`, `GetWatchConfig`, and Redis-backed presence scaffolding. Full live gift broadcast, moderation, product bindings, and production live cutover remain V3.

### 5. Notification stays the canary, but can be minimal

NotificationService remains the first gRPC canary. The earliest canary may be limited to `Ping`, auth, tracing, proto generation, deployment, and metrics. Full email provider delivery can follow once the gRPC stack is proven.

## Consequences

**Good**
- Architecture risk is retired early while the user/data migration blast radius is small.
- The hardest transaction chain (Commerce -> Payments -> Economy -> Events -> Audit) is validated before full marketplace work.
- Real-time services get proto, auth, tracing, WebSocket, and deployment paths before they carry production load.
- Membership entitlement logic stays close to payments and wallet state.

**Bad**
- V1 now includes an internal/staging commerce slice that is not the full marketplace, so documentation must clearly distinguish V1-AVS from V2 user-facing commerce.
- Chat and LiveRuntime skeletons increase early infrastructure work.
- Notification may no longer prove provider delivery before Chat begins unless the team explicitly keeps that gate.

**Neutral**
- The number of gRPC services remains three: Notification, Chat, LiveRuntime.
- Production user cutover can still be scheduled after migration validation; architecture-first work does not require earlier customer exposure.

## Anti-decision

We do NOT:

- Add a Membership gRPC service.
- Add Commerce, Payments, or Economy gRPC services.
- Let LiveRuntime debit wallets, grant gifts, or mutate commerce/membership state.
- Treat V1-AVS Commerce as a complete mobile marketplace.
- Split services because data volume is small; we split only where the runtime boundary is real.
