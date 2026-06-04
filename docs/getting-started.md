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

## Weeks 2-16 at a glance (Django + canary gRPC)

| Week | Deliverable |
|---|---|
| 2 | Django baseline (settings layering, errors lib, pagination, logging, telemetry, health) |
| 3 | Proto pipeline + OpenTelemetry across-process + JWT public key distribution |
| 4-5 | Identity V1 (with legacy account import + password hash compatibility) |
| 6-7 | Economy V1 (with legacy wallet balance import + ledger) |
| 8 | Events V1 (Outbox + Dispatcher + DLQ) + Audit V1 |
| 9 | NotificationService launches (canary), wired to welcome email on registration |
| 10 | PlatformConfig + Branding API |
| 11 | Payments V1 (Stripe + Blockchain LBC + LTT backends), top-up event flows through NotificationService |
| 12-13 | ChatService launches, DM scenario |
| 14 | Membership V1 (one-shot + Stripe subscription) |
| 15-16 | LiveRuntimeService launches, end-to-end gift flow (sync debit + async broadcast) |

V2 (post-cutover): content (drama, video catalog), commerce (shop, cart, orders), full membership.
V3: Live Runtime advanced features, real transcoding, push notifications, additional blockchain networks.

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
