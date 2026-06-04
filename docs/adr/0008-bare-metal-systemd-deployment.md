# ADR-0008: Bare-metal + systemd deployment (no containers in production)

## Status
Accepted

## Context
The team has servers (VPS / dedicated) and is comfortable operating Linux. There is no immediate need to run on Kubernetes or other container orchestrators. Container-based production deployment would add: image registry, image build pipeline, orchestrator, network overlay, ingress controller, secrets distribution via the platform, observability re-plumbing — significant ops surface for a small team.

For local development, containerization remains useful for environment consistency.

## Decision

### Production
- Linux server (recommended: Ubuntu 22.04 LTS), running services directly via **systemd unit files**.
- **nginx** as reverse proxy + static file server + TLS terminator.
- **PostgreSQL 15** + **Redis 7** installed via apt packages (or compiled from official repos for newer versions). Single-host in V1; replication in V2.
- **gunicorn** with **uvicorn worker** runs Django (ASGI-capable for future WebSocket needs).
- **Celery worker** and **Outbox Dispatcher** are separate systemd units.
- gRPC services (Notification first) are each a separate systemd unit listening on a local port; nginx proxies external traffic if needed (most stays internal).
- Deployments: `git pull` + `systemctl restart <unit>`, orchestrated by **Ansible** playbooks (see `ops/`).
- Secrets via Doppler or AWS Secrets Manager (per ops/secrets.md), mounted as files at startup.
- TLS via Let's Encrypt + certbot, auto-renewing.

### Local development
- **docker-compose** still used for PostgreSQL + Redis on developer machines (consistency).
- Django + services run directly (`make dev`) for fast iteration.
- Profile-split compose: `core` (db + redis), `services` (gRPC), `observability` (Grafana stack).

### Why not k8s
- Operational cost: dedicated effort to maintain control plane, ingress, secrets, observability separately
- One-server deployment is sufficient for V1 and likely V2
- When horizontal scaling matters, we can migrate to k8s by writing k8s manifests; the systemd-first design doesn't preclude it
- Going k8s-first locks in patterns (sidecars, init containers) that bring no benefit at our scale

### Why not Docker in production (V1)
- Adds a build/registry stage to every deploy
- Process supervision becomes container restart policies, less native
- Logs need ship-side configuration; systemd + journald is simpler
- Cold-start time matters: container start adds 1-3s to deploy iteration

## Anti-decision
We do NOT:
- **Use Kubernetes for production V1**
- **Use Docker Swarm / Nomad / Podman** as a "lighter" orchestrator
- **Use Heroku-style PaaS** (Render, Fly.io, Railway): vendor lock-in + higher per-resource cost at scale
- **Use serverless (Lambda, Cloud Run)** for Django: cold starts + 15-minute timeouts conflict with our model
- **Skip Ansible**: manual ssh-deploy is unreviewed; Ansible playbooks force reproducibility

## Consequences

**Good**
- Lowest possible ops surface for small team
- Deploy iteration is fast (git pull + restart)
- Observability is direct (journald → Loki)
- Cost: one VM/box per environment

**Bad**
- Horizontal scaling requires migrating to multi-host (adds load balancer + DB read replicas + Redis cluster); revisited V2/V3
- Single-host = single point of failure until V2; mitigated by warm standby + DB backups
- Recovery from full-server loss requires re-running Ansible from scratch — tested in dry runs

**Neutral**
- Mobile and web see no difference whether backend runs in container or bare metal
