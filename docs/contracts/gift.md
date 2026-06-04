# Gift Contract

Covers: cross-content gift system, GiftTransaction model, send flow for video / drama / live.

**App**: `apps/economy/gift/` (within economy or as submodule, depending on impl)
**Legacy reference**: `MOBILE_API_CONTRACT_FULL.md` §25
**Priority**: 🟡 V2 (mobile-critical for monetization, but content-side; Live broadcast is 🔵 V3)

---

## 1. Static gift catalog

🚫 **Legacy "fixed gift" mode is dropped in new platform.** Only amount-based gifts (with optional preset amounts) are supported. Static catalog endpoint kept for backward UI compatibility but won't include charge logic.

### GET /api/v1/gifts/catalog 🟡 V2
**Auth**: none

#### Response 200
```json
{
  "results": [
    {
      "id": "<uuid>",
      "code": "rose",
      "name": "Rose",
      "emoji": "🌹",
      "icon_url": "...",
      "animation_url": "...",
      "preset_amount": "100.0000",
      "preset_currency": "MP",
      "is_active": true,
      "sort_order": 1
    }
  ]
}
```

#### Diff from legacy
- Removed: `coin_cost`, `points_price` (preset is now explicit `preset_amount` + `preset_currency`)
- Fixed-gift mode is documented in deprecated.md as removed

---

### GET /api/v1/content/live/streams/{stream_id}/gifts 🟡 V2 / 🔵 V3
Returns same catalog, but `stream_id` validates context. Used by mobile live UI.

---

## 2. Send gift — common shape

All three send endpoints have the same request/response structure with only the target differing.

### POST /api/v1/content/video/public/{video_id}/gifts/send 🟡 V2
### POST /api/v1/content/drama/series/{series_id}/gifts/send 🟡 V2
### POST /api/v1/content/live/streams/{stream_id}/gifts/send 🔵 V3

**Auth**: required
**Idempotency**: yes (required header)

#### Request
```json
{
  "amount": "100.0000",
  "currency": "MP",
  "payment_method": "meow_points",
  "gift_code": "rose"
}
```

- `amount`: required, decimal string ∈ {`1`, `10`, `30`, `100`, `200`, `500`} (or custom; preset enforced client-side)
- `currency`: required, must match `payment_method`'s wallet currency (MP or MC)
- `payment_method`: required, `meow_points` or `meow_credit`
- `gift_code`: optional — for icon/animation display only; **doesn't affect charge logic**

#### Response 201
```json
{
  "transaction": {
    "id": "<uuid>",
    "sender_id": "<uuid>",
    "receiver_id": "<uuid>",
    "target": {
      "type": "video",
      "id": "<uuid>"
    },
    "amount": "100.0000",
    "currency": "MP",
    "payment_method": "meow_points",
    "gift_code": "rose",
    "created_at": "..."
  },
  "sender_balance": {"currency": "MP", "amount": "4900.0000"},
  "receiver_balance": {"currency": "MP", "amount": "2400.0000"}
}
```

For Live gifts, response additionally includes the broadcast event:
```json
{
  "transaction": { ... },
  "sender_balance": ...,
  "receiver_balance": ...,
  "event": {
    "id": "<uuid>",
    "type": "gift_event",
    "broadcast_status": "queued"
  }
}
```

`broadcast_status`: `queued` means OutboxEvent emitted; actual delivery happens async via Live Runtime.

#### Errors
- 422 `WALLET_INSUFFICIENT_BALANCE` (detail: `{required, available, currency}`)
- 422 `GIFT_AMOUNT_INVALID`
- 422 `GIFT_SELF_SEND_FORBIDDEN`
- 404 `TARGET_NOT_FOUND`
- 422 `LIVE_STREAM_NOT_LIVE` (live only)

#### Side effects (all gift sends)
- `EconomyService.debit(sender_wallet, SPEND, ...)`
- `EconomyService.credit(receiver_wallet, GIFT_RECEIVED, ...)`
- Creates `GiftTransaction`
- Increments target's `gift_count` and `gift_amount_total` counters
- Emits `OutboxEvent`: `content.<target_type>.Gifted`

#### Side effects (Live only — additional)
- Emits `OutboxEvent`: `content.live.GiftSent` with broadcast payload
- Dispatcher → gRPC call to `LiveRuntimeService.BroadcastGift(stream_id, event)`
- All connected WebSocket viewers receive `gift_event` message

#### Diff from legacy
- Single unified request shape (legacy had two modes)
- `currency` always explicit
- `gift_code` is display hint only, not charge calculation input
- Idempotency key now mandatory (legacy used 2-second dedup window — racy)
- Removed: `gift_id`, `quantity`, `points_charged`/`credits_charged` (now `amount` + `currency`)

---

## 3. GiftTransaction model

```
id: UUID
idempotency_key: string (UNIQUE)
sender_id: UUID (FK User)
receiver_id: UUID (FK User)
target_type: enum (VIDEO, DRAMA_SERIES, LIVE_STREAM)
target_id: UUID
amount: Decimal(18,4)
currency: string (MP | MC)
payment_method: enum (meow_points, meow_credit)
gift_code: string? (display hint)
created_at: datetime

-- denormalized for query speed
sender_wallet_ledger_id: UUID (link to debit ledger entry)
receiver_wallet_ledger_id: UUID (link to credit ledger entry)
```

### Indexes
- `(sender_id, created_at DESC)` — gifts sent
- `(receiver_id, created_at DESC)` — gifts received
- `(target_type, target_id, created_at DESC)` — gifts on a content
- `idempotency_key` UNIQUE

#### Diff from legacy
- Removed: `stream_id`, `video_id`, `drama_series_id` separate FKs → unified `target_type` + `target_id`
- Removed: `gift_id`, `quantity`, `gift_name_snapshot`, `points_price_snapshot` (no fixed-gift mode)
- Removed: `total_points` (legacy redundant)
- Added: `idempotency_key`, `ledger_id` linkbacks

---

## 4. Internal service API

```python
# Module: apps.economy.services.gift or apps.gift.services

class GiftService:
    def send_gift(
        self,
        sender_id: UUID,
        receiver_id: UUID,
        target_type: GiftTargetType,
        target_id: UUID,
        amount: Decimal,
        currency: str,
        payment_method: str,
        idempotency_key: str,
        gift_code: Optional[str] = None,
    ) -> GiftSendResult: ...

    def list_sent(
        self, user_id: UUID, cursor: Optional[str], limit: int = 20
    ) -> PaginatedResult[GiftTransaction]: ...

    def list_received(
        self, user_id: UUID, cursor: Optional[str], limit: int = 20
    ) -> PaginatedResult[GiftTransaction]: ...
```

Transaction logic:
```python
with transaction.atomic():
    # 1. Acquire sender wallet lock
    sender_wallet = lock_wallet_for_update(sender_id, currency)
    
    # 2. Validate balance
    if sender_wallet.balance < amount:
        raise InsufficientBalanceError(...)
    
    # 3. Debit sender
    debit_entry = EconomyService.debit(
        wallet_id=sender_wallet.id,
        entry_type=LedgerEntryType.SPEND,
        amount=amount,
        idempotency_key=f"gift:{idempotency_key}:debit",
        target_type="GiftTransaction",
        target_id=gift_tx_id,
    )
    
    # 4. Credit receiver
    receiver_wallet = get_or_create_wallet(receiver_id, currency)
    credit_entry = EconomyService.credit(
        wallet_id=receiver_wallet.id,
        entry_type=LedgerEntryType.GIFT_RECEIVED,
        amount=amount,
        idempotency_key=f"gift:{idempotency_key}:credit",
        target_type="GiftTransaction",
        target_id=gift_tx_id,
    )
    
    # 5. Create GiftTransaction with linkbacks
    gift_tx = GiftTransaction.objects.create(
        id=gift_tx_id,
        idempotency_key=idempotency_key,
        sender_id=sender_id,
        receiver_id=receiver_id,
        target_type=target_type,
        target_id=target_id,
        amount=amount,
        currency=currency,
        payment_method=payment_method,
        gift_code=gift_code,
        sender_wallet_ledger_id=debit_entry.id,
        receiver_wallet_ledger_id=credit_entry.id,
    )
    
    # 6. Emit Outbox event
    OutboxEvent.objects.create(
        event_type=f"content.{target_type.lower()}.Gifted",
        idempotency_key=f"gift_event:{idempotency_key}",
        payload={
            "gift_tx_id": str(gift_tx_id),
            "sender_id": str(sender_id),
            "receiver_id": str(receiver_id),
            "target_type": target_type,
            "target_id": str(target_id),
            "amount": str(amount),
            "currency": currency,
        },
    )
    
    # 7. For LIVE_STREAM: additional event for runtime broadcast
    if target_type == GiftTargetType.LIVE_STREAM:
        OutboxEvent.objects.create(
            event_type="content.live.GiftSent",
            idempotency_key=f"gift_broadcast:{idempotency_key}",
            payload={
                "stream_id": str(target_id),
                "sender_id": str(sender_id),
                "sender_name": ...,
                "amount": str(amount),
                "currency": currency,
                "gift_code": gift_code,
            },
        )

return GiftSendResult(gift_tx, sender_wallet.balance, receiver_wallet.balance)
```

---

## 5. List gifts (mobile uses via library.md)

### GET /api/v1/account/library/gifts/sent 🟢 V1
### GET /api/v1/account/library/gifts/received 🟢 V1

See library.md §4-5.

---

## 6. Outbox events

| Event | When | Subscribers |
|---|---|---|
| `content.video.Gifted` | After video gift | analytics, video stats |
| `content.drama.Gifted` | After drama gift | analytics, drama stats |
| `content.live.Gifted` | After live gift (always) | analytics |
| `content.live.GiftSent` | **Live only** — broadcast trigger | Live Runtime gRPC |
| `economy.WalletDebited` × 2 | sender wallet | (from EconomyService) |
| `economy.WalletCredited` × 2 | receiver wallet | (from EconomyService) |

---

## 7. Constraints

1. Cannot gift to self (422 `GIFT_SELF_SEND_FORBIDDEN`)
2. Live gifts only allowed if `stream.status == live` (422 `LIVE_STREAM_NOT_LIVE`)
3. Receiver wallet auto-created if missing (rare; unusual case for content viewer who hasn't logged in but received a gift)
4. Same `idempotency_key` returns the original transaction (200 OK, not 201) for replay safety

---

## 8. V1 vs V2/V3 scope

| Feature | V1 | V2 | V3 |
|---|---|---|---|
| Gift catalog | | 🟡 | |
| Video gift send | | 🟡 | |
| Drama gift send | | 🟡 | |
| Live gift send + broadcast | | | 🔵 |
| List sent/received (via Library) | 🟢 | | |
| Gift leaderboards (per stream / per creator) | | | 🔵 |
