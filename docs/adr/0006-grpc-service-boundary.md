# ADR-0006: gRPC service boundary rules

## Status
Accepted

## Context
Three gRPC services are committed for the new platform: Notification (W9 canary), Chat (W12-13), Live Runtime (W15-16). Without strict boundary rules, microservices degrade into a distributed monolith: services that share a database, that read each other's tables, that deploy lock-step. We want the opposite.

## Decision

### Build order (strict)
1. **Notification** (W9, canary). Simplest. Validates proto pipeline, auth propagation, observability, deployment.
2. **Chat** (W12–13).
3. **Live Runtime** (W15–16).

The three are **not** built in parallel. The infrastructure (proto / JWKS / tracing / systemd / CI) is a one-time investment; Notification pays for it; the next two ride on it.

### Boundary rules
1. **A gRPC service must NOT read Django's database directly**, not even read-only. Need data? Get it via RPC or via subscribed Outbox events.
2. **A gRPC service may NOT call Django's HTTP API to mutate state.** Mutations are owned by Django services (`services.py`).
3. **Service-to-service calls use service account JWTs**, never the end-user's token (per ADR-0005).
4. **Each service owns its own persistent storage** (Chat owns its messages DB; Notification owns delivery records). The gRPC service is the source of truth for its domain.
5. **Service interfaces are versioned via proto package** (`<domain>.v1`, `<domain>.v2`). Breaking changes increment the version; both versions coexist for ≥ 1 release.

### Implementation language
V1 ships all three in **Python (grpcio + asyncio)**. See ADR-0007 for the rewrite policy.

### Resource ownership map (no overlap)
| Resource | Owner |
|---|---|
| User / Auth | Django (identity) |
| Wallet / Ledger | Django (economy) |
| Live stream metadata | Django (content/live) |
| Live realtime state, Ant Media bridge | Live Runtime |
| DM messages | ChatService |
| Notification templates + delivery records | NotificationService |

## Anti-decision
We do NOT:
- **Use REST for service-to-service**: gRPC's schema discipline and streaming support are the reason we chose it.
- **Share PostgreSQL between Django and gRPC services**: tempting for "just one little join"; first step to a distributed monolith.
- **Allow gRPC services to mutate Django's domain by writing to its DB**: even with permissions, the contract surface becomes the DB schema instead of an API.
- **Pre-emptively design APIs for hypothetical fourth/fifth services**: design what we need; extend as required.

## Consequences

**Good**
- Each service can be deployed and scaled independently
- Data ownership is explicit; debugging "where does this live" has an answer
- Future Go rewrites per service do not affect callers

**Bad**
- Some data duplication (Notification may cache user emails)
- Cross-service queries become RPC chatter — accepted; usually mitigated by caching at the boundary

**Neutral**
- Proto management is a first-class engineering responsibility (see contracts/conventions.md proto rules)
