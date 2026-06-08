# ADR-0012: On-prem deployment without cloud or containers

## Status

Accepted. Extends [ADR-0008](0008-bare-metal-systemd-deployment.md) (bare-metal +
systemd) and constrains [ADR-0010](0010-observability-backend.md). ADR-0008 said
*how processes are supervised* (systemd, not containers); this ADR says *where
the platform runs and how the cloud-managed services it assumed are replaced* on
owned hardware with no public cloud.

## Context

The platform will run in a **self-owned datacenter / on-prem racks**, possibly
with limited or no public internet egress, on a **small ops team**. The earlier
deployment thinking still leaned on cloud primitives the team will not have:
object storage (S3), a secrets manager (KMS), managed Postgres (RDS), managed
TLS (ACM), and managed observability (CloudWatch).

Most of the stack is **self-host-friendly** — it is a Django WSGI monolith +
Outbox dispatcher + a couple of gRPC sidecars + PostgreSQL + Redis, and the
heavy external deps (Stripe, Ant Media live, notification/live gRPC) are all
behind fake-mode flags (`STRIPE_FAKE_MODE`, `LIVE_RUNTIME_ENABLED`,
`ANT_MEDIA_ENABLED`, `NOTIFICATION_ENABLED`), so the full REST API runs with zero
external dependencies. What needs an explicit decision is the **five things the
cloud used to provide**: object storage, secrets, TLS/CA, HA/failover, and
observability — plus how we keep deploys repeatable and reversible **without
container images**.

## Decision

### Where it runs
- Owned physical hosts, Ubuntu 22.04 LTS (or Debian/Rocky), provisioned and
  configured **only** via the Ansible playbooks in `ops/`. A full host is
  reproducible from Ansible + backups; no manual ssh changes.
- **No public cloud** for the application tier. No managed PaaS/serverless
  (conflicts already recorded in ADR-0008).

### Replacing "immutable images + atomic rollback" (no containers)
- **Release directories + symlink swap** (Capistrano-style):
  `/opt/brandable/releases/<git-sha>/` each with its own venv;
  `/opt/brandable/current` is an atomic symlink. Deploy = unpack → install deps →
  migrate → `ln -sfn` → `systemctl restart`. **Rollback = repoint the symlink +
  restart** (seconds, no rebuild).
- **Dependency delivery is offline-safe**: a build host with internet produces a
  pinned **wheelhouse** (or an internal `devpi` PyPI mirror); production installs
  from it, never directly from PyPI. Versions are fully locked.
- **systemd unit hardening as the isolation substitute**: per-service users +
  `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`,
  `ReadWritePaths`, `MemoryMax`. Namespace-level isolation without Docker.

### The five cloud replacements (chosen tools)
| Concern | On-prem decision |
|---|---|
| **Object storage** (media/video uploads, VOD) | **MinIO** (S3-compatible, systemd binary, erasure-coded). Keeps the S3 SDK code path so a future cloud move is a no-op. NFS/NAS is the fallback when S3 semantics aren't wanted. |
| **Secrets** (JWT RS256 key, Stripe keys, DB password) | **sops + age** (encrypted-in-git, rendered to `/etc/brandable/env` + `*.pem` at deploy) for V1; **HashiCorp Vault** only when dynamic DB creds / audit are required. |
| **TLS / CA** | **Let's Encrypt + DNS-01** if a public domain exists; **smallstep step-ca** (internal ACME) when air-gapped. No hand-rolled self-signed certs. |
| **HA / failover** | Postgres primary + **one async streaming replica**; manual/`repmgr` promote in V1 (Patroni deferred to avoid an etcd/consul control plane). Redis: AOF persistence + restart in V1 (Sentinel deferred). |
| **Observability** | Self-hosted per ADR-0010: Prometheus (scrapes the existing `/internal/metrics` + node/postgres/redis exporters), Loki+promtail (journald→Loki), Tempo (OTel, already wired), Grafana + Alertmanager→SMTP/IM. |

### Data durability (no S3/RDS safety net)
- **pgBackRest** for Postgres (incremental + retention + one-command PITR) to a
  NAS or MinIO; **restore drills are mandatory and scheduled**.
- **PgBouncer** (transaction mode) in front of Postgres for the multi-worker
  monolith.
- **3-2-1 backups** (3 copies, 2 media, 1 offsite) for DB dumps, media, the sops
  keys, and `/etc/brandable/env`.

### Networking & host security (replacing security groups)
- **nftables** default-deny; only 443 faces the internet. Postgres/Redis/gRPC
  bind to `127.0.0.1` or a private VLAN and never egress.
- SSH key-only + bastion + `fail2ban`; `unattended-upgrades`; `chrony` (JWT/TLS
  depend on a correct clock); out-of-band mgmt (IPMI/iDRAC) on an isolated network.

### Phasing
- **V1**: one app host (Django + dispatcher + gRPC) + one Postgres replica +
  MinIO/NAS + an ops host; pgBackRest offsite; step-ca/LE certs; sops secrets.
  Single point of failure accepted — rebuildable in ~30 min from backups + Ansible.
- **V2** (only when an SLA demands it): dual app hosts behind
  **HAProxy + keepalived (VIP)**, Postgres auto-failover (Patroni), Redis Sentinel,
  MinIO multi-node erasure coding.

### Allowed exception: the observability host *may* use containers
Running Prometheus/Loki/Tempo/Grafana as containers on a dedicated **ops host**
is permitted because that stack benefits most from containerization and is not in
the request path. The **application/data tier stays bare-metal**. This is the one
sanctioned hybrid boundary.

## Consequences

**Good**
- Lowest ops surface and cost for a small team; no per-resource cloud bill.
- Deploys stay fast and reversible (symlink swap), parity with the container goal
  of atomic rollback — without a registry/build pipeline.
- The S3-compatible (MinIO) and OTel/Prometheus choices keep a future cloud or
  k8s migration a configuration change, not a rewrite.

**Bad**
- The team now owns hardware lifecycle, capacity, UPS/power, and DR end-to-end.
- HA is manual in V1; a host loss means a short outage + restore, not seamless
  failover.
- Offline dependency delivery (wheelhouse/devpi) and an internal CA are extra
  moving parts a cloud would have hidden.

**Neutral**
- Live video (Ant Media) remains a separately-sized, bandwidth/CPU-heavy host and
  is out of scope here; it stays behind `ANT_MEDIA_ENABLED` until provisioned.
- Vault/Patroni/Sentinel are explicitly deferred, not rejected — they get adopted
  when audit/SLA needs cross their threshold.

## Anti-decision

We do NOT:
- **Use public cloud** for the application or data tier (no RDS/S3/ACM/KMS).
- **Containerize the application/data tier** in production (ADR-0008); containers
  are allowed only on the isolated observability host.
- **Adopt Kubernetes / Swarm / Nomad** to "orchestrate" on-prem — no control plane
  for a single/dual-host footprint.
- **Store secrets as plaintext on disk** — they are sops/age-encrypted or Vault-issued.
- **Put media on a single local disk long-term** — MinIO or NAS, so storage can be
  made redundant and relocated without touching app code.
- **Trust un-restored backups** — pgBackRest restore drills are part of the runbook.
