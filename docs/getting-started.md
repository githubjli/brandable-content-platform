# Getting Started

Week 1 is **infrastructure only**. No business code. Goal: a clean baseline that the next 15 weeks compound on.

---

## Prerequisites

- Docker + Docker Compose
- Python 3.12+
- `uv` (or `poetry`) — pick one and lock it in `pyproject.toml`
- Linux/macOS workstation (Windows via WSL2)
- SSH access to staging server (for deploy testing)

---

## Week-1 checklist (15 items)

- [ ] Initialize monorepo with the directory skeleton from `architecture/modules.md` (empty folders committed as placeholders only for apps we ARE building)
- [ ] `pyproject.toml` with `uv` / `poetry` lockfile committed
- [ ] Django 5 project scaffold with split settings: `base.py`, `local.py`, `staging.py`, `production.py`, `test.py`
- [ ] `docker-compose.yml` brings up PostgreSQL 15 + Redis 7 (`core` profile)
- [ ] `Makefile` targets: `dev`, `test`, `lint`, `proto-gen`, `migrate`, `seed`
- [ ] `pre-commit` config: ruff (format + lint), mypy, django-check, gitleaks
- [ ] CI pipeline: pytest + coverage + import-linter + django-migration-linter + proto drift check + gitleaks
- [ ] OpenTelemetry SDK wired into Django; backend decision (Grafana stack vs Datadog) committed as ADR if changing from ADR-0010
- [ ] JWT library + JWKS endpoint scaffold (returns static dev key until Identity is built)
- [ ] ADRs 0001-0010 drafted and committed (use the templates in `adr/`)
- [ ] All `contracts/` documents committed unchanged from the design phase
- [ ] `GET /api/v1/health` endpoint returns `{"status": "ok", "trace_id": "..."}`
- [ ] Empty `services/notification/` gRPC server with one RPC: `Ping(Empty) → Pong`
- [ ] `docker-compose up` (core profile) brings up: postgres + redis + django + notification (empty shell). All healthy.
- [ ] **Ansible playbook for staging deployment**: `git pull` + `systemctl restart` cycle works against a real server with an empty Django

---

## How to know week 1 is done

Run these four commands; all pass:

```bash
make lint    # ruff + mypy + import-linter clean
make test    # pytest passes, coverage gate met
make dev     # docker-compose up: all services healthy
curl http://localhost:8000/api/v1/health
# Returns 200 with valid trace_id

# Deploy to staging
ansible-playbook ops/ansible/deploy.yml --extra-vars "env=staging branch=main"
# Successfully deploys; staging health endpoint returns 200 with trace_id
# The same trace_id appears in your observability backend (Tempo)
```

---

## Day-to-day commands (always via `make`)

`make` is the only supported entry point for linting and tests — it runs the
tools with the correct working directory and flags. **Do not run `mypy` /
`lint-imports` / `pytest` directly from the repo root** (mypy and import-linter
must run from `django/` or they fail). Full rules + the database/test workflow
live in [CONTRIBUTING.md](../CONTRIBUTING.md).

```bash
make dev          # postgres + redis + django (docker-compose core profile)
make lint         # ruff (format+check) + mypy + import-linter
make test         # full pytest suite (runs on PostgreSQL, not sqlite)
make test-fast    # pytest without coverage — quicker inner loop
make format       # auto-format with ruff
```

Tests run against PostgreSQL and reuse the test DB (`--reuse-db`). After you
regenerate a migration under the same name, rebuild the schema once with
`cd django && ../.venv/bin/pytest --create-db`. See CONTRIBUTING.md §3.

---

## Weeks 2-16 at a glance (architecture-first)

| Week | Deliverable |
|---|---|
| 2 | Django baseline (settings layering, errors lib, pagination, logging, telemetry, health) |
| 3 | Proto pipeline + OpenTelemetry across-process + JWT public key distribution + Notification `Ping` canary |
| 4-5 | Identity V1 (with legacy account import + password hash compatibility) |
| 6-7 | Economy V1 (with legacy wallet balance import + ledger) |
| 8 | Events V1 (Outbox + Dispatcher + DLQ) + Audit V1 |
| 9 | Payments V1 (Stripe + Blockchain LBC + LTT backends) |
| 10 | Commerce V1-AVS: minimal ProductOrder purchase chain (Commerce → Payments → Economy → Events → Audit) |
| 11 | PlatformConfig + Branding API + Notification email canary |
| 12-13 | ChatService early launch: direct room, send/list messages, WebSocket gateway |
| 14 | LiveRuntimeService skeleton: auth/tracing, Ant Media smoke, CreateStream/GetWatchConfig, presence scaffold |
| 15 | Membership Django boundary scaffold + active-membership import support (no Membership gRPC service) |
| 16 | Final migration rehearsal + mobile cutover readiness gate |

V2 (post-cutover): content (drama, video catalog), full commerce (shop, cart, seller, shipping, refunds), user-facing membership orders/subscriptions, full notification channels.
V3: Live Runtime full feature set, live gift broadcast, real transcoding, push-notification maturity, additional blockchain networks.

See `architecture/modules.md` for the full module list and dependency graph.

---

## When you hit a wall

1. Read the relevant `contracts/<domain>.md`.
2. Read `ANTIPATTERNS.md`.
3. Check `adr/` for the underlying decision.
4. If still unclear, open an ADR draft (or contract change PR) for review.
5. If the question is "how should I implement this", check `architecture/grpc-integration.md` / `ops/auth-propagation.md` / `contracts/conventions.md`.

---

## First PR expectations

A typical PR (post W1):
- Touches one domain
- Includes contract update if behavior changes
- Includes test (unit + contract if applicable)
- Includes migration (if schema change) with the linter passing
- Includes Outbox event emission (if cross-app fanout)
- Includes audit log (if sensitive)
- Answers the four PR template questions:
  1. What user-visible behavior changed?
  2. What did the tests cover?
  3. Schema migration? Breaking?
  4. New OutboxEvent or Celery task?

---

## Quick reference

| Need | Look at |
|---|---|
| Dev workflow (make, DB, tests) | `../CONTRIBUTING.md` |
| API for a domain | `contracts/<domain>.md` |
| Cross-cutting rules | `contracts/conventions.md` |
| Why we did X | `adr/` |
| How to call gRPC service | `architecture/grpc-integration.md` |
| Deploy to server | `architecture/deployment.md` |
| Auth flow across services | `ops/auth-propagation.md` |
| Environments / settings | `ops/environments.md` |
| Incident response | `ops/runbooks/` |
| Legacy reference | `legacy/mobile-api-contract-full.md` |
| Migration plan | `migration/migration-plan.md` |
| What features to port | `migration/feature-inventory.md` |
