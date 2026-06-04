# Environments

Three environments, strict isolation, least-privilege access.

---

## 1. Map

| Environment | Purpose | Who writes | Who reads | Data |
|---|---|---|---|---|
| `local` | Per-developer dev | The developer | The developer | Seeded synthetic via `make seed` |
| `staging` | Pre-prod validation | Platform team (manual) + CI deploys | All engineers | Sanitized snapshot of production, refreshed weekly |
| `production` | Real users | CI only (after approval); platform team JIT for incidents | Platform team (via read replica when possible) | Real |

---

## 2. Access rules

- **Production database write**: nobody by default. For incidents, JIT (just-in-time) elevation via PagerDuty audit trail. All commands logged.
- **Production database read**: platform team only, via read replica when available.
- **Staging database**: engineers can read; only platform team can write directly (applications write normally through service accounts).
- **Local**: full control on developer's own machine.

Production ssh access is restricted to a defined set of admin user accounts. Every production ssh session logged.

---

## 3. Local development

```bash
make dev        # docker-compose up: postgres, redis, django, notification (V1)
make seed       # populate with synthetic test data
make migrate    # apply Django migrations
make proto-gen  # regenerate gRPC stubs
make test       # run pytest
make lint       # ruff + mypy + import-linter
```

`make seed` creates:
- 1 platform_config singleton row
- 20 users (mix of admins, creators, regular users)
- Wallets for each user with random non-zero balances
- Sample categories, products, drama series + episodes
- Sample notifications, audit logs

**Synthetic data only**. Never copy from production to local.

---

## 4. Staging

- Refreshed from a sanitized production snapshot weekly (Sunday 02:00 UTC)
- Sanitization removes: emails (replaced with `user-<id>@example.test`), payment provider IDs, real names, phone numbers, IP addresses, push tokens, session tokens
- Used for: migration dry-runs, release candidate validation, manual QA, performance tests
- Has its own observability stack mirroring production layout

### Refresh process
1. `pg_dump` production read-replica
2. Run sanitization SQL script (`ops/migration/sanitize_for_staging.sql`)
3. Restore to staging DB
4. Reset all OAuth/Stripe test keys
5. Validate (canary tests)

---

## 5. Production

- Deployment via CI/CD; no direct human deploys
- Schema migrations gated by manual approval after green CI
- Wallet-touching migrations require: 2 reviewers + maintenance window + backup verification
- Feature flags allow targeted rollout (see `feature-flags.md`)

---

## 6. Environment variables

Each environment has its own `.env` file deployed by Ansible to `/etc/bcp/<service>.env`. Format:

```bash
# /etc/bcp/django.env
DATABASE_URL=postgresql://bcp_django:<secret-ref>@localhost:5432/bcp_django
REDIS_URL=redis://localhost:6379/0
JWT_PRIVATE_KEY_PATH=/run/secrets/jwt-private.pem        # mounted from secrets manager
JWT_PUBLIC_KEY_URL=https://identity.bcp.example.com/.well-known/jwks.json
NOTIFICATION_SERVICE_ADDR=localhost:50051
CHAT_SERVICE_ADDR=                                         # empty until V2
LIVE_RUNTIME_ADDR=                                          # empty until V3
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
SENTRY_DSN=
ENVIRONMENT=local|staging|production
LOG_LEVEL=info
ALLOWED_HOSTS=api.bcp.example.com
DJANGO_SETTINGS_MODULE=config.settings.production
```

Secrets are not in env files directly; they're file paths to secrets-manager-mounted files. See `secrets.md`.

---

## 7. docker-compose profiles (local only)

```yaml
# ops/docker-compose.yml
services:
  postgres:           { profiles: [core] }
  redis:              { profiles: [core] }
  django:             { profiles: [core] }
  notification:       { profiles: [services] }
  chat:               { profiles: [services] }
  live_runtime:       { profiles: [services] }
  prometheus:         { profiles: [observability] }
  loki:               { profiles: [observability] }
  tempo:              { profiles: [observability] }
  grafana:            { profiles: [observability] }
```

Common invocations:
```bash
docker-compose --profile core up                                   # minimal stack
docker-compose --profile core --profile services up                # with gRPC
docker-compose --profile core --profile services --profile observability up  # everything
```

---

## 8. Configuration parity

Django settings are split:
```
django/config/settings/
├── base.py         # common (DEBUG=False, INSTALLED_APPS, MIDDLEWARE, ...)
├── local.py        # extends base, DEBUG=True, dummy services
├── staging.py      # extends base, production-like with debug toggles
├── production.py   # extends base, strict
└── test.py         # extends base, in-memory caches, fast hashing
```

`DJANGO_SETTINGS_MODULE` env var picks. No environment-specific behavior in `base.py`.

---

## 9. Differences in behavior by environment

| Behavior | local | staging | production |
|---|---|---|---|
| DEBUG | True | False | False |
| Password hashing iterations | low | full | full |
| Email sending | console backend | sandbox | live |
| Stripe keys | test | test | live |
| Blockchain | mock/devnet | testnet | mainnet |
| Trace sampling | 100% | 100% | 10% (+100% on errors) |
| Log level | debug | info | info |
| Feature flags | all-on (override) | per-prod-config | production set |
| CORS | permissive | restricted | restricted |
| Idempotency cache TTL | 1 min | 24h | 24h |

---

## 10. Anti-patterns

- ❌ Pointing local at production database
- ❌ Sharing production read credentials in Slack/Notion
- ❌ Editing production settings.py directly
- ❌ Running un-reviewed scripts against production DB
- ❌ Using "test" Stripe keys in production (or vice versa)
- ❌ Skipping staging for "small" changes
