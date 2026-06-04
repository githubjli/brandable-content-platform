# Commerce Contract

Covers: shop catalog (buyer-facing), cart, product orders, seller applications, seller store management, seller orders, shipping addresses, refund requests.

**App**: `apps/commerce/`
**Legacy reference**: `MOBILE_API_CONTRACT_FULL.md` §13, §26-33, §40
**Priority**: 🟡 V2 (mobile-critical, large scope, post-V1)

---

## 1. Shop — Buyer

### GET /api/v1/commerce/shop/banners 🟡 V2
**Auth**: none

#### Response 200
```json
{
  "results": [
    {
      "id": "<uuid>",
      "title": "...",
      "description": "...",
      "cover_image_url": "...",
      "action_type": "product|category|external",
      "action_target": "...",
      "sort_order": 1
    }
  ]
}
```

Not paginated.

### GET /api/v1/commerce/shop/categories 🟡 V2
**Auth**: none

#### Response 200
```json
{
  "results": [
    {"id": null, "name": "All", "slug": "all"},
    {"id": "<uuid>", "name": "...", "slug": "..."}
  ]
}
```

#### Diff from legacy
- Synthetic "All" category retained for mobile compatibility but uses `id: null` instead of `id: 0`
- Reconciled with `/api/v1/public/categories/` — these are the same source now (distinct in legacy)

---

### GET /api/v1/commerce/shop/products 🟡 V2
**Auth**: none
**Cursor-paginated**

#### Request (query)
```
?cursor=<>&limit=20
&category=<slug>     (default no filter)
&q=<search>           (OR on title/description/slug)
&seller_id=<uuid>     (filter by seller)
&ordering=-created_at|-view_count|price_amount
```

#### Response 200
```json
{
  "results": [
    {
      "id": "<uuid>",
      "title": "...",
      "description": "...",
      "price": {"amount": "29.99", "currency": "USD"},
      "alternate_prices": {
        "MP": "3000.0000",
        "MC": "30.0000"
      },
      "cover_image_url": "...",
      "stock_quantity": 100,
      "store": {
        "id": "<uuid>",
        "slug": "...",
        "name": "...",
        "owner": {"id": "<uuid>", "display_name": "..."}
      },
      "category": {"id": "<uuid>", "name": "...", "slug": "..."},
      "status": "active"
    }
  ],
  "cursor": {"next": "...", "prev": null}
}
```

#### Diff from legacy
- `price` nested object with currency (legacy was flat `price_amount` + `price_currency`)
- Alternate prices unified under `alternate_prices` map keyed by currency
- Legacy flat fields `meow_points_price` / `meow_credit_price` removed

---

### GET /api/v1/commerce/shop/products/{product_id} 🟡 V2
Single product, same shape + `created_at`, `updated_at`, `description_html`.

---

## 2. Cart

Cart is **persistent (DB-backed)**, not session-only.

### GET /api/v1/commerce/cart 🟡 V2
**Auth**: required
**Cursor-paginated**

#### Response 200
```json
{
  "results": [
    {
      "id": "<uuid>",
      "product": { /* same as product object */ },
      "created_at": "..."
    }
  ],
  "cursor": {"next": "...", "prev": null}
}
```

### POST /api/v1/commerce/cart 🟡 V2
**Auth**: required
**Idempotency**: yes (server-enforced: idempotent — adding same product twice is no-op)

#### Request
```json
{ "product_id": "<uuid>" }
```

#### Response 201
Cart item.

### DELETE /api/v1/commerce/cart/{item_id} 🟡 V2
**Auth**: required

#### Response 204
No body.

### GET /api/v1/commerce/cart/count 🟡 V2
**Auth**: required

#### Response 200
```json
{ "count": 3 }
```

#### Diff from legacy
- Replaces `/api/cart/items/` and `/api/cart/count/`
- Cursor pagination

---

## 3. Product Orders

### POST /api/v1/commerce/orders 🟡 V2
**Auth**: required
**Idempotency**: yes (required header)

#### Request
```json
{
  "product_id": "<uuid>",
  "quantity": 1,
  "shipping_address_id": "<uuid>",
  "payment_provider": "stripe",
  "payment_asset": "USD"
}
```

`payment_provider` ∈ {`stripe`, `blockchain`, `wallet`}.
- `blockchain` requires `blockchain_network` ∈ {`lbc`, `ltt`, `eth`(future), ...} + `currency` (token like `LBC`, `THB-LTT`, `USDT-ETH`).
- `wallet` uses `payment_asset` ∈ {`MP`, `MC`}.

`payment_asset` is the currency to charge in (e.g., `USD`, `LBC`, `THB`, `MP`, `MC`).

#### Response 201
```json
{
  "order_no": "ORD-...",
  "product_snapshot": {
    "id": "<uuid>",
    "title": "...",
    "cover_image_url": "...",
    "price_at_order": {"amount": "29.99", "currency": "USD"}
  },
  "quantity": 1,
  "amounts": {
    "subtotal": {"amount": "29.99", "currency": "USD"},
    "platform_fee": {"amount": "1.50", "currency": "USD"},
    "seller_receivable": {"amount": "28.49", "currency": "USD"}
  },
  "shipping_address_snapshot": { /* full address */ },
  "seller_store": {"id": "<uuid>", "slug": "...", "name": "..."},
  "status": "pending_payment",
  "payment": {
    "provider": "stripe",
    "intent_id": "pi_...",
    "client_secret": "pi_..._secret_..."
  },
  "expires_at": "...",
  "qr_payload": {...},
  "qr_text": "...",
  "created_at": "..."
}
```

#### Errors
- 422 `STOCK_INSUFFICIENT` (detail: `{requested, available}`)
- 422 `WALLET_INSUFFICIENT_BALANCE` (for MP/MC payment)
- 404 `PRODUCT_NOT_FOUND`
- 404 `SHIPPING_ADDRESS_NOT_FOUND`

#### Side effects
- Creates `ProductOrder` + linked `payments.Order` (`business_kind=PRODUCT`)
- Allocates stock (reserves quantity)
- Snapshots product + shipping address
- Emits `OutboxEvent`: `commerce.OrderCreated`

#### Diff from legacy
- Snapshots in nested objects (legacy was flat fields)
- Amounts (subtotal, platform_fee, seller_receivable) computed and exposed
- `payment_asset` is explicit currency code (legacy used opaque tags like `thb_ltt` that conflated chain + token; new platform separates `blockchain_network` from `currency`)

---

### GET /api/v1/commerce/orders 🟡 V2
**Auth**: required
**Cursor-paginated**

#### Request (query)
```
?cursor=<>&limit=20
&status=pending_payment,paid,shipping,completed
```

### GET /api/v1/commerce/orders/{order_no} 🟡 V2
Single order detail.

---

### POST /api/v1/commerce/orders/{order_no}/cancel 🟡 V2
**Auth**: required (buyer only)
**Idempotency**: yes

#### Request
```json
{ "reason": "..." }
```

#### Response 200
Updated order with `status: cancelled`.

#### Errors
- 409 `ORDER_NOT_CANCELLABLE` (already settled)

#### Side effects
- Releases stock
- Initiates refund if paid
- Emits `OutboxEvent`: `commerce.OrderCancelled`

---

### POST /api/v1/commerce/orders/{order_no}/confirm-received 🟡 V2
**Auth**: required (buyer only)

#### Side effects
- Transitions SHIPPING → COMPLETED
- Triggers seller payout via OutboxEvent

---

### GET /api/v1/commerce/orders/{order_no}/tracking 🟡 V2
**Auth**: required

#### Response 200
```json
{
  "order_no": "...",
  "carrier": "FedEx",
  "tracking_number": "...",
  "tracking_url": "https://...",
  "shipment_status": "in_transit",
  "estimated_delivery": "...",
  "last_update": "..."
}
```

---

### POST /api/v1/commerce/orders/{order_no}/refund-requests 🟡 V2
**Auth**: required (buyer)
**Idempotency**: yes

#### Request
```json
{
  "reason": "...",
  "requested_amount": "29.99"
}
```

#### Response 201
```json
{
  "id": "<uuid>",
  "order_no": "...",
  "status": "requested",
  "reason": "...",
  "requested_amount": {"amount": "29.99", "currency": "USD"},
  "admin_note": null,
  "resolved_at": null,
  "created_at": "..."
}
```

#### Errors
- 409 `REFUND_ALREADY_ACTIVE`
- 409 `ORDER_NOT_REFUNDABLE`

### GET /api/v1/commerce/orders/{order_no}/refund-requests 🟡 V2
List of refunds for an order.

---

## 4. QR Resolution

### POST /api/v1/commerce/payment-qr/resolve 🟡 V2
**Auth**: none (anonymous QR scan)

#### Request
```json
{ "qr_payload": {...} }
```

#### Response 200
```json
{
  "order_no": "...",
  "product_title": "...",
  "product_image_url": "...",
  "price": {"amount": "29.99", "currency": "USD"},
  "seller_name": "...",
  "payment_asset": "USD",
  "status": "pending_payment",
  "expires_at": "..."
}
```

#### Errors
- 404 `QR_INVALID_OR_EXPIRED`

---

## 5. Seller Application

### POST /api/v1/commerce/seller-applications 🟡 V2
**Auth**: required
**Idempotency**: yes

#### Request
```json
{
  "business_name": "...",
  "tax_id": "...",
  "reason": "..."
}
```

#### Response 201
```json
{
  "id": "<uuid>",
  "user_id": "<uuid>",
  "status": "pending",
  "business_name": "...",
  "tax_id": "...",
  "reason": "...",
  "submitted_at": "...",
  "reviewed_at": null,
  "reviewed_by": null,
  "rejection_reason": null
}
```

#### Errors
- 409 `SELLER_APPLICATION_ALREADY_EXISTS` (pending or approved)

### GET /api/v1/commerce/seller-applications/me 🟡 V2
**Auth**: required

#### Response 200
Latest application or 404.

---

## 6. Seller Store Management

### GET /api/v1/commerce/store/me 🟡 V2
**Auth**: required

#### Response 200
```json
{
  "id": "<uuid>",
  "slug": "...",
  "name": "...",
  "description": "...",
  "owner": {"id": "<uuid>", "display_name": "..."},
  "is_active": true,
  "stats": {
    "total_products": 12,
    "total_orders": 42,
    "total_revenue": {"amount": "1234.56", "currency": "USD"}
  },
  "created_at": "...",
  "updated_at": "..."
}
```

#### Errors
- 404 `STORE_NOT_FOUND` (user is not seller)

### POST /api/v1/commerce/store/me 🟡 V2
**Auth**: required (must have APPROVED SellerApplication)
**Idempotency**: yes

#### Request
```json
{
  "slug": "my-store",
  "name": "My Store",
  "description": "..."
}
```

#### Response 201
Store object.

#### Errors
- 403 `SELLER_NOT_APPROVED`
- 409 `STORE_ALREADY_EXISTS`
- 409 `STORE_SLUG_TAKEN`

### PATCH /api/v1/commerce/store/me 🟡 V2
Update name, description, is_active. **Slug not editable.**

---

## 7. Seller Product Management

### GET /api/v1/commerce/store/me/products 🟡 V2
**Auth**: required (seller)
**Cursor-paginated**

Returns all (draft + active) products.

### POST /api/v1/commerce/store/me/products 🟡 V2
**Auth**: required (seller)
**Idempotency**: yes
**Content-Type**: `multipart/form-data`

#### Request
```
title: string
description: string?
cover_image: <file>?
price_amount: decimal string
price_currency: "USD" | "THB" | ...
alternate_prices: JSON map {"MP": "3000.0000", "MC": "30.0000"}?
stock_quantity: integer
category_id: UUID?
status: "draft" | "active"
```

#### Response 201
Product object.

### GET /api/v1/commerce/store/me/products/{product_id} 🟡 V2
### PATCH /api/v1/commerce/store/me/products/{product_id} 🟡 V2
### DELETE /api/v1/commerce/store/me/products/{product_id} 🟡 V2

**DELETE is soft**: sets `status=archived`. Hard delete not allowed (preserves order history).

#### Diff from legacy
- No hard delete (legacy `DELETE` was hard, cascading to orders)
- `alternate_prices` as map (legacy was flat fields)

---

## 8. Seller Order Management

### GET /api/v1/commerce/store/me/orders 🟡 V2
**Auth**: required (seller)
**Cursor-paginated**

#### Request (query)
```
?cursor=<>&limit=20
&status=paid,shipping
```

#### Response 200
Cursor-paginated seller orders (full ProductOrder shape).

### GET /api/v1/commerce/store/me/orders/{order_no} 🟡 V2
Single order detail.

### POST /api/v1/commerce/store/me/orders/{order_no}/ship 🟡 V2
**Auth**: required (seller)
**Idempotency**: yes

#### Request
```json
{
  "carrier": "FedEx",
  "tracking_number": "...",
  "tracking_url": "https://...",
  "shipped_note": "..."
}
```

#### Response 200
Updated order (status = shipping).

#### Side effects
- Creates `ProductShipment`
- Emits `OutboxEvent`: `commerce.OrderShipped`

---

## 9. Public Store

### GET /api/v1/public/stores/{store_slug} 🟡 V2
**Auth**: none

Public storefront page.

### GET /api/v1/public/stores/{store_slug}/products 🟡 V2
**Auth**: none

Public product list for a store.

---

## 10. Shipping Addresses

⚠️ Legacy had two endpoints with different field names. New platform consolidates to ONE shape.

### GET /api/v1/commerce/shipping-addresses 🟡 V2
**Auth**: required

#### Response 200
```json
{
  "results": [
    {
      "id": "<uuid>",
      "recipient_name": "Jane Doe",
      "phone": "+66...",
      "street_address": "123 Main St",
      "city": "Bangkok",
      "state": "...",
      "postal_code": "10100",
      "country": "TH",
      "is_default": true,
      "created_at": "..."
    }
  ]
}
```

Not paginated (typical < 20 addresses).

#### Diff from legacy
- Field names unified: `recipient_name` (not `name` / `full_name`), `street_address` (not `address`)
- Single endpoint (legacy had `/api/account/shipping-addresses/` AND `/api/shipping-addresses/` with different field names)

⚠️ **Breaking for mobile**: must update field name parsing.

### POST /api/v1/commerce/shipping-addresses 🟡 V2
Create address.

### GET / PATCH / DELETE /api/v1/commerce/shipping-addresses/{address_id} 🟡 V2
Single address ops.

---

## 11. State machine

```
PENDING_PAYMENT ─(pay)─→ PAID ─(seller ship)─→ SHIPPING ─(buyer confirm)─→ COMPLETED ─(admin)─→ SETTLED (terminal)
       │                   │                       │
       └───────── (buyer cancel) ─────────────→ CANCELLED (terminal except SETTLED)

PAID|SHIPPING|COMPLETED ──(buyer request) → RefundRequest{REQUESTED} ──(admin) → APPROVED → REFUNDED (terminal)
                                                                          ↓
                                                                       REJECTED (terminal)
```

---

## 12. Outbox events emitted

| Event | When | Subscribers |
|---|---|---|
| `commerce.OrderCreated` | After POST /orders | analytics, seller notification |
| `commerce.OrderPaid` | After payment confirmed | seller notification, fulfillment |
| `commerce.OrderShipped` | After seller ship | buyer notification |
| `commerce.OrderCompleted` | After buyer confirm | seller payout, analytics |
| `commerce.OrderCancelled` | After cancel | refund processing |
| `commerce.OrderSettled` | After admin settle | accounting |
| `commerce.RefundRequested` | After buyer request | admin notification |
| `commerce.RefundApproved` | After admin approve | refund processing |
| `commerce.RefundRejected` | After admin reject | buyer notification |
| `commerce.RefundCompleted` | After mark-refunded | accounting, wallet credit |
| `commerce.SellerApplicationSubmitted` | After application | admin notification |
| `commerce.SellerApplicationApproved` | After admin approve | identity (`identity.CreatorPromoted`), notification |
| `commerce.SellerApplicationRejected` | After admin reject | applicant notification |
| `commerce.StoreCreated` | After store create | analytics |
| `commerce.ProductCreated` | After product create | search index |
| `commerce.ProductUpdated` | After product update | search index |
| `commerce.ProductArchived` | After soft delete | search index |
| `commerce.CartItemAdded` | After cart add | analytics |
| `commerce.CartItemRemoved` | After cart remove | analytics |

---

## 13. V1 vs V2 scope

| Feature | V1 | V2 | V3 |
|---|---|---|---|
| Shop catalog | | 🟡 | |
| Cart (DB-backed) | | 🟡 | |
| Product orders (Stripe / MP / MC) | | 🟡 | |
| Product orders (Blockchain — LBC) | | 🟡 | |
| Product orders (Blockchain — other networks) | | | 🔵 |
| Seller application + approval | | 🟡 | |
| Seller store management | | 🟡 | |
| Seller product management | | 🟡 | |
| Seller order shipment | | 🟡 | |
| Refund request (user) | | 🟡 | |
| Refund processing (admin) | | 🟡 | |
| Shipping addresses | | 🟡 | |
| QR resolution | | 🟡 | |
| Public storefront | | 🟡 | |
| Stripe Connect (auto seller payouts) | | | 🔵 |
