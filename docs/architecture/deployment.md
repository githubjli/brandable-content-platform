# Deployment Architecture

Single-server, bare-metal + systemd, no containers in production. Per ADR-0008.

---

## 1. Topology (V1)

```
                                       ┌────────────────────────────────────┐
                                       │  Linux server (Ubuntu 22.04 LTS)    │
                                       │                                     │
            HTTPS (443)  ────────────►─┤  nginx                              │
                                       │  • TLS termination (Let's Encrypt)  │
                                       │  • reverse proxy                    │
                                       │  • static files (STATIC_ROOT)        │
                                       │  • media files (MEDIA_ROOT)          │
                                       │                                     │
                                       │  ├─► gunicorn :8000 (Django ASGI)    │
                                       │  │      • bcp-django.service          │
                                       │  │      • 4 uvicorn workers           │
                                       │  │                                    │
                                       │  ├─► :50051 (gRPC) — internal-only    │
                                       │  │      • bcp-notification.service    │
                                       │  │      (V2: bcp-chat, V3: bcp-live)  │
                                       │  │                                    │
                                       │  └─► Grafana :3000 (admin-only)       │
                                       │                                     │
                                       │  ┌─ Background workers ──────────┐   │
                                       │  │ • bcp-celery-worker.service    │   │
                                       │  │ • bcp-celery-beat.service       │   │
                                       │  │ • bcp-dispatcher.service        │   │
                                       │  └─────────────────────────────────┘   │
                                       │                                     │
                                       │  ┌─ Data services ─────────────────┐  │
                                       │  │ • postgresql.service             │  │
                                       │  │ • redis.service                  │  │
                                       │  └──────────────────────────────────┘  │
                                       │                                     │
                                       │  ┌─ Observability ─────────────────┐  │
                                       │  │ • prometheus.service              │  │
                                       │  │ • loki.service + promtail.service │  │
                                       │  │ • tempo.service                   │  │
                                       │  │ • grafana-server.service          │  │
                                       │  │ • alertmanager.service            │  │
                                       │  └────────────────────────────────────┘  │
                                       └────────────────────────────────────┘
```

V2/V3 may split: separate DB host, separate observability host, separate gRPC service hosts. The systemd model accommodates this without restructuring.

---

## 2. systemd unit catalog

| Unit | What | Restart policy | Depends on |
|---|---|---|---|
| `bcp-django.service` | gunicorn + uvicorn | on-failure, 5s | postgresql, redis |
| `bcp-celery-worker.service` | Celery worker | on-failure, 5s | postgresql, redis |
| `bcp-celery-beat.service` | Celery beat (periodic jobs) | on-failure, 5s | postgresql, redis |
| `bcp-dispatcher.service` | OutboxEvent dispatcher | on-failure, 5s | postgresql, redis |
| `bcp-notification.service` | NotificationService gRPC | on-failure, 5s | postgresql, redis |
| `bcp-chat.service` (V2) | ChatService gRPC | on-failure, 5s | postgresql, redis |
| `bcp-live-runtime.service` (V3) | LiveRuntimeService gRPC | on-failure, 5s | redis |

All units live in `ops/systemd/` and are deployed via Ansible to `/etc/systemd/system/`.

### Example unit file

```ini
# /etc/systemd/system/bcp-django.service
[Unit]
Description=brandable-content-platform Django (gunicorn)
After=network.target postgresql.service redis.service
Wants=postgresql.service redis.service

[Service]
Type=notify
User=bcp
Group=bcp
WorkingDirectory=/opt/bcp/django
EnvironmentFile=/etc/bcp/django.env
ExecStart=/opt/bcp/venv/bin/gunicorn config.asgi:application \
  --bind 127.0.0.1:8000 \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --access-logfile - \
  --error-logfile -
Restart=on-failure
RestartSec=5s
TimeoutStartSec=30s

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/bcp/django/media /var/log/bcp
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

---

## 3. nginx layout

```nginx
# /etc/nginx/sites-available/bcp.conf

upstream django {
    server 127.0.0.1:8000 fail_timeout=0;
}

server {
    listen 80;
    server_name api.bcp.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name api.bcp.example.com;

    ssl_certificate /etc/letsencrypt/live/api.bcp.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.bcp.example.com/privkey.pem;

    client_max_body_size 2G;  # for video uploads
    client_body_timeout 300s;

    # Static
    location /static/ {
        alias /opt/bcp/django/staticfiles/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
    location /media/ {
        alias /opt/bcp/django/media/;
        expires 30d;
    }

    # API
    location / {
        proxy_pass http://django;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Trace-Id $http_x_trace_id;
        proxy_read_timeout 60s;
    }
}
```

V3 adds a WebSocket location block for Live Runtime that proxies to `bcp-live-runtime` with `proxy_http_version 1.1` + `Upgrade` headers.

---

## 4. PostgreSQL

V1 single instance:
- `/etc/postgresql/15/main/postgresql.conf` tuned per server resources
- WAL archiving enabled (off-site to S3-compatible storage)
- Daily `pg_dump` + WAL archive (RPO: ~5 min)
- One DB per service: `bcp_django`, `bcp_notification`, `bcp_chat`, `bcp_live_runtime` (gRPC services have their own per ADR-0006)

V2 adds: streaming replica (warm standby), read replica for analytics.

### Backup verification
- Weekly: restore latest backup to a scratch DB on the same host; run sanity SELECTs
- Monthly: full DR drill — restore on a separate host, point Django at it, run integration tests
- Quarterly: rotate WAL archives older than 90 days to cold storage

---

## 5. Redis

V1 single instance:
- `appendonly yes` (AOF persistence)
- Used for: Celery broker, Celery result backend, OutboxDispatcher advisory state, JWKS cache, viewer count dedup, idempotency-key dedup cache

V2 adds: cluster or sentinel for HA.

---

## 6. Deployment process

### `deploy.sh` (Ansible-orchestrated)
```bash
# Run from local machine
ansible-playbook ops/ansible/deploy.yml --extra-vars "env=staging branch=main"
```

Steps the playbook performs:
1. SSH to target server(s)
2. `git fetch && git checkout <branch> && git pull`
3. `cd django && /opt/bcp/venv/bin/pip install -e . --quiet`
4. `python manage.py collectstatic --noinput`
5. `python manage.py migrate --noinput`
6. `make proto-gen` if proto files changed
7. `sudo systemctl restart bcp-django bcp-celery-worker bcp-dispatcher bcp-notification ...`
8. Wait 10s, verify each service via `systemctl is-active`
9. Smoke test: `curl /api/v1/health` → must return 200 with valid `trace_id`
10. If any step fails, abort + alert; previous code remains running on prior commit

### Rollback
```bash
ansible-playbook ops/ansible/deploy.yml --extra-vars "branch=<prev-sha>"
```

Atomic at the systemd-restart level. DB migrations are forward-compatible (per migration conventions in contracts/conventions.md §7).

---

## 7. Configuration & secrets

Per `ops/secrets.md`:
- Non-secret config in `/etc/bcp/<service>.env` (deployed by Ansible)
- Secrets from Doppler / AWS Secrets Manager, mounted as files at startup
- `.env.example` in repo documents required keys (no values)

---

## 8. Networking

V1:
- One server, one public IP
- nginx (443) is the only public-facing port
- gRPC services listen on `127.0.0.1` (internal-only)
- ufw allows: 22 (ssh, restricted IP), 80, 443
- fail2ban watches sshd

V2 if scaling to multi-host:
- Add private network between hosts
- VPN for ssh access
- TLS between Django and gRPC services (or service mesh)

---

## 9. TLS

Let's Encrypt + certbot:
- Initial: `sudo certbot --nginx -d api.bcp.example.com`
- Auto-renewal: certbot systemd timer (default install) + nginx reload hook
- Monitor renewal age; alert if < 14 days to expiry

---

## 10. Backups & DR

| What | Frequency | Where | Retention |
|---|---|---|---|
| PostgreSQL full dump | Daily 02:00 UTC | S3-compatible (off-host) | 30 days hot, 1 year cold |
| WAL archive | Continuous | Same | Same |
| Redis AOF | Continuous local + nightly to S3 | S3-compatible | 7 days |
| Media files | Weekly rsync | S3-compatible | 30 days |
| Application code | git remote (GitHub) | — | indefinite |
| `/etc/bcp/` env files | Daily | S3-compatible (encrypted) | 30 days |
| Server image (full disk) | Monthly | Provider snapshot | 3 months |

DR RTO target: 4 hours. RPO target: 5 minutes (WAL).

---

## 11. Monitoring & alerting

See `architecture/observability.md` for the full observability story. Production alerts route from Alertmanager to PagerDuty (or email/Slack equivalent).

Minimum V1 alert set:
- Any systemd unit not active for > 1 min
- Disk usage > 85%
- Memory usage > 90% sustained 5 min
- PostgreSQL connection count > 80% of max
- nginx 5xx rate > 1%
- DLQ depth > 0
- Outbox dispatch lag > 60s

---

## 12. Anti-patterns

- ❌ ssh deploy from a developer laptop (always via Ansible)
- ❌ `pip install` on the server outside the venv path
- ❌ Editing files directly on the server (any change goes through git + Ansible)
- ❌ Storing secrets in env files committed to repo
- ❌ Running migrations from one engineer's machine against production DB
- ❌ Skipping pre-deploy smoke tests
