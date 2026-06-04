# Antipatterns

Read this on day one. Every item here is a real lesson, either from `django-auth-core` history or from the broader Django ecosystem. Each costs a future incident if violated.

---

## Must never do

1. **Do not create placeholder app folders** for modules we are not building in V1. `apps/creator/`, `apps/analytics/`, `apps/chat/` (Django app), `apps/branding/`, `apps/tenancy/`, `apps/log/` do not exist.
2. **Do not put business logic in views.** Views do: parse input → call service → serialize output → translate exceptions. Per ADR-0002.
3. **Do not call `task.delay()` from business code.** All async side effects go through `OutboxEvent` + dispatcher (per ADR-0003).
4. **Do not let gRPC services read Django's database directly.** Not even read-only. Cross-process data goes via RPC or Outbox events. Per ADR-0006.
5. **Do not write gRPC services in Go in V1.** Python + grpcio is sufficient. Switch later when load proves it. Per ADR-0007.
6. **Do not build the three gRPC services in parallel.** Notification first as canary, then Chat, then Live Runtime. Per ADR-0006.
7. **Do not hardcode brand-specific strings.** `MeowPoint` → `PointWallet` in code. Specific brand strings live only in `PlatformConfig`. Per ADR-0001.
8. **Do not skip `idempotency_key`.** Money-related writes, event handlers, external callbacks all require it.
9. **Do not call `Model.objects.create` directly in tests.** Use `factory_boy`. Schema drift will kill you otherwise.
10. **Do not write code before the ADR.** Major decisions go ADR → review → code, not the other way around.
11. **Do not bypass `EconomyService.credit/debit`** to write `WalletLedger` rows. Enforced by import-linter. Per ADR-0004.
12. **Do not audit asynchronously.** `record_audit()` MUST run in the same transaction as the business write. Per `contracts/audit.md`.
13. **Do not skip `record_audit()` on sensitive admin actions.** See `contracts/audit.md §5` for the required-audit catalog.
14. **Do not use float for money.** `Decimal(18,4)` always.
15. **Do not let the same currency ticker mean different things across providers** without disambiguating. Use the `(provider, network, currency)` tuple. Per `contracts/conventions.md §7`.

---

## Easy mistakes (and how to prevent)

| Trap | Prevention |
|---|---|
| `trace_id` not propagated to Celery / gRPC | telemetry middleware on day 1 |
| JWT public key rotation forgotten | JWKS endpoint + cached refresh + schedule reminder |
| Proto field number reused | `reserved` declarations + CI check (`buf breaking`) |
| Outbox dispatcher single point of failure | leader election (advisory lock) + monitoring lag |
| Legacy account import case collisions | dry-run report first; normalize emails in importer |
| Migration locks large table | `CREATE INDEX CONCURRENTLY` |
| Wallet race conditions | `SELECT FOR UPDATE` + `idempotency_key` UNIQUE |
| docker-compose slows over time | profile split (core / services / observability) from W1 |
| Feature flag becomes permanent | every flag has `expected_removal_date` at creation |
| Audit log "we'll write it later" | grep for `# TODO: audit` in PRs; block merge |
| MP and MC confused | distinct table names + import-linter |
| Stripe / blockchain confused | distinct `payment_provider` enums + adapter base class |

---

## Patterns from legacy that we are NOT carrying forward

### Hidden side effects on GET endpoints
Legacy `GET /api/meow-points/orders/` auto-credited paid purchases as a side effect of reading. New platform separates read from write. Always.

### Lazy wallet creation
Legacy created wallets on first access with a silent warning log. New platform creates explicitly at registration. Silent surprise behavior is forbidden.

### MP packages purchase via blockchain
Legacy allowed buying MP with LBC. New platform: MP is earned-only. See `contracts/deprecated.md` and `contracts/economy.md §4`.

### Daily reward baked into login response
Legacy `POST /api/auth/login/` returned `daily_login_reward` synchronously, with `MeowPointService.grant_daily_login_reward()` running in the request lifecycle. New platform decouples: explicit endpoint OR async grant via Outbox.

### Two different shipping-address endpoints with different field names
`/api/account/shipping-addresses/` vs `/api/shipping-addresses/` — different field names. New platform: single endpoint, unified field names.

### Fixed-gift mode with 2-second dedup window
Legacy Live gift endpoint supported `gift_id + quantity` mode in addition to `amount + payment_method`. New platform: amount mode only. `gift_code` is a display hint.

### One Django app for everything
Legacy `accounts/` has 62 models, 165KB views.py, 3,932 lines of services.py. New platform: 11 Django apps with import-linter rules between them.

### Channel as a "concept"
Legacy had `channel_urls.py`, "channel_id", "subscriber_count" aliases. Mobile and web confirmed unused. New platform: there are users, there are creators (extension of user); there is no channel.

### `linked_wallet_id`, `primary_user_address`, `wallet_link_status`
Legacy User model had blockchain-prototype residue. New platform: drop entirely.

### Three pagination styles
Legacy: `?page=N`, `?after_id=N`, `?limit=N&offset=N`. New platform: cursor only.

### Three error response styles
Legacy: `{detail: "..."}`, `{field: ["..."]}`, `{code: "...", detail: "..."}`. New platform: single envelope `{error: {code, message, detail}}`.

---

## Anti-patterns from broader Django ecosystem we avoid

- **Strict DDD with separate entity/repository/aggregate layers**: see ADR-0002. Adds glue without payoff in Django.
- **django-tenants for multi-tenancy**: see ADR-0001. Premature abstraction.
- **Kafka as the event log**: see ADR-0003. Postgres outbox is sufficient at our scale.
- **k8s for V1 deployment**: see ADR-0008. systemd is sufficient and operationally simpler.
- **Django signals for cross-app events**: signals are sync within request and cannot span processes. Use Outbox.
- **Auto-magic everything**: explicit > implicit, even at the cost of more lines.
- **Premature optimization**: measure first.
- **"Microservice-ready monolith" that's neither**: we are a modular monolith with three out-of-process services. That's the architecture.

---

## When in doubt

Open an ADR draft, request review, then write code. ADRs are cheap; bad architecture is expensive.

If the decision feels small enough to skip an ADR: open an issue, link the discussion, decide in the PR. Don't decide in private chat.
