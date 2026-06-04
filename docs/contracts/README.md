# brandable-content-platform — API Contracts

Authoritative specifications for the new platform's API surface. These are **prescriptive** — they describe what the new backend will expose, not what exists today.

## How to use this directory

- **Backend implementers**: implement endpoints exactly as specified here. Deviations require a contract PR before code.
- **Mobile team**: this is the spec for what you'll see after cutover. Diffs from the current backend are in [`diff-from-legacy.md`](diff-from-legacy.md).
- **Reviewers**: contract changes require two reviewers (per `docs/conventions.md`).

## Directory layout

```
contracts/
├── README.md                    This file
├── conventions.md               Cross-cutting standards (auth, errors, pagination, idempotency, time, money)
├── identity.md                  Auth + Account + Profile + KYC + CreatorProfile + Follow + Public Users/Creators
├── economy.md                   Wallets (Point + Credit) + Ledger + Aggregate Balance
├── payments.md                  Order state machine + Stripe + Blockchain adapter (LBC, LTT, ETH-future, ...) + Wallet (MP/MC)
├── library.md                   Activity (history/liked/purchased/gifts)
├── content-video.md             Public catalog + interactions + creator upload
├── content-drama.md             Series/episodes + 4 unlock methods + creator mgmt
├── content-live.md              Viewer + watch-config + broadcaster + chat REST
├── commerce.md                  Shop catalog + Cart + Product orders + Seller + Store + Shipping
├── membership.md                Plans + Subscription + Manual Blockchain verification (6-step)
├── gift.md                      Cross-content gift system + GiftTransaction
├── live-runtime.md              gRPC contract for Live Runtime service
├── notification.md              gRPC contract for Notification service
├── chat.md                      gRPC contract for Chat service
├── platform-config.md           Branding + theme + app config (singleton)
├── events.md                    OutboxEvent bus + dispatcher + DLQ + full event catalog
├── audit.md                     AuditLog (append-only) + record_audit() + per-domain must-audit list
├── deprecated.md                Endpoints/fields explicitly NOT implemented
└── diff-from-legacy.md          Cutover delta for mobile team
```

## Status legend (used in each spec)

| Symbol | Meaning |
|---|---|
| 🟢 V1 | Ships in V1 (mobile cutover scope) |
| 🟡 V2 | Ships in V2 (post-cutover, mobile keeps using legacy until then) |
| 🔵 V3 | Ships in V3 |
| 🛠 Admin | Admin/internal only; not exposed to mobile |
| 🚫 Dropped | Documented as explicitly NOT implemented; legacy only |
| ⚠️ Breaking | Mobile must update before cutover |

## Reading conventions

Every endpoint spec follows the same format:

```
### <METHOD> /api/v1/<path>
**Status**: 🟢 V1 | 🟡 V2 | ...
**Legacy ref**: MOBILE_API_CONTRACT_FULL.md §N (if applicable)

**Auth**: required / optional / none / service-only
**Idempotency**: yes (write op) / no (read op)

#### Request
JSON shape with types, required/optional flags.

#### Response
- 200/201: success shape
- 4xx/5xx: error codes (see conventions.md for envelope)

#### Side effects
- DB writes
- Wallet operations (cite ADR-0004 invariants)
- OutboxEvent emissions (event_type)
- gRPC service calls

#### Diff from legacy
What changes for mobile (if anything).
```

## Versioning

- All endpoints under `/api/v1/`.
- Breaking changes go to `/api/v2/`. v1 and v2 coexist for at least one release.
- Proto packages use `<domain>.v<N>`.

## Source of truth

Legacy reference: `../../../MOBILE_API_CONTRACT_FULL.md`

When in doubt, this directory wins.
