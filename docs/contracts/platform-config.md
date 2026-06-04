# Platform Config Contract

Covers: brand-wide configuration (branding, theme, app config). Singleton in V1 (single-brand); will become per-brand table in future multi-brand.

**App**: `apps/platform_config/`
**Related ADR**: [ADR-0001 Single brand, deferred tenancy](../adr/0001-single-brand-deferred-tenancy.md)

---

## 1. The singleton

V1 has exactly **one row** in `PlatformConfig` table. All code reads via:

```python
from apps.platform_config.services import get_platform_config

config = get_platform_config()
print(config.site_name)
```

The function caches the row (TTL 60s). Cache invalidates on PATCH.

---

## 2. Public endpoint (client-facing)

### GET /api/v1/platform/config 🟢 V1
**Auth**: none
**Cached**: yes (CDN-friendly, 5 min TTL)

#### Response 200
```json
{
  "site": {
    "name": "Brandable Platform",
    "tagline": "Watch, share, connect",
    "logo_url": "https://...",
    "favicon_url": "https://...",
    "primary_color": "#FF6B35",
    "secondary_color": "#004E89",
    "support_email": "support@example.com"
  },
  "client": {
    "min_supported_app_version": "2.0.0",
    "force_upgrade_below": "1.5.0"
  },
  "features": {
    "live_enabled": true,
    "drama_enabled": true,
    "commerce_enabled": true,
    "membership_enabled": true,
    "registration_open": true
  },
  "providers": {
    "stripe_publishable_key": "pk_..."
  },
  "links": {
    "terms_url": "https://...",
    "privacy_url": "https://...",
    "help_url": "https://..."
  },
  "generated_at": "2026-06-04T10:00:00Z"
}
```

#### Diff from legacy
**New endpoint.** Legacy hardcoded branding in `settings.py`. Mobile must now read this at app start (and re-read on stale).

---

## 3. Admin endpoint

### GET /api/v1/admin/platform/config 🛠 Admin
**Auth**: required + admin
Same shape as public, plus internal fields (provider secret refs, feature flag config).

### PATCH /api/v1/admin/platform/config 🛠 Admin
**Auth**: required + admin
**Idempotency**: yes

Update any subset of fields.

#### Side effects
- Updates singleton row
- Invalidates cache
- Emits `OutboxEvent`: `platform.ConfigUpdated`
- Writes `AuditLog` (high-sensitivity change)

---

## 4. Field types & validation

| Field | Type | Validation |
|---|---|---|
| `site.name` | string | 1-100 chars |
| `site.primary_color` | string | hex `#RRGGBB` |
| `client.min_supported_app_version` | string | semver |
| `features.*_enabled` | boolean | — |
| `providers.stripe_publishable_key` | string | starts with `pk_` |
| `links.*_url` | string | valid URL |

Secrets (Stripe secret key, JWT private key, etc.) are **NOT** in this table. They live in the secrets manager (per `docs/secrets.md`).

---

## 5. Cache strategy

- Server-side: `get_platform_config()` caches in-process for 60s
- HTTP: `Cache-Control: public, max-age=300` on response (5 min CDN cache)
- Updates trigger cache purge via PATCH endpoint

---

## 6. Future multi-brand

When triggered (per ADR-0001):
- Add `Brand` table; backfill one row from singleton
- Add nullable `brand_id` to `PlatformConfig`; backfill; flip non-null
- API path becomes `/api/v1/platform/{brand_slug}/config` OR resolved by `Host` header
- `get_platform_config()` takes brand context

V1 implements with this future in mind: `get_platform_config()` does **not** take brand context, but its callers don't assume singleton.

---

## 7. Outbox events emitted

| Event | When |
|---|---|
| `platform.ConfigUpdated` | After PATCH |
| `platform.FeatureToggled` | When feature flag changes |
