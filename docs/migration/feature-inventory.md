# Feature Inventory

The single source of truth for **what we are porting from `django-auth-core`**, what we are redesigning, and what we are intentionally leaving behind.

Cross-references `legacy/mobile-api-contract-full.md` (snapshot of legacy backend's API surface) and `contracts/diff-from-legacy.md` (mobile cutover changes).

---

## How to use this file

- Every meaningful feature in the legacy system has a row
- **Decision** column: `keep` / `redesign` / `drop` / `defer`
- **Priority** column: V1 / V2 / V3 (mapping to 16-week plan; later weeks for V2/V3)
- **Reuse level**: `copy` / `adapt` / `rewrite`
- **Mobile**: mobile uses it (✅) / doesn't (❌)
- **Web**: same
- When decisions change, update this file in the same PR

---

## Identity / Auth

| Feature | Legacy location | New location | Decision | Priority | Reuse | Mobile | Web |
|---|---|---|---|---|---|---|---|
| Email + password registration | `accounts/views.py::register` | `apps/identity/services.py::register_user` | redesign | V1 | adapt | ✅ | ✅ |
| Login | `accounts/views.py::login` | `apps/identity/services.py::authenticate` | redesign | V1 | adapt | ✅ | ✅ |
| Refresh token | `accounts/jwt_views.py` | `apps/identity/services.py::refresh_token` | redesign | V1 | adapt | ✅ | ✅ |
| /me endpoint | `accounts/views.py::me` | `apps/identity/views.py::MeView` | redesign | V1 | adapt | ✅ | ✅ |
| Password change | `accounts/views.py::password_change` | `apps/identity/services.py::change_password` | keep | V1 | copy | ❌ | ✅ |
| Password reset (email) | (missing in legacy) | `apps/identity/services.py::request_password_reset` | redesign | V1 | rewrite | ✅ | ✅ |
| Password reset confirm | (missing in legacy) | `apps/identity/services.py::reset_password` | new | V1 | new | ✅ | ✅ |
| Email verification | (missing in legacy) | (TBD V2) | defer | V2 | new | — | — |
| 2FA | not present | new | new | V2 | new | — | — |
| Force logout (sessions) | not present | new | new | V1 | new | ✅ | — |
| Social login | `accounts/oauth/` if present | TBD | drop | — | — | ❌ | ❌ |
| User profile | `accounts/profile.py` | split: `identity.User` + `identity.CreatorProfile` | redesign | V1 | adapt | ✅ | ⚠️ |
| Avatar upload | `accounts/avatar.py` | `apps/identity/services.py::set_avatar` | keep | V1 | copy | ✅ | ⚠️ |
| KYC profile + 4-state machine | `accounts/kyc*` | `apps/identity/kyc/` | redesign | V1 | adapt | ✅ | ❌ |
| KYC document upload | `accounts/kyc_views.py::KycDocumentUploadAPIView` | `apps/identity/services.py::upload_kyc_document` | keep | V1 | copy | ✅ | ❌ |
| Daily login reward | `MeowPointService.grant_daily_login_reward` (in login response) | Decoupled: explicit endpoint + Outbox grant | redesign | V1 | rewrite | ✅ | ❌ |

---

## Economy

| Feature | Legacy location | New location | Decision | Priority | Reuse | Mobile | Web |
|---|---|---|---|---|---|---|---|
| MeowPointWallet → PointWallet | `wallets/meow_points*` | `apps/economy/models.py::PointWallet` | redesign | V1 | adapt | ✅ | ❌ |
| MeowCreditWallet → CreditWallet | `wallets/meow_credit*` | `apps/economy/models.py::CreditWallet` | redesign | V1 | adapt | ✅ | ❌ |
| credit/debit operations | `wallets/services.py` | `apps/economy/services.py::credit, debit` | rewrite | V1 | rewrite | ✅ | ❌ |
| WalletLedger with invariants | `wallets/meow_point_ledger`, `meow_credit_ledger` | `apps/economy/models.py::WalletLedger` | redesign | V1 | adapt | indirect | ❌ |
| balance_after invariant | partial in legacy | strict in new | new | V1 | new | ✅ | ❌ |
| idempotency_key UNIQUE | missing in legacy | strict in new | new | V1 | new | ✅ | ❌ |
| SELECT FOR UPDATE | missing in legacy | strict in new | new | V1 | new | ✅ | ❌ |
| Wallet history endpoint | `meow_points_views.py::ledger` | `apps/economy/views.py::LedgerHistoryView` | keep | V1 | copy | ⚠️ | ❌ |
| Balance API (aggregate) | `user_balance_urls.py` | `apps/economy/views.py::WalletDetailView` | redesign | V1 | adapt | ✅ | ❌ |
| MP packages + purchase | `MeowPointPackage*`, `point-orders` | 🚫 **removed** (MP is earned-only now) | drop | — | — | ❌-was-✅ | ❌ |
| MC packages + recharge | `MeowCreditPackage*`, `credit-recharges` | `apps/economy/services.py::create_credit_recharge` | redesign | V1 | adapt | ✅ | ❌ |
| Credit redeem | `MeowCreditRedeem*` | `apps/economy/services.py::request_credit_redeem` | redesign | V2 | adapt | ❌ | ❌ |
| Daily login reward grant | embedded in login | `apps/economy/services.py::claim_daily_login_reward` | rewrite | V1 | rewrite | ✅ | ❌ |
| Legacy wallet prototype endpoints | `wallet_prototype_urls.py` | 🚫 **not implemented** | drop | — | — | ❌ | ❌ |

---

## Payments

| Feature | Legacy location | New location | Decision | Priority | Reuse | Mobile | Web |
|---|---|---|---|---|---|---|---|
| Order model (state machine) | `payments/order.py` | `apps/payments/models.py::Order` | redesign | V1 | adapt | ⚠️ | ❌ |
| Stripe adapter | (missing) | `apps/payments/adapters/stripe.py` | new | V1 | new | ✅ | ❌ |
| Blockchain adapter (generic) | partial (LBC only) | `apps/payments/adapters/blockchain/base.py` | redesign | V1 | rewrite | ✅ | ❌ |
| LBC backend | `LbryDaemonClient` | `apps/payments/adapters/blockchain/lbc.py` | redesign | V1 | adapt | ⚠️ | ❌ |
| LTT backend (THB-LTT) | (missing) | `apps/payments/adapters/blockchain/ltt.py` | new | V1 | new | TBD | ❌ |
| Other chain backends (ETH, TRON, ...) | n/a | future | defer | V3 | new | — | — |
| Stripe webhook handler | (missing) | `apps/payments/views.py::StripeWebhookView` | new | V1 | new | indirect | indirect |
| Blockchain webhook handler | (missing) | `apps/payments/views.py::BlockchainWebhookView` | new | V2 | new | indirect | indirect |
| Manual verify-now flow | per-domain (mp/mc/membership) | unified `apps/payments/services.py::verify_blockchain_payment` | redesign | V1 | adapt | ✅ | ❌ |

---

## Content — Live

| Feature | Legacy location | New location | Decision | Priority | Reuse | Mobile | Web |
|---|---|---|---|---|---|---|---|
| LiveStream model + state machine | `live/models.py::LiveStream` | `apps/content/live/models.py::LiveStream` | redesign | V3 | adapt | ✅ | ✅ |
| Stream key issuance | `live/services.py` | `services/live_runtime/` | redesign | V3 | adapt | ✅ | ✅ |
| Ant Media REST integration | `AntMediaLiveAdapter` | `services/live_runtime/adapters/ant_media.py` | redesign | V3 | adapt | indirect | indirect |
| WebSocket chat (Django Channels) | `consumers.py` | `services/live_runtime/ws_gateway.py` | rewrite | V3 | rewrite | ✅ | ⚠️ |
| Chat REST history | `live_urls.py::messages` | `apps/content/live/views.py` | keep | V3 | copy | ✅ | — |
| GiftTransaction (live) | `gift_views.py::LiveGiftSendAPIView` | `apps/content/live/services.py::send_live_gift` + Outbox → gRPC broadcast | redesign | V3 | adapt | ✅ | ❌ |
| Live products (linked to stream) | `LiveStreamProduct*` | `apps/content/live/models.py::LiveStreamProductBinding` | keep | V3 | copy | ✅ | — |
| Live payment methods (creator-side) | `live_urls.py::payment-methods` | `apps/content/live/models.py::LivePaymentMethod` | keep | V3 | copy | ❌ | ❌ |
| Thumbnail capture | `live/thumbnail.py` | `services/live_runtime/services.py::capture_thumbnail` | redesign | V3 | adapt | indirect | indirect |

---

## Content — Drama

| Feature | Legacy location | New location | Decision | Priority | Reuse | Mobile | Web |
|---|---|---|---|---|---|---|---|
| DramaSeries CRUD | `drama_views.py` (27KB single file) | `apps/content/drama/` (split per viewset) | redesign | V2 | adapt | ✅ | ❌ |
| DramaEpisode | `drama_views.py` | `apps/content/drama/models.py::DramaEpisode` | redesign | V2 | adapt | ✅ | ❌ |
| 4 unlock methods (free/MP/MC/membership) | `DramaUnlockAPIView` | `apps/content/drama/services.py::unlock_episode` | redesign | V2 | adapt | ✅ | ❌ |
| Watch progress (series + episode scope) | `DramaProgressUpsertAPIView` | `apps/content/drama/services.py::upsert_progress` | keep | V2 | copy | ✅ | ❌ |
| Favorites | `DramaFavoriteAPIView` | `apps/content/drama/services.py::toggle_favorite` | keep | V2 | copy | ✅ | ❌ |
| Comments (threaded) | `DramaCommentListCreateAPIView` | `apps/content/drama/views.py::CommentViewSet` | keep | V2 | copy | ✅ | ❌ |
| Share tracking | `DramaShareAPIView` | `apps/content/drama/services.py::track_share` | keep | V2 | copy | ✅ | ❌ |
| View tracking (dedup) | `DramaSeriesViewTrackAPIView` | `apps/content/drama/services.py::track_view` | keep | V2 | copy | ✅ | ❌ |
| Drama gift send | `DramaGiftSendAPIView` | `apps/content/drama/services.py::send_drama_gift` | redesign | V2 | adapt | ✅ | ❌ |
| Creator drama management | `creator_drama_urls.py` | `apps/content/drama/views.py::CreatorDramaViewSet` | redesign | V3 | rewrite | ❌ | ❌ |

---

## Content — Video

| Feature | Legacy location | New location | Decision | Priority | Reuse | Mobile | Web |
|---|---|---|---|---|---|---|---|
| Public video catalog | `public_video_urls.py` | `apps/content/video/views.py::PublicVideoViewSet` | redesign | V2 | adapt | ✅ | ✅ |
| Video upload (creator) | `video_urls.py::upload` | `apps/content/video/services.py::upload_video` | redesign | V2 | adapt | ⚠️ | ✅ |
| Likes + comments + shares + views | scattered | `apps/content/video/services.py` | redesign | V2 | adapt | ✅ | ✅ |
| Interaction summary | `interaction-summary` | `apps/content/video/views.py::InteractionSummaryView` | keep | V2 | copy | ✅ | ✅ |
| Video gift send | `PublicVideoGiftSendAPIView` | `apps/content/video/services.py::send_video_gift` | redesign | V2 | adapt | ✅ | ❌ |
| Thumbnail generation | `generate_video_thumbnail()` | `apps/content/video/tasks.py::generate_thumbnail` | keep | V2 | copy | indirect | indirect |
| Regenerate thumbnail | `VideoRegenerateThumbnailAPIView` | `apps/content/video/views.py::RegenerateThumbnailView` | keep | V2 | copy | ⚠️ | ✅ |
| Real transcoding | (missing) | (V3+ defer) | defer | V3 | new | — | — |
| CDN integration | (missing) | (V3+ defer) | defer | V3 | new | — | — |

---

## Commerce

| Feature | Legacy location | New location | Decision | Priority | Reuse | Mobile | Web |
|---|---|---|---|---|---|---|---|
| Shop banners | `ShopBannerListAPIView` | `apps/commerce/views.py::ShopBannerViewSet` | keep | V2 | copy | ✅ | ❌ |
| Shop categories | `ShopCategoryListAPIView` | `apps/commerce/views.py::ShopCategoryViewSet` | redesign | V2 | adapt | ✅ | ❌ |
| Shop products (catalog) | `ShopProductListAPIView` | `apps/commerce/views.py::ProductListView` | redesign | V2 | adapt | ✅ | ❌ |
| Cart (DB-backed) | `cart_urls.py` (skeleton) | `apps/commerce/models.py::Cart` + service | rewrite | V2 | rewrite | ✅ | ❌ |
| Product orders | `product_order_urls.py` | `apps/commerce/services.py::create_product_order` | redesign | V2 | adapt | ✅ | ❌ |
| Order state machine (5 states) | `ProductOrder.STATUS_*` | `apps/commerce/models.py::ProductOrder` | redesign | V2 | adapt | ✅ | ❌ |
| QR resolve | `PaymentQRResolveAPIView` | `apps/commerce/views.py::PaymentQRResolveView` | keep | V2 | copy | ✅ | ❌ |
| Order tracking | `ProductOrderTrackingAPIView` | `apps/commerce/views.py::TrackingView` | keep | V2 | copy | ✅ | ❌ |
| Confirm received (buyer) | `ProductOrderConfirmReceivedAPIView` | `apps/commerce/services.py::confirm_received` | keep | V2 | copy | ✅ | ❌ |
| Refund request | `ProductRefundRequestListCreateAPIView` | `apps/commerce/services.py::request_refund` | redesign | V2 | adapt | ✅ | ❌ |
| Refund admin actions | `admin_urls.py::refund_*` | `apps/commerce/admin.py` | redesign | V2 | adapt | 🛠 | 🛠 |
| Seller application | `seller_application_urls.py` | `apps/commerce/services.py::submit_seller_application` | keep | V2 | copy | ✅ | ❌ |
| Seller store management | `store_urls.py` | `apps/commerce/services.py::manage_store` | keep | V2 | copy | ✅ | ❌ |
| Seller product CRUD | `store_urls.py::products` | `apps/commerce/services.py::manage_seller_products` | keep | V2 | copy | ✅ | ❌ |
| Seller order management | `creator_shop_urls.py::orders` | `apps/commerce/services.py::manage_seller_orders` | keep | V2 | copy | ✅ | ❌ |
| Mark shipped | `SellerProductOrderShipAPIView` | `apps/commerce/services.py::mark_shipped` | keep | V2 | copy | ✅ | ❌ |
| Shipping address (V2 consolidated) | `shipping_urls.py` AND `account_urls.py` (two!) | `apps/commerce/services.py::manage_shipping_addresses` (single endpoint) | rewrite | V2 | rewrite | ✅ | ❌ |

---

## Membership

| Feature | Legacy location | New location | Decision | Priority | Reuse | Mobile | Web |
|---|---|---|---|---|---|---|---|
| Membership plans | `MembershipPlanListAPIView` | `apps/membership/views.py::PlanListView` | redesign | V2 | adapt | ✅ | ❌ |
| One-shot order create | `MembershipOrderCreateAPIView` | `apps/membership/services.py::create_membership_order` | redesign | V2 | adapt | ✅ | ❌ |
| Manual blockchain verify (6 steps) | `ManualMembershipPayment*` | `apps/membership/services.py::manual_*` | redesign | V2 | adapt | ✅ | ❌ |
| GET /me current membership | `MembershipMeAPIView` | `apps/membership/views.py::MeView` | keep | V2 | copy | ✅ | ❌ |
| Billing subscription (recurring) | `BillingSubscription*` | `apps/membership/services.py::create_subscription` | redesign | V2 | adapt | ⚠️ | ❌ |
| Stripe subscription (auto-renew) | (missing) | `apps/membership/adapters/stripe_subscription.py` | new | V2 | new | TBD | ❌ |
| Past-due dunning | (missing) | (V3 defer) | defer | V3 | new | — | — |

---

## Cross-cutting / Infrastructure

| Feature | Legacy location | New location | Decision | Priority | Reuse | Mobile | Web |
|---|---|---|---|---|---|---|---|
| Email sending | scattered Django EmailBackend | `services/notification/` gRPC | redesign | V1 | rewrite | indirect | indirect |
| SMS sending | (missing) | `services/notification/` V2 | new | V2 | new | — | — |
| Push notifications | (missing) | `services/notification/` V2 | new | V2 | new | — | — |
| Live chat WebSocket | `consumers.py` (Django Channels) | `services/live_runtime/` gRPC + WS gateway | rewrite | V3 | rewrite | ✅ | ⚠️ |
| 1:1 / group chat | (missing) | `services/chat/` gRPC | new | V2 | new | TBD | TBD |
| Audit logging | inconsistent / scattered | `apps/audit/` | new | V1 | new | indirect | indirect |
| Outbox event bus | (missing) | `apps/events/` | new | V1 | new | indirect | indirect |
| Branding | `settings.py` constants | `apps/platform_config/` singleton | redesign | V1 | rewrite | ✅ | ✅ |
| Feature flags | (missing) | `apps/platform_config/feature_flags` | new | V1 | new | indirect | indirect |
| Admin panel | Django admin | Django admin | keep | V1 | copy | 🛠 | 🛠 |

---

## Library / Activity

| Feature | Legacy location | New location | Decision | Priority | Reuse | Mobile | Web |
|---|---|---|---|---|---|---|---|
| History tab (mixed drama + video) | `library_urls.py::history` | `apps/identity/views/library.py::HistoryView` | redesign | V1 | adapt | ✅ | ❌ |
| Liked tab (videos) | `library_urls.py::liked` | `apps/identity/views/library.py::LikedView` | keep | V1 | copy | ✅ | ❌ |
| Purchased tab (mixed drama/order/membership) | `library_urls.py::purchased` | `apps/identity/views/library.py::PurchasedView` | redesign | V1 | adapt | ✅ | ❌ |
| Gifts sent tab | `library_urls.py::gifts/sent` | `apps/identity/views/library.py::GiftsSentView` | keep | V1 | copy | ✅ | ❌ |
| Gifts received tab | `library_urls.py::gifts/received` | same | keep | V1 | copy | ✅ | ❌ |
| Downloads tab | not present | not in scope | drop | — | — | ❌ | ❌ |

---

## Frontend usage matrix (summary)

Cross-references contracts/diff-from-legacy.md. Both frontends consume:
- Auth (full)
- Public videos (mostly)
- Public creators / users
- Follow (mobile uses new path; web uses legacy until migration)

Mobile-only:
- Drama, Wallets (MP/MC), Library, Shop, Cart, Orders, Store, Membership, KYC, Daily reward, Gifts

Web-only:
- My Videos (uploaded video management)
- Live (V3 — both will use; V1-V2 web stays on legacy backend)

Neither:
- channel_urls / creator_live_urls / `_url` legacy fields → confirmed deletable
- Push notifications → defer
- Real transcoding → defer

---

## Open decisions before W4

- [ ] OAuth / social login: drop confirmed or defer? Pull user analytics to verify.
- [ ] Refund flow: V2 priority OR V3 if Stripe Connect needs setup first
- [ ] Drama / Video feature subsets: which views actually used by mobile? Pull endpoint analytics.
- [ ] Leaderboards (gift): in-memory enough or persistent?
- [ ] Stripe subscription: V2 priority OR V3?
- [ ] Live products feature: needed in V3 or defer to V4?
