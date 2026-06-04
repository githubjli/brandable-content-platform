# ADR-0001: Single brand, deferred tenancy

## Status
Accepted

## Context
The product positions itself as "brandable," but V1 launches with **one brand**. Building row-level multi-tenancy (Brand table, middleware, `brand_id` on every table, tenant-scoped managers) up front would cost ~1 week of engineering time, add cognitive overhead to every model and query for years, and may never pay off.

## Decision
V1 ships single-brand. No `Brand` table. No `brand_id` columns on business tables. No tenancy middleware. Branding lives in a singleton `PlatformConfig` row (see contracts/platform-config.md).

To keep future multi-brand migration cheap, pay a small upfront tax:

1. UUID primary keys everywhere (no auto-incrementing integers).
2. Branding read via `get_platform_config()`, never hardcoded.
3. Service signatures stateless w.r.t. brand context (no `brand_id` arg that's always ignored).
4. No brand-specific strings in code identifiers (`MeowPoint` → `PointWallet` in module names; the brand-specific word "Meow" appears only in `PlatformConfig`).
5. JWTs include an `aud` claim, reserved for future brand identifiers.

## Migration trigger
Multi-tenancy work is triggered when **either** condition holds:
- A second brand is contractually committed with launch <= 3 months out, or
- A B2B2C scenario emerges requiring per-tenant branding in a single deployment.

"Maybe someday we'll have more brands" is **not** a trigger.

## Consequences

**Good**
- V1 ships ~1 week sooner
- Every developer reads simpler models/queries
- No risk of tenancy-bypass security bugs in V1

**Bad**
- When trigger hits, we spend ~1 sprint: add `Brand` table, add nullable `brand_id` to tenant-scoped tables, backfill, flip non-null, add middleware
- The "small upfront tax" requires discipline (we will catch slips in code review and import-linter)

**Neutral**
- Mobile/web get a `Brand` concept only when multi-brand is real

## Anti-decision
We do NOT use Django's `django-tenants` package or schema-per-tenant. Pre-emptive multi-tenancy infrastructure is the most common over-engineering pattern in early SaaS; we explicitly refuse it.
