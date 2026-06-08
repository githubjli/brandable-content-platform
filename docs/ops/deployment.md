# Single-host deployment

How to run the whole platform on **one Linux server**, bare-metal + systemd, per
[ADR-0008](../adr/0008-bare-metal-systemd-deployment.md). No Docker in
production: containers are a *local-dev* convenience only (`make dev`). For the
environment model (local / staging / production) see
[environments.md](./environments.md); for secret handling see
[secrets.md](./secrets.md).

> **Why not docker-compose in prod?** ADR-0008 rejects it (build/registry stage,
> container supervision, log shipping, cold-start cost). systemd + journald is
> the chosen supervisor. The repo ships the units in `ops/systemd/`.

---

## 1. Topology (one VM)

```
                      ┌──────────────── one Linux host ─────────────────┐
   Internet ─ 443 ──► │ nginx  (TLS, static/media, reverse proxy)        │
                      │   └─► 127.0.0.1:8000  gunicorn (Django, WSGI)    │  systemd: brandable-django
                      │        ├─ PostgreSQL 15   (apt)                   │
                      │        └─ Redis 7         (apt)                   │
                      │   Outbox dispatcher (manage.py run_dispatcher)   │  systemd: brandable-dispatcher
                      │   Notification gRPC  127.0.0.1:50051  (optional) │  systemd: brandable-notification
                      │   Live Runtime gRPC  127.0.0.1:50053  (optional) │  systemd: brandable-live-runtime
                      └──────────────────────────────────────────────────┘
   Real live video (Ant Media) is heavy — run it on its OWN host (or leave the
   live plane in fake mode). Everything else fits comfortably on one box.
```

**What's mandatory vs optional on day one**

| Component | Needed for | Notes |
|---|---|---|
| nginx + gunicorn + Postgres + Redis | the entire REST API | mandatory |
| Outbox dispatcher | events → handlers (welcome email, analytics hooks) | strongly recommended |
| Notification gRPC | welcome-email canary | optional; gated by `NOTIFICATION_ENABLED` |
| Live Runtime gRPC | real live streaming | optional; gated by `LIVE_RUNTIME_ENABLED` |
| Ant Media | real RTMP/WebRTC ingest+playback | separate host; gated by `ANT_MEDIA_ENABLED` |
| Stripe | real card payments | gated by `STRIPE_FAKE_MODE=false` |

The API is fully usable with only the mandatory row — payments and live run in
**fake mode** and still settle/serve end-to-end.

---

## 2. Sizing

| Profile | vCPU | RAM | Disk | Covers |
|---|---|---|---|---|
| Starter | 2 | 4 GB | 40 GB SSD | API + dispatcher + notification, fake-mode payments/live |
| Growth | 4 | 8 GB | 80 GB SSD | + real Stripe, more gunicorn workers, journald/Loki retention |

gunicorn workers: start at `2 × vCPU` (the shipped unit uses 4). Live video is
**not** sized here — Ant Media wants its own CPU/bandwidth budget.

---

## 3. Provision the host (Ubuntu 22.04 LTS)

```bash
sudo apt update && sudo apt install -y \
  python3.12 python3.12-venv \
  postgresql-15 redis-server nginx certbot python3-certbot-nginx git

# Service user + layout
sudo useradd -r -m -d /opt/brandable-content-platform -s /bin/bash brandable
sudo mkdir -p /etc/brandable/keys
sudo chown -R brandable:brandable /opt/brandable-content-platform
```

### PostgreSQL + Redis
```bash
sudo -u postgres psql -c "CREATE USER brandable WITH PASSWORD '<DB_PASSWORD>';"
sudo -u postgres psql -c "CREATE DATABASE brandable OWNER brandable;"
# Redis: bind 127.0.0.1 only (default on Ubuntu) and set a password if exposed.
```

---

## 4. Application

```bash
sudo -u brandable -i
cd /opt/brandable-content-platform
git clone <repo-url> .

# Virtualenv at the path the systemd units expect
python3.12 -m venv /opt/brandable-venv
/opt/brandable-venv/bin/pip install uv
/opt/brandable-venv/bin/uv pip install --python /opt/brandable-venv/bin/python -e ".[dev]"

# Generate the gRPC stubs (committed, but regenerate to be safe)
make proto-gen
```

### Secrets / env
```bash
# JWT production key pair (NOT make gen-dev-keys)
openssl genrsa -out /etc/brandable/keys/jwt_private.pem 2048
openssl rsa -in /etc/brandable/keys/jwt_private.pem -pubout -out /etc/brandable/keys/jwt_public.pem
sudo chown -R brandable:brandable /etc/brandable/keys && sudo chmod 600 /etc/brandable/keys/jwt_private.pem

# Env file read by every systemd unit
sudo cp ops/env.production.example /etc/brandable/env
sudo chown root:brandable /etc/brandable/env && sudo chmod 640 /etc/brandable/env
sudo editor /etc/brandable/env        # fill in DJANGO_SECRET_KEY, DATABASE_URL, ALLOWED_HOSTS, ...
```

### Migrate + static
```bash
cd /opt/brandable-content-platform/django
set -a; . /etc/brandable/env; set +a
/opt/brandable-venv/bin/python manage.py migrate --no-input
/opt/brandable-venv/bin/python manage.py collectstatic --no-input
/opt/brandable-venv/bin/python manage.py check --deploy   # sanity-check prod hardening
/opt/brandable-venv/bin/python manage.py createsuperuser  # for /admin
```

---

## 5. systemd units

The repo ships them in `ops/systemd/`:

| Unit | Process |
|---|---|
| `brandable-django.service` | gunicorn (Django, `127.0.0.1:8000`) |
| `brandable-dispatcher.service` | Outbox dispatcher (`manage.py run_dispatcher`) |
| `brandable-notification.service` | Notification gRPC (`:50051`) — optional |
| `brandable-live-runtime.service` | Live Runtime gRPC (`:50053`) — optional |

```bash
sudo cp ops/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now brandable-django brandable-dispatcher
# Optional sidecars:
sudo systemctl enable --now brandable-notification     # if NOTIFICATION_ENABLED=true
sudo systemctl enable --now brandable-live-runtime     # if LIVE_RUNTIME_ENABLED=true
```

---

## 6. nginx + TLS

```bash
sudo cp ops/nginx/brandable.conf /etc/nginx/sites-available/brandable
sudo ln -s /etc/nginx/sites-available/brandable /etc/nginx/sites-enabled/
sudo editor /etc/nginx/sites-available/brandable   # set server_name + the allow CIDRs
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d api.example.com             # auto-renewing TLS
```

The shipped conf already:
- redirects 80 → 443, serves `/static/` + `/media/`, `client_max_body_size 2g`;
- **denies `/internal/metrics`** and **restricts `/admin/`** to private/operator CIDRs (edit them).

---

## 7. Verify

```bash
curl -fsS https://api.example.com/api/v1/health           # → 200
curl -fsS https://api.example.com/.well-known/jwks.json    # JWT public keys
systemctl status brandable-django brandable-dispatcher --no-pager
journalctl -u brandable-django -n 50 --no-pager
```

---

## 8. Deploys & rollback

Per ADR-0008, deploys are `git pull` + restart, ideally driven by the Ansible
playbook in `ops/ansible/deploy.yml`:

```bash
cd /opt/brandable-content-platform && git pull
/opt/brandable-venv/bin/uv pip install --python /opt/brandable-venv/bin/python -e ".[dev]"
cd django && /opt/brandable-venv/bin/python manage.py migrate --no-input
/opt/brandable-venv/bin/python manage.py collectstatic --no-input
sudo systemctl restart brandable-django brandable-dispatcher
```

Rollback = `git checkout <previous-tag>` + restart (migrations are written to be
forward-compatible across one release; see
[ADR-0009](../adr/0009-migration-strategy.md)).

---

## 9. Backups & DR

- **Postgres**: nightly `pg_dump` to off-host storage (cron), plus WAL archiving
  for PITR once traffic justifies it.
- **JWT keys** + `/etc/brandable/env`: back up to the secrets manager, not the box.
- Single-host = single point of failure until V2 (warm standby + replica). DR =
  re-run Ansible against a fresh box + restore the latest dump.

---

## 10. Going to real payments / live

- **Stripe**: set `STRIPE_FAKE_MODE=false` + `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET`,
  and point the Stripe dashboard webhook at `POST /api/v1/payments/webhooks/stripe`.
- **Live**: stand up Ant Media (own host), set `ANT_MEDIA_ENABLED=true` +
  `ANT_MEDIA_BASE_URL` on the live-runtime unit, and `LIVE_RUNTIME_ENABLED=true`
  on Django so the adapter calls the gRPC service. Broadcast fan-out (the
  viewer WebSocket gateway) is still deferred — see the live-runtime contract.
